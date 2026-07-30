"""add mission execution mode

Revision ID: 20260729_0018
Revises: 20260729_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0018"
down_revision: str | Sequence[str] | None = "20260729_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "execution_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'require_confirmation'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("missions", "execution_mode")
