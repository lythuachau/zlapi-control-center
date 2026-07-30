"""listener event deduplication

Revision ID: b81e3cf45a72
Revises: f6c12a9e44d1
Create Date: 2026-07-29 16:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b81e3cf45a72"
down_revision: str | Sequence[str] | None = "f6c12a9e44d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("dedupe_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_events_account_type_dedupe",
        "events",
        ["account_id", "event_type", "dedupe_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_events_account_type_dedupe", table_name="events")
    op.drop_column("events", "dedupe_key")
