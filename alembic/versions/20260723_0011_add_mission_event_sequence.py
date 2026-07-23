"""add mission event sequence

Revision ID: 20260723_0011
Revises: 20260722_0010
Create Date: 2026-07-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0011"
down_revision: Union[str, Sequence[str], None] = "20260722_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "missions",
        sa.Column(
            "last_event_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE missions SET "
            "execution_log = COALESCE(("
            "SELECT jsonb_agg(event || jsonb_build_object('sequence', ordinal) "
            "ORDER BY ordinal) "
            "FROM jsonb_array_elements(COALESCE(missions.execution_log, "
            "'[]'::jsonb)) WITH ORDINALITY AS entries(event, ordinal)"
            "), '[]'::jsonb), "
            "last_event_sequence = jsonb_array_length(COALESCE("
            "missions.execution_log, '[]'::jsonb))"
        )
    else:
        op.execute(
            "UPDATE missions SET "
            "execution_log = COALESCE(("
            "SELECT json_group_array(json(event)) FROM ("
            "SELECT json_set(value, '$.sequence', CAST(key AS INTEGER) + 1) "
            "AS event FROM json_each(COALESCE(missions.execution_log, '[]')) "
            "ORDER BY CAST(key AS INTEGER)"
            ")"
            "), '[]'), "
            "last_event_sequence = json_array_length(COALESCE("
            "missions.execution_log, '[]'))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "UPDATE missions SET execution_log = COALESCE(("
            "SELECT jsonb_agg(event - 'sequence' ORDER BY ordinal) "
            "FROM jsonb_array_elements(COALESCE(missions.execution_log, "
            "'[]'::jsonb)) WITH ORDINALITY AS entries(event, ordinal)"
            "), '[]'::jsonb)"
        )
    else:
        op.execute(
            "UPDATE missions SET execution_log = COALESCE(("
            "SELECT json_group_array(json(event)) FROM ("
            "SELECT json_remove(value, '$.sequence') AS event "
            "FROM json_each(COALESCE(missions.execution_log, '[]')) "
            "ORDER BY CAST(key AS INTEGER)"
            ")"
            "), '[]')"
        )
    op.drop_column("missions", "last_event_sequence")
