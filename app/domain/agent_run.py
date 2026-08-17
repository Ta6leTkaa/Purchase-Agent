from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.browser_command import AgentDecision, CommandExecutionResult
from app.domain.browser_page import BrowserPageSnapshot


class AgentLoopStatus(StrEnum):
    COMPLETED = "completed"
    WAITING_FOR_USER = "waiting_for_user"
    NO_MATCH = "no_match"
    EXHAUSTED = "exhausted"
    STALLED = "stalled"


class AgentLoopStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=1)
    decision: AgentDecision
    result: CommandExecutionResult


class AgentLoopResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AgentLoopStatus
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    page_snapshot: BrowserPageSnapshot
    steps: tuple[AgentLoopStep, ...]
