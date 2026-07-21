import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260721_0009_add_mission_provider_id"
)


def test_mission_provider_id_migration_preserves_legacy_rows() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.String, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            missions.insert().values(id="mission-1", title="Legacy mission")
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)
        migration.upgrade()

        row = connection.execute(sa.text(
            "SELECT id, title, provider_id FROM missions"
        )).mappings().one()
        assert dict(row) == {
            "id": "mission-1",
            "title": "Legacy mission",
            "provider_id": None,
        }

        connection.execute(sa.text(
            "UPDATE missions SET provider_id = 'mock_train' WHERE id = 'mission-1'"
        ))
        assert connection.execute(sa.text(
            "SELECT provider_id FROM missions WHERE id = 'mission-1'"
        )).scalar_one() == "mock_train"

        migration.downgrade()
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
        assert "provider_id" not in columns
        assert "title" in columns
