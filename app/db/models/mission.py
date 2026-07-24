from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.db.base import Base
from app.db.models.identity import GUID, preferences_type
from app.domain.execution import ExecutionEvent, validate_event_sequence
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainTicketMissionPayload,
    TrainConstraints,
)
from app.domain.provider import ProviderOption
from app.services.mission_event_store import mission_json_event_store


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
    mission_type: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=MissionType.TRAIN_TICKET.value,
        server_default=text("'train_ticket'"),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(preferences_type, "sqlite"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolved_provider_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(),
        nullable=True,
    )
    execution_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_execution_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    last_event_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
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
    validate_event_sequence(
        mission.execution_log,
        last_event_sequence=mission.last_event_sequence,
    )
    return MissionModel(
        id=mission.id,
        type="train_trip",
        mission_type=mission.mission_type.value,
        payload=mission.payload.model_dump(mode="json"),
        title=mission.title,
        status=mission.status.value,
        provider=mission.provider,
        provider_id=mission.provider_id,
        resolved_provider_id=mission.resolved_provider_id,
        scheduled_at=mission.scheduled_at,
        claimed_at=mission.claimed_at,
        execution_attempts=mission.execution_attempts,
        max_execution_attempts=mission.max_execution_attempts,
        last_event_sequence=mission.last_event_sequence,
        participant_ids=[
            str(participant_id)
            for participant_id in mission.participant_ids
        ],
        constraints=mission.constraints.model_dump(mode="json"),
        fallback_rules=mission.fallback_rules.model_dump(mode="json"),
        execution_log=mission_json_event_store.serialize(mission.execution_log),
        best_option=(
            mission.best_option.model_dump(mode="json")
            if mission.best_option is not None
            else None
        ),
    )


def mission_from_model(model: MissionModel) -> Mission:
    execution_attempts = getattr(model, "execution_attempts", 0) or 0
    max_execution_attempts = getattr(model, "max_execution_attempts", 3) or 3
    last_event_sequence = getattr(model, "last_event_sequence", 0) or 0
    execution_log = mission_json_event_store.deserialize(
        model.execution_log,
        last_event_sequence=last_event_sequence,
    )
    mission_data = {
        "id": model.id,
        "type": MissionType.TRAIN_TICKET,
        "mission_type": MissionType(
            getattr(model, "mission_type", MissionType.TRAIN_TICKET.value)
            or MissionType.TRAIN_TICKET.value
        ),
        "payload": TrainTicketMissionPayload.model_validate(model.payload),
        "title": model.title,
        "status": MissionStatus(model.status),
        "participant_ids": [
            UUID(str(participant_id))
            for participant_id in model.participant_ids
        ],
        "provider": model.provider,
        "provider_id": getattr(model, "provider_id", None),
        "resolved_provider_id": getattr(model, "resolved_provider_id", None),
        "scheduled_at": model.scheduled_at,
        "claimed_at": model.claimed_at,
        "execution_attempts": execution_attempts,
        "max_execution_attempts": max(
            max_execution_attempts,
            execution_attempts,
        ),
        "last_event_sequence": last_event_sequence,
        "constraints": TrainConstraints.model_validate(model.constraints),
        "fallback_rules": FallbackRules.model_validate(model.fallback_rules),
        "execution_log": execution_log,
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
        mission = Mission.model_construct(**mission_data)
        mission.mark_event_sequence_persisted()
        return mission

    return Mission(**mission_data)
