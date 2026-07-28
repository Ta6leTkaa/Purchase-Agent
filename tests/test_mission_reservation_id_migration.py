import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260728_0016_add_mission_reservation_id"
)


def test_reservation_id_migration_adds_and_removes_only_target_columns() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("status", sa.String, nullable=False),
    )
    attempts = sa.Table(
        "mission_execution_attempts",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("mission_id", sa.String, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            missions.insert().values(id="mission-1", status="waiting")
        )
        connection.execute(
            attempts.insert().values(id="attempt-1", mission_id="mission-1")
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        migration.upgrade()

        mission_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        attempt_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "mission_execution_attempts"
            )
        }
        assert "reservation_id" in mission_columns
        assert "reservation_id" in attempt_columns
        assert connection.execute(
            sa.text(
                "SELECT reservation_id FROM missions WHERE id = 'mission-1'"
            )
        ).scalar_one() is None

        migration.downgrade()

        remaining_mission_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        remaining_attempt_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "mission_execution_attempts"
            )
        }
        assert "reservation_id" not in remaining_mission_columns
        assert "reservation_id" not in remaining_attempt_columns
        assert connection.execute(
            sa.text("SELECT status FROM missions WHERE id = 'mission-1'")
        ).scalar_one() == "waiting"
