import asyncio
import builtins
import io
import json
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import cli
from app.domain.execution_attempt import MissionExecutionAttempt
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionSummary,
    MissionType,
    TrainConstraints,
)
from app.repositories.mission import MissionRepository
from app.services.deployment_smoke import (
    DeploymentSmokeCheck,
    DeploymentSmokeResult,
)
from app.services.mission_event_projection import (
    MissionEventProjectionRebuildResult,
)
from app.services.mission_pagination import MissionCursor
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository

CURRENT_TIME = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def test_smoke_api_command_passes_cli_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str | None, float]] = []

    async def fake_smoke_api_command(
        base_url: str,
        api_key: str | None,
        admin_api_key: str | None,
        timeout_seconds: float,
    ) -> int:
        calls.append((base_url, api_key, admin_api_key, timeout_seconds))
        return 0

    monkeypatch.setattr(cli, "smoke_api_command", fake_smoke_api_command)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "smoke-api",
                "--base-url",
                "https://api.example.test",
                "--api-key",
                "client-key",
                "--admin-api-key",
                "admin-key",
                "--timeout-seconds",
                "12.5",
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [
        ("https://api.example.test", "client-key", "admin-key", 12.5)
    ]


def test_smoke_api_command_writes_report_and_failure_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_deployment_smoke(**kwargs: object) -> DeploymentSmokeResult:
        return DeploymentSmokeResult(
            ok=False,
            checks=[
                DeploymentSmokeCheck(
                    name="readiness",
                    ok=False,
                    status_code=503,
                    message="Unexpected status or response shape.",
                )
            ],
        )

    monkeypatch.setattr(cli, "run_deployment_smoke", fake_run_deployment_smoke)
    stdout = io.StringIO()

    exit_code = asyncio.run(
        cli.smoke_api_command(
            "https://api.example.test",
            "client-key",
            "admin-key",
            10,
            stdout=stdout,
        )
    )

    assert exit_code == 1
    assert json.loads(stdout.getvalue())["checks"][0]["name"] == "readiness"


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


def test_prune_notifications_command_passes_retention_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[timedelta, int, bool]] = []

    async def fake_prune_notifications_command(
        retention: timedelta,
        limit: int,
        *,
        dry_run: bool = False,
    ) -> int:
        calls.append((retention, limit, dry_run))
        return 0

    monkeypatch.setattr(
        cli,
        "prune_notifications_command",
        fake_prune_notifications_command,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "prune-notifications",
                "--retention-days",
                "45",
                "--limit",
                "250",
                "--dry-run",
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [(timedelta(days=45), 250, True)]


def test_prune_creation_receipts_passes_retention_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[timedelta, int, bool]] = []

    async def fake_prune_creation_receipts_command(
        retention: timedelta,
        limit: int,
        *,
        dry_run: bool = False,
    ) -> int:
        calls.append((retention, limit, dry_run))
        return 0

    monkeypatch.setattr(
        cli,
        "prune_creation_receipts_command",
        fake_prune_creation_receipts_command,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "prune-creation-receipts",
                "--retention-days",
                "60",
                "--limit",
                "200",
                "--dry-run",
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [(timedelta(days=60), 200, True)]


@pytest.mark.parametrize("retention_days", ["0", "3651", "invalid"])
def test_prune_notifications_rejects_invalid_retention(
    retention_days: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            ["prune-notifications", "--retention-days", retention_days]
        )

    assert exc_info.value.code == 2


def test_worker_command_passes_cli_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[timedelta, int, timedelta]] = []

    async def fake_run_worker(
        poll_interval: timedelta,
        limit: int,
        claim_timeout: timedelta,
    ) -> int:
        calls.append((poll_interval, limit, claim_timeout))
        return 0

    monkeypatch.setattr(cli, "_run_worker_until_signal", fake_run_worker)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "worker",
                "--poll-interval-seconds",
                "2.5",
                "--limit",
                "25",
                "--claim-timeout-seconds",
                "120",
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [
        (timedelta(seconds=2.5), 25, timedelta(seconds=120))
    ]


