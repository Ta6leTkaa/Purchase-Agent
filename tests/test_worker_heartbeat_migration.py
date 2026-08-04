import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260804_0028_add_worker_heartbeats"
)


def test_worker_heartbeat_migration_creates_table_and_index() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert inspector.get_pk_constraint("worker_heartbeats")[
            "constrained_columns"
        ] == ["worker_kind", "instance_id"]
        assert {index["name"] for index in inspector.get_indexes(
            "worker_heartbeats"
        )} == {"ix_worker_heartbeats_heartbeat_at"}

        migration.downgrade()
        assert "worker_heartbeats" not in sa.inspect(connection).get_table_names()
