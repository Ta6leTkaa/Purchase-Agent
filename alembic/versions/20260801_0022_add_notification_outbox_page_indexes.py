"""add notification outbox page indexes

Revision ID: 20260801_0022
Revises: 20260730_0021
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260801_0022"
down_revision: str | Sequence[str] | None = "20260730_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_notification_outbox_page",
        "notification_outbox",
        ["occurred_at", "id"],
    )
    op.create_index(
        "ix_notification_outbox_status_page",
        "notification_outbox",
        ["status", "occurred_at", "id"],
    )
    op.create_index(
        "ix_notification_outbox_mission_page",
        "notification_outbox",
        ["mission_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_outbox_mission_page",
        table_name="notification_outbox",
    )
    op.drop_index(
        "ix_notification_outbox_status_page",
        table_name="notification_outbox",
    )
    op.drop_index(
        "ix_notification_outbox_page",
        table_name="notification_outbox",
    )
