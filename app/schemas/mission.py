from uuid import UUID, uuid4

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)


class MissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: MissionType
    title: str
    participant_ids: list[UUID] = Field(min_length=1)
    provider: str = Field(min_length=1)
    constraints: TrainConstraints
    fallback_rules: FallbackRules = Field(default_factory=FallbackRules)

    @model_validator(mode="after")
    def validate_participants(self) -> Self:
        if len(set(self.participant_ids)) != len(self.participant_ids):
            msg = "participant_ids must be unique"
            raise ValueError(msg)

        if self.constraints.passengers_count != len(self.participant_ids):
            msg = "passengers_count must match participant_ids count"
            raise ValueError(msg)

        return self

    def to_domain(self) -> Mission:
        return Mission(
            id=uuid4(),
            type=self.type,
            title=self.title,
            status=MissionStatus.created,
            participant_ids=self.participant_ids,
            provider=self.provider,
            constraints=self.constraints,
            fallback_rules=self.fallback_rules,
            execution_log=[],
            best_option=None,
        )
