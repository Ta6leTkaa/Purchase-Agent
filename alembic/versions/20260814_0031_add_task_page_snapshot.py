"""add task page snapshot

Revision ID: 20260814_0031
Revises: 20260814_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260814_0031"
down_revision: str | Sequence[str] | None = "20260814_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "page_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "page_snapshot")
