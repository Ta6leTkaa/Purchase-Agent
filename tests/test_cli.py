import asyncio
import io
import json
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app import cli
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)


def test_process_due_command_uses_default_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits: list[int] = []

    async def fake_process_due_command(limit: int) -> int:
        limits.append(limit)
        return 0

    monkeypatch.setattr(cli, "process_due_command", fake_process_due_command)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["process-due"])

    assert exc_info.value.code == 0
    assert limits == [100]


def test_process_due_command_passes_custom_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits: list[int] = []

    async def fake_process_due_command(limit: int) -> int:
        limits.append(limit)
        return 0

    monkeypatch.setattr(cli, "process_due_command", fake_process_due_command)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["process-due", "--limit", "50"])

    assert exc_info.value.code == 0
    assert limits == [50]


@pytest.mark.parametrize("limit", ["0", "501"])
def test_process_due_command_rejects_invalid_limit(limit: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["process-due", "--limit", limit])

    assert exc_info.value.code == 2


def test_process_due_command_returns_zero_and_writes_json_on_success() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission([identity.id for identity in identities])
        await mission_repository.create(mission)
        stdout = io.StringIO()

        exit_code = await cli.process_due_command(
            100,
            mission_repository=mission_repository,
            identity_repository=identity_repository,
            current_time=CURRENT_TIME,
            stdout=stdout,
        )
        output = json.loads(stdout.getvalue())
        stored_mission = await mission_repository.get(mission.id)

        assert exit_code == 0
        assert output["processed_count"] == 1
        assert output["succeeded_mission_ids"] == [str(mission.id)]
        assert isinstance(output["succeeded_mission_ids"][0], str)
        assert UUID(output["succeeded_mission_ids"][0]) == mission.id
        assert stored_mission is not None
        assert stored_mission.status is MissionStatus.requires_confirmation
        assert stored_mission.status is not MissionStatus.processing

    asyncio.run(scenario())


def test_process_due_command_does_not_process_same_mission_twice() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission([identity.id for identity in identities])
        await mission_repository.create(mission)
        first_stdout = io.StringIO()
        second_stdout = io.StringIO()

        first_exit_code = await cli.process_due_command(
            100,
            mission_repository=mission_repository,
            identity_repository=identity_repository,
            current_time=CURRENT_TIME,
            stdout=first_stdout,
        )
        second_exit_code = await cli.process_due_command(
            100,
            mission_repository=mission_repository,
            identity_repository=identity_repository,
            current_time=CURRENT_TIME,
            stdout=second_stdout,
        )

        assert first_exit_code == 0
        assert second_exit_code == 0
        assert json.loads(first_stdout.getvalue())["processed_count"] == 1
        assert json.loads(second_stdout.getvalue())["processed_count"] == 0

    asyncio.run(scenario())


def test_process_due_command_returns_one_when_mission_fails() -> None:
    async def scenario() -> None:
        identity_repository = InMemoryIdentityRepository()
        mission_repository = InMemoryMissionRepository()
        mission = make_mission([uuid4()])
        await mission_repository.create(mission)
        stdout = io.StringIO()

        exit_code = await cli.process_due_command(
            100,
            mission_repository=mission_repository,
            identity_repository=identity_repository,
            current_time=CURRENT_TIME,
            stdout=stdout,
        )
        output = json.loads(stdout.getvalue())

        assert exit_code == 1
        assert output["processed_count"] == 1
        assert output["failed_mission_ids"] == [str(mission.id)]

    asyncio.run(scenario())


def test_process_due_command_writes_safe_stderr_on_infrastructure_error() -> None:
    async def scenario() -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        secret = "purchase_agent:purchase_agent@localhost"
        mission_repository = BrokenMissionRepository(secret)
        identity_repository = InMemoryIdentityRepository()

        exit_code = await cli.process_due_command(
            100,
            mission_repository=mission_repository,
            identity_repository=identity_repository,
            current_time=CURRENT_TIME,
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert "Infrastructure error" in stderr.getvalue()
        assert secret not in stdout.getvalue()
        assert secret not in stderr.getvalue()

    asyncio.run(scenario())


class BrokenMissionRepository:
    def __init__(self, message: str) -> None:
        self._message = message

    async def create(self, mission: Mission) -> Mission:
        raise NotImplementedError

    async def list(self) -> list[Mission]:
        raise NotImplementedError

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        raise NotImplementedError

    async def claim_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        raise RuntimeError(self._message)

    async def get(self, mission_id: UUID) -> Mission | None:
        raise NotImplementedError

    async def update(self, mission: Mission) -> Mission:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


def make_identity() -> Identity:
    return Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 1),
    )


def make_mission(participant_ids: list[UUID]) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Family train trip",
        status=MissionStatus.waiting,
        participant_ids=participant_ids,
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=len(participant_ids),
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(allow_adjacent_compartments=True),
        scheduled_at=CURRENT_TIME,
    )
