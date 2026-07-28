import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

migration = importlib.import_module(
    "alembic.versions.20260728_0015_add_open_execution_attempt_constraint"
)


def test_open_execution_attempt_constraint_allows_only_one_processing_row() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    attempts = sa.Table(
        "mission_execution_attempts",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("mission_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]
        migration.upgrade()

        indexes = sa.inspect(connection).get_indexes(
            "mission_execution_attempts"
        )
        assert any(
            index["name"] == "uq_mission_execution_attempts_one_open"
            and index["unique"]
            for index in indexes
        )

        connection.execute(
            attempts.insert().values(
                id="attempt-1",
                mission_id="mission-1",
                status="processing",
            )
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                attempts.insert().values(
                    id="attempt-2",
                    mission_id="mission-1",
                    status="processing",
                )
            )
        connection.execute(
            attempts.insert().values(
                id="attempt-3",
                mission_id="mission-1",
                status="completed",
            )
        )

        migration.downgrade()

        connection.execute(
            attempts.insert().values(
                id="attempt-4",
                mission_id="mission-1",
                status="processing",
            )
        )


def test_open_execution_attempt_constraint_rejects_existing_duplicates() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    attempts = sa.Table(
        "mission_execution_attempts",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("mission_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            attempts.insert(),
            [
                {
                    "id": "attempt-1",
                    "mission_id": "mission-1",
                    "status": "processing",
                },
                {
                    "id": "attempt-2",
                    "mission_id": "mission-1",
                    "status": "processing",
                },
            ],
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="multiple processing attempts"):
            migration.upgrade()
