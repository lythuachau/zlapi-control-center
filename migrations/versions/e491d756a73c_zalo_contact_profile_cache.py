"""Zalo contact profile cache

Revision ID: e491d756a73c
Revises: c34da718bf29
Create Date: 2026-07-30 04:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e491d756a73c"
down_revision: str | Sequence[str] | None = "c34da718bf29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "zalo_contact_profiles",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("online", sa.Boolean(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["zalo_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", "user_id"),
    )
    op.create_index(
        "ix_zalo_contact_profiles_synced_at",
        "zalo_contact_profiles",
        ["synced_at"],
        unique=False,
    )
    op.create_index(
        "ix_zalo_contact_profiles_account_name",
        "zalo_contact_profiles",
        ["account_id", "name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_zalo_contact_profiles_account_name",
        table_name="zalo_contact_profiles",
    )
    op.drop_index(
        "ix_zalo_contact_profiles_synced_at",
        table_name="zalo_contact_profiles",
    )
    op.drop_table("zalo_contact_profiles")
