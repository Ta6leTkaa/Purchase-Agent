import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260730_0020_add_notification_outbox"
)


def test_notification_outbox_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]
            for column in inspector.get_columns("notification_outbox")
        }
        assert {
            "id",
            "mission_id",
            "event_id",
            "event_type",
            "status",
            "delivery_attempts",
            "available_at",
        } <= columns
        assert {
            index["name"]
            for index in inspector.get_indexes("notification_outbox")
        } == {"ix_notification_outbox_dispatch"}

        migration.downgrade()
        assert "notification_outbox" not in inspector.get_table_names()
