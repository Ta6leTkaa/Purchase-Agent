import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.adapters.base import ProviderAdapter
from app.adapters.registry import ProviderRegistry
from app.domain.identity import Identity
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionExecutionMode,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability
from app.services.mission_update import (
    InvalidMissionUpdateError,
    MissionUpdateNotAllowedError,
    update_mission,
)
from app.services.provider_errors import UnsupportedExecutionModeError
from app.storage.memory import InMemoryMissionRepository

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


class SearchOnlyAdapter(ProviderAdapter):
    @property
    def provider_id(self) -> str:
        return "search_only"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(
            {ProviderCapability(mission_type=MissionType.TRAIN_TICKET)}
        )

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        return []

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> ReservationResult:
        raise NotImplementedError


def make_mission(
    *,
    status: MissionStatus = MissionStatus.created,
    provider_id: str | None = None,
    execution_attempts: int = 0,
) -> Mission:
    return Mission(
        id=uuid4(),
        title="Original title",
        status=status,
        participant_ids=[uuid4()],
        provider="rzd",
        provider_id=provider_id,
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=1,
        ),
        execution_attempts=execution_attempts,
    )


def test_update_mission_changes_configuration_and_records_audit_event() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)

        updated = await update_mission(
            mission.id,
            repository,
            ProviderRegistry([]),
            title="Updated title",
            fallback_rules=FallbackRules(allow_any_coupe_seats=True),
            execution_mode=MissionExecutionMode.SEARCH_ONLY,
            max_execution_attempts=5,
            clock=lambda: NOW,
        )

        assert updated.title == "Updated title"
        assert updated.fallback_rules.allow_any_coupe_seats is True
        assert updated.execution_mode is MissionExecutionMode.SEARCH_ONLY
        assert updated.max_execution_attempts == 5
        event = updated.execution_log[-1]
        assert event.type == "mission_updated"
        assert event.timestamp == NOW
        assert event.metadata == {
            "changed_fields": [
                "execution_mode",
                "fallback_rules",
                "max_execution_attempts",
                "title",
            ],
            "previous": {
                "title": "Original title",
                "fallback_rules": {
                    "allow_adjacent_compartments": None,
                    "allow_any_coupe_seats": None,
                    "notify_only_if_no_match": None,
                },
                "execution_mode": "require_confirmation",
                "max_execution_attempts": 3,
            },
            "current": {
                "title": "Updated title",
                "fallback_rules": {
                    "allow_adjacent_compartments": None,
                    "allow_any_coupe_seats": True,
                    "notify_only_if_no_match": None,
                },
                "execution_mode": "search_only",
                "max_execution_attempts": 5,
            },
        }

    asyncio.run(scenario())


def test_update_mission_noop_does_not_record_event() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission()
        await repository.create(mission)

        updated = await update_mission(
            mission.id,
            repository,
            ProviderRegistry([]),
            title=mission.title,
        )

        assert updated.last_event_sequence == 0
        assert updated.execution_log == []

    asyncio.run(scenario())


def test_update_mission_allows_paused_mission() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(status=MissionStatus.paused)
        await repository.create(mission)

        updated = await update_mission(
            mission.id,
            repository,
            ProviderRegistry([]),
            title="Updated while paused",
            clock=lambda: NOW,
        )

        assert updated.status is MissionStatus.paused
        assert updated.title == "Updated while paused"
        assert updated.execution_log[-1].type == "mission_updated"

    asyncio.run(scenario())


def test_update_mission_rejects_active_mission() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(status=MissionStatus.running)
        await repository.create(mission)

        with pytest.raises(MissionUpdateNotAllowedError):
            await update_mission(
                mission.id,
                repository,
                ProviderRegistry([]),
                title="Too late",
            )

    asyncio.run(scenario())


def test_update_mission_rejects_attempt_limit_below_used_attempts() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(execution_attempts=2)
        await repository.create(mission)

        with pytest.raises(InvalidMissionUpdateError):
            await update_mission(
                mission.id,
                repository,
                ProviderRegistry([]),
                max_execution_attempts=1,
            )

    asyncio.run(scenario())


def test_update_mission_checks_explicit_provider_capability() -> None:
    async def scenario() -> None:
        repository = InMemoryMissionRepository()
        mission = make_mission(provider_id="search_only")
        await repository.create(mission)

        with pytest.raises(UnsupportedExecutionModeError):
            await update_mission(
                mission.id,
                repository,
                ProviderRegistry([SearchOnlyAdapter()]),
                execution_mode=MissionExecutionMode.AUTO_PURCHASE,
            )

        stored = await repository.get(mission.id)
        assert stored is not None
        assert (
            stored.execution_mode
            is MissionExecutionMode.REQUIRE_CONFIRMATION
        )
        assert stored.execution_log == []

    asyncio.run(scenario())
