"""The jobs table doubles as the Postgres queue (T008, FR-083).

Enqueueing is an insert in the caller's transaction, so a job exists exactly when the row
that caused it does; workers claim with FOR UPDATE SKIP LOCKED. Internal kinds
(``process_inbound``, ``maintenance``) never leave the API: GET /jobs/{id} shows only the
contract's kinds.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

API_KINDS = (
    "voice_import", "product_import", "creative", "reel", "brief", "erasure", "eval_run",
    "replay", "campaign_send", "public_showcase",
)  # fmt: skip
INTERNAL_KINDS = ("process_inbound", "maintenance")


def _kinds(values: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column("jobs", sa.Column("task", sa.Text, nullable=False, server_default=""))
    op.add_column(
        "jobs",
        sa.Column(
            "payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column("jobs", sa.Column("attempts", sa.Integer, nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"))
    op.add_column(
        "jobs",
        sa.Column(
            "run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    op.add_column("jobs", sa.Column("locked_by", sa.Text))
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", _kinds(API_KINDS + INTERNAL_KINDS))
    op.create_index(
        "ix_jobs_claim",
        "jobs",
        ["run_after", "created_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "uq_jobs_task_idempotency_key",
        "jobs",
        ["task", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_task_idempotency_key", "jobs")
    op.drop_index("ix_jobs_claim", "jobs")
    op.execute(
        f"DELETE FROM jobs WHERE kind IN ({', '.join(repr(kind) for kind in INTERNAL_KINDS)})"
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", _kinds(API_KINDS))
    for column in (
        "heartbeat_at",
        "locked_by",
        "run_after",
        "max_attempts",
        "attempts",
        "payload",
        "task",
    ):
        op.drop_column("jobs", column)
