"""add notification retention index

Revision ID: 20260802_0023
Revises: 20260801_0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0023"
down_revision: str | Sequence[str] | None = "20260801_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notification_outbox_retention",
        "notification_outbox",
        ["status", "delivered_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_outbox_retention",
        table_name="notification_outbox",
    )
