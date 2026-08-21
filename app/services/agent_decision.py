from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.browser_command import AgentDecision
from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageSnapshot,
)
from app.domain.task import AgentTask
from app.services.fuzzy_matching import fuzzy_text_score, normalize_fuzzy_text

_MAX_AGENT_CONTROLS = 60
_MAX_AGENT_VISIBLE_TEXT = 6_000


class AgentPageStage(StrEnum):
    AUTHENTICATION = "authentication"
    REVIEW = "review"
    VISUAL_SELECTION = "visual_selection"
    FORM_ENTRY = "form_entry"
    OPTION_SELECTION = "option_selection"
    BROWSING = "browsing"
    UNKNOWN = "unknown"


class AgentVisibleControl(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    control_id: str
    frame_index: int = Field(default=0, ge=0, le=9)
    frame_url: str | None = Field(default=None, max_length=2_048)
    kind: BrowserControlKind
    label: str
    field_name: str | None = Field(default=None, max_length=200)
    goal_match_score: float = Field(default=0.0, ge=0.0, le=1.0)
    role: str | None = None
    nearby_text: str | None = Field(default=None, max_length=600)
    required: bool = False
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
    result_url: str | None = Field(default=None, max_length=2_048)
    result_page_stage: AgentPageStage | None = None
    page_changed: bool | None = None
    visual_point: tuple[float, float] | None = None
    visual_end_point: tuple[float, float] | None = None
    zoom_direction: str | None = Field(default=None, pattern=r"^(in|out)$")
    zoom_intensity: int | None = Field(default=None, ge=1, le=3)


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
    page_stage: AgentPageStage
    visible_text: str = Field(default="", max_length=12_000)
    intent: dict[str, object]
    controls: tuple[AgentVisibleControl, ...] = Field(max_length=_MAX_AGENT_CONTROLS)
    total_control_count: int = Field(default=0, ge=0, le=300)
    controls_truncated: bool = False
    previous_actions: tuple[AgentActionObservation, ...] = Field(
        default=(),
        max_length=20,
    )
    clarifications: tuple[AgentClarification, ...] = Field(default=(), max_length=20)
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
    goal_hints = _goal_hints(task)
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
        visible_text=_compact_visible_text(snapshot.visible_text, goal_hints),
        intent=intent,
        controls=_select_agent_controls(snapshot, goal_hints),
        total_control_count=len(snapshot.controls),
        controls_truncated=len(snapshot.controls) > _MAX_AGENT_CONTROLS,
        previous_actions=previous_actions[-20:],
        clarifications=tuple(
            AgentClarification(
                question=item.question,
                answer=item.answer,
            )
            for item in task.clarifications[-20:]
        ),
        screenshot_data_url=screenshot_data_url,
        page_stage=classify_agent_page_stage(snapshot),
    )


def classify_agent_page_stage(snapshot: BrowserPageSnapshot) -> AgentPageStage:
    """Return a non-authoritative workflow hint from generic page affordances."""
    controls = tuple(control for control in snapshot.controls if not control.disabled)
    searchable = " ".join(
        " ".join(
            part
            for part in (control.label, control.field_name, control.nearby_text)
            if part
        ).casefold()
        for control in controls
    )
    if any(term in searchable for term in _AUTHENTICATION_TERMS):
        return AgentPageStage.AUTHENTICATION
    if any(term in searchable for term in _REVIEW_TERMS):
        return AgentPageStage.REVIEW
    if any(
        control.kind in {BrowserControlKind.CANVAS, BrowserControlKind.SVG}
        for control in controls
    ):
        return AgentPageStage.VISUAL_SELECTION
    if any(control.kind in _FORM_CONTROL_KINDS for control in controls):
        return AgentPageStage.FORM_ENTRY
    if any(control.kind in _OPTION_CONTROL_KINDS for control in controls):
        return AgentPageStage.OPTION_SELECTION
    if any(
        control.kind in {BrowserControlKind.LINK, BrowserControlKind.CLICKABLE}
        for control in controls
    ):
        return AgentPageStage.BROWSING
    return AgentPageStage.UNKNOWN


_FORM_CONTROL_KINDS = {
    BrowserControlKind.TEXT,
    BrowserControlKind.DATE,
    BrowserControlKind.EMAIL,
    BrowserControlKind.TEL,
    BrowserControlKind.NUMBER,
}
_OPTION_CONTROL_KINDS = {
    BrowserControlKind.SELECT,
    BrowserControlKind.RADIO,
    BrowserControlKind.CHECKBOX,
    BrowserControlKind.BUTTON,
}
_AUTHENTICATION_TERMS = (
    "password",
    "passcode",
    "парол",
    "код подтверждения",
    "verification code",
)
_REVIEW_TERMS = (
    "confirm order",
    "confirm purchase",
    "pay now",
    "payment",
    "оплатить",
    "подтвердить заказ",
    "подтвердить покупку",
)


