import asyncio
import base64
import binascii
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.execution import ExecutionEvent
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolvedEventPayload,
    ProviderSelectionChangedEventPayload,
)
from app.repositories.mission import MissionRepository
from app.services.mission_errors import MissionNotFoundError

DEFAULT_PROVIDER_HISTORY_PAGE_SIZE = 50
MAX_PROVIDER_HISTORY_PAGE_SIZE = 100
DEFAULT_PROVIDER_HISTORY_WAIT_SECONDS = 0
MAX_PROVIDER_HISTORY_WAIT_SECONDS = 30
DEFAULT_PROVIDER_HISTORY_INCREMENT_LIMIT = 100
MAX_PROVIDER_HISTORY_INCREMENT_LIMIT = 500
MAX_PROVIDER_HISTORY_WAIT = timedelta(
    seconds=MAX_PROVIDER_HISTORY_WAIT_SECONDS
)
PROVIDER_HISTORY_POLL_INTERVAL = timedelta(milliseconds=500)
_CURSOR_VERSION = 1


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


class InvalidProviderHistoryCursorError(ValueError):
    """Raised when a provider history cursor cannot be safely used."""


class ProviderHistoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: UUID
    occurred_at: datetime
    event_index: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_occurred_at(self) -> "ProviderHistoryCursor":
        if (
            self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("cursor occurred_at must be timezone-aware")
        return self


class ProviderHistoryCursorCodec:
    def encode(self, cursor: ProviderHistoryCursor) -> str:
        payload = {
            "event_index": cursor.event_index,
            "mission_id": str(cursor.mission_id),
            "occurred_at": cursor.occurred_at.isoformat(),
            "v": _CURSOR_VERSION,
        }
        encoded_payload = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return base64.urlsafe_b64encode(encoded_payload).decode().rstrip("=")

    def decode(self, value: str) -> ProviderHistoryCursor:
        try:
            padding = "=" * (-len(value) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(f"{value}{padding}").decode()
            )
            if (
                not isinstance(payload, dict)
                or payload.pop("v", None) != _CURSOR_VERSION
            ):
                raise ValueError("unsupported cursor version")
            return ProviderHistoryCursor.model_validate(payload)
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            ValidationError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidProviderHistoryCursorError(
                "Provider history cursor is invalid"
            ) from exc


class ProviderResolutionHistoryPageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    limit: int = Field(
        default=DEFAULT_PROVIDER_HISTORY_PAGE_SIZE,
        ge=1,
        le=MAX_PROVIDER_HISTORY_PAGE_SIZE,
    )
    cursor: ProviderHistoryCursor | None = None


class ProviderResolutionHistoryItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
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


class MissionProviderResolutionHistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    items: tuple[ProviderResolutionHistoryItem, ...]
    limit: int = Field(ge=1, le=MAX_PROVIDER_HISTORY_PAGE_SIZE)
    has_more: bool
    next_cursor: ProviderHistoryCursor | None

    @model_validator(mode="after")
    def validate_page(self) -> "MissionProviderResolutionHistoryPage":
        if self.has_more:
            if not self.items or self.next_cursor is None:
                raise ValueError("non-final history page requires a next cursor")
            if self.next_cursor.mission_id != self.mission_id:
                raise ValueError("next cursor must belong to the mission")
            if self.next_cursor.occurred_at != self.items[-1].occurred_at:
                raise ValueError("next cursor must follow the last visible item")
        elif self.next_cursor is not None:
            raise ValueError("final history page must not have a next cursor")
        return self


class ProviderResolutionIncrementRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    since_sequence: int = Field(ge=0)
    limit: int = Field(
        default=DEFAULT_PROVIDER_HISTORY_INCREMENT_LIMIT,
        ge=1,
        le=MAX_PROVIDER_HISTORY_INCREMENT_LIMIT,
    )
    wait_timeout: timedelta = timedelta(
        seconds=DEFAULT_PROVIDER_HISTORY_WAIT_SECONDS
    )

    @model_validator(mode="after")
    def validate_wait_timeout(self) -> "ProviderResolutionIncrementRequest":
        if self.wait_timeout < timedelta(0):
            raise ValueError("wait timeout must not be negative")
        if self.wait_timeout > MAX_PROVIDER_HISTORY_WAIT:
            raise ValueError(
                "wait timeout exceeds the maximum provider history wait"
            )
        return self


class AsyncWaiter(Protocol):
    def monotonic(self) -> float:
        ...

    async def sleep(self, delay: timedelta) -> None:
        ...


class AsyncioWaiter:
    def monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep(self, delay: timedelta) -> None:
        await asyncio.sleep(delay.total_seconds())


class MissionReadRepositoryFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[MissionRepository]:
        ...


class StaticMissionReadRepositoryFactory:
    """Supplies a read-only repository where persistence has no session scope."""

    def __init__(self, mission_repository: MissionRepository) -> None:
        self._mission_repository = mission_repository

    @asynccontextmanager
    async def open(self) -> AsyncIterator[MissionRepository]:
        yield self._mission_repository


class MissionProviderResolutionIncrement(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    since_sequence: int = Field(ge=0)
    latest_sequence: int = Field(ge=0)
    has_more: bool
    items: tuple[ProviderResolutionHistoryItem, ...]

    @model_validator(mode="after")
    def validate_increment(self) -> "MissionProviderResolutionIncrement":
        if self.latest_sequence < self.since_sequence:
            raise ValueError("latest sequence cannot precede since sequence")
        previous_sequence = self.since_sequence
        for item in self.items:
            if item.sequence <= previous_sequence:
                raise ValueError("increment items must be strictly increasing")
            previous_sequence = item.sequence
        if self.items:
            if self.latest_sequence != self.items[-1].sequence:
                raise ValueError("latest sequence must match the final item")
        else:
            if self.latest_sequence != self.since_sequence:
                raise ValueError("empty increment must retain since sequence")
            if self.has_more:
                raise ValueError("empty increment cannot have more items")
        if self.has_more and not self.items:
            raise ValueError("increment with more items cannot be empty")
        return self


class GetMissionProviderResolutionHistory:
    def __init__(self, mission_repository: MissionRepository) -> None:
        self._mission_repository = mission_repository

    async def execute(
        self,
        mission_id: UUID,
        request: ProviderResolutionHistoryPageRequest | None = None,
    ) -> MissionProviderResolutionHistoryPage:
        page_request = request or ProviderResolutionHistoryPageRequest()
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError
        if (
            page_request.cursor is not None
            and page_request.cursor.mission_id != mission.id
        ):
            raise InvalidProviderHistoryCursorError(
                "Provider history cursor does not belong to this mission"
            )

        ordered_events = sorted(
            (
                (index, event)
                for index, event in enumerate(mission.execution_log)
                if event.type in PROVIDER_HISTORY_EVENT_TYPES
            ),
            key=_history_event_key,
        )
        after_cursor = page_request.cursor
        if after_cursor is not None:
            ordered_events = [
                indexed_event
                for indexed_event in ordered_events
                if _history_event_key(indexed_event)
                > (after_cursor.occurred_at, after_cursor.event_index)
            ]

        fetched_events = ordered_events[: page_request.limit + 1]
        has_more = len(fetched_events) > page_request.limit
        visible_events = fetched_events[: page_request.limit]
        next_cursor = None
        if has_more:
            event_index, event = visible_events[-1]
            next_cursor = ProviderHistoryCursor(
                mission_id=mission.id,
                occurred_at=event.timestamp,
                event_index=event_index,
            )

        return MissionProviderResolutionHistoryPage(
            mission_id=mission.id,
            items=tuple(
                provider_event_to_history_item(event)
                for _, event in visible_events
            ),
            limit=page_request.limit,
            has_more=has_more,
            next_cursor=next_cursor,
        )


class GetMissionProviderResolutionIncrement:
    def __init__(
        self,
        mission_read_repository_factory: MissionReadRepositoryFactory,
        waiter: AsyncWaiter,
        poll_interval: timedelta = PROVIDER_HISTORY_POLL_INTERVAL,
    ) -> None:
        if poll_interval <= timedelta(0):
            raise ValueError("poll interval must be greater than zero")
        if poll_interval > MAX_PROVIDER_HISTORY_WAIT:
            raise ValueError("poll interval exceeds the maximum wait")
        self._mission_read_repository_factory = (
            mission_read_repository_factory
        )
        self._waiter = waiter
        self._poll_interval = poll_interval

    async def execute(
        self,
        mission_id: UUID,
        request: ProviderResolutionIncrementRequest,
    ) -> MissionProviderResolutionIncrement:
        result = await self._read_increment_once(
            mission_id=mission_id,
            since_sequence=request.since_sequence,
            limit=request.limit,
        )
        if result.items or request.wait_timeout <= timedelta(0):
            return result

        deadline = self._waiter.monotonic() + request.wait_timeout.total_seconds()
        while True:
            remaining_seconds = deadline - self._waiter.monotonic()
            if remaining_seconds <= 0:
                return await self._read_increment_once(
                    mission_id=mission_id,
                    since_sequence=request.since_sequence,
                    limit=request.limit,
                )

            await self._waiter.sleep(
                min(
                    self._poll_interval,
                    timedelta(seconds=remaining_seconds),
                )
            )
            result = await self._read_increment_once(
                mission_id=mission_id,
                since_sequence=request.since_sequence,
                limit=request.limit,
            )
            if result.items or self._waiter.monotonic() >= deadline:
                return result

    async def _read_increment_once(
        self,
        *,
        mission_id: UUID,
        since_sequence: int,
        limit: int,
    ) -> MissionProviderResolutionIncrement:
        async with self._mission_read_repository_factory.open() as repository:
            mission = await repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError

        candidates: list[ExecutionEvent] = []
        for event in mission.execution_log:
            if event.sequence <= since_sequence:
                continue
            if event.type not in PROVIDER_HISTORY_EVENT_TYPES:
                continue
            candidates.append(event)
            if len(candidates) == limit + 1:
                break

        has_more = len(candidates) > limit
        items = tuple(
            provider_event_to_history_item(event)
            for event in candidates[:limit]
        )
        latest_sequence = (
            items[-1].sequence if items else since_sequence
        )
        return MissionProviderResolutionIncrement(
            mission_id=mission.id,
            since_sequence=since_sequence,
            latest_sequence=latest_sequence,
            has_more=has_more,
            items=items,
        )


def _history_event_key(
    indexed_event: tuple[int, ExecutionEvent],
) -> tuple[datetime, int]:
    event_index, event = indexed_event
    return event.timestamp, event_index


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
        sequence=event.sequence,
        event_type=event_type,
        occurred_at=event.timestamp,
        payload=payload_type.model_validate(event.metadata),
    )
