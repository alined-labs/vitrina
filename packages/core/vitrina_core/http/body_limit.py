"""Request body limits: a body over its route's limit is refused before anything reads it.

Neither uvicorn nor Starlette limits a request body. Without this, the idempotency middleware
would buffer a whole POST body before authentication, FastAPI would parse a whole JSON body
before the route checks anything, and uploads would be spooled in full before a route could
count them. The API is one small replica that also receives the WhatsApp webhook, so one
oversized request must not take it down.

A ``Content-Length`` over the limit is answered 413 at once. A body sent without one is counted
as it arrives and cut off at the limit: the 413 comes from FastAPI's problem handler when a
route is reading it, or from here when a middleware is.
"""

from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vitrina_core.http.problems import ProblemCode, ProblemError, problem_response

DEFAULT_BODY_LIMIT = 1024 * 1024
"""Every body without a limit of its own is JSON, and far smaller than this."""


class BodyTooLargeError(HTTPException):
    """Raised from ``receive`` once a body passes its limit; it becomes a 413 problem."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


class BodyLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, limits: Mapping[str, int], default: int = DEFAULT_BODY_LIMIT
    ) -> None:
        """``limits``: request path (``/v1/assets``) to its largest body in bytes."""
        self.app = app
        self.limits = dict(limits)
        self.default = default

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limits.get(scope["path"], self.default)
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await _too_large(limit)(scope, receive, send)
            return

        received = 0
        started = False

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLargeError
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except BodyTooLargeError:
            if started:
                raise
            await _too_large(limit)(scope, receive, send)


def _too_large(limit: int) -> ASGIApp:
    detail = f"The request body is over this route's limit of {limit} bytes."
    return problem_response(ProblemError(ProblemCode.PAYLOAD_TOO_LARGE, detail=detail))
