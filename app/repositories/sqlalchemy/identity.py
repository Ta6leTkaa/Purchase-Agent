from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.identity import (
    DocumentModel,
    IdentityModel,
    identity_from_model,
    identity_to_model,
)
from app.domain.identity import Identity
from app.repositories.identity import IdentityRepository


class SqlAlchemyIdentityRepository(IdentityRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, identity: Identity) -> Identity:
        model = identity_to_model(identity)
        self._session.add(model)
        await self._session.flush()
        return identity_from_model(model)

    async def list(self) -> list[Identity]:
        result = await self._session.execute(
            select(IdentityModel).options(selectinload(IdentityModel.documents))
        )
        return [
            identity_from_model(model)
            for model in result.scalars().unique().all()
        ]

    async def get(self, identity_id: UUID) -> Identity | None:
        result = await self._session.execute(
            select(IdentityModel)
            .where(IdentityModel.id == identity_id)
            .options(selectinload(IdentityModel.documents))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return identity_from_model(model)

    async def clear(self) -> None:
        await self._session.execute(delete(DocumentModel))
        await self._session.execute(delete(IdentityModel))
        await self._session.flush()


def get_sqlalchemy_identity_repository(
    session: AsyncSession,
) -> SqlAlchemyIdentityRepository:
    return SqlAlchemyIdentityRepository(session)
