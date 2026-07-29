"""add mission event projection

Revision ID: 20260729_0017
Revises: 20260728_0016
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260729_0017"
down_revision: str | Sequence[str] | None = "20260728_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    mission_id_type = postgresql.UUID(as_uuid=True).with_variant(
        sa.String(length=36),
        "sqlite",
    )
    op.create_table(
        "mission_events",
        sa.Column("mission_id", mission_id_type, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_id", mission_id_type, nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event", json_type, nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("mission_id", "sequence"),
        sa.UniqueConstraint("event_id", name="uq_mission_events_event_id"),
    )
    op.create_index(
        "ix_mission_events_mission_sequence",
        "mission_events",
        ["mission_id", "sequence"],
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            "INSERT INTO mission_events "
            "(mission_id, sequence, event_id, event_type, occurred_at, event) "
            "SELECT missions.id, (event->>'sequence')::integer, "
            "(event->>'event_id')::uuid, event->>'type', "
            "(event->>'timestamp')::timestamptz, event "
            "FROM missions CROSS JOIN LATERAL "
            "jsonb_array_elements(COALESCE(missions.execution_log, '[]'::jsonb)) "
            "AS entries(event)"
        )
    else:
        op.execute(
            "INSERT INTO mission_events "
            "(mission_id, sequence, event_id, event_type, occurred_at, event) "
            "SELECT missions.id, CAST(json_extract(value, '$.sequence') AS INTEGER), "
            "json_extract(value, '$.event_id'), json_extract(value, '$.type'), "
            "json_extract(value, '$.timestamp'), value "
            "FROM missions, json_each(COALESCE(missions.execution_log, '[]'))"
        )


def downgrade() -> None:
    op.drop_index("ix_mission_events_mission_sequence", table_name="mission_events")
    op.drop_table("mission_events")
