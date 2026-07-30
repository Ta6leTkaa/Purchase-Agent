"""add notification recipients

Revision ID: 20260730_0021
Revises: 20260730_0020
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260730_0021"
down_revision: str | Sequence[str] | None = "20260730_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_outbox",
        sa.Column(
            "recipient_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_outbox", "recipient_ids")
