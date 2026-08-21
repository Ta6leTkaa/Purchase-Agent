from typing import Protocol

from app.domain.agent_run import (
    AgentDecisionMetadata,
    AgentLoopResult,
    AgentLoopStatus,
    AgentLoopStep,
)
from app.domain.browser_command import (
    AgentDecision,
    AgentFinishOutcome,
    AskUserCommand,
    ClickVisualCommand,
    CommandExecutionResult,
    DragVisualCommand,
    FinishCommand,
    HoverVisualCommand,
    ZoomVisualCommand,
)
from app.domain.browser_page import BrowserPageSnapshot
from app.domain.task import AgentTask
from app.services.agent_decision import (
    AgentActionObservation,
    AgentDecisionProvider,
    build_agent_decision_context,
    classify_agent_page_stage,
)


class AgentBrowserRuntime(Protocol):
    async def observe(self, task: AgentTask) -> BrowserPageSnapshot: ...

    async def execute_command(
        self,
        task: AgentTask,
        decision: AgentDecision,
        *,
        approved_sensitive: bool = False,
    ) -> CommandExecutionResult: ...

    async def capture_visual_context(self, task: AgentTask) -> str | None: ...


async def run_agent_loop(
    task: AgentTask,
    *,
    provider: AgentDecisionProvider,
    runtime: AgentBrowserRuntime,
    max_steps: int = 12,
    repeated_command_limit: int = 3,
    approved_sensitive: bool = False,
) -> AgentLoopResult:
    """Run a bounded observe-decide-act cycle without persisting hidden state."""
    if max_steps < 1:
        raise ValueError("max_steps must be at least one")
    if repeated_command_limit < 2:
        raise ValueError("repeated_command_limit must be at least two")

    snapshot = await runtime.observe(task)
    current_task = task.model_copy(update={"page_snapshot": snapshot})
    observations = _previous_observations(task)
    steps: list[AgentLoopStep] = []
    previous_fingerprint: str | None = None
    repeated_commands = 0

    for sequence in range(1, max_steps + 1):
        screenshot_data_url = await _capture_visual_context(runtime, current_task)
        context = build_agent_decision_context(
            current_task,
            previous_actions=observations,
            screenshot_data_url=screenshot_data_url,
        )
        decision = await provider.decide(context)
        fingerprint = decision.command.model_dump_json()
        if fingerprint == previous_fingerprint:
            repeated_commands += 1
        else:
            previous_fingerprint = fingerprint
            repeated_commands = 1

        if repeated_commands >= repeated_command_limit:
            return AgentLoopResult(
                status=AgentLoopStatus.STALLED,
                reason_code="repeated_command_limit",
                page_snapshot=snapshot,
                steps=tuple(steps),
            )

        result = await runtime.execute_command(
            current_task,
            decision,
            approved_sensitive=approved_sensitive,
        )
        steps.append(
            AgentLoopStep(
                sequence=sequence,
                decision=decision,
                decision_metadata=_provider_decision_metadata(provider),
                result=result,
            )
        )
        observations = (
            *observations,
            _action_observation(decision, result, before_snapshot=snapshot),
        )[-20:]
        if result.page_snapshot is not None:
            snapshot = result.page_snapshot
            current_task = current_task.model_copy(update={"page_snapshot": snapshot})

        if isinstance(decision.command, FinishCommand):
            return AgentLoopResult(
                status=_finish_status(decision.command.outcome),
                reason_code=result.reason_code,
                page_snapshot=snapshot,
                steps=tuple(steps),
            )
        if isinstance(decision.command, AskUserCommand) or result.requires_user:
            return AgentLoopResult(
                status=AgentLoopStatus.WAITING_FOR_USER,
                reason_code=result.reason_code,
                page_snapshot=snapshot,
                steps=tuple(steps),
            )

    return AgentLoopResult(
        status=AgentLoopStatus.EXHAUSTED,
        reason_code="maximum_steps_reached",
        page_snapshot=snapshot,
        steps=tuple(steps),
    )


