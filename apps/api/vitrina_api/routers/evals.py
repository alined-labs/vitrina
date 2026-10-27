"""Evals & Metrics router. Owner: MX0 (plan.md §3).

Contract paths: /evals/runs, /evals/runs/{run_id}, /metrics/summary, /metrics/costs.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Evals & Metrics"])
