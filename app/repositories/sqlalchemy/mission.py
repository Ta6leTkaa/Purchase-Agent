from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission import (
    MissionModel,
    mission_from_model,
    mission_to_model,
)
from app.domain.mission import Mission, MissionStatus
from app.repositories.mission import (
    InvalidRepositoryTimeError,
    MissionRepository,
    RepositoryEntityNotFoundError,
)
from app.repositories.sqlalchemy.provider_history import (
    SqlAlchemyProviderHistoryProjectionRepository,
)
from app.services.provider_history_projection import (
    execution_event_to_provider_projection,
)
from app.services.mission_state_machine import MissionStateMachine


class MissionEventSequenceConflictError(Exception):
    pass


class SqlAlchemyMissionRepository(MissionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, mission: Mission) -> Mission:
        model = mission_to_model(mission)
        self._session.add(model)
        await self._append_provider_history_events(
            mission,
            previous_last_event_sequence=0,
        )
        await self._session.flush()
        return mission_from_model(model)

    async def list(self) -> list[Mission]:
        result = await self._session.execute(
            select(MissionModel).order_by(MissionModel.created_at)
        )
        return [
            mission_from_model(model)
            for model in result.scalars().all()
        ]

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        result = await self._session.execute(
            select(MissionModel)
            .where(MissionModel.status == "waiting")
            .where(MissionModel.scheduled_at.is_not(None))
            .where(MissionModel.scheduled_at <= current_time)
            .where(
                MissionModel.execution_attempts
                < MissionModel.max_execution_attempts
            )
            .order_by(MissionModel.scheduled_at.asc())
            .limit(limit)
        )
        return [
            mission_from_model(model)
            for model in result.scalars().all()
        ]

    async def claim_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        result = await self._session.execute(
            select(MissionModel)
            .where(MissionModel.status == MissionStatus.waiting.value)
            .where(MissionModel.scheduled_at.is_not(None))
            .where(MissionModel.scheduled_at <= current_time)
            .where(
                MissionModel.execution_attempts
                < MissionModel.max_execution_attempts
            )
            .order_by(MissionModel.scheduled_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        models = list(result.scalars().all())
        for model in models:
            model.status = MissionStatus.processing.value
            model.claimed_at = current_time
            model.execution_attempts += 1

        await self._session.flush()
        await self._session.commit()
        return [
            mission_from_model(model)
            for model in models
        ]

    async def list_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[Mission]:
        _validate_stale_processing_arguments(
            current_time,
            claim_timeout,
            limit,
        )
        stale_before = current_time - claim_timeout
        result = await self._session.execute(
            select(MissionModel)
            .where(MissionModel.status == MissionStatus.processing.value)
            .where(MissionModel.claimed_at.is_not(None))
            .where(MissionModel.claimed_at <= stale_before)
            .order_by(MissionModel.claimed_at.asc())
            .limit(limit)
        )
        return [
            mission_from_model(model)
            for model in result.scalars().all()
        ]

    async def recover_stale_processing(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
        limit: int = 100,
    ) -> list[Mission]:
        _validate_stale_processing_arguments(
            current_time,
            claim_timeout,
            limit,
        )
        stale_before = current_time - claim_timeout
        result = await self._session.execute(
            select(MissionModel)
            .where(MissionModel.status == MissionStatus.processing.value)
            .where(MissionModel.claimed_at.is_not(None))
            .where(MissionModel.claimed_at <= stale_before)
            .order_by(MissionModel.claimed_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        state_machine = MissionStateMachine()
        recovered_missions: list[Mission] = []
        for model in result.scalars().all():
            mission = mission_from_model(model)
            state_machine.recover_stale(mission, current_time)
            model.status = mission.status.value
            model.claimed_at = mission.claimed_at
            model.last_event_sequence = mission.last_event_sequence
            model.execution_log = [
                event.model_dump(mode="json")
                for event in mission.execution_log
            ]
            recovered_missions.append(mission)

        await self._session.flush()
        await self._session.commit()
        return recovered_missions

    async def get(self, mission_id: UUID) -> Mission | None:
        model = await self._session.get(MissionModel, mission_id)
        if model is None:
            return None
        return mission_from_model(model)

    async def exists(self, mission_id: UUID) -> bool:
        result = await self._session.execute(
            select(MissionModel.id).where(MissionModel.id == mission_id)
        )
        return result.scalar_one_or_none() is not None

    async def update(self, mission: Mission) -> Mission:
        updated_model = mission_to_model(mission)
        result = await self._session.execute(
            update(MissionModel)
            .where(MissionModel.id == mission.id)
            .where(
                MissionModel.last_event_sequence
                == mission.persisted_last_event_sequence
            )
            .values(
                type=updated_model.type,
                mission_type=updated_model.mission_type,
                payload=updated_model.payload,
                title=updated_model.title,
                status=updated_model.status,
                provider=updated_model.provider,
                provider_id=updated_model.provider_id,
                resolved_provider_id=updated_model.resolved_provider_id,
                scheduled_at=updated_model.scheduled_at,
                claimed_at=updated_model.claimed_at,
                execution_attempts=updated_model.execution_attempts,
                max_execution_attempts=updated_model.max_execution_attempts,
                last_event_sequence=updated_model.last_event_sequence,
                participant_ids=updated_model.participant_ids,
                constraints=updated_model.constraints,
                fallback_rules=updated_model.fallback_rules,
                execution_log=updated_model.execution_log,
                best_option=updated_model.best_option,
            )
        )
        if result.rowcount == 0:
            existing_model = await self._session.get(MissionModel, mission.id)
            if existing_model is None:
                raise RepositoryEntityNotFoundError
            raise MissionEventSequenceConflictError

        await self._session.flush()
        await self._append_provider_history_events(
            mission,
            previous_last_event_sequence=mission.persisted_last_event_sequence,
        )
        await self._session.flush()
        mission.mark_event_sequence_persisted()
        return mission

    async def clear(self) -> None:
        await self._session.execute(delete(MissionModel))
        await self._session.flush()

    async def _append_provider_history_events(
        self,
        mission: Mission,
        *,
        previous_last_event_sequence: int,
    ) -> None:
        events = [
            projection
            for event_index, event in enumerate(mission.execution_log)
            if event.sequence > previous_last_event_sequence
            if (
                projection := execution_event_to_provider_projection(
                    mission_id=mission.id,
                    event=event,
                    legacy_event_index=event_index,
                )
            ) is not None
        ]
        if events:
            await SqlAlchemyProviderHistoryProjectionRepository(
                self._session
            ).append_many(events)


def get_sqlalchemy_mission_repository(
    session: AsyncSession,
) -> SqlAlchemyMissionRepository:
    return SqlAlchemyMissionRepository(session)


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
