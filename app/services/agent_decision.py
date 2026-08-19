from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.browser_command import AgentDecision
from app.domain.browser_page import BrowserControlKind
from app.domain.task import AgentTask


class AgentVisibleControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str
    frame_index: int = Field(default=0, ge=0, le=9)
    frame_url: str | None = Field(default=None, max_length=2_048)
    kind: BrowserControlKind
    label: str
    role: str | None = None
    nearby_text: str | None = Field(default=None, max_length=600)
    disabled: bool = False
    checked: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
    pressed: bool | None = None
    options: tuple[str, ...] = ()


class AgentActionObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1, max_length=32)
    target: str | None = Field(default=None, max_length=200)
    result: str = Field(min_length=1, max_length=100)


class AgentClarification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2_000)


class AgentDecisionContext(BaseModel):
    """Bounded, value-free page context safe to send to a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    goal: str
    current_url: str
    page_title: str
    visible_text: str = Field(default="", max_length=12_000)
    intent: dict[str, object]
    controls: tuple[AgentVisibleControl, ...] = Field(max_length=200)
    previous_actions: tuple[AgentActionObservation, ...] = Field(
        default=(),
        max_length=20,
    )
    clarifications: tuple[AgentClarification, ...] = Field(
        default=(), max_length=20
    )
    screenshot_data_url: str | None = Field(default=None, exclude=True)


class AgentDecisionProvider(Protocol):
    async def decide(self, context: AgentDecisionContext) -> AgentDecision: ...


def build_agent_decision_context(
    task: AgentTask,
    *,
    previous_actions: tuple[AgentActionObservation, ...] = (),
    screenshot_data_url: str | None = None,
) -> AgentDecisionContext:
    if task.page_snapshot is None:
        raise ValueError("task page snapshot is required for an agent decision")
    snapshot = task.page_snapshot
    intent = (
        task.intent.model_dump(mode="json", exclude_none=True)
        if task.intent is not None
        else {}
    )
    return AgentDecisionContext(
        task_id=str(task.id),
        goal=task.instruction,
        current_url=snapshot.url,
        page_title=snapshot.title,
        visible_text=snapshot.visible_text,
        intent=intent,
        controls=tuple(
            AgentVisibleControl(
                control_id=control.control_id,
                frame_index=control.frame_index,
                frame_url=control.frame_url,
                kind=control.kind,
                label=control.label,
                role=control.role,
                nearby_text=control.nearby_text,
                disabled=control.disabled,
                checked=control.checked,
                selected=control.selected,
                expanded=control.expanded,
                pressed=control.pressed,
                options=control.options,
            )
            for control in snapshot.controls[:200]
        ),
        previous_actions=previous_actions[-20:],
        clarifications=tuple(
            AgentClarification(
                question=item.question,
                answer=item.answer,
            )
            for item in task.clarifications[-20:]
        ),
        screenshot_data_url=screenshot_data_url,
    )
