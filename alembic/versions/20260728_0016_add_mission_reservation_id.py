"""add mission reservation id

Revision ID: 20260728_0016
Revises: 20260728_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_0016"
down_revision: str | Sequence[str] | None = "20260728_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("reservation_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "mission_execution_attempts",
        sa.Column("reservation_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mission_execution_attempts", "reservation_id")
    op.drop_column("missions", "reservation_id")
