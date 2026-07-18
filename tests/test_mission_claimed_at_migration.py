import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260718_0004_add_mission_claimed_at"
)


def test_claimed_at_migration_preserves_existing_missions() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            missions.insert().values(
                id="mission-1",
                status="processing",
                scheduled_at=None,
            )
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)

        migration.upgrade()

        columns_after_upgrade = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        row_after_upgrade = connection.execute(sa.text(
            "SELECT id, status, claimed_at FROM missions"
        )).mappings().one()

        assert "claimed_at" in columns_after_upgrade
        assert row_after_upgrade["id"] == "mission-1"
        assert row_after_upgrade["status"] == "processing"
        assert row_after_upgrade["claimed_at"] is None

        migration.downgrade()

        columns_after_downgrade = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        row_after_downgrade = connection.execute(sa.text(
            "SELECT id, status FROM missions"
        )).mappings().one()

        assert "claimed_at" not in columns_after_downgrade
        assert row_after_downgrade["id"] == "mission-1"
        assert row_after_downgrade["status"] == "processing"
