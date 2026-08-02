"""add mission operational indexes

Revision ID: 20260802_0024
Revises: 20260802_0023
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0024"
down_revision: str | Sequence[str] | None = "20260802_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_missions_created_page", ["created_at", "id"]),
    (
        "ix_missions_status_created_page",
        ["status", "created_at", "id"],
    ),
    (
        "ix_missions_type_created_page",
        ["mission_type", "created_at", "id"],
    ),
    (
        "ix_missions_due_claim",
        ["status", "scheduled_at", "id"],
    ),
    (
        "ix_missions_stale_claim",
        ["status", "claimed_at", "id"],
    ),
)


def upgrade() -> None:
    for name, columns in _INDEXES:
        op.create_index(name, "missions", columns)


def downgrade() -> None:
    for name, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name="missions")
