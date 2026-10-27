"""``X-Request-Id`` on every request and response (FR-084, NFR-06).

The middleware is the outermost layer the app adds, so it also turns an unexpected
exception into a 500 problem that still carries the request id.
"""

import logging
import re

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vitrina_core.http.context import request_id_var, seller_id_var
from vitrina_core.http.problems import internal_error_response
from vitrina_core.ids import uuid7

logger = logging.getLogger("vitrina.http")

# Ids from clients are echoed only when short and plain, so they stay safe in logs.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id")
        request_id = incoming if incoming and _SAFE_ID.match(incoming) else uuid7().hex
        token = request_id_var.set(request_id)
        seller_token = seller_id_var.set(None)
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("unhandled error", extra={"request_id": request_id})
            if started:
                raise
            await internal_error_response()(scope, receive, send_with_id)
        finally:
            seller_id_var.reset(seller_token)
            request_id_var.reset(token)
