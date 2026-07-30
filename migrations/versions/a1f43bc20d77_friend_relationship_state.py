"""Friend relationship state

Revision ID: a1f43bc20d77
Revises: e491d756a73c
Create Date: 2026-07-30 04:45:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f43bc20d77"
down_revision: str | Sequence[str] | None = "e491d756a73c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "zalo_contact_profiles",
        sa.Column(
            "relationship_status",
            sa.String(length=32),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "zalo_contact_profiles",
        sa.Column(
            "blocked_by_me",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_zalo_contact_profiles_relationship_status",
        "zalo_contact_profiles",
        ["relationship_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_zalo_contact_profiles_relationship_status",
        table_name="zalo_contact_profiles",
    )
    op.drop_column("zalo_contact_profiles", "blocked_by_me")
    op.drop_column("zalo_contact_profiles", "relationship_status")
