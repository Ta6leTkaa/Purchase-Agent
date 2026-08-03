import builtins
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.identity import (
    DocumentModel,
    IdentityModel,
    identity_from_model,
    identity_to_model,
)
from app.domain.identity import Identity, IdentitySummary, Preferences
from app.repositories.identity import (
    IdentityRepository,
    IdentityVersionConflictError,
)
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
            .execution_options(populate_existing=True)
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
        expected_version: int,
    ) -> Identity | None:
        result = await self._session.execute(
            select(IdentityModel)
            .where(IdentityModel.id == identity_id)
            .options(selectinload(IdentityModel.documents))
            .execution_options(populate_existing=True)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        if model.version != expected_version:
            raise IdentityVersionConflictError
        version_result = await self._session.execute(
            update(IdentityModel)
            .where(IdentityModel.id == identity_id)
            .where(IdentityModel.version == expected_version)
            .values(
                preferences=preferences.model_dump(mode="json"),
                version=expected_version + 1,
            )
            .returning(IdentityModel.version)
        )
        new_version = version_result.scalar_one_or_none()
        if new_version is None:
            raise IdentityVersionConflictError
        model.preferences = preferences.model_dump(mode="json")
        model.version = new_version
        await self._session.flush()
        return identity_from_model(model)

    async def update(
        self,
        identity: Identity,
        expected_version: int,
    ) -> Identity | None:
        result = await self._session.execute(
            update(IdentityModel)
            .where(IdentityModel.id == identity.id)
            .where(IdentityModel.version == expected_version)
            .values(
                display_name=identity.display_name,
                first_name=identity.first_name,
                last_name=identity.last_name,
                birth_date=identity.birth_date,
                version=expected_version + 1,
            )
            .returning(IdentityModel.id)
        )
        if result.scalar_one_or_none() is None:
            if await self.get(identity.id) is None:
                return None
            raise IdentityVersionConflictError
        await self._session.execute(
            delete(DocumentModel).where(
                DocumentModel.identity_id == identity.id
            )
        )
        self._session.add_all([
            DocumentModel(
                id=document.id,
                identity_id=identity.id,
                type=document.type.value,
                number=document.number,
                expires_at=document.expires_at,
            )
            for document in identity.documents
        ])
        await self._session.flush()
        return identity.model_copy(update={"version": expected_version + 1})

    async def delete(self, identity_id: UUID, expected_version: int) -> bool:
        result = await self._session.execute(
            delete(IdentityModel)
            .where(IdentityModel.id == identity_id)
            .where(IdentityModel.version == expected_version)
            .returning(IdentityModel.id)
        )
        await self._session.flush()
        if result.scalar_one_or_none() is not None:
            return True
        if await self.get(identity_id) is None:
            return False
        raise IdentityVersionConflictError

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
