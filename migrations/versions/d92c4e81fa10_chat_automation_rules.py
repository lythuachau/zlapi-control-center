"""chat automation rules

Revision ID: d92c4e81fa10
Revises: a1f43bc20d77
Create Date: 2026-07-30 06:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d92c4e81fa10"
down_revision: str | Sequence[str] | None = "a1f43bc20d77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["zalo_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "kind",
            name="uq_automation_rules_account_kind",
        ),
    )
    op.create_index(
        op.f("ix_automation_rules_account_id"),
        "automation_rules",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_rules_kind"),
        "automation_rules",
        ["kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_rules_enabled"),
        "automation_rules",
        ["enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_rules_last_triggered_at"),
        "automation_rules",
        ["last_triggered_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_rules_created_at"),
        "automation_rules",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_automation_rules_updated_at"),
        "automation_rules",
        ["updated_at"],
        unique=False,
    )

    op.create_table(
        "automation_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("source_event_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("trigger_kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["zalo_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id",
            "source_event_id",
            name="uq_automation_runs_rule_source_event",
        ),
    )
    for column in (
        "rule_id",
        "account_id",
        "source_event_id",
        "job_id",
        "trigger_kind",
        "status",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_automation_runs_{column}"),
            "automation_runs",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in (
        "created_at",
        "status",
        "trigger_kind",
        "job_id",
        "source_event_id",
        "account_id",
        "rule_id",
    ):
        op.drop_index(
            op.f(f"ix_automation_runs_{column}"),
            table_name="automation_runs",
        )
    op.drop_table("automation_runs")
    for column in (
        "updated_at",
        "created_at",
        "last_triggered_at",
        "enabled",
        "kind",
        "account_id",
    ):
        op.drop_index(
            op.f(f"ix_automation_rules_{column}"),
            table_name="automation_rules",
        )
    op.drop_table("automation_rules")
