from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.agent_run import AgentLoopStatus
from app.domain.browser_command import (
    AgentDecision,
    AgentFinishOutcome,
    AskUserCommand,
    ClickCommand,
    ClickVisualCommand,
    CommandExecutionResult,
    DragVisualCommand,
    FinishCommand,
    HoverVisualCommand,
    ScrollCommand,
    ZoomVisualCommand,
)
from app.domain.browser_page import BrowserPageSnapshot
from app.domain.task import AgentTask
from app.services.agent_decision import AgentDecisionContext
from app.services.agent_loop import (
    _action_observation,
    _command_target,
    run_agent_loop,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _snapshot(title: str = "Cinema") -> BrowserPageSnapshot:
    return BrowserPageSnapshot(
        url="https://cinema.example.com/films",
        title=title,
        captured_at=NOW,
        controls=(),
    )


def _task() -> AgentTask:
    return AgentTask(
        id=uuid4(),
        instruction="Купи билет на Колобка",
        target_url="https://cinema.example.com/films",
        person_ids=(uuid4(),),
        created_at=NOW,
    )


def _decision(command: object) -> AgentDecision:
    return AgentDecision(
        command=command,
        rationale="This is the next safe action",
        expected_result="The page advances",
    )


@pytest.mark.parametrize(
    ("command", "target"),
    [
        (
            ClickVisualCommand(
                action="click_visual",
                control_id="control_1",
                x_ratio=0.25,
                y_ratio=0.75,
            ),
            "control_1@(0.250,0.750)",
        ),
        (
            HoverVisualCommand(
                action="hover_visual",
                control_id="control_2",
                x_ratio=0.3333,
                y_ratio=0.6666,
            ),
            "control_2@(0.333,0.667)",
        ),
        (
            DragVisualCommand(
                action="drag_visual",
                control_id="control_3",
                start_x_ratio=0.2,
                start_y_ratio=0.5,
                end_x_ratio=0.8,
                end_y_ratio=0.5,
            ),
            "control_3@(0.200,0.500)->(0.800,0.500)",
        ),
        (
            ZoomVisualCommand(
                action="zoom_visual",
                control_id="control_4",
                direction="in",
                intensity=2,
            ),
            "control_4:in:2",
        ),
    ],
)
def test_visual_command_target_preserves_spatial_details(
    command: object, target: str
) -> None:
    assert _command_target(_decision(command)) == target


def test_hover_observation_exposes_structured_visual_point() -> None:
    observation = _action_observation(
        _decision(
            HoverVisualCommand(
                action="hover_visual",
                control_id="control_2",
                x_ratio=0.3,
                y_ratio=0.7,
            )
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="visual_control_hovered",
        ),
    )

    assert observation.visual_point == (0.3, 0.7)
    assert observation.visual_end_point is None
    assert observation.result == "visual_control_hovered"


def test_drag_and_zoom_observations_expose_structured_details() -> None:
    drag = _action_observation(
        _decision(
            DragVisualCommand(
                action="drag_visual",
                control_id="control_3",
                start_x_ratio=0.2,
                start_y_ratio=0.4,
                end_x_ratio=0.8,
                end_y_ratio=0.6,
            )
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="visual_control_dragged",
        ),
    )
    zoom = _action_observation(
        _decision(
            ZoomVisualCommand(
                action="zoom_visual",
                control_id="control_3",
                direction="in",
                intensity=2,
            )
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="visual_control_zoomed",
        ),
    )

    assert drag.visual_point == (0.2, 0.4)
    assert drag.visual_end_point == (0.8, 0.6)
    assert zoom.zoom_direction == "in"
    assert zoom.zoom_intensity == 2


class ScriptedProvider:
    def __init__(self, *decisions: AgentDecision) -> None:
        self.decisions = list(decisions)
        self.contexts: list[AgentDecisionContext] = []

    async def decide(self, context: AgentDecisionContext) -> AgentDecision:
        self.contexts.append(context)
        return self.decisions.pop(0)


class ScriptedRuntime:
    def __init__(self, *results: CommandExecutionResult) -> None:
        self.results = list(results)
        self.executed: list[AgentDecision] = []
        self.visual_context_calls = 0

    async def observe(self, task: AgentTask) -> BrowserPageSnapshot:
        return _snapshot()

    async def capture_visual_context(self, task: AgentTask) -> str | None:
        self.visual_context_calls += 1
        return "data:image/jpeg;base64,dGVzdA=="

    async def execute_command(
        self,
        task: AgentTask,
        decision: AgentDecision,
        *,
        approved_sensitive: bool = False,
    ) -> CommandExecutionResult:
        self.executed.append(decision)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_loop_observes_executes_and_finishes() -> None:
    provider = ScriptedProvider(
        _decision(ClickCommand(action="click", control_id="control_1")),
        _decision(
            FinishCommand(
                action="finish",
                outcome=AgentFinishOutcome.GOAL_REACHED,
                summary="Ticket selected",
            )
        ),
    )
    changed = _snapshot("Checkout")
    runtime = ScriptedRuntime(
        CommandExecutionResult(
            succeeded=True,
            reason_code="control_clicked",
            page_snapshot=changed,
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="finished_goal_reached",
            page_snapshot=changed,
        ),
    )

    result = await run_agent_loop(_task(), provider=provider, runtime=runtime)

    assert result.status is AgentLoopStatus.COMPLETED
    assert result.page_snapshot.title == "Checkout"
    assert len(result.steps) == 2
    assert runtime.visual_context_calls == 2
    assert provider.contexts[0].screenshot_data_url is not None
    assert provider.contexts[1].previous_actions[0].result == "control_clicked"
    assert provider.contexts[1].previous_actions[0].page_changed is True
    assert provider.contexts[1].previous_actions[0].result_url.endswith("/films")


@pytest.mark.asyncio
async def test_loop_allows_model_to_recover_from_failed_action() -> None:
    provider = ScriptedProvider(
        _decision(ClickCommand(action="click", control_id="control_1")),
        _decision(ScrollCommand(action="scroll", direction="down")),
        _decision(
            FinishCommand(
                action="finish",
                outcome=AgentFinishOutcome.NO_MATCH,
                summary="No matching session",
            )
        ),
    )
    runtime = ScriptedRuntime(
        CommandExecutionResult(succeeded=False, reason_code="control_not_found"),
        CommandExecutionResult(
            succeeded=True,
            reason_code="page_scrolled",
            page_snapshot=_snapshot(),
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="finished_no_match",
            page_snapshot=_snapshot(),
        ),
    )

    result = await run_agent_loop(_task(), provider=provider, runtime=runtime)

    assert result.status is AgentLoopStatus.NO_MATCH
    assert len(result.steps) == 3
    assert provider.contexts[1].previous_actions[-1].result == "control_not_found"


@pytest.mark.asyncio
async def test_loop_passes_hover_point_to_the_following_decision() -> None:
    provider = ScriptedProvider(
        _decision(
            HoverVisualCommand(
                action="hover_visual",
                control_id="control_1",
                x_ratio=0.35,
                y_ratio=0.65,
            )
        ),
        _decision(
            FinishCommand(
                action="finish",
                outcome=AgentFinishOutcome.READY_FOR_USER,
                summary="Matching visual option found",
            )
        ),
    )
    runtime = ScriptedRuntime(
        CommandExecutionResult(
            succeeded=True,
            reason_code="visual_control_hovered",
            page_snapshot=_snapshot(),
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="finished_ready_for_user",
            page_snapshot=_snapshot(),
        ),
    )

    await run_agent_loop(_task(), provider=provider, runtime=runtime)

    hover = provider.contexts[1].previous_actions[-1]
    assert hover.action == "hover_visual"
    assert hover.visual_point == (0.35, 0.65)
    assert hover.result == "visual_control_hovered"
    assert hover.result_url == "https://cinema.example.com/films"
    assert hover.result_page_stage == "unknown"
    assert hover.page_changed is False


@pytest.mark.asyncio
async def test_loop_stops_when_user_input_is_required() -> None:
    provider = ScriptedProvider(
        _decision(AskUserCommand(action="ask_user", question="Choose a city"))
    )
    runtime = ScriptedRuntime(
        CommandExecutionResult(
            succeeded=False,
            reason_code="user_input_required",
            requires_user=True,
        )
    )

    result = await run_agent_loop(_task(), provider=provider, runtime=runtime)

    assert result.status is AgentLoopStatus.WAITING_FOR_USER
    assert result.reason_code == "user_input_required"


@pytest.mark.asyncio
async def test_loop_stops_before_third_identical_command() -> None:
    repeated = _decision(ScrollCommand(action="scroll", direction="down"))
    provider = ScriptedProvider(repeated, repeated, repeated)
    runtime = ScriptedRuntime(
        CommandExecutionResult(
            succeeded=True,
            reason_code="page_scrolled",
            page_snapshot=_snapshot(),
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="page_scrolled",
            page_snapshot=_snapshot(),
        ),
    )

    result = await run_agent_loop(_task(), provider=provider, runtime=runtime)

    assert result.status is AgentLoopStatus.STALLED
    assert result.reason_code == "repeated_command_limit"
    assert len(runtime.executed) == 2


@pytest.mark.asyncio
async def test_loop_has_hard_step_limit() -> None:
    provider = ScriptedProvider(
        _decision(ScrollCommand(action="scroll", direction="down")),
        _decision(ScrollCommand(action="scroll", direction="up")),
    )
    runtime = ScriptedRuntime(
        CommandExecutionResult(
            succeeded=True,
            reason_code="page_scrolled",
            page_snapshot=_snapshot(),
        ),
        CommandExecutionResult(
            succeeded=True,
            reason_code="page_scrolled",
            page_snapshot=_snapshot(),
        ),
    )

    result = await run_agent_loop(
        _task(), provider=provider, runtime=runtime, max_steps=2
    )

    assert result.status is AgentLoopStatus.EXHAUSTED
    assert result.reason_code == "maximum_steps_reached"
    assert len(result.steps) == 2
