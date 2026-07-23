import importlib
import json

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

migration = importlib.import_module(
    "alembic.versions.20260723_0011_add_mission_event_sequence"
)


def test_event_sequence_migration_normalizes_json_event_order() -> None:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    missions = sa.Table(
        "missions",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("execution_log", sa.JSON, nullable=False),
    )
    metadata.create_all(engine)
    events = [
        {
            "sequence": 19,
            "type": "provider_resolved",
            "timestamp": "2026-07-23T10:02:00Z",
            "metadata": {"provider_id": "provider_b"},
        },
        {
            "type": "provider_resolution_failed",
            "timestamp": "2026-07-23T10:00:00Z",
            "metadata": {"reason": "ambiguous_provider"},
        },
    ]

    with engine.begin() as connection:
        connection.execute(
            missions.insert().values(id="mission-1", execution_log=events)
        )
        context = MigrationContext.configure(connection)
        migration.op = Operations(context)

        migration.upgrade()

        row = connection.execute(sa.text(
            "SELECT execution_log, last_event_sequence FROM missions"
        )).mappings().one()
        execution_log = _json_value(row["execution_log"])

        assert [event["sequence"] for event in execution_log] == [1, 2]
        assert [event["type"] for event in execution_log] == [
            "provider_resolved",
            "provider_resolution_failed",
        ]
        assert execution_log[0]["metadata"] == {"provider_id": "provider_b"}
        assert row["last_event_sequence"] == 2

        migration.downgrade()

        downgraded_row = connection.execute(sa.text(
            "SELECT execution_log FROM missions"
        )).mappings().one()
        downgraded_log = _json_value(downgraded_row["execution_log"])
        assert all("sequence" not in event for event in downgraded_log)
        assert [event["type"] for event in downgraded_log] == [
            "provider_resolved",
            "provider_resolution_failed",
        ]


def _json_value(value: object) -> list[dict[str, object]]:
    if isinstance(value, str):
        return json.loads(value)
    assert isinstance(value, list)
    return value
