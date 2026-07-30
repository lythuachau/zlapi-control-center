"""persistent GIF library

Revision ID: c34da718bf29
Revises: b81e3cf45a72
Create Date: 2026-07-30 02:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c34da718bf29"
down_revision: str | Sequence[str] | None = "b81e3cf45a72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gif_library_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_path"),
    )
    op.create_index(
        op.f("ix_gif_library_items_sha256"),
        "gif_library_items",
        ["sha256"],
        unique=True,
    )
    op.create_index(
        op.f("ix_gif_library_items_favorite"),
        "gif_library_items",
        ["favorite"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gif_library_items_created_at"),
        "gif_library_items",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gif_library_items_last_used_at"),
        "gif_library_items",
        ["last_used_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_gif_library_items_last_used_at"),
        table_name="gif_library_items",
    )
    op.drop_index(
        op.f("ix_gif_library_items_created_at"),
        table_name="gif_library_items",
    )
    op.drop_index(
        op.f("ix_gif_library_items_favorite"),
        table_name="gif_library_items",
    )
    op.drop_index(
        op.f("ix_gif_library_items_sha256"),
        table_name="gif_library_items",
    )
    op.drop_table("gif_library_items")
