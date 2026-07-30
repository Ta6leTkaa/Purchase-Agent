import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260729_0018_add_mission_execution_mode"
)


def test_execution_mode_migration_backfills_and_removes_column() -> None:
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

        row = connection.execute(
            sa.text(
                "SELECT status, execution_mode FROM missions "
                "WHERE id = 'mission-1'"
            )
        ).mappings().one()
        assert dict(row) == {
            "status": "waiting",
            "execution_mode": "require_confirmation",
        }

        migration.downgrade()

        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        assert "execution_mode" not in columns
