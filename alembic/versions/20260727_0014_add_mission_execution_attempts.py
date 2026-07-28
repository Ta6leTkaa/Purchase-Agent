"""add mission execution attempts

Revision ID: 20260727_0014
Revises: 20260727_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260727_0014"
down_revision: str | Sequence[str] | None = "20260727_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    mission_id_type = postgresql.UUID(as_uuid=True).with_variant(
        sa.String(length=36),
        "sqlite",
    )
    attempt_id_type = postgresql.UUID(as_uuid=True).with_variant(
        sa.String(length=36),
        "sqlite",
    )
    op.create_table(
        "mission_execution_attempts",
        sa.Column("id", attempt_id_type, nullable=False),
        sa.Column("mission_id", mission_id_type, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_provider_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "mission_id",
            "attempt_number",
            name="uq_mission_execution_attempt_number",
        ),
    )
    op.create_index(
        "ix_mission_execution_attempts_mission_claimed_at",
        "mission_execution_attempts",
        ["mission_id", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mission_execution_attempts_mission_claimed_at",
        table_name="mission_execution_attempts",
    )
    op.drop_table("mission_execution_attempts")
