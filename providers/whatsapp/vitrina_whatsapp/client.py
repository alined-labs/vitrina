"""WhatsApp Cloud API messenger: text, image and template messages (FR-028, FR-064).

``WhatsAppClient`` implements ``vitrina_core.providers.Messenger`` (T010); the local mock is
the other implementation (ADR-0011). Each send is one ``POST /{version}/{phone_number_id}/
messages`` with a timeout. Throttling and server errors are retried with exponential backoff,
at most 3 attempts in total (R12, AS-3.11). Meta asks clients to key error handling on its
error codes, which often arrive as HTTP 400, so this adapter keeps its own retry loop instead
of ``with_backoff`` (whose Retry-After handling expects HTTP 429). A send that may already have
reached Meta (read timeout, dropped connection) is never retried: a duplicate message to a
customer is worse than a reported failure. A marketing template goes further: a server error can
also hide a message Meta took, so a template is posted again only after an answer that proves
nothing went out (``nothing_sent``). Replies keep the server-error retry: they answer a customer
who is waiting, and no rule counts them. Pacing (≥ 6 s between messages to one recipient)
is the caller's job (``conversation``, T027).
"""

import asyncio
import logging
import mimetypes
from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import TracebackType
from typing import Any, Literal, Self
from urllib.parse import quote, urlsplit

import httpx2
from pydantic import BaseModel, Field, ValidationError

from vitrina_core.providers import (
    Blob,
    Messenger,
    OutboundMessage,
    OutboundTemplate,
    OutboundText,
    ProviderError,
    SendResult,
)
from vitrina_core.providers.errors import ErrorScope
from vitrina_whatsapp.config import DEFAULT_GRAPH_VERSION, GRAPH_BASE_URL, WhatsAppConfig

logger = logging.getLogger(__name__)

PROVIDER = "whatsapp"

# Cloud API error codes whose documented remedy is to try again (developers.facebook.com/docs/
# whatsapp/cloud-api/support/error-codes, read 2026-10-27): API and account rate limits (4,
# 80007), throughput (130429), pair rate limit (131056), unknown or temporary failures (1, 2,
# 131000, 131016). Also maintenance mode (131057): the page gives no remedy, only that the account
# is undergoing it, for instance during a throughput upgrade, so it is treated as a wait.
RETRYABLE_ERROR_CODES = frozenset({1, 2, 4, 80007, 130429, 131000, 131016, 131056, 131057})
# Of those, the ones that say the message did not go out, whatever the HTTP status. Code 1 does
# not: the page calls it an invalid request "or possible server error" (read 2026-11-02).
RETRY_SAFE_ERROR_CODES = RETRYABLE_ERROR_CODES - {1}

# Who a refusal concerns, from the same page (read 2026-11-01), so a send to many customers knows
# whether to go on. About this customer: the business blocked them (130403), a marketing
# experiment held the message (130472), the number is the sender's (131021), undeliverable
# (131026), outside the 24 h window (131047), held for ecosystem engagement (131049), opted out
# of marketing (131050), too many messages to this customer (131056).
RECIPIENT_ERROR_CODES = frozenset({130403, 130472, 131021, 131026, 131047, 131049, 131050, 131056})
# About the account, number or template, so the next customer fares no better: token and
# permissions (0, 3, 10, 190, 200-299, 131005), number deleted or unregistered (33, 131045,
# 133010), account restricted (368, 131031), payment (131042), sending limits (131048, 131064),
# marketing templates off (131063), the template itself missing,
# refused, paused or disabled or its parameter count wrong (132000, 132001, 132007, 132015,
# 132016). Any other code stays ``unknown``: the page does not say whom it concerns.
ACCOUNT_ERROR_CODES = frozenset(
    {0, 3, 10, 33, 190, 368, 131005, 131031, 131042, 131045, 131048, 131063, 131064}
    | {132000, 132001, 132007, 132015, 132016, 133010}
    | set(range(200, 300))
)

# The largest inbound media WhatsApp delivers that this adapter reads: audio and video up to
# 16 MB, images up to 5 MB (Cloud API media reference, read 2026-10-27).
MAX_MEDIA_BYTES = 16 * 1024 * 1024

# Failures that happen before the request leaves this process: retrying cannot duplicate a send.
_NEVER_SENT = (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)

# A number as a request timeout sets four limits, one each to connect, write, read and wait for a
# free connection, so one attempt can last that many times the timeout.
_TIMEOUT_PHASES = 4


