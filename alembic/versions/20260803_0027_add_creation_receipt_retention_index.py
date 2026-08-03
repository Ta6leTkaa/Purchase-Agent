"""add creation receipt retention index

Revision ID: 20260803_0027
Revises: 20260803_0026
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_0027"
down_revision: str | Sequence[str] | None = "20260803_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_resource_creation_receipts_retention",
        "resource_creation_receipts",
        ["created_at", "scope", "idempotency_key"],
        postgresql_where=sa.text("resource_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_creation_receipts_retention",
        table_name="resource_creation_receipts",
    )
