from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.execution import ExecutionEvent
from app.repositories.mission import MissionRepository
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_history_projection import (
    ProviderHistoryProjectionEvent,
    execution_event_to_provider_projection,
)


class ProviderHistoryProjectionVerificationStatus(StrEnum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"


class ProviderHistoryProjectionMismatchField(StrEnum):
    EVENT_TYPE = "event_type"
    OCCURRED_AT = "occurred_at"
    PAYLOAD = "payload"


class ProviderHistoryProjectionMismatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    fields: tuple[ProviderHistoryProjectionMismatchField, ...]


class MissionProviderHistoryProjectionVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    status: ProviderHistoryProjectionVerificationStatus
    canonical_event_count: int = Field(ge=0)
    projection_event_count: int = Field(ge=0)
    missing_projection_sequences: tuple[int, ...]
    unexpected_projection_sequences: tuple[int, ...]
    mismatches: tuple[ProviderHistoryProjectionMismatch, ...]

    @model_validator(mode="after")
    def validate_status(self) -> "MissionProviderHistoryProjectionVerification":
        has_difference = bool(
            self.missing_projection_sequences
            or self.unexpected_projection_sequences
            or self.mismatches
            or self.canonical_event_count != self.projection_event_count
        )
        expected = (
            ProviderHistoryProjectionVerificationStatus.INCONSISTENT
            if has_difference
            else ProviderHistoryProjectionVerificationStatus.CONSISTENT
        )
        if self.status is not expected:
            raise ValueError("verification status does not match comparison")
        return self


class VerifyMissionProviderHistoryProjection:
    def __init__(self, mission_repository: MissionRepository, projection_reader: object) -> None:
        self._mission_repository = mission_repository
        self._projection_reader = projection_reader

    async def execute(self, mission_id: UUID) -> MissionProviderHistoryProjectionVerification:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError
        projection_events = await self._projection_reader.list_all_for_mission(mission_id)
        return compare_provider_history_projection(
            mission_id=mission_id,
            canonical_events=mission.execution_log,
            projection_events=projection_events,
        )


def compare_provider_history_projection(
    *,
    mission_id: UUID,
    canonical_events: list[ExecutionEvent],
    projection_events: list[ProviderHistoryProjectionEvent],
) -> MissionProviderHistoryProjectionVerification:
    if len({event.sequence for event in canonical_events}) != len(canonical_events):
        raise ValueError("canonical event sequences must be unique")
    if len({event.sequence for event in projection_events}) != len(projection_events):
        raise ValueError("projection event sequences must be unique")
    canonical = {
        event.sequence: projection
        for index, event in enumerate(canonical_events)
        if (
            projection := execution_event_to_provider_projection(
                mission_id=mission_id,
                event=event,
                legacy_event_index=index,
            )
        ) is not None
    }
    projected = {event.sequence: event for event in projection_events}
    missing = tuple(sorted(set(canonical) - set(projected)))
    unexpected = tuple(sorted(set(projected) - set(canonical)))
    mismatches: list[ProviderHistoryProjectionMismatch] = []
    for sequence in sorted(set(canonical) & set(projected)):
        expected = canonical[sequence]
        actual = projected[sequence]
        fields: list[ProviderHistoryProjectionMismatchField] = []
        if expected.event_type != actual.event_type:
            fields.append(ProviderHistoryProjectionMismatchField.EVENT_TYPE)
        if _normalize_datetime(expected.occurred_at) != _normalize_datetime(actual.occurred_at):
            fields.append(ProviderHistoryProjectionMismatchField.OCCURRED_AT)
        if expected.payload.model_dump(mode="json") != actual.payload.model_dump(mode="json"):
            fields.append(ProviderHistoryProjectionMismatchField.PAYLOAD)
        if fields:
            mismatches.append(ProviderHistoryProjectionMismatch(sequence=sequence, fields=tuple(fields)))
    inconsistent = bool(missing or unexpected or mismatches)
    return MissionProviderHistoryProjectionVerification(
        mission_id=mission_id,
        status=(ProviderHistoryProjectionVerificationStatus.INCONSISTENT if inconsistent else ProviderHistoryProjectionVerificationStatus.CONSISTENT),
        canonical_event_count=len(canonical), projection_event_count=len(projected),
        missing_projection_sequences=missing, unexpected_projection_sequences=unexpected,
        mismatches=tuple(mismatches),
    )


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("provider history timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)
