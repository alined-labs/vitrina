"""Catalog router. Owner: MX0 (plan.md §3).

Contract paths: /products, /products/import, /products/{product_id}, /facts, /facts/{fact_id}.
"""

from fastapi import APIRouter

router = APIRouter(tags=["Catalog"])