async def _capture_visual_context(
    runtime: AgentBrowserRuntime,
    task: AgentTask,
) -> str | None:
    capture = getattr(runtime, "capture_visual_context", None)
    if capture is None:
        return None
    result = await capture(task)
    if result is None or isinstance(result, str):
        return result
    raise TypeError("visual context capture must return a data URL or None")


def _provider_decision_metadata(
    provider: AgentDecisionProvider,
) -> AgentDecisionMetadata | None:
    metadata = getattr(provider, "last_decision_metadata", None)
    return metadata if isinstance(metadata, AgentDecisionMetadata) else None


def _finish_status(outcome: AgentFinishOutcome) -> AgentLoopStatus:
    if outcome is AgentFinishOutcome.NO_MATCH:
        return AgentLoopStatus.NO_MATCH
    if outcome in {
        AgentFinishOutcome.READY_FOR_USER,
        AgentFinishOutcome.NEEDS_USER,
    }:
        return AgentLoopStatus.WAITING_FOR_USER
    return AgentLoopStatus.COMPLETED


def _command_target(decision: AgentDecision) -> str | None:
    command = decision.command
    if isinstance(command, (ClickVisualCommand, HoverVisualCommand)):
        return f"{command.control_id}@({command.x_ratio:.3f},{command.y_ratio:.3f})"
    if isinstance(command, DragVisualCommand):
        return (
            f"{command.control_id}@({command.start_x_ratio:.3f},"
            f"{command.start_y_ratio:.3f})->({command.end_x_ratio:.3f},"
            f"{command.end_y_ratio:.3f})"
        )
    if isinstance(command, ZoomVisualCommand):
        return f"{command.control_id}:{command.direction}:{command.intensity}"
    for attribute in ("control_id", "url", "question", "outcome"):
        value = getattr(command, attribute, None)
        if value is not None:
            return str(value)[:200]
    return None


def _action_observation(
    decision: AgentDecision,
    result: CommandExecutionResult,
    *,
    before_snapshot: BrowserPageSnapshot | None = None,
) -> AgentActionObservation:
    command = decision.command
    result_snapshot = result.page_snapshot
    visual_point: tuple[float, float] | None = None
    visual_end_point: tuple[float, float] | None = None
    zoom_direction: str | None = None
    zoom_intensity: int | None = None
    if isinstance(command, (ClickVisualCommand, HoverVisualCommand)):
        visual_point = (command.x_ratio, command.y_ratio)
    elif isinstance(command, DragVisualCommand):
        visual_point = (
            command.start_x_ratio,
            command.start_y_ratio,
        )
        visual_end_point = (
            command.end_x_ratio,
            command.end_y_ratio,
        )
    elif isinstance(command, ZoomVisualCommand):
        zoom_direction = command.direction
        zoom_intensity = command.intensity
    return AgentActionObservation(
        action=command.action,
        target=_command_target(decision),
        result=result.reason_code,
        result_url=result_snapshot.url if result_snapshot is not None else None,
        result_page_stage=(
            classify_agent_page_stage(result_snapshot)
            if result_snapshot is not None
            else None
        ),
        page_changed=(
            _page_semantically_changed(before_snapshot, result_snapshot)
            if before_snapshot is not None and result_snapshot is not None
            else None
        ),
        visual_point=visual_point,
        visual_end_point=visual_end_point,
        zoom_direction=zoom_direction,
        zoom_intensity=zoom_intensity,
    )


def _previous_observations(task: AgentTask) -> tuple[AgentActionObservation, ...]:
    if task.agent_run is None:
        return ()
    observations: list[AgentActionObservation] = []
    before_snapshot: BrowserPageSnapshot | None = None
    for step in task.agent_run.steps[-20:]:
        observations.append(
            _action_observation(
                step.decision,
                step.result,
                before_snapshot=before_snapshot,
            )
        )
        if step.result.page_snapshot is not None:
            before_snapshot = step.result.page_snapshot
    return tuple(observations)


def _page_semantically_changed(
    before: BrowserPageSnapshot,
    after: BrowserPageSnapshot,
) -> bool:
    return (
        before.url,
        before.title,
        before.visible_text,
        before.controls,
    ) != (
        after.url,
        after.title,
        after.visible_text,
        after.controls,
    )
