"""add mission payload

Revision ID: 20260719_0008
Revises: 20260719_0007
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0008"
down_revision: Union[str, Sequence[str], None] = "20260719_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "missions",
            sa.Column("payload", postgresql.JSONB(), nullable=True),
        )
        op.execute(
            "UPDATE missions SET payload = jsonb_build_object("
            "'origin', constraints->>'from_city', "
            "'destination', constraints->>'to_city', "
            "'departure_date', constraints->>'travel_date')"
        )
    else:
        op.add_column("missions", sa.Column("payload", sa.JSON(), nullable=True))
        op.execute(
            "UPDATE missions SET payload = json_object("
            "'origin', json_extract(constraints, '$.from_city'), "
            "'destination', json_extract(constraints, '$.to_city'), "
            "'departure_date', json_extract(constraints, '$.travel_date'))"
        )
    incomplete = bind.execute(sa.text(
        "SELECT COUNT(*) FROM missions "
        "WHERE payload IS NULL"
    )).scalar_one()
    if incomplete:
        raise RuntimeError("Cannot create mission payload from legacy constraints")
    op.alter_column("missions", "payload", nullable=False)


def downgrade() -> None:
    op.drop_column("missions", "payload")
