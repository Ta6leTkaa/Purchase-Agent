"""add provider history read projection

Revision ID: 20260724_0012
Revises: 20260723_0011
Create Date: 2026-07-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0012"
down_revision: Union[str, Sequence[str], None] = "20260723_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROVIDER_EVENT_TYPES = (
    "'provider_selection_changed', "
    "'provider_resolved', "
    "'provider_resolution_failed'"
)


def upgrade() -> None:
    bind = op.get_bind()
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    mission_id_type = postgresql.UUID(as_uuid=True).with_variant(
        sa.String(length=36),
        "sqlite",
    )
    op.create_table(
        "mission_provider_history_events",
        sa.Column("mission_id", mission_id_type, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("legacy_event_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            f"event_type IN ({_PROVIDER_EVENT_TYPES})",
            name="ck_mission_provider_history_event_type",
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("mission_id", "sequence"),
    )
    op.create_index(
        "ix_mission_provider_history_events_occurred_at_sequence",
        "mission_provider_history_events",
        ["mission_id", "occurred_at", "legacy_event_index"],
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            "INSERT INTO mission_provider_history_events "
            "(mission_id, sequence, event_type, occurred_at, payload, legacy_event_index) "
            "SELECT missions.id, (event->>'sequence')::integer, event->>'type', "
            "(event->>'timestamp')::timestamptz, COALESCE(event->'metadata', '{}'::jsonb), "
            "ordinal - 1 "
            "FROM missions CROSS JOIN LATERAL "
            "jsonb_array_elements(COALESCE(missions.execution_log, '[]'::jsonb)) "
            "WITH ORDINALITY AS entries(event, ordinal) "
            f"WHERE event->>'type' IN ({_PROVIDER_EVENT_TYPES})"
        )
    else:
        op.execute(
            "INSERT INTO mission_provider_history_events "
            "(mission_id, sequence, event_type, occurred_at, payload, legacy_event_index) "
            "SELECT missions.id, CAST(json_extract(value, '$.sequence') AS INTEGER), "
            "json_extract(value, '$.type'), json_extract(value, '$.timestamp'), "
            "COALESCE(json_extract(value, '$.metadata'), '{}'), CAST(key AS INTEGER) "
            "FROM missions, json_each(COALESCE(missions.execution_log, '[]')) "
            f"WHERE json_extract(value, '$.type') IN ({_PROVIDER_EVENT_TYPES})"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_mission_provider_history_events_occurred_at_sequence",
        table_name="mission_provider_history_events",
    )
    op.drop_table("mission_provider_history_events")