@pytest.mark.parametrize("poll_interval", ["0", "-1", "3601", "invalid"])
def test_worker_command_rejects_invalid_poll_interval(
    poll_interval: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            ["worker", "--poll-interval-seconds", poll_interval]
        )

    assert exc_info.value.code == 2


def test_worker_command_opens_separate_recovery_and_processing_sessions() -> None:
    async def scenario() -> None:
        mission_repository = InMemoryMissionRepository()
        identity_repository = InMemoryIdentityRepository()
        session_maker = CountingFakeSessionMaker()
        dependencies = cli.CliDependencies(
            session_maker=cast(
                async_sessionmaker[AsyncSession],
                session_maker,
            ),
            mission_repository_factory=lambda session: mission_repository,
            identity_repository_factory=lambda session: identity_repository,
            clock=lambda: CURRENT_TIME,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = await cli.worker_command(
            timedelta(milliseconds=1),
            10,
            dependencies=dependencies,
            max_cycles=2,
            stdout=stdout,
            stderr=stderr,
        )

        output_lines = stdout.getvalue().splitlines()
        assert exit_code == 0
        assert session_maker.calls == 4
        assert len(output_lines) == 2
        assert all(
            json.loads(line)["processing"]["processed_count"] == 0
            for line in output_lines
        )
        assert stderr.getvalue() == ""

    asyncio.run(scenario())


def test_worker_recovers_stale_mission_before_processing_same_cycle() -> None:
    async def scenario() -> None:
        mission_repository = InMemoryMissionRepository()
        identity_repository = InMemoryIdentityRepository()
        identities = [
            await identity_repository.create(make_identity())
            for _ in range(4)
        ]
        mission = make_mission([identity.id for identity in identities])
        mission.status = MissionStatus.processing
        mission.claimed_at = CURRENT_TIME - timedelta(minutes=20)
        mission.execution_attempts = 1
        await mission_repository.create(mission)
        dependencies = cli.CliDependencies(
            session_maker=cast(
                async_sessionmaker[AsyncSession],
                CountingFakeSessionMaker(),
            ),
            mission_repository_factory=lambda session: mission_repository,
            identity_repository_factory=lambda session: identity_repository,
            clock=lambda: CURRENT_TIME,
        )
        stdout = io.StringIO()

        exit_code = await cli.worker_command(
            timedelta(seconds=1),
            10,
            claim_timeout=timedelta(minutes=15),
            dependencies=dependencies,
            max_cycles=1,
            stdout=stdout,
        )
        output = json.loads(stdout.getvalue())
        stored = await mission_repository.get(mission.id)

        assert exit_code == 0
        assert output["recovered_mission_ids"] == [str(mission.id)]
        assert output["stale_failed_mission_ids"] == []
        assert output["processing"]["succeeded_mission_ids"] == [
            str(mission.id)
        ]
        assert stored is not None
        assert stored.status is MissionStatus.requires_confirmation
        assert stored.execution_attempts == 2

    asyncio.run(scenario())


def test_worker_reports_exhausted_stale_mission_without_reprocessing() -> None:
    async def scenario() -> None:
        mission_repository = InMemoryMissionRepository()
        identity_repository = InMemoryIdentityRepository()
        mission = make_mission([uuid4()])
        mission.status = MissionStatus.processing
        mission.claimed_at = CURRENT_TIME - timedelta(minutes=20)
        mission.execution_attempts = 1
        mission.max_execution_attempts = 1
        await mission_repository.create(mission)
        dependencies = cli.CliDependencies(
            session_maker=cast(
                async_sessionmaker[AsyncSession],
                CountingFakeSessionMaker(),
            ),
            mission_repository_factory=lambda session: mission_repository,
            identity_repository_factory=lambda session: identity_repository,
            clock=lambda: CURRENT_TIME,
        )
        stdout = io.StringIO()

        await cli.worker_command(
            timedelta(seconds=1),
            10,
            claim_timeout=timedelta(minutes=15),
            dependencies=dependencies,
            max_cycles=1,
            stdout=stdout,
        )
        output = json.loads(stdout.getvalue())

        assert output["recovered_mission_ids"] == []
        assert output["stale_failed_mission_ids"] == [str(mission.id)]
        assert output["processing"]["processed_count"] == 0
        assert mission.status is MissionStatus.failed

    asyncio.run(scenario())


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


def test_recover_stale_command_uses_default_arguments(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[timedelta, int]] = []

    async def fake_recover_stale_command(
        claim_timeout: timedelta,
        limit: int,
    ) -> tuple[int, cli.StaleMissionRecoveryResult]:
        calls.append((claim_timeout, limit))
        return 0, cli.StaleMissionRecoveryResult(
            recovered_count=0,
            recovered_mission_ids=[],
        )

    monkeypatch.setattr(
        cli,
        "recover_stale_command",
        fake_recover_stale_command,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["recover-stale"])

    assert exc_info.value.code == 0
    assert calls == [(timedelta(seconds=900), 100)]
    assert json.loads(capsys.readouterr().out) == {
        "recovered_count": 0,
        "recovered_mission_ids": [],
    }


def test_recover_stale_command_passes_custom_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[timedelta, int]] = []

    async def fake_recover_stale_command(
        claim_timeout: timedelta,
        limit: int,
    ) -> tuple[int, cli.StaleMissionRecoveryResult]:
        calls.append((claim_timeout, limit))
        return 0, cli.StaleMissionRecoveryResult(
            recovered_count=0,
            recovered_mission_ids=[],
        )

    monkeypatch.setattr(
        cli,
        "recover_stale_command",
        fake_recover_stale_command,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "recover-stale",
                "--claim-timeout-seconds",
                "1800",
                "--limit",
                "50",
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [(timedelta(seconds=1800), 50)]


@pytest.mark.parametrize(
    "arguments",
    [
        ["recover-stale", "--claim-timeout-seconds", "0"],
        ["recover-stale", "--claim-timeout-seconds", "86401"],
        ["recover-stale", "--limit", "0"],
        ["recover-stale", "--limit", "501"],
    ],
)
def test_recover_stale_command_rejects_invalid_arguments(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(arguments)

    assert exc_info.value.code == 2


def test_recover_stale_command_returns_json_without_processing_missions() -> None:
    async def scenario() -> None:
        repository = CapturingMissionRepository()
        stale_mission = make_processing_mission(
            claimed_at=CURRENT_TIME - timedelta(minutes=16)
        )
        fresh_mission = make_processing_mission(
            claimed_at=CURRENT_TIME - timedelta(minutes=14)
        )
        await repository.create(stale_mission)
        await repository.create(fresh_mission)
        dependencies = make_cli_dependencies(repository)

        exit_code, result = await cli.recover_stale_command(
            timedelta(minutes=15),
            100,
            dependencies=dependencies,
        )
        output = json.loads(result.model_dump_json())
        stored_stale_mission = await repository.get(stale_mission.id)
        stored_fresh_mission = await repository.get(fresh_mission.id)

        assert exit_code == 0
        assert output == {
            "recovered_count": 1,
            "recovered_mission_ids": [str(stale_mission.id)],
        }
        assert repository.recovery_arguments == [
            (CURRENT_TIME, timedelta(minutes=15), 100)
        ]
        assert stored_stale_mission is not None
        assert stored_stale_mission.status is MissionStatus.waiting
        assert stored_fresh_mission is not None
        assert stored_fresh_mission.status is MissionStatus.processing

    asyncio.run(scenario())


def test_recover_stale_command_succeeds_when_no_missions_are_stale() -> None:
    async def scenario() -> None:
        repository = CapturingMissionRepository()

        exit_code, result = await cli.recover_stale_command(
            timedelta(minutes=15),
            100,
            dependencies=make_cli_dependencies(repository),
        )

        assert exit_code == 0
        assert result.recovered_count == 0
        assert result.recovered_mission_ids == []

    asyncio.run(scenario())


def test_recover_stale_command_writes_safe_stderr_on_infrastructure_error() -> None:
    async def scenario() -> None:
        secret = "purchase_agent:purchase_agent@localhost"
        stderr = io.StringIO()
        dependencies = make_cli_dependencies(
            BrokenMissionRepository(secret)
        )

        exit_code, result = await cli.recover_stale_command(
            timedelta(minutes=15),
            100,
            dependencies=dependencies,
            stderr=stderr,
        )

        assert exit_code == 1
        assert result.recovered_count == 0
        assert result.recovered_mission_ids == []
        assert "Infrastructure error" in stderr.getvalue()
        assert secret not in result.model_dump_json()
        assert secret not in stderr.getvalue()

    asyncio.run(scenario())


def test_rebuild_mission_events_command_writes_projection_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_rebuild_command() -> tuple[
        int, MissionEventProjectionRebuildResult
    ]:
        return 0, MissionEventProjectionRebuildResult(3, 7)

    monkeypatch.setattr(
        cli,
        "rebuild_mission_event_projection_command",
        fake_rebuild_command,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["rebuild-mission-events"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == "Processed missions: 3\nInserted events: 7\n"


def test_rebuild_mission_events_command_writes_safe_error_on_failure() -> None:
    async def scenario() -> None:
        stderr = io.StringIO()
        secret = "purchase_agent:purchase_agent@localhost"
        dependencies = cli.CliDependencies(
            session_maker=cast(
                async_sessionmaker[AsyncSession],
                BrokenCommitSessionMaker(secret),
            )
        )

        exit_code, result = await cli.rebuild_mission_event_projection_command(
            dependencies=dependencies,
            stderr=stderr,
        )

        assert exit_code == 1
        assert result == MissionEventProjectionRebuildResult(0, 0)
        assert "Infrastructure error" in stderr.getvalue()
        assert secret not in stderr.getvalue()

    asyncio.run(scenario())


class BrokenMissionRepository:
    def __init__(self, message: str) -> None:
        self._message = message

    async def create(self, mission: Mission) -> Mission:
        raise NotImplementedError

    async def list(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        del status, mission_type, limit
        raise NotImplementedError

    async def list_summaries(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[MissionSummary]:
        del status, mission_type, limit
        raise NotImplementedError

    async def list_summary_page_candidates(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        cursor: MissionCursor | None = None,
        limit: int = 101,
    ) -> builtins.list[MissionSummary]:
        del status, mission_type, cursor, limit
        raise NotImplementedError

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        raise NotImplementedError

    async def claim_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        raise RuntimeError(self._message)

    async def expire_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        raise RuntimeError(self._message)

    async def list_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        raise NotImplementedError

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        raise RuntimeError(self._message)

    async def get(self, mission_id: UUID) -> Mission | None:
        raise NotImplementedError

    async def exists(self, mission_id: UUID) -> bool:
        raise NotImplementedError

    async def references_identity(self, identity_id: UUID) -> bool:
        raise NotImplementedError

    async def list_execution_attempts(
        self,
        mission_id: UUID,
    ) -> builtins.list[MissionExecutionAttempt]:
        raise NotImplementedError

    async def update(self, mission: Mission) -> Mission:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


class CapturingMissionRepository(InMemoryMissionRepository):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_arguments: list[tuple[datetime, timedelta, int]] = []

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[Mission]:
        self.recovery_arguments.append((current_time, claim_timeout, limit))
        return await super().recover_stale_processing(
            current_time,
            claim_timeout,
            limit,
        )


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def commit(self) -> None:
        return None


class BrokenCommitSession(FakeSession):
    async def execute(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError(self._message)

    def __init__(self, message: str) -> None:
        self._message = message


class BrokenCommitSessionMaker:
    def __init__(self, message: str) -> None:
        self._message = message

    def __call__(self) -> BrokenCommitSession:
        return BrokenCommitSession(self._message)


class FakeSessionMaker:
    def __call__(self) -> FakeSession:
        return FakeSession()


class CountingFakeSessionMaker:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> FakeSession:
        self.calls += 1
        return FakeSession()


def make_cli_dependencies(
    repository: MissionRepository,
) -> cli.CliDependencies:
    return cli.CliDependencies(
        session_maker=cast(
            async_sessionmaker[AsyncSession],
            FakeSessionMaker(),
        ),
        mission_repository_factory=lambda session: repository,
        clock=lambda: CURRENT_TIME,
    )


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


def make_processing_mission(claimed_at: datetime) -> Mission:
    mission = make_mission([uuid4()])
    mission.status = MissionStatus.processing
    mission.claimed_at = claimed_at
    return mission
