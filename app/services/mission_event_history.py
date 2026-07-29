from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.execution import ExecutionEvent
from app.repositories.mission import MissionRepository
from app.services.mission_errors import MissionNotFoundError

DEFAULT_MISSION_EVENT_PAGE_SIZE = 50
MAX_MISSION_EVENT_PAGE_SIZE = 100


class MissionEventHistoryPageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(
        default=DEFAULT_MISSION_EVENT_PAGE_SIZE,
        ge=1,
        le=MAX_MISSION_EVENT_PAGE_SIZE,
    )


class MissionEventHistoryPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    after_sequence: int
    latest_sequence: int
    has_more: bool
    items: tuple[ExecutionEvent, ...]


class GetMissionEventHistory:
    def __init__(self, mission_repository: MissionRepository) -> None:
        self._mission_repository = mission_repository

    async def execute(
        self,
        mission_id: UUID,
        request: MissionEventHistoryPageRequest,
    ) -> MissionEventHistoryPage:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError

        matching_events = [
            event
            for event in mission.execution_log
            if event.sequence > request.after_sequence
        ]
        fetched_events = matching_events[: request.limit + 1]
        items = tuple(fetched_events[: request.limit])
        has_more = len(fetched_events) > request.limit
        latest_sequence = (
            items[-1].sequence if items else request.after_sequence
        )
        return MissionEventHistoryPage(
            mission_id=mission.id,
            after_sequence=request.after_sequence,
            latest_sequence=latest_sequence,
            has_more=has_more,
            items=items,
        )
