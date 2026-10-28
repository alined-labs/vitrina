"""Public interface of the learning module (constitution Art. VI.3).

Attribution analytics (T044): ``AttributionConsumer`` turns the conversation module's ``ad.*``
events into ``attribution_events``; ``attribution_report`` and ``objection_report`` serve
``/analytics/*``; ``ad_stats`` fills ``Ad.stats`` in the ads routes. Planned: generate_brief,
apply_brief (plan.md §3).
"""

from vitrina_learning.attribution import (
    AdDirectory,
    AdInfo,
    AttributionConsumer,
    CreativeAds,
    ad_stats,
    attribution_report,
    build_attribution_report,
    build_objection_report,
    objection_report,
)
from vitrina_learning.schemas import (
    AdAttribution,
    AdObjections,
    AngleAttribution,
    AttributionReport,
    ObjectionReport,
)
from vitrina_learning.tasks import ATTRIBUTION_SWEEP

__all__ = [
    "ATTRIBUTION_SWEEP",
    "AdAttribution",
    "AdDirectory",
    "AdInfo",
    "AdObjections",
    "AngleAttribution",
    "AttributionConsumer",
    "AttributionReport",
    "CreativeAds",
    "ObjectionReport",
    "ad_stats",
    "attribution_report",
    "build_attribution_report",
    "build_objection_report",
    "objection_report",
]
