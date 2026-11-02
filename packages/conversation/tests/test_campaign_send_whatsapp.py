"""FR-064: a campaign pass through the real WhatsApp client, answered by a scripted Graph API.

The real client raises on a refusal and never answers ``failed``; these tests keep the send
pass honest about that, and about which refusals are one customer's and which the account's.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx2
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from vitrina_compliance.api import (
    AdRef,
    CampaignDeps,
    Candidate,
    InMemoryCampaigns,
    InMemoryCompliance,
    ReactivationCampaignCreate,
    Segment,
    preview_campaign,
    run_send,
    schedule_send,
)
from vitrina_compliance.calendar import BOGOTA
from vitrina_compliance.consents import ConsentRecordCreate
from vitrina_compliance.contacts import ContactIdentity
from vitrina_whatsapp.client import WhatsAppClient

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
PRODUCT = UUID("00000000-0000-7000-8000-0000000000b1")
AD = AdRef(UUID("00000000-0000-7000-8000-0000000000d1"), "A7")
WEDNESDAY_10 = datetime(2026, 10, 28, 10, 0, tzinfo=BOGOTA)
NUMBERS = ("573000000011", "573000000012", "573000000013")


def accepted(_number: str) -> httpx2.Response:
    return httpx2.Response(200, json={"messages": [{"id": "wamid.VITRINA-FIXTURE-OUT-0001"}]})


def refused(status: int, code: int) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"code": code}})


async def no_wait(_seconds: float) -> None:
    return None


async def send_all(answer: dict[str, httpx2.Response]) -> tuple[Any, list[str]]:
    """A campaign to ``NUMBERS``; each is answered as ``answer`` says, or accepted."""
    tried: list[str] = []

    def graph(request: httpx2.Request) -> httpx2.Response:
        number = json.loads(request.content)["to"]
        tried.append(number)
        return answer.get(number, accepted(number))

    identity = ContactIdentity(
        pepper=SecretStr("campaign-whatsapp-pepper"),
        key=SecretStr(Fernet.generate_key().decode()),
    )
    compliance = InMemoryCompliance(identity)
    found = []
    for number in NUMBERS:
        contact = await compliance.resolve_contact(SELLER, number)
        await compliance.append_consent(
            SELLER,
            contact.id,
            ConsentRecordCreate(
                purpose="marketing",
                status="granted",
                text_shown="¿Te aviso cuando haya promo? Responde SÍ",
                response_text="sí",
                channel="whatsapp",
            ),
            recorded_by="agent",
        )
        found.append(Candidate(contact.id, "shipping", PRODUCT, "Mochila tejida azul"))

    async def candidates(
        seller_id: UUID,
        segment: Segment,
        *,
        now: datetime,
        contact_ids: Sequence[UUID] | None = None,
    ) -> list[Candidate]:
        return found

    async def ads(seller_id: UUID, product_id: UUID, *, ad_id: UUID | None = None) -> AdRef:
        return AD

    store = InMemoryCampaigns(compliance)
    async with WhatsAppClient(
        access_token="fixture-access-token",
        phone_number_id="100000000000001",
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(graph)),
        sleep=no_wait,
    ) as client:
        deps = CampaignDeps(store, compliance, candidates, ads, messenger=client)
        preview = await preview_campaign(
            deps,
            SELLER,
            ReactivationCampaignCreate(template_name="vitrina_reactivacion"),
            now=WEDNESDAY_10,
        )
        await schedule_send(store, SELLER, preview.id, template_approved=True)
        run = await run_send(
            deps, SELLER, preview.id, business_name="Mochilas Ana", now=WEDNESDAY_10
        )
    return run, tried


@pytest.mark.parametrize(
    "number", NUMBERS, ids=("the first customer", "a middle customer", "the last customer")
)
async def test_fr_064_whatsapp_refusing_one_customer_leaves_the_others(number: str) -> None:
    run, tried = await send_all({number: refused(400, 131026)})

    assert tried == list(NUMBERS)
    assert (run.campaign.status, run.next_at, run.stopped) == ("sent", None, None)
    assert (run.campaign.counts.sent, run.campaign.counts.failed) == (2, 1)
    assert {d.reason for d in run.campaign.decisions if d.decision == "failed"} == {"SEND_FAILED"}


async def test_fr_064_an_expired_token_tries_one_customer_only() -> None:
    run, tried = await send_all({NUMBERS[0]: refused(401, 190)})

    assert tried == [NUMBERS[0]]
    assert (run.campaign.status, run.campaign.counts.failed) == ("sent", 3)
    assert run.stopped == "Cloud API refused the request (HTTP 401, code 190)"


async def test_fr_064_a_rate_limit_keeps_everyone_waiting() -> None:
    run, tried = await send_all({NUMBERS[0]: refused(429, 130429)})

    assert tried == [NUMBERS[0]] * 3  # the client's own attempts, then the pass stops
    assert (run.campaign.status, run.paused) == ("sending", True)
    assert (run.campaign.counts.allowed, run.campaign.counts.failed) == (3, 0)
