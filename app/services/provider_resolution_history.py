from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.execution import ExecutionEvent
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolvedEventPayload,
    ProviderSelectionChangedEventPayload,
)
from app.repositories.mission import MissionRepository
from app.services.mission_errors import MissionNotFoundError


class ProviderHistoryEventType(str, Enum):
    provider_selection_changed = "provider_selection_changed"
    provider_resolved = "provider_resolved"
    provider_resolution_failed = "provider_resolution_failed"


ProviderHistoryPayload = (
    ProviderSelectionChangedEventPayload
    | ProviderResolvedEventPayload
    | ProviderResolutionFailedEventPayload
)
PROVIDER_HISTORY_EVENT_TYPES = frozenset(ProviderHistoryEventType)


class ProviderResolutionHistoryItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: ProviderHistoryEventType
    occurred_at: datetime
    payload: ProviderHistoryPayload

    @model_validator(mode="after")
    def validate_payload_type(self) -> "ProviderResolutionHistoryItem":
        expected_payload_type = {
            ProviderHistoryEventType.provider_selection_changed: (
                ProviderSelectionChangedEventPayload
            ),
            ProviderHistoryEventType.provider_resolved: (
                ProviderResolvedEventPayload
            ),
            ProviderHistoryEventType.provider_resolution_failed: (
                ProviderResolutionFailedEventPayload
            ),
        }[self.event_type]
        if not isinstance(self.payload, expected_payload_type):
            raise ValueError("provider history payload does not match event type")
        return self


class MissionProviderResolutionHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    items: tuple[ProviderResolutionHistoryItem, ...]


class GetMissionProviderResolutionHistory:
    def __init__(self, mission_repository: MissionRepository) -> None:
        self._mission_repository = mission_repository

    async def execute(self, mission_id: UUID) -> MissionProviderResolutionHistory:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError

        filtered_events = [
            (index, event)
            for index, event in enumerate(mission.execution_log)
            if event.type in PROVIDER_HISTORY_EVENT_TYPES
        ]
        ordered_events = sorted(
            filtered_events,
            key=lambda indexed_event: (
                indexed_event[1].timestamp,
                indexed_event[0],
            ),
        )
        return MissionProviderResolutionHistory(
            mission_id=mission.id,
            items=tuple(
                provider_event_to_history_item(event)
                for _, event in ordered_events
            ),
        )


def provider_event_to_history_item(
    event: ExecutionEvent,
) -> ProviderResolutionHistoryItem:
    try:
        event_type = ProviderHistoryEventType(event.type)
    except ValueError as exc:
        raise ValueError(
            f"Unsupported provider history event type '{event.type}'"
        ) from exc

    payload_type = {
        ProviderHistoryEventType.provider_selection_changed: (
            ProviderSelectionChangedEventPayload
        ),
        ProviderHistoryEventType.provider_resolved: ProviderResolvedEventPayload,
        ProviderHistoryEventType.provider_resolution_failed: (
            ProviderResolutionFailedEventPayload
        ),
    }[event_type]
    return ProviderResolutionHistoryItem(
        event_type=event_type,
        occurred_at=event.timestamp,
        payload=payload_type.model_validate(event.metadata),
    )
