"""Mark the eval runs that may become the result every seller sees as latest.

Only a full run started with the service token counts. Nothing records who started an older
run or whether it was full, so existing rows do not count until the next such run finishes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_runs",
        sa.Column("counts_as_latest", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("eval_runs", "counts_as_latest")
