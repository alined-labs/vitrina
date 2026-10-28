"""Public interface of the learning module (constitution Art. VI.3).

Attribution analytics (T044): ``AttributionConsumer`` turns the conversation module's ``ad.*``
events into ``attribution_events``; ``attribution_report`` and ``objection_report`` serve
``/analytics/*``; ``ad_stats`` fills ``Ad.stats`` in the ads routes. Weekly briefs (T070): ``build_dataset``
and ``write_brief`` behind the ``learning.generate_brief`` job, kept by ``PostgresBriefs``.
Planned: apply_brief (T071).
"""

from vitrina_learning.attribution import (
    AdDirectory,
    AdInfo,
    AttributionConsumer,
    CreativeAds,
    ad_stats,
    attributed_chats,
    attribution_report,
    build_attribution_report,
    build_objection_report,
    objection_report,
)
from vitrina_learning.briefs import (
    GENERATE_TASK,
    AppliedBrief,
    Brief,
    BriefContent,
    BriefDataset,
    PostgresBriefs,
    apply_brief,
    build_dataset,
    write_brief,
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
    "GENERATE_TASK",
    "AdAttribution",
    "AdDirectory",
    "AdInfo",
    "AdObjections",
    "AngleAttribution",
    "AppliedBrief",
    "AttributionConsumer",
    "AttributionReport",
    "Brief",
    "BriefContent",
    "BriefDataset",
    "CreativeAds",
    "ObjectionReport",
    "PostgresBriefs",
    "ad_stats",
    "apply_brief",
    "attributed_chats",
    "attribution_report",
    "build_attribution_report",
    "build_dataset",
    "build_objection_report",
    "objection_report",
    "write_brief",
]
