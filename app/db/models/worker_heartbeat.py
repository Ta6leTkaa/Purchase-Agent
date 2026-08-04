from datetime import datetime

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mission import AwareDateTime
from app.domain.worker_health import WorkerHeartbeat, WorkerKind


class WorkerHeartbeatModel(Base):
    __tablename__ = "worker_heartbeats"

    worker_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(AwareDateTime())
    heartbeat_at: Mapped[datetime] = mapped_column(AwareDateTime(), index=True)
    last_success_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


def worker_heartbeat_from_model(model: WorkerHeartbeatModel) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_kind=WorkerKind(model.worker_kind),
        instance_id=model.instance_id,
        started_at=model.started_at,
        heartbeat_at=model.heartbeat_at,
        last_success_at=model.last_success_at,
        consecutive_failures=model.consecutive_failures,
    )