class WhatsAppSendError(ProviderError):
    """A request that Meta refused or that could not be completed.

    ``retryable`` tells the caller whether trying again later can succeed; ``retry_after_s`` is
    set when Meta asked for a pause longer than a send is allowed to block. ``scope`` says
    whether the refusal concerns this customer or the whole account, and ``nothing_sent`` whether
    Meta's answer proves the message did not go out. ``code``,
    ``details`` and ``fbtrace_id`` are Meta's, for handling (131047: outside the 24 h window,
    send a template) and for support requests.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        attempts: int,
        status_code: int | None = None,
        code: int | None = None,
        details: str | None = None,
        fbtrace_id: str | None = None,
        retry_after_s: float | None = None,
        scope: ErrorScope = "unknown",
        nothing_sent: bool = False,
    ) -> None:
        super().__init__(
            message,
            provider=PROVIDER,
            retryable=retryable,
            status_code=status_code,
            scope=scope,
            retry_after_s=retry_after_s,
            nothing_sent=nothing_sent,
        )
        self.attempts = attempts
        self.code = code
        self.details = details
        self.fbtrace_id = fbtrace_id


class WhatsAppClient(Messenger):
    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        graph_version: str = DEFAULT_GRAPH_VERSION,
        base_url: str = GRAPH_BASE_URL,
        http: httpx2.AsyncClient | None = None,
        timeout_s: float = 10.0,
        max_attempts: int = 3,
        backoff_s: float = 1.0,
        max_backoff_s: float = 8.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not access_token or not phone_number_id:
            raise ValueError("WhatsApp sends need an access token and a phone number ID")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.graph_url = f"{base_url.rstrip('/')}/{graph_version}"
        number_url = f"{self.graph_url}/{phone_number_id}"
        self.messages_url = f"{number_url}/messages"
        self.media_url = f"{number_url}/media"
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._owns_http = http is None
        self._http = httpx2.AsyncClient() if http is None else http
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._max_backoff_s = max_backoff_s
        self._sleep = sleep

    @classmethod
    def from_config(cls, config: WhatsAppConfig, *, http: httpx2.AsyncClient | None = None) -> Self:
        return cls(
            access_token=config.access_token,
            phone_number_id=config.phone_number_id,
            graph_version=config.graph_version,
            http=http,
        )

    @property
    def longest_send_s(self) -> float:
        """The most one send can take, retries included: every attempt at its limit, then waits.

        A wait is at most ``max_backoff_s``: when Meta asks for longer, the send gives up instead.
        """
        attempts = self._max_attempts * _TIMEOUT_PHASES * self._timeout_s
        return attempts + (self._max_attempts - 1) * self._max_backoff_s

    async def send(self, message: OutboundMessage) -> SendResult:
        """``Messenger`` entry point: one outbound message of any kind."""
        if isinstance(message, OutboundText):
            return await self.send_text(message.to, message.text)
        if isinstance(message, OutboundTemplate):
            return await self.send_template(
                message.to,
                message.template_name,
                language=message.language,
                components=message.components,
            )
        if message.image_url is not None:
            return await self.send_image(
                message.to, link=message.image_url, caption=message.caption
            )
        if message.image is not None:
            media_id = await self.upload_media(message.image)
            return await self.send_image(message.to, media_id=media_id, caption=message.caption)
        raise ValueError("an outbound image needs image or image_url")

    async def send_text(
        self, to: str, body: str, *, preview_url: bool = False, reply_to: str | None = None
    ) -> SendResult:
        """Free text; Meta delivers it only inside the 24 h customer-service window."""
        return await self._send(to, "text", {"body": body, "preview_url": preview_url}, reply_to)

    async def send_image(
        self,
        to: str,
        *,
        link: str | None = None,
        media_id: str | None = None,
        caption: str | None = None,
        reply_to: str | None = None,
    ) -> SendResult:
        """An image by public HTTPS ``link`` or by uploaded ``media_id`` (exactly one)."""
        if link is not None and media_id is None:
            image: dict[str, str] = {"link": link}
        elif media_id is not None and link is None:
            image = {"id": media_id}
        else:
            raise ValueError("send_image needs exactly one of link or media_id")
        if caption:
            image["caption"] = caption
        return await self._send(to, "image", image, reply_to)

    async def send_template(
        self,
        to: str,
        name: str,
        *,
        language: str = "es",
        components: Sequence[Mapping[str, Any]] = (),
    ) -> SendResult:
        """An approved template; the only kind of message allowed outside the 24 h window."""
        template: dict[str, Any] = {"name": name, "language": {"code": language}}
        if components:
            template["components"] = [dict(component) for component in components]
        return await self._send(to, "template", template, None, retry_unclear=False)

    async def upload_media(self, blob: Blob) -> str:
        """Upload ``blob`` so it can be sent by ID; returns the media ID."""
        extension = mimetypes.guess_extension(blob.mime_type) or ""
        return await self._request(
            "POST",
            self.media_url,
            _uploaded,
            repeatable=True,  # a second upload only leaves an unused media object behind
            data={"messaging_product": "whatsapp", "type": blob.mime_type},
            files={"file": (f"upload{extension}", blob.data, blob.mime_type)},
        )

    async def download_media(self, media_id: str) -> Blob:
        """Inbound media by the id a webhook carries (T050, T051).

        The Graph API gives a URL that lasts 5 minutes; the bytes come from it with the same
        token. A 404 there means the URL expired, so a fresh one is asked for once.
        """
        for attempt in (1, 2):
            info = await self._request(
                "GET", f"{self.graph_url}/{quote(media_id, safe='')}", _media_info, repeatable=True
            )
            if urlsplit(info.url).scheme != "https":
                # The token goes with the download: never over plain HTTP or another scheme.
                raise WhatsAppSendError(
                    "the media link is not https", retryable=False, attempts=attempt
                )
            try:
                data = await self._request("GET", info.url, _media_bytes, repeatable=True)
            except WhatsAppSendError as error:
                if error.status_code == 404 and attempt == 1:
                    continue
                raise
            return Blob(data=data, mime_type=info.mime_type)
        raise AssertionError("unreachable: the second attempt returns or raises")

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _send(
        self,
        to: str,
        kind: str,
        content: Mapping[str, Any],
        reply_to: str | None,
        *,
        retry_unclear: bool = True,
    ) -> SendResult:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _recipient(to),
            "type": kind,
            kind: content,
        }
        if reply_to:
            payload["context"] = {"message_id": reply_to}
        return await self._request(
            "POST",
            self.messages_url,
            _accepted,
            repeatable=False,
            retry_unclear=retry_unclear,
            json=payload,
        )

    async def _request[T](
        self,
        method: Literal["GET", "POST"],
        url: str,
        read: Callable[[httpx2.Response, int], T],
        *,
        repeatable: bool,
        retry_unclear: bool = True,
        **content: Any,
    ) -> T:
        """One request with the retry policy; ``read`` turns a successful response into a result.

        ``repeatable`` requests may be resent after any transport failure; the others only when
        the failure happened before the request left (``_NEVER_SENT``). Without ``retry_unclear``,
        an answer that does not prove nothing went out is raised at once.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._http.request(
                    method, url, headers=self._headers, timeout=self._timeout_s, **content
                )
            except httpx2.RequestError as exc:
                if not (repeatable or isinstance(exc, _NEVER_SENT)):
                    raise WhatsAppSendError(
                        f"Cloud API request failed after it was sent ({type(exc).__name__}); "
                        "delivery unknown, not retried",
                        retryable=False,
                        attempts=attempt,
                    ) from exc
                error = WhatsAppSendError(
                    f"could not reach the Cloud API ({type(exc).__name__})",
                    retryable=True,
                    attempts=attempt,
                    scope="account",  # nothing reached Meta: the next customer fares no better
                    # A repeatable request may have left; a send retried here never did.
                    nothing_sent=isinstance(exc, _NEVER_SENT),
                )
                error.__cause__ = exc
            else:
                if response.is_success:
                    return read(response, attempt)
                error = _refused(response, attempt)

            delay = (
                None
                if not (retry_unclear or error.nothing_sent)
                else self._retry_delay(error, attempt)
            )
            if delay is None:
                raise error
            logger.warning(
                "WhatsApp request attempt %d/%d failed (HTTP %s, code %s); retrying in %.1f s",
                attempt,
                self._max_attempts,
                error.status_code,
                error.code,
                delay,
            )
            await self._sleep(delay)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _retry_delay(self, error: WhatsAppSendError, attempt: int) -> float | None:
        """Seconds to wait before the next attempt, or None to give up now."""
        if not error.retryable or attempt >= self._max_attempts:
            return None
        if error.retry_after_s is not None and error.retry_after_s > self._max_backoff_s:
            return None  # Meta wants a longer pause than a send may block: the caller reschedules.
        backoff = min(self._backoff_s * 2.0 ** (attempt - 1), self._max_backoff_s)
        return max(backoff, error.retry_after_s or 0.0)


