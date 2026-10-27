"""Health router. Owner: MX0 (plan.md §3).

Contract paths: /health, /health/providers.
"""

from importlib.metadata import version
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Health"])


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@router.get("/health", operation_id="getHealth", summary="Liveness")
def get_health() -> Health:
    return Health(status="ok", version=version("vitrina-api"))
