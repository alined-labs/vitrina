"""FR-064: a campaign pass through the real WhatsApp client, answered by a scripted Graph API.

The real client raises on a refusal and never answers ``failed``; these tests keep the send
pass honest about that, and about which refusals are one customer's and which the account's. The
pass runs as the job runs it: with a clock and a pacer, so a contact is read again after each wait.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
    SendRun,
    preview_campaign,
    run_send,
    schedule_send,
)
from vitrina_compliance.calendar import BOGOTA
from vitrina_compliance.campaigns import CLOSING_MARGIN, FIRST_PAUSE
from vitrina_compliance.consents import ConsentRecordCreate
from vitrina_compliance.contacts import ContactIdentity
from vitrina_conversation.campaigns import paced
from vitrina_conversation.pacing import Pacer
from vitrina_whatsapp.client import WhatsAppClient

SELLER = UUID("00000000-0000-7000-8000-00000000000a")
PRODUCT = UUID("00000000-0000-7000-8000-0000000000b1")
AD = AdRef(UUID("00000000-0000-7000-8000-0000000000d1"), "A7")
WEDNESDAY_10 = datetime(2026, 10, 28, 10, 0, tzinfo=BOGOTA)
NUMBERS = ("573000000011", "573000000012", "573000000013")
TEMPLATE = "vitrina_reactivacion"

Answer = httpx2.Response | type[httpx2.TransportError]


def accepted() -> httpx2.Response:
    return httpx2.Response(200, json={"messages": [{"id": "wamid.VITRINA-FIXTURE-OUT-0001"}]})


def refused(status: int, code: int | None = None) -> httpx2.Response:
    error = {"code": code} if code is not None else {}
    return httpx2.Response(status, json={"error": error})


async def no_wait(_seconds: float) -> None:
    return None


@dataclass
class Campaign:
    """A campaign to ``NUMBERS`` on the real client; ``answers`` are per number, else accepted."""

    answers: dict[str, Answer] = field(default_factory=dict)
    tried: list[str] = field(default_factory=list)
    now: datetime = WEDNESDAY_10
    deps: CampaignDeps | None = None
    campaign_id: UUID | None = None

    def graph(self, request: httpx2.Request) -> httpx2.Response:
        number = json.loads(request.content)["to"]
        self.tried.append(number)
        answer = self.answers.get(number, accepted())
        if isinstance(answer, httpx2.Response):
            return answer
        raise answer("scripted transport failure", request=request)

    async def start(self, client: WhatsAppClient) -> None:
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
        self.deps = CampaignDeps(
            store,
            compliance,
            candidates,
            ads,
            messenger=client,
            pace=paced(Pacer(0.0)),
            clock=lambda: self.now,
        )
        preview = await preview_campaign(
            self.deps, SELLER, ReactivationCampaignCreate(template_name=TEMPLATE), now=self.now
        )
        await schedule_send(store, SELLER, preview.id, approved_template=TEMPLATE)
        self.campaign_id = preview.id

    async def run_pass(self, *, pauses: int = 0) -> SendRun:
        assert self.deps is not None
        assert self.campaign_id is not None
        return await run_send(
            self.deps,
            SELLER,
            self.campaign_id,
            business_name="Mochilas Ana",
            now=self.now,
            pauses=pauses,
        )

    def client(self) -> WhatsAppClient:
        return WhatsAppClient(
            access_token="fixture-access-token",
            phone_number_id="100000000000001",
            http=httpx2.AsyncClient(transport=httpx2.MockTransport(self.graph)),
            sleep=no_wait,
        )


async def send_all(answers: dict[str, Answer]) -> tuple[SendRun, list[str]]:
    campaign = Campaign(answers)
    async with campaign.client() as client:
        await campaign.start(client)
        return await campaign.run_pass(), campaign.tried


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


async def test_fr_064_the_pair_limit_fails_that_customer_and_does_not_pause_the_others() -> None:
    run, tried = await send_all({NUMBERS[0]: refused(400, 131056)})

    assert tried == [NUMBERS[0]] * 3 + list(NUMBERS[1:])  # the client retries the limit itself
    assert (run.paused, run.next_at, run.campaign.status) == (False, None, "sent")
    assert (run.campaign.counts.sent, run.campaign.counts.failed) == (2, 1)


async def test_fr_064_maintenance_mode_pauses_and_does_not_end_the_campaign() -> None:
    run, _ = await send_all({NUMBERS[0]: refused(400, 131057)})

    assert (run.paused, run.stopped, run.campaign.status) == (True, None, "sending")
    assert run.campaign.counts.failed == 0


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_fr_064_a_server_error_through_a_whole_pass_never_asks_that_customer_twice(
    status: int,
) -> None:
    campaign = Campaign({NUMBERS[0]: refused(status)})
    async with campaign.client() as client:
        await campaign.start(client)

        first = await campaign.run_pass()

        assert campaign.tried == [NUMBERS[0]]
        assert (first.paused, first.campaign.status) == (True, "sending")
        assert (first.campaign.counts.failed, first.campaign.counts.allowed) == (1, 2)

        # Meta is back: a blind retry would now reach the customer it may already have reached.
        del campaign.answers[NUMBERS[0]]
        campaign.now = WEDNESDAY_10 + FIRST_PAUSE
        second = await campaign.run_pass(pauses=1)

    assert campaign.tried == list(NUMBERS)
    assert (second.paused, second.next_at, second.campaign.status) == (False, None, "sent")
    assert (second.campaign.counts.sent, second.campaign.counts.failed) == (2, 1)


async def test_fr_064_a_server_error_that_lasts_costs_one_customer_per_pause() -> None:
    campaign = Campaign({number: refused(503) for number in NUMBERS})
    async with campaign.client() as client:
        await campaign.start(client)
        first = await campaign.run_pass()
        assert first.next_at is not None
        campaign.now = first.next_at
        second = await campaign.run_pass(pauses=1)

    assert campaign.tried == list(NUMBERS[:2])  # never the same one twice
    assert (second.paused, second.campaign.counts.failed) == (True, 2)


async def test_fr_064_a_read_timeout_through_a_whole_pass_is_not_sent_again() -> None:
    campaign = Campaign({NUMBERS[1]: httpx2.ReadTimeout})
    async with campaign.client() as client:
        await campaign.start(client)

        run = await campaign.run_pass()
        again = await campaign.run_pass()

    assert campaign.tried == list(NUMBERS)  # one unclear send does not stop the others
    assert (run.paused, run.campaign.status) == (False, "sent")
    assert (run.campaign.counts.sent, run.campaign.counts.failed) == (2, 1)
    assert again.campaign.counts == run.campaign.counts


async def test_fr_064_read_timeouts_in_a_row_pause_without_a_second_try() -> None:
    campaign = Campaign(dict.fromkeys(NUMBERS, httpx2.ReadTimeout))
    async with campaign.client() as client:
        await campaign.start(client)
        run = await campaign.run_pass()

    assert campaign.tried == list(NUMBERS)
    assert (run.paused, run.campaign.counts.failed) == (True, 3)


def test_as_6_3_a_send_that_starts_before_the_margin_is_over_before_closing() -> None:
    client = WhatsAppClient(access_token="fixture-access-token", phone_number_id="1")

    assert timedelta(seconds=client.longest_send_s) < CLOSING_MARGIN


async def test_as_6_3_every_retry_ends_inside_the_margin() -> None:
    """The real retries with their real waits, counted on a clock that never sleeps."""
    waited: list[float] = []

    async def count(seconds: float) -> None:
        waited.append(seconds)

    client = WhatsAppClient(
        access_token="fixture-access-token",
        phone_number_id="1",
        http=httpx2.AsyncClient(
            transport=httpx2.MockTransport(
                lambda _request: httpx2.Response(
                    429, headers={"Retry-After": "8"}, json={"error": {"code": 130429}}
                )
            )
        ),
        sleep=count,
    )
    with pytest.raises(Exception, match="130429"):
        await client.send_template(NUMBERS[0], TEMPLATE)
    await client.aclose()

    assert len(waited) == 2
    assert sum(waited) <= client.longest_send_s


async def test_as_6_3_the_real_client_is_not_called_inside_the_margin() -> None:
    campaign = Campaign(now=WEDNESDAY_10.replace(hour=19) - CLOSING_MARGIN)
    async with campaign.client() as client:
        await campaign.start(client)
        run = await campaign.run_pass()

    assert campaign.tried == []
    assert {d.decision for d in run.campaign.decisions} == {"deferred"}
