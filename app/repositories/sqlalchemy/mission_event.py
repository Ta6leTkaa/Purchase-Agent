from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission_event import MissionEventModel
from app.domain.execution import ExecutionEvent
from app.services.mission_event_store import mission_json_event_store


class SqlAlchemyMissionEventProjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_many(
        self,
        mission_id: UUID,
        events: tuple[ExecutionEvent, ...],
    ) -> None:
        self._session.add_all(
            [
                MissionEventModel(
                    mission_id=mission_id,
                    sequence=event.sequence,
                    event_id=event.event_id,
                    event_type=event.type,
                    occurred_at=event.timestamp,
                    event=mission_json_event_store.serialize([event])[0],
                )
                for event in events
            ]
        )

    async def list_after(
        self,
        mission_id: UUID,
        after_sequence: int,
        fetch_limit: int,
    ) -> list[ExecutionEvent]:
        result = await self._session.execute(
            select(MissionEventModel)
            .where(MissionEventModel.mission_id == mission_id)
            .where(MissionEventModel.sequence > after_sequence)
            .order_by(MissionEventModel.sequence.asc())
            .limit(fetch_limit)
        )
        return [
            mission_json_event_store.deserialize(
                [model.event],
                last_event_sequence=model.sequence,
                mission_id=mission_id,
            )[0]
            for model in result.scalars().all()
        ]

    async def list_all(self, mission_id: UUID) -> list[ExecutionEvent]:
        return await self.list_after(mission_id, 0, 2**31 - 1)
