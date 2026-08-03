from __future__ import annotations

import builtins
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission import (
    MissionModel,
    mission_from_model,
    mission_to_model,
)
from app.db.models.mission_execution_attempt import MissionExecutionAttemptModel
from app.db.models.notification_outbox import NotificationOutboxMessageModel
from app.domain.execution_attempt import (
    MissionExecutionAttempt,
    MissionExecutionAttemptStatus,
)
from app.domain.mission import (
    Mission,
    MissionExecutionMode,
    MissionStatus,
    MissionSummary,
    MissionType,
)
from app.repositories.mission import (
    InvalidRepositoryTimeError,
    MissionRepository,
    RepositoryEntityNotFoundError,
)
from app.repositories.sqlalchemy.mission_event import (
    SqlAlchemyMissionEventProjectionRepository,
)
from app.repositories.sqlalchemy.provider_history import (
    SqlAlchemyProviderHistoryProjectionRepository,
)
from app.services.mission_event_store import mission_json_event_store
from app.services.mission_pagination import MissionCursor
from app.services.mission_state_machine import MissionStateMachine
from app.services.mission_statistics import MissionStatistics
from app.services.notification_outbox import NOTIFICATION_EVENT_TYPES
from app.services.provider_history_projection import (
    execution_event_to_provider_projection,
)


class MissionEventSequenceConflictError(Exception):
    pass


