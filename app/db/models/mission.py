from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db.base import Base
from app.db.models.identity import GUID, preferences_type
from app.domain.execution import ExecutionEvent
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import ProviderOption


class AwareDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Any,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class MissionModel(Base):
    __tablename__ = "missions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(),
        nullable=True,
    )
    participant_ids: Mapped[list[str]] = mapped_column(
        preferences_type,
        nullable=False,
    )
    constraints: Mapped[dict[str, Any]] = mapped_column(
        preferences_type,
        nullable=False,
    )
    fallback_rules: Mapped[dict[str, Any]] = mapped_column(
        preferences_type,
        nullable=False,
    )
    execution_log: Mapped[list[dict[str, Any]]] = mapped_column(
        preferences_type,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    best_option: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(preferences_type, "sqlite"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


def mission_to_model(mission: Mission) -> MissionModel:
    return MissionModel(
        id=mission.id,
        type=mission.type.value,
        title=mission.title,
        status=mission.status.value,
        provider=mission.provider,
        scheduled_at=mission.scheduled_at,
        claimed_at=mission.claimed_at,
        participant_ids=[
            str(participant_id)
            for participant_id in mission.participant_ids
        ],
        constraints=mission.constraints.model_dump(mode="json"),
        fallback_rules=mission.fallback_rules.model_dump(mode="json"),
        execution_log=[
            event.model_dump(mode="json")
            for event in mission.execution_log
        ],
        best_option=(
            mission.best_option.model_dump(mode="json")
            if mission.best_option is not None
            else None
        ),
    )


def mission_from_model(model: MissionModel) -> Mission:
    mission_data = {
        "id": model.id,
        "type": MissionType(model.type),
        "title": model.title,
        "status": MissionStatus(model.status),
        "participant_ids": [
            UUID(str(participant_id))
            for participant_id in model.participant_ids
        ],
        "provider": model.provider,
        "scheduled_at": model.scheduled_at,
        "claimed_at": model.claimed_at,
        "constraints": TrainConstraints.model_validate(model.constraints),
        "fallback_rules": FallbackRules.model_validate(model.fallback_rules),
        "execution_log": [
            ExecutionEvent.model_validate(event)
            for event in model.execution_log
        ],
        "best_option": (
            ProviderOption.model_validate(model.best_option)
            if model.best_option is not None
            else None
        ),
    }

    if (
        mission_data["status"] is MissionStatus.processing
        and mission_data["claimed_at"] is None
    ):
        return Mission.model_construct(**mission_data)

    return Mission(**mission_data)
