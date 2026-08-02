from __future__ import annotations

import asyncio
import builtins
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.execution_attempt import (
    MissionExecutionAttempt,
    MissionExecutionAttemptStatus,
)
from app.domain.identity import Identity, Preferences
from app.domain.mission import Mission, MissionStatus, MissionSummary, MissionType
from app.repositories.mission import InvalidRepositoryTimeError
from app.services.mission_state_machine import MissionStateMachine


class InMemoryIdentityRepository:
    def __init__(self) -> None:
        self._identities: dict[UUID, Identity] = {}

    async def create(self, identity: Identity) -> Identity:
        self._identities[identity.id] = identity
        return identity

    async def list(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> list[Identity]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        normalized = query.strip().casefold() if query is not None else None
        if normalized == "":
            raise ValueError("query must not be blank")
        return [
            identity
            for identity in self._identities.values()
            if normalized is None
            or any(
                normalized in value.casefold()
                for value in (
                    identity.display_name,
                    identity.first_name,
                    identity.last_name,
                )
            )
        ][:limit]

    async def get(self, identity_id: UUID) -> Identity | None:
        return self._identities.get(identity_id)

    async def update_preferences(
        self,
        identity_id: UUID,
        preferences: Preferences,
    ) -> Identity | None:
        identity = self._identities.get(identity_id)
        if identity is None:
            return None
        identity.preferences = preferences
        return identity

    async def clear(self) -> None:
        self._identities.clear()


class InMemoryMissionRepository:
    def __init__(self) -> None:
        self._missions: dict[UUID, Mission] = {}
        self._execution_attempts: dict[UUID, list[MissionExecutionAttempt]] = {}
        self._claim_lock = asyncio.Lock()

    async def create(self, mission: Mission) -> Mission:
        self._missions[mission.id] = mission
        return mission

    async def list(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        return [
            mission
            for mission in self._missions.values()
            if (status is None or mission.status is status)
            and (mission_type is None or mission.type is mission_type)
        ][:limit]

    async def list_summaries(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[MissionSummary]:
        missions = await self.list(
            status=status,
            mission_type=mission_type,
            limit=limit,
        )
        return [MissionSummary.from_mission(mission) for mission in missions]

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        due_missions = [
            mission
            for mission in self._missions.values()
            if mission.status is MissionStatus.waiting
            and mission.scheduled_at is not None
            and mission.scheduled_at <= current_time
            and (
                mission.expires_at is None
                or mission.expires_at > current_time
            )
            and not mission.has_exhausted_attempts
        ]
        return sorted(
            due_missions,
            key=lambda mission: mission.scheduled_at or current_time,
        )[:limit]

    async def claim_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        async with self._claim_lock:
            due_missions = [
                mission
                for mission in self._missions.values()
                if mission.status is MissionStatus.waiting
                and mission.scheduled_at is not None
                and mission.scheduled_at <= current_time
                and (
                    mission.expires_at is None
                    or mission.expires_at > current_time
                )
                and not mission.has_exhausted_attempts
            ]
            claimed_missions = sorted(
                due_missions,
                key=lambda mission: mission.scheduled_at or current_time,
            )[:limit]
            for mission in claimed_missions:
                mission.status = MissionStatus.processing
                mission.claimed_at = current_time
                mission.execution_attempts += 1
                self._execution_attempts.setdefault(mission.id, []).append(
                    MissionExecutionAttempt(
                        mission_id=mission.id,
                        attempt_number=mission.execution_attempts,
                        status=MissionExecutionAttemptStatus.processing,
                        claimed_at=current_time,
                    )
                )
                self._missions[mission.id] = mission
            return claimed_missions

    async def list_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_stale_processing_arguments(
            current_time,
            claim_timeout,
            limit,
        )
        stale_before = current_time - claim_timeout
        stale_missions = [
            mission
            for mission in self._missions.values()
            if mission.status is MissionStatus.processing
            and mission.claimed_at is not None
            and mission.claimed_at <= stale_before
        ]
        return sorted(
            stale_missions,
            key=lambda mission: mission.claimed_at or current_time,
        )[:limit]

    async def expire_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        async with self._claim_lock:
            candidates = sorted(
                (
                    mission
                    for mission in self._missions.values()
                    if mission.status
                    in {
                        MissionStatus.created,
                        MissionStatus.waiting,
                        MissionStatus.paused,
                    }
                    and mission.expires_at is not None
                    and mission.expires_at <= current_time
                ),
                key=lambda mission: mission.expires_at or current_time,
            )[:limit]
            state_machine = MissionStateMachine()
            for mission in candidates:
                previous_status = mission.status
                assert mission.expires_at is not None
                state_machine.transition(mission, MissionStatus.expired)
                mission.record_event(
                    timestamp=current_time,
                    event_type="mission_expired",
                    message="Mission expired before execution.",
                    metadata={
                        "expires_at": mission.expires_at.isoformat(),
                        "previous_status": previous_status.value,
                    },
                )
                self._missions[mission.id] = mission
            return candidates

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_stale_processing_arguments(
            current_time,
            claim_timeout,
            limit,
        )
        stale_before = current_time - claim_timeout
        async with self._claim_lock:
            stale_missions = [
                mission
                for mission in self._missions.values()
                if mission.status is MissionStatus.processing
                and mission.claimed_at is not None
                and mission.claimed_at <= stale_before
            ]
            recovered_missions = sorted(
                stale_missions,
                key=lambda mission: mission.claimed_at or current_time,
            )[:limit]
            state_machine = MissionStateMachine()
            for mission in recovered_missions:
                state_machine.recover_stale(mission, current_time)
                self._close_execution_attempt(
                    mission,
                    finished_at=current_time,
                    status=(
                        MissionExecutionAttemptStatus.failed
                        if mission.status is MissionStatus.failed
                        else MissionExecutionAttemptStatus.recovered
                    ),
                )
                self._missions[mission.id] = mission
            return recovered_missions

    async def list_execution_attempts(
        self,
        mission_id: UUID,
    ) -> builtins.list[MissionExecutionAttempt]:
        return list(self._execution_attempts.get(mission_id, ()))

    async def get(self, mission_id: UUID) -> Mission | None:
        return self._missions.get(mission_id)

    async def exists(self, mission_id: UUID) -> bool:
        return mission_id in self._missions

    async def update(self, mission: Mission) -> Mission:
        if mission.status is MissionStatus.processing:
            self._set_open_attempt_provider(mission)
        elif mission.status in {
            MissionStatus.requires_confirmation,
            MissionStatus.completed,
            MissionStatus.failed,
        }:
            attempts = self._execution_attempts.get(mission.id, [])
            if (
                attempts
                and attempts[-1].status
                is MissionExecutionAttemptStatus.processing
            ):
                self._close_execution_attempt(
                    mission,
                    finished_at=_mission_event_time(mission),
                    status=MissionExecutionAttemptStatus(mission.status.value),
                )
        self._missions[mission.id] = mission
        return mission

    async def clear(self) -> None:
        self._missions.clear()
        self._execution_attempts.clear()

    def _set_open_attempt_provider(self, mission: Mission) -> None:
        attempts = self._execution_attempts.get(mission.id, [])
        if not attempts or mission.resolved_provider_id is None:
            return
        attempt = attempts[-1]
        if attempt.status is not MissionExecutionAttemptStatus.processing:
            return
        attempts[-1] = attempt.model_copy(
            update={"resolved_provider_id": mission.resolved_provider_id}
        )

    def _close_execution_attempt(
        self,
        mission: Mission,
        *,
        finished_at: datetime,
        status: MissionExecutionAttemptStatus,
    ) -> None:
        attempts = self._execution_attempts.get(mission.id, [])
        if not attempts:
            return
        attempt = attempts[-1]
        if attempt.status is not MissionExecutionAttemptStatus.processing:
            return
        attempts[-1] = attempt.model_copy(
            update={
                "status": status,
                "finished_at": finished_at,
                "resolved_provider_id": mission.resolved_provider_id,
                "reservation_id": mission.reservation_id,
            }
        )


class MemoryStore:
    def __init__(self) -> None:
        self.identities = InMemoryIdentityRepository()
        self.missions = InMemoryMissionRepository()

    async def create_identity(self, identity: Identity) -> Identity:
        return await self.identities.create(identity)

    async def list_identities(self) -> list[Identity]:
        return await self.identities.list()

    async def get_identity(self, identity_id: UUID) -> Identity | None:
        return await self.identities.get(identity_id)

    async def create_mission(self, mission: Mission) -> Mission:
        return await self.missions.create(mission)

    async def list_missions(self) -> list[Mission]:
        return await self.missions.list()

    async def get_mission(self, mission_id: UUID) -> Mission | None:
        return await self.missions.get(mission_id)

    async def update_mission(self, mission: Mission) -> Mission:
        return await self.missions.update(mission)

    async def clear_identities(self) -> None:
        await self.identities.clear()

    def clear(self) -> None:
        self.identities._identities.clear()
        self.missions._missions.clear()
        self.missions._execution_attempts.clear()


store = MemoryStore()


def _validate_list_due_arguments(
    current_time: datetime,
    limit: int,
) -> None:
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise InvalidRepositoryTimeError(
            "current_time must be timezone-aware"
        )
    if limit <= 0:
        raise ValueError("limit must be greater than 0")


def _validate_stale_processing_arguments(
    current_time: datetime,
    claim_timeout: timedelta,
    limit: int,
) -> None:
    _validate_list_due_arguments(current_time, limit)
    if claim_timeout <= timedelta(0):
        raise ValueError("claim_timeout must be greater than 0")


def _mission_event_time(mission: Mission) -> datetime:
    if mission.execution_log:
        return mission.execution_log[-1].timestamp
    if mission.claimed_at is not None:
        return mission.claimed_at
    raise ValueError("cannot close execution attempt without a timestamp")
