import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260729_0019_add_mission_expiry"
)


def test_mission_expiry_migration_adds_indexed_nullable_column() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]

        migration.upgrade()

        inspector = sa.inspect(connection)
        expires_at = next(
            column
            for column in inspector.get_columns("missions")
            if column["name"] == "expires_at"
        )
        assert expires_at["nullable"] is True
        assert {
            index["name"] for index in inspector.get_indexes("missions")
        } == {"ix_missions_expires_at"}

        migration.downgrade()
        assert "expires_at" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns("missions")
        }
