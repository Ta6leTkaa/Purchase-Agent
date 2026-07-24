from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission import MissionModel, mission_from_model
from app.db.models.provider_history import MissionProviderHistoryEventModel
from app.repositories.sqlalchemy.provider_history import (
    SqlAlchemyProviderHistoryProjectionRepository,
)
from app.services.provider_history_projection import (
    execution_event_to_provider_projection,
)

REBUILD_BATCH_SIZE = 500


@dataclass(frozen=True)
class ProviderHistoryProjectionRebuildResult:
    processed_missions: int
    processed_provider_events: int
    inserted_rows: int


class RebuildProviderHistoryProjection:
    async def execute(
        self,
        session: AsyncSession,
    ) -> ProviderHistoryProjectionRebuildResult:
        await session.execute(delete(MissionProviderHistoryEventModel))
        writer = SqlAlchemyProviderHistoryProjectionRepository(session)
        processed_missions = 0
        processed_provider_events = 0
        pending = []
        stream = await session.stream_scalars(
            select(MissionModel).order_by(MissionModel.id)
        )
        async for model in stream:
            mission = mission_from_model(model)
            processed_missions += 1
            for event_index, event in enumerate(mission.execution_log):
                projection = execution_event_to_provider_projection(
                    mission_id=mission.id,
                    event=event,
                    legacy_event_index=event_index,
                )
                if projection is not None:
                    pending.append(projection)
                    processed_provider_events += 1
            if len(pending) >= REBUILD_BATCH_SIZE:
                await writer.append_many(pending)
                await session.flush()
                pending.clear()
        if pending:
            await writer.append_many(pending)
            await session.flush()
        return ProviderHistoryProjectionRebuildResult(
            processed_missions=processed_missions,
            processed_provider_events=processed_provider_events,
            inserted_rows=processed_provider_events,
        )
