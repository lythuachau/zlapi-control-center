"""conversation_read_state

Revision ID: f6c12a9e44d1
Revises: a28fbb585ad4
Create Date: 2026-07-29 11:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c12a9e44d1"
down_revision: str | Sequence[str] | None = "a28fbb585ad4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_reads",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("last_read_event_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["zalo_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "account_id", "thread_id"),
    )
    op.create_index(
        "ix_conversation_reads_user_account",
        "conversation_reads",
        ["user_id", "account_id"],
        unique=False,
    )
    op.create_index(
        "ix_events_account_thread_cursor",
        "events",
        ["account_id", "thread_id", "id"],
        unique=False,
    )

    # Existing users start with current history marked as read. New events after
    # this migration are counted normally, so deployment does not create a flood
    # of historical unread badges.
    op.execute(
        sa.text(
            """
            INSERT INTO conversation_reads (
                user_id,
                account_id,
                thread_id,
                last_read_event_id,
                created_at,
                updated_at
            )
            SELECT
                users.id,
                events.account_id,
                events.thread_id,
                MAX(events.id),
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM users
            CROSS JOIN events
            WHERE events.account_id IS NOT NULL
              AND events.thread_id IS NOT NULL
              AND events.event_type = 'message.received'
            GROUP BY users.id, events.account_id, events.thread_id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_events_account_thread_cursor", table_name="events")
    op.drop_index(
        "ix_conversation_reads_user_account",
        table_name="conversation_reads",
    )
    op.drop_table("conversation_reads")
