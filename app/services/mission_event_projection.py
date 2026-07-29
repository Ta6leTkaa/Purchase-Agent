from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission import MissionModel, mission_from_model
from app.db.models.mission_event import MissionEventModel
from app.domain.execution import ExecutionEvent
from app.repositories.sqlalchemy.mission_event import (
    SqlAlchemyMissionEventProjectionRepository,
)
from app.services.mission_errors import MissionNotFoundError

if TYPE_CHECKING:
    from app.repositories.mission import MissionRepository


REBUILD_BATCH_SIZE = 500


@dataclass(frozen=True)
class MissionEventProjectionRebuildResult:
    processed_missions: int
    inserted_events: int


class RebuildMissionEventProjection:
    """Recreate the disposable relational read model from canonical events."""

    async def execute(
        self,
        session: AsyncSession,
    ) -> MissionEventProjectionRebuildResult:
        await session.execute(delete(MissionEventModel))
        writer = SqlAlchemyMissionEventProjectionRepository(session)
        processed_missions = 0
        inserted_events = 0
        pending: list[tuple[UUID, tuple[ExecutionEvent, ...]]] = []
        stream = await session.stream_scalars(
            select(MissionModel).order_by(MissionModel.id)
        )
        async for model in stream:
            mission = mission_from_model(model)
            processed_missions += 1
            events = tuple(mission.execution_log)
            if not events:
                continue
            pending.append((mission.id, events))
            inserted_events += len(events)
            if len(pending) >= REBUILD_BATCH_SIZE:
                await _append_pending_events(writer, pending)
                await session.flush()
                pending.clear()
        if pending:
            await _append_pending_events(writer, pending)
            await session.flush()
        return MissionEventProjectionRebuildResult(
            processed_missions=processed_missions,
            inserted_events=inserted_events,
        )


async def rebuild_mission_event_projection(
    session: AsyncSession,
) -> MissionEventProjectionRebuildResult:
    """Compatibility wrapper for callers that do not need the service object."""
    return await RebuildMissionEventProjection().execute(session)


async def _append_pending_events(
    writer: SqlAlchemyMissionEventProjectionRepository,
    pending: list[tuple[UUID, tuple[ExecutionEvent, ...]]],
) -> None:
    for mission_id, events in pending:
        await writer.append_many(mission_id, events)


class MissionEventProjectionVerificationStatus(StrEnum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"


class MissionEventProjectionMismatchField(StrEnum):
    EVENT = "event"


class MissionEventProjectionMismatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    fields: tuple[MissionEventProjectionMismatchField, ...]


class MissionEventProjectionVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    status: MissionEventProjectionVerificationStatus
    canonical_event_count: int = Field(ge=0)
    projection_event_count: int = Field(ge=0)
    missing_projection_sequences: tuple[int, ...]
    unexpected_projection_sequences: tuple[int, ...]
    mismatches: tuple[MissionEventProjectionMismatch, ...]

    @model_validator(mode="after")
    def validate_status(self) -> "MissionEventProjectionVerification":
        is_inconsistent = bool(
            self.missing_projection_sequences
            or self.unexpected_projection_sequences
            or self.mismatches
            or self.canonical_event_count != self.projection_event_count
        )
        expected_status = (
            MissionEventProjectionVerificationStatus.INCONSISTENT
            if is_inconsistent
            else MissionEventProjectionVerificationStatus.CONSISTENT
        )
        if self.status is not expected_status:
            raise ValueError("verification status does not match comparison")
        return self


class MissionEventProjectionVerificationReader(Protocol):
    async def list_all(self, mission_id: UUID) -> list[ExecutionEvent]:
        ...


class VerifyMissionEventProjection:
    def __init__(
        self,
        mission_repository: "MissionRepository",
        projection_reader: MissionEventProjectionVerificationReader,
    ) -> None:
        self._mission_repository = mission_repository
        self._projection_reader = projection_reader

    async def execute(
        self,
        mission_id: UUID,
    ) -> MissionEventProjectionVerification:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError
        projected_events = await self._projection_reader.list_all(mission_id)
        return compare_mission_event_projection(
            mission_id=mission_id,
            canonical_events=mission.execution_log,
            projected_events=projected_events,
        )


def compare_mission_event_projection(
    *,
    mission_id: UUID,
    canonical_events: list[ExecutionEvent],
    projected_events: list[ExecutionEvent],
) -> MissionEventProjectionVerification:
    _validate_unique_sequences(canonical_events, "canonical")
    _validate_unique_sequences(projected_events, "projection")
    canonical = {event.sequence: event for event in canonical_events}
    projected = {event.sequence: event for event in projected_events}
    missing = tuple(sorted(set(canonical) - set(projected)))
    unexpected = tuple(sorted(set(projected) - set(canonical)))
    mismatches = tuple(
        MissionEventProjectionMismatch(
            sequence=sequence,
            fields=(MissionEventProjectionMismatchField.EVENT,),
        )
        for sequence in sorted(set(canonical) & set(projected))
        if canonical[sequence].model_dump(mode="json")
        != projected[sequence].model_dump(mode="json")
    )
    is_inconsistent = bool(missing or unexpected or mismatches)
    return MissionEventProjectionVerification(
        mission_id=mission_id,
        status=(
            MissionEventProjectionVerificationStatus.INCONSISTENT
            if is_inconsistent
            else MissionEventProjectionVerificationStatus.CONSISTENT
        ),
        canonical_event_count=len(canonical),
        projection_event_count=len(projected),
        missing_projection_sequences=missing,
        unexpected_projection_sequences=unexpected,
        mismatches=mismatches,
    )


def mission_event_projection_matches(
    canonical_events: list[ExecutionEvent],
    projected_events: list[ExecutionEvent],
) -> bool:
    return [event.model_dump(mode="json") for event in canonical_events] == [
        event.model_dump(mode="json") for event in projected_events
    ]


def _validate_unique_sequences(
    events: list[ExecutionEvent],
    source: str,
) -> None:
    if len({event.sequence for event in events}) != len(events):
        raise ValueError(f"{source} event sequences must be unique")
