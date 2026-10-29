"""T071: a brief becomes creative jobs and a reactivation campaign preview (AS-5.2, FR-051)."""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from vitrina_compliance.api import TEMPLATE_NAME, ReactivationCampaignCreate
from vitrina_creative.api import CreativeJobCreate
from vitrina_learning.briefs import Brief, apply_brief
from vitrina_learning.testing import BAG, HANDMADE_AD, WALLET

BRIEF = UUID("0192f320-0a1b-7c2d-8e3f-4a5b6c7d8e01")
SELLER = UUID("00000000-0000-7000-8000-00000000000a")


def brief(**changes: object) -> Brief:
    data: dict[str, object] = {
        "id": BRIEF,
        "seller_id": SELLER,
        "period_from": date(2026, 10, 1),
        "period_to": date(2026, 10, 28),
        "status": "ready",
        "insights": [],
        "recommended_angles": [],
        "creative_directives": [
            {"product_id": BAG, "angle": "handmade", "hook": "story", "scene_brief": "Telar."},
            {"product_id": WALLET, "angle": "price", "scene_brief": "Mesa de madera."},
        ],
        "reactivation_segments": [
            {"objection_tag": "shipping", "message_angle": "envío", "suggested_ad_id": None},
            {"objection_tag": "price", "message_angle": "precio", "suggested_ad_id": HANDMADE_AD},
        ],
        "created_at": datetime(2026, 10, 28, tzinfo=UTC),
    }
    data.update(changes)
    return Brief.model_validate(data)


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.jobs: list[CreativeJobCreate] = []
        self.campaigns: list[ReactivationCampaignCreate] = []

    async def check(self, params: CreativeJobCreate) -> None:
        self.events.append(f"check {params.product_id}")

    async def start(self, params: CreativeJobCreate) -> UUID:
        self.events.append(f"start {params.product_id}")
        self.jobs.append(params)
        return UUID(int=len(self.jobs))

    async def preview(self, data: ReactivationCampaignCreate) -> UUID:
        self.campaigns.append(data)
        return UUID(int=99)


async def test_as_5_2_each_directive_is_a_creative_job_with_the_briefs_angle_and_scene() -> None:
    recorder = Recorder()

    applied = await apply_brief(
        brief(), check=recorder.check, start=recorder.start, preview=recorder.preview
    )

    assert applied.creative_job_ids == [UUID(int=1), UUID(int=2)]
    assert [
        (job.product_id, job.angles, job.scene_brief, job.brief_id) for job in recorder.jobs
    ] == [
        (BAG, ["handmade"], "Telar.", BRIEF),
        (WALLET, ["price"], "Mesa de madera.", BRIEF),
    ]
    # Every directive passes the dry run before any job is queued.
    assert recorder.events == [f"check {BAG}", f"check {WALLET}", f"start {BAG}", f"start {WALLET}"]


async def test_as_5_2_a_directive_with_a_hook_asks_its_job_for_that_hook_only() -> None:
    recorder = Recorder()

    await apply_brief(brief(), check=recorder.check, start=recorder.start, preview=recorder.preview)

    # The bag's directive names the story hook; the wallet's names none, so its job keeps
    # every hook the product can carry.
    assert [job.model_dump(include={"hooks"}) for job in recorder.jobs] == [
        {"hooks": ["story"]},
        {},
    ]


async def test_fr_051_the_segments_become_one_campaign_preview_nothing_is_sent() -> None:
    recorder = Recorder()

    applied = await apply_brief(
        brief(), check=recorder.check, start=recorder.start, preview=recorder.preview
    )

    [campaign] = recorder.campaigns
    assert applied.reactivation_campaign_id == UUID(int=99)
    assert (campaign.brief_id, campaign.template_name, campaign.ad_id) == (
        BRIEF,
        TEMPLATE_NAME,
        HANDMADE_AD,
    )
    assert campaign.segment.objection_tags == ["price", "shipping"]  # type: ignore[union-attr]


async def test_a_brief_without_segments_starts_no_campaign() -> None:
    recorder = Recorder()

    applied = await apply_brief(
        brief(reactivation_segments=[]),
        check=recorder.check,
        start=recorder.start,
        preview=recorder.preview,
    )

    assert applied.reactivation_campaign_id is None
    assert recorder.campaigns == []


async def test_a_directive_that_fails_the_dry_run_queues_nothing() -> None:
    recorder = Recorder()

    async def refuse(params: CreativeJobCreate) -> None:
        if params.product_id == WALLET:
            raise ValueError("the product has no photo")

    with pytest.raises(ValueError, match="no photo"):
        await apply_brief(brief(), check=refuse, start=recorder.start, preview=recorder.preview)

    assert recorder.jobs == []
    assert recorder.campaigns == []
