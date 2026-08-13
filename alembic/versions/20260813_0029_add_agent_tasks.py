"""add agent tasks

Revision ID: 20260813_0029
Revises: 20260804_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0029"
down_revision: str | None = "20260804_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "agent_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=False),
        sa.Column("person_ids", json_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("inferred_kind", sa.String(length=64), nullable=True),
        sa.Column("waiting_reason", sa.String(length=64), nullable=True),
        sa.Column("permissions", json_type, nullable=False),
        sa.Column("plan", json_type, nullable=True),
        sa.Column("journal", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tasks_status", "agent_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_status", table_name="agent_tasks")
    op.drop_table("agent_tasks")