class SqlAlchemyMissionRepository(MissionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_statistics(
        self,
        current_time: datetime,
        claim_timeout: timedelta,
    ) -> MissionStatistics:
        _validate_stale_processing_arguments(current_time, claim_timeout, 1)
        status_result = await self._session.execute(
            select(MissionModel.status, func.count(MissionModel.id)).group_by(
                MissionModel.status
            )
        )
        missions_by_status = {
            MissionStatus(status): count for status, count in status_result.all()
        }
        pending_statuses = [
            MissionStatus.created.value,
            MissionStatus.waiting.value,
            MissionStatus.paused.value,
        ]
        stale_before = current_time - claim_timeout
        counters = (
            await self._session.execute(
                select(
                    func.count(MissionModel.id),
                    func.sum(
                        case(
                            (
                                (MissionModel.status == MissionStatus.waiting.value)
                                & MissionModel.scheduled_at.is_not(None)
                                & (MissionModel.scheduled_at <= current_time)
                                & (
                                    MissionModel.expires_at.is_(None)
                                    | (MissionModel.expires_at > current_time)
                                )
                                & (
                                    MissionModel.execution_attempts
                                    < MissionModel.max_execution_attempts
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                MissionModel.status.in_(pending_statuses)
                                & MissionModel.expires_at.is_not(None)
                                & (MissionModel.expires_at <= current_time),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                (MissionModel.status == MissionStatus.processing.value)
                                & MissionModel.claimed_at.is_not(None)
                                & (MissionModel.claimed_at <= stale_before),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                (MissionModel.status == MissionStatus.waiting.value)
                                & (
                                    MissionModel.execution_attempts
                                    >= MissionModel.max_execution_attempts
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                )
            )
        ).one()
        total, due, expired_pending, stale_processing, exhausted_waiting = counters
        return MissionStatistics(
            generated_at=current_time,
            total_missions=total,
            missions_by_status=missions_by_status,
            due_missions=due or 0,
            expired_pending_missions=expired_pending or 0,
            stale_processing_missions=stale_processing or 0,
            exhausted_waiting_missions=exhausted_waiting or 0,
            claim_timeout_seconds=int(claim_timeout.total_seconds()),
        )

    async def create(self, mission: Mission) -> Mission:
        model = mission_to_model(mission)
        self._session.add(model)
        await self._append_mission_events(
            mission,
            previous_last_event_sequence=0,
        )
        await self._append_provider_history_events(
            mission,
            previous_last_event_sequence=0,
        )
        await self._session.flush()
        return mission_from_model(model)

    async def list(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_list_limit(limit)
        statement = select(MissionModel)
        if status is not None:
            statement = statement.where(MissionModel.status == status.value)
        if mission_type is not None:
            statement = statement.where(
                MissionModel.mission_type == mission_type.value
            )
        result = await self._session.execute(
            statement.order_by(
                MissionModel.created_at,
                MissionModel.id,
            ).limit(limit)
        )
        return [
            mission_from_model(model)
            for model in result.scalars().all()
        ]

    async def list_summaries(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        limit: int = 100,
    ) -> builtins.list[MissionSummary]:
        _validate_list_limit(limit)
        statement = select(
            MissionModel.id,
            MissionModel.mission_type,
            MissionModel.title,
            MissionModel.status,
            MissionModel.execution_mode,
            MissionModel.provider_id,
            MissionModel.resolved_provider_id,
            MissionModel.scheduled_at,
            MissionModel.expires_at,
            MissionModel.execution_attempts,
            MissionModel.max_execution_attempts,
            MissionModel.last_event_sequence,
            MissionModel.participant_ids,
        )
        if status is not None:
            statement = statement.where(MissionModel.status == status.value)
        if mission_type is not None:
            statement = statement.where(
                MissionModel.mission_type == mission_type.value
            )
        result = await self._session.execute(
            statement.order_by(
                MissionModel.created_at,
                MissionModel.id,
            ).limit(limit)
        )
        return [
            MissionSummary(
                id=row.id,
                type=MissionType(row.mission_type),
                title=row.title,
                status=MissionStatus(row.status),
                execution_mode=MissionExecutionMode(row.execution_mode),
                provider_id=row.provider_id,
                resolved_provider_id=row.resolved_provider_id,
                scheduled_at=row.scheduled_at,
                expires_at=row.expires_at,
                execution_attempts=row.execution_attempts,
                max_execution_attempts=row.max_execution_attempts,
                last_event_sequence=row.last_event_sequence,
                participant_count=len(row.participant_ids),
            )
            for row in result.all()
        ]

    async def list_summary_page_candidates(
        self,
        *,
        status: MissionStatus | None = None,
        mission_type: MissionType | None = None,
        cursor: MissionCursor | None = None,
        limit: int = 101,
    ) -> builtins.list[MissionSummary]:
        _validate_list_limit(limit)
        statement = select(
            MissionModel.id,
            MissionModel.mission_type,
            MissionModel.title,
            MissionModel.status,
            MissionModel.execution_mode,
            MissionModel.provider_id,
            MissionModel.resolved_provider_id,
            MissionModel.scheduled_at,
            MissionModel.expires_at,
            MissionModel.execution_attempts,
            MissionModel.max_execution_attempts,
            MissionModel.last_event_sequence,
            MissionModel.participant_ids,
        )
        if status is not None:
            statement = statement.where(MissionModel.status == status.value)
        if mission_type is not None:
            statement = statement.where(
                MissionModel.mission_type == mission_type.value
            )
        if cursor is not None:
            statement = statement.where(MissionModel.id > cursor.mission_id)
        result = await self._session.execute(
            statement.order_by(MissionModel.id).limit(limit)
        )
        return [
            MissionSummary(
                id=row.id,
                type=MissionType(row.mission_type),
                title=row.title,
                status=MissionStatus(row.status),
                execution_mode=MissionExecutionMode(row.execution_mode),
                provider_id=row.provider_id,
                resolved_provider_id=row.resolved_provider_id,
                scheduled_at=row.scheduled_at,
                expires_at=row.expires_at,
                execution_attempts=row.execution_attempts,
                max_execution_attempts=row.max_execution_attempts,
                last_event_sequence=row.last_event_sequence,
                participant_count=len(row.participant_ids),
            )
            for row in result.all()
        ]

    async def list_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        result = await self._session.execute(
            select(MissionModel)
            .where(MissionModel.status == "waiting")
            .where(MissionModel.scheduled_at.is_not(None))
            .where(MissionModel.scheduled_at <= current_time)
            .where(
                (MissionModel.expires_at.is_(None))
                | (MissionModel.expires_at > current_time)
            )
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
    ) -> builtins.list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        result = await self._session.execute(
            select(MissionModel)
            .where(MissionModel.status == MissionStatus.waiting.value)
            .where(MissionModel.scheduled_at.is_not(None))
            .where(MissionModel.scheduled_at <= current_time)
            .where(
                (MissionModel.expires_at.is_(None))
                | (MissionModel.expires_at > current_time)
            )
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
            self._session.add(
                MissionExecutionAttemptModel(
                    id=uuid4(),
                    mission_id=model.id,
                    attempt_number=model.execution_attempts,
                    status=MissionExecutionAttemptStatus.processing.value,
                    claimed_at=current_time,
                )
            )

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
    ) -> builtins.list[Mission]:
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

    async def expire_due(
        self,
        current_time: datetime,
        limit: int = 100,
    ) -> builtins.list[Mission]:
        _validate_list_due_arguments(current_time, limit)
        result = await self._session.execute(
            select(MissionModel)
            .where(
                MissionModel.status.in_(
                    [
                        MissionStatus.created.value,
                        MissionStatus.waiting.value,
                        MissionStatus.paused.value,
                    ]
                )
            )
            .where(MissionModel.expires_at.is_not(None))
            .where(MissionModel.expires_at <= current_time)
            .order_by(MissionModel.expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        expired_missions: list[Mission] = []
        state_machine = MissionStateMachine()
        for model in result.scalars().all():
            mission = mission_from_model(model)
            previous_status = mission.status
            previous_sequence = mission.last_event_sequence
            state_machine.transition(mission, MissionStatus.expired)
            assert mission.expires_at is not None
            mission.record_event(
                timestamp=current_time,
                event_type="mission_expired",
                message="Mission expired before execution.",
                metadata={
                    "expires_at": mission.expires_at.isoformat(),
                    "previous_status": previous_status.value,
                },
            )
            model.status = mission.status.value
            model.last_event_sequence = mission.last_event_sequence
            model.execution_log = mission_json_event_store.serialize(
                mission.execution_log
            )
            await self._append_mission_events(
                mission,
                previous_last_event_sequence=previous_sequence,
            )
            mission.mark_event_sequence_persisted()
            expired_missions.append(mission)

        await self._session.flush()
        await self._session.commit()
        return expired_missions

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
            model.execution_log = mission_json_event_store.serialize(
                mission.execution_log
            )
            await self._close_open_execution_attempt(
                mission,
                finished_at=current_time,
                status=(
                    MissionExecutionAttemptStatus.failed
                    if mission.status is MissionStatus.failed
                    else MissionExecutionAttemptStatus.recovered
                ),
            )
            recovered_missions.append(mission)

        await self._session.flush()
        await self._session.commit()
        return recovered_missions

    async def list_execution_attempts(
        self,
        mission_id: UUID,
    ) -> builtins.list[MissionExecutionAttempt]:
        result = await self._session.execute(
            select(MissionExecutionAttemptModel)
            .where(MissionExecutionAttemptModel.mission_id == mission_id)
            .order_by(MissionExecutionAttemptModel.attempt_number.asc())
        )
        return [
            _execution_attempt_from_model(model)
            for model in result.scalars().all()
        ]

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

    async def references_identity(self, identity_id: UUID) -> bool:
        result = await self._session.execute(
            select(MissionModel.id)
            .where(
                MissionModel.participant_ids.contains([str(identity_id)])
            )
            .limit(1)
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
                execution_mode=updated_model.execution_mode,
                provider_id=updated_model.provider_id,
                resolved_provider_id=updated_model.resolved_provider_id,
                reservation_id=updated_model.reservation_id,
                scheduled_at=updated_model.scheduled_at,
                expires_at=updated_model.expires_at,
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
        if cast(CursorResult[object], result).rowcount == 0:
            existing_model = await self._session.get(MissionModel, mission.id)
            if existing_model is None:
                raise RepositoryEntityNotFoundError
            raise MissionEventSequenceConflictError

        await self._session.flush()
        await self._synchronize_open_execution_attempt(mission)
        await self._session.flush()
        await self._append_provider_history_events(
            mission,
            previous_last_event_sequence=mission.persisted_last_event_sequence,
        )
        await self._append_mission_events(
            mission,
            previous_last_event_sequence=mission.persisted_last_event_sequence,
        )
        await self._session.flush()
        mission.mark_event_sequence_persisted()
        return mission

    async def _synchronize_open_execution_attempt(
        self,
        mission: Mission,
    ) -> None:
        if mission.status is MissionStatus.processing:
            if mission.resolved_provider_id is not None:
                await self._session.execute(
                    update(MissionExecutionAttemptModel)
                    .where(
                        MissionExecutionAttemptModel.mission_id == mission.id
                    )
                    .where(
                        MissionExecutionAttemptModel.status
                        == MissionExecutionAttemptStatus.processing.value
                    )
                    .values(resolved_provider_id=mission.resolved_provider_id)
                )
            return

        status_by_mission_status = {
            MissionStatus.requires_confirmation: (
                MissionExecutionAttemptStatus.requires_confirmation
            ),
            MissionStatus.completed: MissionExecutionAttemptStatus.completed,
            MissionStatus.failed: MissionExecutionAttemptStatus.failed,
        }
        attempt_status = status_by_mission_status.get(mission.status)
        if attempt_status is not None:
            open_attempt_id = await self._session.scalar(
                select(MissionExecutionAttemptModel.id)
                .where(
                    MissionExecutionAttemptModel.mission_id == mission.id
                )
                .where(
                    MissionExecutionAttemptModel.status
                    == MissionExecutionAttemptStatus.processing.value
                )
                .limit(1)
            )
            if open_attempt_id is None:
                return
            await self._close_open_execution_attempt(
                mission,
                finished_at=_mission_event_time(mission),
                status=attempt_status,
            )

    async def _close_open_execution_attempt(
        self,
        mission: Mission,
        *,
        finished_at: datetime,
        status: MissionExecutionAttemptStatus,
    ) -> None:
        await self._session.execute(
            update(MissionExecutionAttemptModel)
            .where(MissionExecutionAttemptModel.mission_id == mission.id)
            .where(
                MissionExecutionAttemptModel.status
                == MissionExecutionAttemptStatus.processing.value
            )
            .values(
                status=status.value,
                finished_at=finished_at,
                resolved_provider_id=mission.resolved_provider_id,
                reservation_id=mission.reservation_id,
            )
        )

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
            for event_index, event in (
                mission_json_event_store.indexed_events_after(
                    mission.execution_log,
                    previous_last_event_sequence,
                )
            )
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

    async def _append_mission_events(
        self,
        mission: Mission,
        *,
        previous_last_event_sequence: int,
    ) -> None:
        events = mission_json_event_store.events_after(
            mission.execution_log,
            previous_last_event_sequence,
        )
        if events:
            await SqlAlchemyMissionEventProjectionRepository(
                self._session
            ).append_many(mission.id, events)
            for event in events:
                if event.type not in NOTIFICATION_EVENT_TYPES:
                    continue
                self._session.add(
                    NotificationOutboxMessageModel(
                        id=uuid4(),
                        mission_id=mission.id,
                        event_id=event.event_id,
                        event_type=event.type,
                        occurred_at=event.timestamp,
                        payload=mission_json_event_store.serialize([event])[0],
                        recipient_ids=[
                            str(participant_id)
                            for participant_id in mission.participant_ids
                        ],
                        status="pending",
                        delivery_attempts=0,
                        available_at=event.timestamp,
                    )
                )


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


def _validate_list_limit(limit: int) -> None:
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


def _execution_attempt_from_model(
    model: MissionExecutionAttemptModel,
) -> MissionExecutionAttempt:
    return MissionExecutionAttempt(
        id=model.id,
        mission_id=model.mission_id,
        attempt_number=model.attempt_number,
        status=MissionExecutionAttemptStatus(model.status),
        claimed_at=model.claimed_at,
        finished_at=model.finished_at,
        resolved_provider_id=model.resolved_provider_id,
        reservation_id=model.reservation_id,
    )


def _mission_event_time(mission: Mission) -> datetime:
    if mission.execution_log:
        return mission.execution_log[-1].timestamp
    if mission.claimed_at is not None:
        return mission.claimed_at
    raise ValueError("cannot close execution attempt without a timestamp")
