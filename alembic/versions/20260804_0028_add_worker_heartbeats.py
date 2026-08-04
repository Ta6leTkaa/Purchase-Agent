"""add worker heartbeats

Revision ID: 20260804_0028
Revises: 20260803_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260804_0028"
down_revision: str | Sequence[str] | None = "20260803_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_kind", sa.String(length=32), nullable=False),
        sa.Column("instance_id", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("worker_kind", "instance_id"),
    )
    op.create_index(
        "ix_worker_heartbeats_heartbeat_at",
        "worker_heartbeats",
        ["heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_heartbeats_heartbeat_at",
        table_name="worker_heartbeats",
    )
    op.drop_table("worker_heartbeats")