def _recipient(to: str) -> str:
    """Cloud API recipients are the digits of the E.164 number (the customer's ``wa_id``)."""
    digits = to.strip().removeprefix("+")
    if not (digits.isascii() and digits.isdigit()):
        raise ValueError("recipient must be an E.164 phone number or a WhatsApp ID")
    return digits


def _accepted(response: httpx2.Response, attempt: int) -> SendResult:
    try:
        body = _Accepted.model_validate_json(response.content)
    except ValidationError as exc:
        raise WhatsAppSendError(
            "Cloud API answered 2xx without a message id; the message may have been sent",
            retryable=False,
            attempts=attempt,
            status_code=response.status_code,
        ) from exc
    return SendResult(message_id=body.messages[0].id, status="sent")


def _media_info(response: httpx2.Response, attempt: int) -> _MediaInfo:
    try:
        return _MediaInfo.model_validate_json(response.content)
    except ValidationError as exc:
        raise WhatsAppSendError(
            "Cloud API answered a media lookup without a URL",
            retryable=False,
            attempts=attempt,
            status_code=response.status_code,
        ) from exc


def _media_bytes(response: httpx2.Response, attempt: int) -> bytes:
    if len(response.content) > MAX_MEDIA_BYTES:
        raise WhatsAppSendError(
            f"media of {len(response.content)} bytes is larger than WhatsApp allows",
            retryable=False,
            attempts=attempt,
            status_code=response.status_code,
        )
    return response.content


