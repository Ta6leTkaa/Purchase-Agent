from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.execution import ExecutionEvent
from app.services.provider_resolution_history import (
    PROVIDER_HISTORY_EVENT_TYPES,
    ProviderHistoryCursor,
    ProviderHistoryEventType,
    ProviderHistoryPayload,
    provider_event_to_history_item,
)


class ProviderHistoryProjectionEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    sequence: int = Field(ge=1)
    event_type: ProviderHistoryEventType
    occurred_at: datetime
    payload: ProviderHistoryPayload
    legacy_event_index: int = Field(ge=0)


class ProviderHistoryProjectionReader(Protocol):
    async def list_page(
        self,
        *,
        mission_id: UUID,
        cursor: ProviderHistoryCursor | None,
        fetch_limit: int,
    ) -> list[ProviderHistoryProjectionEvent]:
        ...

    async def list_since(
        self,
        *,
        mission_id: UUID,
        since_sequence: int,
        fetch_limit: int,
    ) -> list[ProviderHistoryProjectionEvent]:
        ...


class ProviderHistoryProjectionReaderFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[ProviderHistoryProjectionReader]:
        ...


def execution_event_to_provider_projection(
    *,
    mission_id: UUID,
    event: ExecutionEvent,
    legacy_event_index: int,
) -> ProviderHistoryProjectionEvent | None:
    if event.type not in PROVIDER_HISTORY_EVENT_TYPES:
        return None
    item = provider_event_to_history_item(event)
    return ProviderHistoryProjectionEvent(
        mission_id=mission_id,
        sequence=item.sequence,
        event_type=item.event_type,
        occurred_at=item.occurred_at,
        payload=item.payload,
        legacy_event_index=legacy_event_index,
    )
