"""add task control mode

Revision ID: 20260816_0034
Revises: 20260815_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0034"
down_revision: str | Sequence[str] | None = "20260815_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "control_mode",
            sa.String(length=32),
            nullable=False,
            server_default="step_by_step",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "control_mode")
