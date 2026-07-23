from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.domain.execution import ExecutionEvent
from app.domain.mission import MissionType
from app.domain.provider_id import normalize_provider_id


class ProviderSelectionMode(str, Enum):
    explicit = "explicit"
    automatic = "automatic"


class ProviderResolutionFailureReason(str, Enum):
    unknown_provider = "unknown_provider"
    unsupported_mission_type = "unsupported_mission_type"
    no_supporting_provider = "no_supporting_provider"
    ambiguous_provider = "ambiguous_provider"


class ProviderResolvedEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    mission_type: MissionType
    selection_mode: ProviderSelectionMode

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, value: str) -> str:
        normalized_value = normalize_provider_id(value)
        assert normalized_value is not None
        return normalized_value


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
        if self.previous_selection_mode is not _selection_mode_for(
            self.previous_provider_id
        ):
            raise ValueError("previous selection mode is invalid")
        if self.new_selection_mode is not _selection_mode_for(
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
        previous_selection_mode=_selection_mode_for(previous_provider_id),
        new_selection_mode=_selection_mode_for(new_provider_id),
    )
    return ExecutionEvent(
        timestamp=occurred_at,
        type="provider_selection_changed",
        message="Mission provider selection changed.",
        metadata=payload.model_dump(mode="json"),
    )


def _selection_mode_for(provider_id: str | None) -> ProviderSelectionMode:
    return (
        ProviderSelectionMode.explicit
        if provider_id is not None
        else ProviderSelectionMode.automatic
    )
