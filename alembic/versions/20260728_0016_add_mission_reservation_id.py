"""add mission reservation id

Revision ID: 20260728_0016
Revises: 20260728_0015
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0016"
down_revision: Union[str, Sequence[str], None] = "20260728_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
