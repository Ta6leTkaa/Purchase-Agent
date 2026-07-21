"""add mission provider id

Revision ID: 20260721_0009
Revises: 20260719_0008
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0009"
down_revision: Union[str, Sequence[str], None] = "20260719_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("provider_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("missions", "provider_id")
