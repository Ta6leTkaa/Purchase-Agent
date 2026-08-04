import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.cli import CliDependencies, worker_health_command
from app.db.base import Base
from app.domain.worker_health import WorkerKind
from app.repositories.sqlalchemy.worker_heartbeat import (
    SqlAlchemyWorkerHeartbeatRepository,
)


@pytest.mark.asyncio
async def test_worker_health_command_reports_fresh_heartbeat() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, class_=AsyncSession)
        async with sessions() as session:
            await SqlAlchemyWorkerHeartbeatRepository(session).record(
                worker_kind=WorkerKind.MISSION,
                instance_id="worker-1",
                current_time=now,
                success=True,
            )
            await session.commit()
        output = io.StringIO()
        exit_code = await worker_health_command(
            WorkerKind.MISSION,
            "worker-1",
            timedelta(seconds=60),
            dependencies=CliDependencies(
                session_maker=sessions,
                clock=lambda: now + timedelta(seconds=10),
                worker_heartbeat_repository_factory=(
                    SqlAlchemyWorkerHeartbeatRepository
                ),
            ),
            stdout=output,
        )
    finally:
        await engine.dispose()

    assert exit_code == 0
    assert json.loads(output.getvalue())["healthy"] is True


@pytest.mark.asyncio
async def test_worker_health_command_rejects_missing_storage() -> None:
    stderr = io.StringIO()
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        exit_code = await worker_health_command(
            WorkerKind.MISSION,
            "missing",
            timedelta(seconds=60),
            dependencies=CliDependencies(
                session_maker=async_sessionmaker(engine, class_=AsyncSession),
            ),
            stderr=stderr,
        )
    finally:
        await engine.dispose()

    assert exit_code == 2
    assert "unavailable" in stderr.getvalue()
