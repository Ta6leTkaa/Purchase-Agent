from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.sqlalchemy.resource_creation_idempotency import (
    SqlAlchemyResourceCreationIdempotencyStore,
)
from app.services.resource_creation_idempotency import (
    ResourceCreationConflictError,
    ResourceCreationInProgressError,
    ResourceCreationScope,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_creation_receipt_replays_completed_resource(
    test_session: AsyncSession,
) -> None:
    store = SqlAlchemyResourceCreationIdempotencyStore(test_session)
    resource_id = uuid4()

    first = await store.begin(
        scope=ResourceCreationScope.IDENTITY,
        key="identity-create",
        fingerprint="a" * 64,
    )
    await store.complete(
        scope=ResourceCreationScope.IDENTITY,
        key="identity-create",
        resource_id=resource_id,
    )
    replay = await store.begin(
        scope=ResourceCreationScope.IDENTITY,
        key="identity-create",
        fingerprint="a" * 64,
    )

    assert first is None
    assert replay == resource_id


async def test_creation_receipt_rejects_changed_fingerprint(
    test_session: AsyncSession,
) -> None:
    store = SqlAlchemyResourceCreationIdempotencyStore(test_session)
    await store.begin(
        scope=ResourceCreationScope.MISSION,
        key="mission-create",
        fingerprint="a" * 64,
    )

    with pytest.raises(ResourceCreationConflictError):
        await store.begin(
            scope=ResourceCreationScope.MISSION,
            key="mission-create",
            fingerprint="b" * 64,
        )


async def test_creation_receipt_reports_in_progress_request(
    test_session: AsyncSession,
) -> None:
    store = SqlAlchemyResourceCreationIdempotencyStore(test_session)
    await store.begin(
        scope=ResourceCreationScope.MISSION,
        key="pending-create",
        fingerprint="a" * 64,
    )

    with pytest.raises(ResourceCreationInProgressError):
        await store.begin(
            scope=ResourceCreationScope.MISSION,
            key="pending-create",
            fingerprint="a" * 64,
        )


async def test_creation_receipt_scopes_are_independent(
    test_session: AsyncSession,
) -> None:
    store = SqlAlchemyResourceCreationIdempotencyStore(test_session)

    identity_result = await store.begin(
        scope=ResourceCreationScope.IDENTITY,
        key="shared-key",
        fingerprint="a" * 64,
    )
    mission_result = await store.begin(
        scope=ResourceCreationScope.MISSION,
        key="shared-key",
        fingerprint="b" * 64,
    )

    assert identity_result is None
    assert mission_result is None
