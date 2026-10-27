"""The table the evals own, mirroring migration 0001 (data-model.md ``eval_runs``)."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from vitrina_core.db import metadata

eval_runs = sa.Table(
    "eval_runs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("suite", sa.Text, nullable=False),
    sa.Column("replay", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("case_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("cases", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("git_sha", sa.Text),
    sa.Column(
        "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
)
