"""add mission resolved provider id

Revision ID: 20260722_0010
Revises: 20260721_0009
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_0010"
down_revision: Union[str, Sequence[str], None] = "20260721_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("resolved_provider_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("missions", "resolved_provider_id")
