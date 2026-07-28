"""add open execution attempt constraint

Revision ID: 20260728_0015
Revises: 20260727_0014
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0015"
down_revision: Union[str, Sequence[str], None] = "20260727_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPEN_ATTEMPT_CONDITION = "status = 'processing'"


def upgrade() -> None:
    _assert_no_duplicate_open_attempts()
    op.create_index(
        "uq_mission_execution_attempts_one_open",
        "mission_execution_attempts",
        ["mission_id"],
        unique=True,
        postgresql_where=sa.text(_OPEN_ATTEMPT_CONDITION),
        sqlite_where=sa.text(_OPEN_ATTEMPT_CONDITION),
    )


def _assert_no_duplicate_open_attempts() -> None:
    duplicate_mission_id = op.get_bind().execute(
        sa.text(
            "SELECT mission_id FROM mission_execution_attempts "
            "WHERE status = 'processing' GROUP BY mission_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).scalar_one_or_none()
    if duplicate_mission_id is not None:
        raise RuntimeError(
            "Cannot enforce one open execution attempt because mission "
            f"'{duplicate_mission_id}' has multiple processing attempts"
        )


def downgrade() -> None:
    op.drop_index(
        "uq_mission_execution_attempts_one_open",
        table_name="mission_execution_attempts",
    )
