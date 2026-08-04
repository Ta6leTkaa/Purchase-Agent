from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.worker_heartbeat import (
    WorkerHeartbeatModel,
    worker_heartbeat_from_model,
)
from app.domain.worker_health import WorkerHeartbeat, WorkerKind


class SqlAlchemyWorkerHeartbeatRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        worker_kind: WorkerKind,
        instance_id: str,
        current_time: datetime,
        success: bool,
    ) -> WorkerHeartbeat:
        key = {"worker_kind": worker_kind.value, "instance_id": instance_id}
        model = await self._session.get(WorkerHeartbeatModel, key)
        if model is None:
            model = WorkerHeartbeatModel(
                **key,
                started_at=current_time,
                heartbeat_at=current_time,
                last_success_at=current_time if success else None,
                consecutive_failures=0 if success else 1,
            )
            self._session.add(model)
        else:
            model.heartbeat_at = current_time
            if success:
                model.last_success_at = current_time
                model.consecutive_failures = 0
            else:
                model.consecutive_failures += 1
        await self._session.flush()
        return worker_heartbeat_from_model(model)

    async def get(
        self,
        worker_kind: WorkerKind,
        instance_id: str,
    ) -> WorkerHeartbeat | None:
        model = await self._session.get(
            WorkerHeartbeatModel,
            {"worker_kind": worker_kind.value, "instance_id": instance_id},
        )
        return worker_heartbeat_from_model(model) if model is not None else None

    async def list_all(self) -> list[WorkerHeartbeat]:
        result = await self._session.execute(
            select(WorkerHeartbeatModel).order_by(
                WorkerHeartbeatModel.worker_kind,
                WorkerHeartbeatModel.instance_id,
            )
        )
        return [
            worker_heartbeat_from_model(model) for model in result.scalars().all()
        ]
