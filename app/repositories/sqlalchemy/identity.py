import builtins
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.identity import (
    DocumentModel,
    IdentityModel,
    identity_from_model,
    identity_to_model,
)
from app.domain.identity import Identity, IdentitySummary, Preferences
from app.repositories.identity import IdentityRepository
from app.services.identity_pagination import IdentityCursor


class SqlAlchemyIdentityRepository(IdentityRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, identity: Identity) -> Identity:
        model = identity_to_model(identity)
        self._session.add(model)
        await self._session.flush()
        return identity_from_model(model)

    async def list(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> builtins.list[Identity]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        statement = select(IdentityModel).options(
            selectinload(IdentityModel.documents)
        )
        pattern = _identity_search_pattern(query)
        if pattern is not None:
            statement = statement.where(
                or_(
                    IdentityModel.display_name.ilike(pattern, escape="\\"),
                    IdentityModel.first_name.ilike(pattern, escape="\\"),
                    IdentityModel.last_name.ilike(pattern, escape="\\"),
                )
            )
        result = await self._session.execute(
            statement.order_by(
                IdentityModel.created_at,
                IdentityModel.id,
            ).limit(limit)
        )
        return [
            identity_from_model(model)
            for model in result.scalars().unique().all()
        ]

    async def list_summaries(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> builtins.list[IdentitySummary]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        statement = select(IdentityModel.id, IdentityModel.display_name)
        pattern = _identity_search_pattern(query)
        if pattern is not None:
            statement = statement.where(
                or_(
                    IdentityModel.display_name.ilike(pattern, escape="\\"),
                    IdentityModel.first_name.ilike(pattern, escape="\\"),
                    IdentityModel.last_name.ilike(pattern, escape="\\"),
                )
            )
        result = await self._session.execute(
            statement.order_by(
                IdentityModel.created_at,
                IdentityModel.id,
            ).limit(limit)
        )
        return [
            IdentitySummary(id=identity_id, display_name=display_name)
            for identity_id, display_name in result.all()
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

    async def list_summary_page_candidates(
        self,
        *,
        query: str | None = None,
        cursor: IdentityCursor | None = None,
        limit: int = 101,
    ) -> builtins.list[IdentitySummary]:
        if limit <= 0:
            raise ValueError("limit must be greater than 0")
        statement = select(IdentityModel.id, IdentityModel.display_name)
        pattern = _identity_search_pattern(query)
        if pattern is not None:
            statement = statement.where(
                or_(
                    IdentityModel.display_name.ilike(pattern, escape="\\"),
                    IdentityModel.first_name.ilike(pattern, escape="\\"),
                    IdentityModel.last_name.ilike(pattern, escape="\\"),
                )
            )
        if cursor is not None:
            statement = statement.where(IdentityModel.id > cursor.identity_id)
        result = await self._session.execute(
            statement.order_by(IdentityModel.id).limit(limit)
        )
        return [
            IdentitySummary(id=identity_id, display_name=display_name)
            for identity_id, display_name in result.all()
        ]

    async def update_preferences(
        self,
        identity_id: UUID,
        preferences: Preferences,
    ) -> Identity | None:
        result = await self._session.execute(
            select(IdentityModel)
            .where(IdentityModel.id == identity_id)
            .options(selectinload(IdentityModel.documents))
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        model.preferences = preferences.model_dump(mode="json")
        await self._session.flush()
        return identity_from_model(model)

    async def delete(self, identity_id: UUID) -> bool:
        await self._session.execute(
            delete(DocumentModel).where(
                DocumentModel.identity_id == identity_id
            )
        )
        result = await self._session.execute(
            delete(IdentityModel)
            .where(IdentityModel.id == identity_id)
            .returning(IdentityModel.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def clear(self) -> None:
        await self._session.execute(delete(DocumentModel))
        await self._session.execute(delete(IdentityModel))
        await self._session.flush()


def get_sqlalchemy_identity_repository(
    session: AsyncSession,
) -> SqlAlchemyIdentityRepository:
    return SqlAlchemyIdentityRepository(session)


def _identity_search_pattern(query: str | None) -> str | None:
    if query is None:
        return None
    normalized = query.strip()
    if not normalized:
        raise ValueError("query must not be blank")
    escaped = (
        normalized.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"
