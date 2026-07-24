from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.provider_history import MissionProviderHistoryEventModel
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolvedEventPayload,
    ProviderSelectionChangedEventPayload,
)
from app.services.provider_history_projection import (
    ProviderHistoryProjectionEvent,
)
from app.services.provider_resolution_history import (
    ProviderHistoryCursor,
    ProviderHistoryEventType,
)


class SqlAlchemyProviderHistoryProjectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_many(
        self,
        events: list[ProviderHistoryProjectionEvent],
    ) -> None:
        self._session.add_all(
            [
                MissionProviderHistoryEventModel(
                    mission_id=event.mission_id,
                    sequence=event.sequence,
                    event_type=event.event_type.value,
                    occurred_at=event.occurred_at,
                    payload=event.payload.model_dump(mode="json"),
                    legacy_event_index=event.legacy_event_index,
                )
                for event in events
            ]
        )

    async def list_since(
        self,
        *,
        mission_id: UUID,
        since_sequence: int,
        fetch_limit: int,
    ) -> list[ProviderHistoryProjectionEvent]:
        result = await self._session.execute(
            select(MissionProviderHistoryEventModel)
            .where(MissionProviderHistoryEventModel.mission_id == mission_id)
            .where(MissionProviderHistoryEventModel.sequence > since_sequence)
            .order_by(MissionProviderHistoryEventModel.sequence.asc())
            .limit(fetch_limit)
        )
        return [
            projection_from_model(model)
            for model in result.scalars().all()
        ]

    async def list_page(
        self,
        *,
        mission_id: UUID,
        cursor: ProviderHistoryCursor | None,
        fetch_limit: int,
    ) -> list[ProviderHistoryProjectionEvent]:
        statement = select(MissionProviderHistoryEventModel).where(
            MissionProviderHistoryEventModel.mission_id == mission_id
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    MissionProviderHistoryEventModel.occurred_at
                    > cursor.occurred_at,
                    and_(
                        MissionProviderHistoryEventModel.occurred_at
                        == cursor.occurred_at,
                        MissionProviderHistoryEventModel.legacy_event_index
                        > cursor.event_index,
                    ),
                )
            )
        result = await self._session.execute(
            statement.order_by(
                MissionProviderHistoryEventModel.occurred_at.asc(),
                MissionProviderHistoryEventModel.legacy_event_index.asc(),
            ).limit(fetch_limit)
        )
        return [
            projection_from_model(model)
            for model in result.scalars().all()
        ]


def projection_from_model(
    model: MissionProviderHistoryEventModel,
) -> ProviderHistoryProjectionEvent:
    event_type = ProviderHistoryEventType(model.event_type)
    payload_type = {
        ProviderHistoryEventType.provider_selection_changed: (
            ProviderSelectionChangedEventPayload
        ),
        ProviderHistoryEventType.provider_resolved: ProviderResolvedEventPayload,
        ProviderHistoryEventType.provider_resolution_failed: (
            ProviderResolutionFailedEventPayload
        ),
    }[event_type]
    payload_model = payload_type.model_validate(model.payload)
    return ProviderHistoryProjectionEvent(
        mission_id=model.mission_id,
        sequence=model.sequence,
        event_type=event_type,
        occurred_at=model.occurred_at,
        payload=payload_model,
        legacy_event_index=model.legacy_event_index,
    )