def _uploaded(response: httpx2.Response, attempt: int) -> str:
    try:
        return _Uploaded.model_validate_json(response.content).id
    except ValidationError as exc:
        raise WhatsAppSendError(
            "Cloud API answered 2xx to a media upload without a media id",
            retryable=True,
            attempts=attempt,
            status_code=response.status_code,
        ) from exc


def _refused(response: httpx2.Response, attempt: int) -> WhatsAppSendError:
    try:
        error = _ErrorBody.model_validate_json(response.content).error
    except ValidationError:
        error = _GraphError()
    retryable = (
        response.status_code == 429
        or response.status_code >= 500
        or error.code in RETRYABLE_ERROR_CODES
    )
    return WhatsAppSendError(
        f"Cloud API refused the request (HTTP {response.status_code}, code {error.code})",
        retryable=retryable,
        scope=_scope(response.status_code, error.code, retryable),
        nothing_sent=_nothing_sent(response.status_code, error.code),
        attempts=attempt,
        status_code=response.status_code,
        code=error.code,
        details=error.error_data.details if error.error_data else None,
        fbtrace_id=error.fbtrace_id,
        retry_after_s=_retry_after_s(response),
    )


def _scope(status_code: int, code: int | None, retryable: bool) -> ErrorScope:
    """A code about the customer wins: the pair limit (131056) is retried but concerns one."""
    if code in RECIPIENT_ERROR_CODES:
        return "recipient"
    if code in ACCOUNT_ERROR_CODES or status_code in (401, 403) or retryable:
        return "account"
    return "unknown"


def _nothing_sent(status_code: int, code: int | None) -> bool:
    """A refusal Meta answered, or a code that says so; a 5xx alone may hide an accepted send."""
    if code in RETRY_SAFE_ERROR_CODES:
        return True
    return status_code < 500 and code != 1


def _retry_after_s(response: httpx2.Response) -> float | None:
    """``Retry-After`` in seconds; the HTTP-date form is not used by the Graph API."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class _AcceptedMessage(BaseModel):
    id: str


class _Accepted(BaseModel):
    messages: tuple[_AcceptedMessage, ...] = Field(min_length=1)


class _Uploaded(BaseModel):
    id: str


class _MediaInfo(BaseModel):
    url: str
    mime_type: str


class _ErrorData(BaseModel):
    details: str | None = None


class _GraphError(BaseModel):
    code: int | None = None
    error_data: _ErrorData | None = None
    fbtrace_id: str | None = None


class _ErrorBody(BaseModel):
    error: _GraphError
