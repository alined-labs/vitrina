"""Public router. Owner: MX1 (plan.md §3).

Contract paths: /public/showcases, /public/showcases/{slug}.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Public"])
