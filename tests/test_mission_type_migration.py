import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260719_0007_add_mission_type"
)


def test_mission_type_migration_updates_existing_rows() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(missions.insert().values(id="mission-1"))
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        migration.upgrade()

        row = connection.execute(sa.text(
            "SELECT id, mission_type FROM missions"
        )).mappings().one()
        assert dict(row) == {"id": "mission-1", "mission_type": "train_ticket"}

        migration.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        assert "mission_type" not in columns
