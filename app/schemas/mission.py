from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

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
