from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.domain.worker_health import WorkerKind
from app.repositories.sqlalchemy.worker_heartbeat import (
    SqlAlchemyWorkerHeartbeatRepository,
)


@pytest.mark.asyncio
async def test_records_success_and_consecutive_failures() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, class_=AsyncSession)
        now = datetime(2026, 8, 4, 12, tzinfo=UTC)
        async with sessions() as session:
            repository = SqlAlchemyWorkerHeartbeatRepository(session)
            first = await repository.record(
                worker_kind=WorkerKind.MISSION,
                instance_id="worker-1",
                current_time=now,
                success=True,
            )
            await session.commit()
        async with sessions() as session:
            repository = SqlAlchemyWorkerHeartbeatRepository(session)
            failed = await repository.record(
                worker_kind=WorkerKind.MISSION,
                instance_id="worker-1",
                current_time=now + timedelta(seconds=5),
                success=False,
            )
            await repository.record(
                worker_kind=WorkerKind.NOTIFICATION,
                instance_id="worker-2",
                current_time=now,
                success=True,
            )
            await session.commit()
        async with sessions() as session:
            heartbeats = await SqlAlchemyWorkerHeartbeatRepository(session).list_all()

        assert first.last_success_at == now
        assert failed.consecutive_failures == 1
        assert failed.last_success_at == now
        assert [item.worker_kind for item in heartbeats] == [
            WorkerKind.MISSION,
            WorkerKind.NOTIFICATION,
        ]
    finally:
        await engine.dispose()
