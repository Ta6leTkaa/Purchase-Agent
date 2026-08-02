import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260802_0023_add_notification_retention_index"
)


def test_notification_retention_index_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "notification_outbox",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]

        migration.upgrade()

        indexes = sa.inspect(connection).get_indexes("notification_outbox")
        assert indexes[0]["name"] == "ix_notification_outbox_retention"
        assert indexes[0]["column_names"] == [
            "status",
            "delivered_at",
            "id",
        ]

        migration.downgrade()
        assert sa.inspect(connection).get_indexes("notification_outbox") == []