def _control_goal_match_score(
    label: str,
    nearby_text: str | None,
    search_terms: tuple[str, ...],
) -> float:
    candidate = " ".join(part for part in (label, nearby_text) if part)
    return round(
        max(
            (fuzzy_text_score(term, candidate) for term in search_terms),
            default=0.0,
        ),
        3,
    )


def _goal_hints(task: AgentTask) -> tuple[str, ...]:
    phrases = list(task.intent.search_terms if task.intent is not None else ())
    if task.intent is not None:
        phrases.extend(
            place
            for place in (task.intent.origin, task.intent.destination)
            if place is not None
        )
    tokens = normalize_fuzzy_text(task.instruction).split()
    phrases.extend(token for token in tokens if len(token) >= 3 or token.isdecimal())
    return tuple(dict.fromkeys(phrases))


def _select_agent_controls(
    snapshot: BrowserPageSnapshot,
    goal_hints: tuple[str, ...],
) -> tuple[AgentVisibleControl, ...]:
    ranked: list[tuple[float, int, AgentVisibleControl]] = []
    seen: set[tuple[int, str, str]] = set()
    for index, control in enumerate(snapshot.controls):
        if control.disabled or _is_uninformative_visual(control.kind, control.label):
            continue
        signature = (
            control.frame_index,
            normalize_fuzzy_text(control.field_name or ""),
            normalize_fuzzy_text(control.label),
        )
        if signature in seen:
            continue
        seen.add(signature)
        goal_score = _control_goal_match_score(
            control.label,
            control.nearby_text,
            goal_hints,
        )
        visible = AgentVisibleControl(
            control_id=control.control_id,
            frame_index=control.frame_index,
            frame_url=control.frame_url,
            kind=control.kind,
            label=control.label,
            field_name=control.field_name,
            goal_match_score=goal_score,
            role=control.role,
            nearby_text=control.nearby_text,
            required=control.required,
            disabled=control.disabled,
            checked=control.checked,
            selected=control.selected,
            expanded=control.expanded,
            pressed=control.pressed,
            options=control.options,
        )
        ranked.append((_control_priority(visible), index, visible))
    selected = sorted(
        sorted(ranked, key=lambda item: (-item[0], item[1]))[:_MAX_AGENT_CONTROLS],
        key=lambda item: item[1],
    )
    return tuple(item[2] for item in selected)


def _control_priority(control: AgentVisibleControl) -> float:
    kind_priority = {
        BrowserControlKind.TEXT: 35,
        BrowserControlKind.DATE: 35,
        BrowserControlKind.EMAIL: 35,
        BrowserControlKind.TEL: 35,
        BrowserControlKind.NUMBER: 35,
        BrowserControlKind.SELECT: 32,
        BrowserControlKind.RADIO: 30,
        BrowserControlKind.CHECKBOX: 30,
        BrowserControlKind.BUTTON: 28,
        BrowserControlKind.CANVAS: 28,
        BrowserControlKind.LINK: 18,
        BrowserControlKind.CLICKABLE: 16,
        BrowserControlKind.SVG: 2,
        BrowserControlKind.OTHER: 1,
    }[control.kind]
    state_priority = (
        8
        if any(
            state is not None
            for state in (
                control.checked,
                control.selected,
                control.expanded,
                control.pressed,
            )
        )
        else 0
    )
    return (
        control.goal_match_score * 100
        + kind_priority
        + state_priority
        + (15 if control.required else 0)
    )


def _is_uninformative_visual(kind: BrowserControlKind, label: str) -> bool:
    return kind is BrowserControlKind.SVG and normalize_fuzzy_text(label) in {
        "svg",
        "visual svg",
    }


def _compact_visible_text(text: str, goal_hints: tuple[str, ...]) -> str:
    if len(text) <= _MAX_AGENT_VISIBLE_TEXT:
        return text
    lines = text.splitlines()
    relevant = [
        line
        for line in lines
        if _control_goal_match_score(line, None, goal_hints) >= 0.7
    ]
    candidates = [*lines[:40], *relevant, *lines[-20:]]
    compact = "\n".join(dict.fromkeys(candidates))
    return compact[:_MAX_AGENT_VISIBLE_TEXT]
