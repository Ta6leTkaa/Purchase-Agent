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


class AgentDecisionMetadata(BaseModel):
    """Operational decision data without prompts or hidden model reasoning."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=32)
    model: str = Field(min_length=1, max_length=100)
    duration_ms: int = Field(ge=0, le=600_000)
    fallback_used: bool = False
    attempted_models: tuple[str, ...] = Field(default=(), max_length=3)


class AgentLoopStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: int = Field(ge=1)
    decision: AgentDecision
    decision_metadata: AgentDecisionMetadata | None = None
    result: CommandExecutionResult


class AgentLoopResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AgentLoopStatus
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    page_snapshot: BrowserPageSnapshot
    steps: tuple[AgentLoopStep, ...]


def merge_agent_loop_results(
    previous: AgentLoopResult | None,
    current: AgentLoopResult,
    *,
    max_steps: int = 50,
) -> AgentLoopResult:
    """Keep a bounded action history while retaining the latest run outcome."""
    if max_steps < 1:
        raise ValueError("max_steps must be at least one")
    previous_steps = previous.steps if previous is not None else ()
    retained = (*previous_steps, *current.steps)[-max_steps:]
    steps = tuple(
        step.model_copy(update={"sequence": sequence})
        for sequence, step in enumerate(retained, start=1)
    )
    return current.model_copy(update={"steps": steps})
