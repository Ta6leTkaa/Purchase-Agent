import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260719_0005_add_mission_execution_attempts"
)


def test_execution_attempts_migration_preserves_existing_missions() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            missions.insert().values(id="mission-1", status="waiting")
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)

        migration.upgrade()

        columns_after_upgrade = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        row_after_upgrade = connection.execute(sa.text(
            "SELECT id, status, execution_attempts FROM missions"
        )).mappings().one()

        assert "execution_attempts" in columns_after_upgrade
        assert dict(row_after_upgrade) == {
            "id": "mission-1",
            "status": "waiting",
            "execution_attempts": 0,
        }

        migration.downgrade()

        columns_after_downgrade = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        row_after_downgrade = connection.execute(sa.text(
            "SELECT id, status FROM missions"
        )).mappings().one()

        assert "execution_attempts" not in columns_after_downgrade
        assert dict(row_after_downgrade) == {
            "id": "mission-1",
            "status": "waiting",
        }
