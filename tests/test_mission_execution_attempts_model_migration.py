import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260727_0014_add_mission_execution_attempts"
)


def test_execution_attempt_migration_creates_and_removes_only_attempt_table() -> None:
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
        migration.op = Operations(context)  # type: ignore[attr-defined]

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "mission_execution_attempts" in inspector.get_table_names()
        columns = {
            column["name"]
            for column in inspector.get_columns("mission_execution_attempts")
        }
        assert columns >= {
            "id",
            "mission_id",
            "attempt_number",
            "status",
            "claimed_at",
            "finished_at",
            "resolved_provider_id",
        }
        assert connection.execute(
            sa.text("SELECT status FROM missions WHERE id = 'mission-1'")
        ).scalar_one() == "waiting"

        migration.downgrade()

        assert "mission_execution_attempts" not in sa.inspect(
            connection
        ).get_table_names()
        assert connection.execute(
            sa.text("SELECT status FROM missions WHERE id = 'mission-1'")
        ).scalar_one() == "waiting"
