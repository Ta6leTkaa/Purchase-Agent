"""create mission table

Revision ID: 20260713_0002
Revises: 20260713_0001
Create Date: 2026-07-13

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260713_0002"
down_revision: str | None = "20260713_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("participant_ids", postgresql.JSONB(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column("fallback_rules", postgresql.JSONB(), nullable=False),
        sa.Column(
            "execution_log",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("best_option", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("missions")
