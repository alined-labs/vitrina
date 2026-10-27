"""Jobs router. Owner: MX0 (plan.md §3).

Contract paths: /jobs/{job_id}.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Jobs"])
