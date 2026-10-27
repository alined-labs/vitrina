"""Creative router. Owner: MX1 (plan.md §3).

Contract paths: /creative/jobs, /creative/jobs/{job_id}, /creative/reels, /creatives/{creative_id}.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Creative"])
