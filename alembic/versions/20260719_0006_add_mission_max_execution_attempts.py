"""add mission max execution attempts

Revision ID: 20260719_0006
Revises: 20260719_0005
Create Date: 2026-07-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0006"
down_revision: Union[str, Sequence[str], None] = "20260719_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "max_execution_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )
    op.execute(
        "UPDATE missions SET max_execution_attempts = CASE "
        "WHEN execution_attempts > 3 THEN execution_attempts ELSE 3 END"
    )


def downgrade() -> None:
    op.drop_column("missions", "max_execution_attempts")
