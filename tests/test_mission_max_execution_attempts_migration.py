import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260719_0006_add_mission_max_execution_attempts"
)


def test_max_execution_attempts_migration_preserves_attempt_counts() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("execution_attempts", sa.Integer, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(missions.insert(), [
            {"id": "mission-0", "execution_attempts": 0},
            {"id": "mission-5", "execution_attempts": 5},
        ])
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)

        migration.upgrade()

        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("missions")
        }
        rows = connection.execute(sa.text(
            "SELECT id, execution_attempts, max_execution_attempts "
            "FROM missions ORDER BY id"
        )).mappings().all()

        assert columns["max_execution_attempts"]["nullable"] is False
        assert [dict(row) for row in rows] == [
            {
                "id": "mission-0",
                "execution_attempts": 0,
                "max_execution_attempts": 3,
            },
            {
                "id": "mission-5",
                "execution_attempts": 5,
                "max_execution_attempts": 5,
            },
        ]

        migration.downgrade()

        remaining_columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        remaining_rows = connection.execute(sa.text(
            "SELECT id, execution_attempts FROM missions ORDER BY id"
        )).mappings().all()

        assert "max_execution_attempts" not in remaining_columns
        assert [dict(row) for row in remaining_rows] == [
            {"id": "mission-0", "execution_attempts": 0},
            {"id": "mission-5", "execution_attempts": 5},
        ]
