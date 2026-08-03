"""add identity versions

Revision ID: 20260803_0025
Revises: 20260802_0024
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_0025"
down_revision: str | Sequence[str] | None = "20260802_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "identities",
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.drop_constraint(
        "documents_identity_id_fkey",
        "documents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "documents_identity_id_fkey",
        "documents",
        "identities",
        ["identity_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "documents_identity_id_fkey",
        "documents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "documents_identity_id_fkey",
        "documents",
        "identities",
        ["identity_id"],
        ["id"],
    )
    op.drop_column("identities", "version")
