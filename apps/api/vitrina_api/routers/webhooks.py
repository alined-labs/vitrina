"""Webhooks router. Owner: MX2 (plan.md §3).

Contract paths: /webhooks/whatsapp.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Query, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.datastructures import State

from vitrina_catalog.sellers import InMemorySellerStore
from vitrina_conversation.api import (
    BackgroundInboundQueue,
    InboundQueue,
    Ingest,
    InvalidSignatureError,
    PayloadTooLargeError,
    PostgresInboundQueue,
    bookings,
    conversation_store,
    memory_pipeline,
    messenger,
    pacer,
    sellers_in_memory,
    sellers_on_postgres,
)
from vitrina_core.auth import WhatsAppSignature
from vitrina_core.http import ProblemCode, ProblemError, problem_responses
from vitrina_core.settings import Settings
from vitrina_whatsapp.handshake import verify_subscription

router = APIRouter(tags=["Webhooks"])


@router.get(
    "/webhooks/whatsapp",
    operation_id="verifyWhatsappWebhook",
    summary="Meta verification handshake",
    response_class=PlainTextResponse,
    response_description="Echoes `hub.challenge`",
    responses=problem_responses(403),
)
async def verify_whatsapp_webhook(
    request: Request,
    mode: Annotated[Literal["subscribe"], Query(alias="hub.mode")],
    token: Annotated[str, Query(alias="hub.verify_token")],
    challenge: Annotated[str, Query(alias="hub.challenge")],
) -> str:
    settings: Settings = request.app.state.settings
    expected = settings.whatsapp_verify_token
    answer = verify_subscription(
        mode, token, challenge, verify_token=expected.get_secret_value() if expected else ""
    )
    if answer is None:
        raise ProblemError(ProblemCode.FORBIDDEN, detail="the verify token does not match")
    return answer


@router.post(
    "/webhooks/whatsapp",
    operation_id="receiveWhatsappWebhook",
    summary="Inbound messages and status callbacks from WhatsApp Cloud API",
    description=(
        "Validates `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with the app secret),\n"
        "persists the raw payload, deduplicates by `message_id`, enqueues `process_inbound`,\n"
        "and acknowledges within 2 s (FR-021). Opt-out keywords are handled before anything "
        "else (FR-063).\n"
    ),
    response_class=Response,
    response_description="Acknowledged",
    responses=problem_responses(401),
)
async def receive_whatsapp_webhook(
    request: Request,
    background: BackgroundTasks,
    signature: WhatsAppSignature,
    _payload: Annotated[
        dict[str, Any],
        Body(description="Raw WhatsApp Cloud API webhook payload (passed through; see Meta docs)"),
    ],
) -> Response:
    body = await request.body()  # the exact bytes Meta signed
    try:
        await _ingest(request.app.state, background).receive(body, signature)
    except InvalidSignatureError as error:
        raise ProblemError(
            ProblemCode.INVALID_SIGNATURE, detail="X-Hub-Signature-256 does not match the body"
        ) from error
    except PayloadTooLargeError as error:
        raise ProblemError(ProblemCode.PAYLOAD_TOO_LARGE, detail=str(error)) from error
    return Response(status_code=200)


def _ingest(state: State, background: BackgroundTasks) -> Ingest:
    settings: Settings = state.settings
    store = conversation_store(state)
    if state.sessions is not None:
        queue: InboundQueue = PostgresInboundQueue(state.sessions, state.jobs)
        sellers = sellers_on_postgres(state.sessions)
    else:
        # No database: answer after the ack, in this process, through the same pipeline.
        queue = BackgroundInboundQueue(
            background.add_task,
            lambda: memory_pipeline(
                settings=settings,
                store=store,
                compliance=state.compliance,
                seller_settings=state.sellers.settings,
                chat=state.providers.chat,
                catalog=state.catalog,
                messenger=messenger(state),
                pacer=pacer(state),
                bookings=bookings(state),
                providers=state.providers,
            ),
        )
        directory = state.sellers
        sellers = sellers_in_memory(
            directory if isinstance(directory, InMemorySellerStore) else InMemorySellerStore()
        )
    secret = settings.whatsapp_app_secret
    return Ingest(
        store=store,
        queue=queue,
        sellers=sellers,
        app_secret=secret.get_secret_value() if secret else "",
    )
