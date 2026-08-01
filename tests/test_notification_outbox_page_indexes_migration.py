import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260801_0022_add_notification_outbox_page_indexes"
)


def test_notification_outbox_page_indexes_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "notification_outbox",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("mission_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]

        migration.upgrade()

        inspector = sa.inspect(connection)
        indexes = {
            index["name"]: index["column_names"]
            for index in inspector.get_indexes("notification_outbox")
        }
        assert indexes == {
            "ix_notification_outbox_page": ["occurred_at", "id"],
            "ix_notification_outbox_status_page": [
                "status",
                "occurred_at",
                "id",
            ],
            "ix_notification_outbox_mission_page": [
                "mission_id",
                "occurred_at",
                "id",
            ],
        }

        migration.downgrade()
        assert sa.inspect(connection).get_indexes("notification_outbox") == []
