from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission import (
    MissionModel,
    mission_from_model,
    mission_to_model,
)
from app.domain.mission import Mission
from app.repositories.mission import (
    MissionRepository,
    RepositoryEntityNotFoundError,
)


class SqlAlchemyMissionRepository(MissionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, mission: Mission) -> Mission:
        model = mission_to_model(mission)
        self._session.add(model)
        await self._session.flush()
        return mission_from_model(model)

    async def list(self) -> list[Mission]:
        result = await self._session.execute(
            select(MissionModel).order_by(MissionModel.created_at)
        )
        return [
            mission_from_model(model)
            for model in result.scalars().all()
        ]

    async def get(self, mission_id: UUID) -> Mission | None:
        model = await self._session.get(MissionModel, mission_id)
        if model is None:
            return None
        return mission_from_model(model)

    async def update(self, mission: Mission) -> Mission:
        model = await self._session.get(MissionModel, mission.id)
        if model is None:
            raise RepositoryEntityNotFoundError

        updated_model = mission_to_model(mission)
        model.type = updated_model.type
        model.title = updated_model.title
        model.status = updated_model.status
        model.provider = updated_model.provider
        model.scheduled_at = updated_model.scheduled_at
        model.participant_ids = updated_model.participant_ids
        model.constraints = updated_model.constraints
        model.fallback_rules = updated_model.fallback_rules
        model.execution_log = updated_model.execution_log
        model.best_option = updated_model.best_option

        await self._session.flush()
        return mission_from_model(model)

    async def clear(self) -> None:
        await self._session.execute(delete(MissionModel))
        await self._session.flush()


def get_sqlalchemy_mission_repository(
    session: AsyncSession,
) -> SqlAlchemyMissionRepository:
    return SqlAlchemyMissionRepository(session)
