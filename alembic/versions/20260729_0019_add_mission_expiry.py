"""add mission expiry

Revision ID: 20260729_0019
Revises: 20260729_0018
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260729_0019"
down_revision: str | Sequence[str] | None = "20260729_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_missions_expires_at",
        "missions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_missions_expires_at", table_name="missions")
    op.drop_column("missions", "expires_at")
