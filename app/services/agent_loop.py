from typing import Protocol

from app.domain.agent_run import AgentLoopResult, AgentLoopStatus, AgentLoopStep
from app.domain.browser_command import (
    AgentDecision,
    AgentFinishOutcome,
    AskUserCommand,
    CommandExecutionResult,
    FinishCommand,
)
from app.domain.browser_page import BrowserPageSnapshot
from app.domain.task import AgentTask
from app.services.agent_decision import (
    AgentActionObservation,
    AgentDecisionProvider,
    build_agent_decision_context,
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
            AgentLoopStep(sequence=sequence, decision=decision, result=result)
        )
        observations = (
            *observations,
            AgentActionObservation(
                action=decision.command.action,
                target=_command_target(decision),
                result=result.reason_code,
            ),
        )[-20:]
        if result.page_snapshot is not None:
            snapshot = result.page_snapshot
            current_task = current_task.model_copy(
                update={"page_snapshot": snapshot}
            )

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
    for attribute in ("control_id", "url", "question", "outcome"):
        value = getattr(command, attribute, None)
        if value is not None:
            return str(value)[:200]
    return None


def _previous_observations(task: AgentTask) -> tuple[AgentActionObservation, ...]:
    if task.agent_run is None:
        return ()
    return tuple(
        AgentActionObservation(
            action=step.decision.command.action,
            target=_command_target(step.decision),
            result=step.result.reason_code,
        )
        for step in task.agent_run.steps[-20:]
    )
