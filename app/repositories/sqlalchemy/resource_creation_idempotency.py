from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.resource_creation_receipt import ResourceCreationReceiptModel
from app.services.resource_creation_idempotency import (
    ResourceCreationConflictError,
    ResourceCreationInProgressError,
    ResourceCreationScope,
)


class SqlAlchemyResourceCreationIdempotencyStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        fingerprint: str,
    ) -> UUID | None:
        inserted = await self._session.execute(
            insert(ResourceCreationReceiptModel)
            .values(
                scope=scope.value,
                idempotency_key=key,
                request_fingerprint=fingerprint,
            )
            .on_conflict_do_nothing(
                index_elements=["scope", "idempotency_key"]
            )
            .returning(ResourceCreationReceiptModel.idempotency_key)
        )
        if inserted.scalar_one_or_none() is not None:
            return None
        receipt = await self._session.scalar(
            select(ResourceCreationReceiptModel).where(
                ResourceCreationReceiptModel.scope == scope.value,
                ResourceCreationReceiptModel.idempotency_key == key,
            )
        )
        assert receipt is not None
        if receipt.request_fingerprint != fingerprint:
            raise ResourceCreationConflictError
        if receipt.resource_id is None:
            raise ResourceCreationInProgressError
        return receipt.resource_id

    async def complete(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        resource_id: UUID,
    ) -> None:
        await self._session.execute(
            update(ResourceCreationReceiptModel)
            .where(
                ResourceCreationReceiptModel.scope == scope.value,
                ResourceCreationReceiptModel.idempotency_key == key,
            )
            .values(resource_id=resource_id)
        )

    async def abort(
        self,
        *,
        scope: ResourceCreationScope,
        key: str,
        fingerprint: str,
    ) -> None:
        await self._session.execute(
            delete(ResourceCreationReceiptModel).where(
                ResourceCreationReceiptModel.scope == scope.value,
                ResourceCreationReceiptModel.idempotency_key == key,
                ResourceCreationReceiptModel.request_fingerprint == fingerprint,
                ResourceCreationReceiptModel.resource_id.is_(None),
            )
        )
