from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.resource_creation_receipt import ResourceCreationReceiptModel
from app.services.resource_creation_idempotency import (
    ResourceCreationConflictError,
    ResourceCreationInProgressError,
    ResourceCreationReceiptKey,
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

    async def prune_completed_before(
        self,
        cutoff: datetime,
        limit: int,
        *,
        dry_run: bool = False,
    ) -> list[ResourceCreationReceiptKey]:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        candidates = (
            select(
                ResourceCreationReceiptModel.scope,
                ResourceCreationReceiptModel.idempotency_key,
            )
            .where(
                ResourceCreationReceiptModel.resource_id.is_not(None),
                ResourceCreationReceiptModel.created_at < cutoff,
            )
            .order_by(
                ResourceCreationReceiptModel.created_at,
                ResourceCreationReceiptModel.scope,
                ResourceCreationReceiptModel.idempotency_key,
            )
            .limit(limit)
        )
        if dry_run:
            result = await self._session.execute(candidates)
        else:
            candidate_rows = candidates.cte("creation_receipt_prune_candidates")
            result = await self._session.execute(
                delete(ResourceCreationReceiptModel)
                .where(
                    tuple_(
                        ResourceCreationReceiptModel.scope,
                        ResourceCreationReceiptModel.idempotency_key,
                    ).in_(
                        select(
                            candidate_rows.c.scope,
                            candidate_rows.c.idempotency_key,
                        )
                    )
                )
                .returning(
                    ResourceCreationReceiptModel.scope,
                    ResourceCreationReceiptModel.idempotency_key,
                )
            )
        return [
            ResourceCreationReceiptKey(
                scope=ResourceCreationScope(scope),
                idempotency_key=idempotency_key,
            )
            for scope, idempotency_key in result.all()
        ]
