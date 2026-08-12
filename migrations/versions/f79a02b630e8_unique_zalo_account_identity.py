"""Keep one control-center account per verified Zalo UID.

Revision ID: f79a02b630e8
Revises: d92c4e81fa10
Create Date: 2026-07-31 04:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f79a02b630e8"
down_revision: str | Sequence[str] | None = "d92c4e81fa10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT zalo_uid, COUNT(*) AS account_count
            FROM zalo_accounts
            WHERE zalo_uid IS NOT NULL
            GROUP BY zalo_uid
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot lock Zalo identities: duplicate non-null zalo_uid values exist. "
            "Resolve duplicate managed accounts before retrying this migration."
        )
    op.create_index(
        "uq_zalo_accounts_zalo_uid",
        "zalo_accounts",
        ["zalo_uid"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_zalo_accounts_zalo_uid", table_name="zalo_accounts")
