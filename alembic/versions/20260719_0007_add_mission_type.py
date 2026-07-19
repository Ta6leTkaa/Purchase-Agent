"""add mission type

Revision ID: 20260719_0007
Revises: 20260719_0006
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0007"
down_revision: Union[str, Sequence[str], None] = "20260719_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "mission_type",
            sa.String(),
            nullable=False,
            server_default=sa.text("'train_ticket'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("missions", "mission_type")
