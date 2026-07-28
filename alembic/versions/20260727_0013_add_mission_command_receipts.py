"""add mission command receipts

Revision ID: 20260727_0013
Revises: 20260724_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260727_0013"
down_revision: str | Sequence[str] | None = "20260724_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite")
    op.create_table(
        "mission_command_receipts",
        sa.Column("idempotency_key", sa.String(255), primary_key=True),
        sa.Column("mission_id", uuid_type, nullable=False),
        sa.Column("command", sa.String(32), nullable=False),
        sa.Column("result_mission_id", uuid_type, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("mission_command_receipts")
