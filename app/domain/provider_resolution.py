from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.execution import ExecutionEvent
from app.domain.mission import Mission, MissionType
from app.domain.provider_id import normalize_provider_id


class ProviderSelectionMode(str, Enum):
    explicit = "explicit"
    automatic = "automatic"


class ProviderResolutionFailureReason(str, Enum):
    unknown_provider = "unknown_provider"
    unsupported_mission_type = "unsupported_mission_type"
    no_supporting_provider = "no_supporting_provider"
    ambiguous_provider = "ambiguous_provider"


class ProviderResolutionPreviewOutcome(str, Enum):
    resolved = "resolved"
    unknown_provider = "unknown_provider"
    unsupported_mission_type = "unsupported_mission_type"
    no_supporting_provider = "no_supporting_provider"
    ambiguous_provider = "ambiguous_provider"


class ProviderResolvedEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    mission_type: MissionType
    selection_mode: ProviderSelectionMode
    snapshot: "ProviderResolutionSnapshot | None" = None

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        normalized_value = normalize_provider_id(value)
        assert normalized_value is not None
        return normalized_value

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ProviderResolvedEventPayload":
        if self.snapshot is None:
            return self
        if self.provider_id != self.snapshot.resolved_provider_id:
            raise ValueError("resolved provider does not match snapshot")
        if self.mission_type is not self.snapshot.mission_type:
            raise ValueError("mission type does not match snapshot")
        if self.selection_mode is not self.snapshot.selection_mode:
            raise ValueError("selection mode does not match snapshot")
        return self


class ProviderResolutionSnapshot(BaseModel):
    """Immutable resolver context persisted with one successful resolution."""

    model_config = ConfigDict(frozen=True)

    selection_mode: ProviderSelectionMode
    requested_provider_id: str | None
    resolved_provider_id: str
    candidate_provider_ids: tuple[str, ...]
    mission_type: MissionType

    @field_validator("requested_provider_id")
    @classmethod
    def validate_requested_provider_id(cls, value: str | None) -> str | None:
        return normalize_provider_id(value)

    @field_validator("resolved_provider_id")
    @classmethod
    def validate_resolved_provider_id(cls, value: str) -> str:
        normalized_value = normalize_provider_id(value)
        assert normalized_value is not None
        return normalized_value

    @field_validator("candidate_provider_ids")
    @classmethod
    def validate_candidate_provider_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized_ids: list[str] = []
        for provider_id in value:
            normalized_provider_id = normalize_provider_id(provider_id)
            assert normalized_provider_id is not None
            normalized_ids.append(normalized_provider_id)
        if not normalized_ids:
            raise ValueError("candidate provider IDs must not be empty")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("candidate provider IDs must be unique")
        return tuple(normalized_ids)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "ProviderResolutionSnapshot":
        if self.resolved_provider_id not in self.candidate_provider_ids:
            raise ValueError("resolved provider must be a candidate")
        if self.selection_mode is ProviderSelectionMode.automatic:
            if self.requested_provider_id is not None:
                raise ValueError("automatic snapshot cannot request a provider")
        elif self.requested_provider_id != self.resolved_provider_id:
            raise ValueError("explicit snapshot must resolve requested provider")
        return self


ProviderResolvedEventPayload.model_rebuild()


class ProviderResolutionFailedEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: ProviderResolutionFailureReason
    mission_type: MissionType
    requested_provider_id: str | None = None
    candidate_provider_ids: tuple[str, ...] = ()

    @field_validator("requested_provider_id")
    @classmethod
    def validate_requested_provider_id(cls, value: str | None) -> str | None:
        return normalize_provider_id(value)

    @field_validator("candidate_provider_ids")
    @classmethod
    def validate_candidate_provider_ids(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized_ids = tuple(
            normalize_provider_id(provider_id)
            for provider_id in value
        )
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("candidate provider IDs must be unique")
        return normalized_ids

    @model_validator(mode="after")
    def validate_reason_fields(self) -> "ProviderResolutionFailedEventPayload":
        explicit_reasons = {
            ProviderResolutionFailureReason.unknown_provider,
            ProviderResolutionFailureReason.unsupported_mission_type,
        }
        if self.reason in explicit_reasons:
            if self.requested_provider_id is None or self.candidate_provider_ids:
                raise ValueError("explicit resolution failure payload is invalid")
        elif self.reason is ProviderResolutionFailureReason.no_supporting_provider:
            if self.requested_provider_id is not None or self.candidate_provider_ids:
                raise ValueError("no-supporting-provider payload is invalid")
        elif (
            self.requested_provider_id is not None
            or len(self.candidate_provider_ids) < 2
        ):
            raise ValueError("ambiguous-provider payload is invalid")
        return self


class ProviderSelectionChangedEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    previous_provider_id: str | None
    new_provider_id: str | None
    previous_selection_mode: ProviderSelectionMode
    new_selection_mode: ProviderSelectionMode

    @field_validator("previous_provider_id", "new_provider_id")
    @classmethod
    def validate_provider_id(cls, value: str | None) -> str | None:
        return normalize_provider_id(value)

    @model_validator(mode="after")
    def validate_selection_change(
        self,
    ) -> "ProviderSelectionChangedEventPayload":
        if self.previous_provider_id == self.new_provider_id:
            raise ValueError("provider selection must change")
        if self.previous_selection_mode is not selection_mode_for(
            self.previous_provider_id
        ):
            raise ValueError("previous selection mode is invalid")
        if self.new_selection_mode is not selection_mode_for(
            self.new_provider_id
        ):
            raise ValueError("new selection mode is invalid")
        return self


def create_provider_selection_changed_event(
    *,
    previous_provider_id: str | None,
    new_provider_id: str | None,
    occurred_at: datetime,
) -> ExecutionEvent:
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    payload = ProviderSelectionChangedEventPayload(
        previous_provider_id=previous_provider_id,
        new_provider_id=new_provider_id,
        previous_selection_mode=selection_mode_for(previous_provider_id),
        new_selection_mode=selection_mode_for(new_provider_id),
    )


def create_provider_resolution_snapshot(
    *,
    mission: Mission,
    resolved_provider_id: str,
    candidate_provider_ids: tuple[str, ...],
) -> ProviderResolutionSnapshot:
    return ProviderResolutionSnapshot(
        selection_mode=selection_mode_for(mission.provider_id),
        requested_provider_id=mission.provider_id,
        resolved_provider_id=resolved_provider_id,
        candidate_provider_ids=candidate_provider_ids,
        mission_type=mission.mission_type,
    )
    return ExecutionEvent(
        timestamp=occurred_at,
        type="provider_selection_changed",
        message="Mission provider selection changed.",
        metadata=payload.model_dump(mode="json"),
    )


def selection_mode_for(provider_id: str | None) -> ProviderSelectionMode:
    return (
        ProviderSelectionMode.explicit
        if provider_id is not None
        else ProviderSelectionMode.automatic
    )
