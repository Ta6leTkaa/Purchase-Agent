from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.identity import GUID, preferences_type
from app.domain.task import AgentTask


class AgentTaskModel(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    person_ids: Mapped[list[str]] = mapped_column(preferences_type, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    inferred_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    permissions: Mapped[dict[str, Any]] = mapped_column(
        preferences_type, nullable=False
    )
    plan: Mapped[dict[str, Any] | None] = mapped_column(preferences_type, nullable=True)
    journal: Mapped[dict[str, Any] | None] = mapped_column(
        preferences_type, nullable=True
    )
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(
        preferences_type,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    page_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        preferences_type,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def agent_task_to_model(task: AgentTask) -> AgentTaskModel:
    data = task.model_dump(mode="json")
    return AgentTaskModel(
        id=task.id,
        version=task.version,
        instruction=task.instruction,
        target_url=task.target_url,
        person_ids=data["person_ids"],
        status=task.status.value,
        inferred_kind=task.inferred_kind,
        waiting_reason=task.waiting_reason.value if task.waiting_reason else None,
        permissions=data["permissions"],
        plan=data["plan"],
        journal=data["journal"],
        approvals=data["approvals"],
        page_snapshot=data["page_snapshot"],
        created_at=task.created_at,
    )


def agent_task_from_model(model: AgentTaskModel) -> AgentTask:
    return AgentTask.model_validate(
        {
            "id": model.id,
            "version": model.version,
            "instruction": model.instruction,
            "target_url": model.target_url,
            "person_ids": model.person_ids,
            "status": model.status,
            "inferred_kind": model.inferred_kind,
            "waiting_reason": model.waiting_reason,
            "permissions": model.permissions,
            "plan": model.plan,
            "journal": model.journal,
            "approvals": model.approvals,
            "page_snapshot": model.page_snapshot,
            "created_at": model.created_at,
        }
    )
