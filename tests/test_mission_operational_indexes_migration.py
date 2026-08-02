import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260802_0024_add_mission_operational_indexes"
)


def test_mission_operational_indexes_migration_round_trip() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("mission_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]

        migration.upgrade()

        indexes = {
            index["name"]: index["column_names"]
            for index in sa.inspect(connection).get_indexes("missions")
        }
        assert indexes == {
            "ix_missions_created_page": ["created_at", "id"],
            "ix_missions_status_created_page": [
                "status",
                "created_at",
                "id",
            ],
            "ix_missions_type_created_page": [
                "mission_type",
                "created_at",
                "id",
            ],
            "ix_missions_due_claim": ["status", "scheduled_at", "id"],
            "ix_missions_stale_claim": ["status", "claimed_at", "id"],
        }

        migration.downgrade()
        assert sa.inspect(connection).get_indexes("missions") == []
