from datetime import UTC, datetime

import pytest

from app.domain.agent_run import (
    AgentLoopResult,
    AgentLoopStatus,
    AgentLoopStep,
    merge_agent_loop_results,
)
from app.domain.browser_command import (
    AgentDecision,
    ClickVisualCommand,
    CommandExecutionResult,
)
from app.domain.browser_page import BrowserPageSnapshot

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _result(*, x_ratio: float, reason_code: str) -> AgentLoopResult:
    snapshot = BrowserPageSnapshot(
        url="https://cinema.example.com/seats",
        title="Seats",
        captured_at=NOW,
        controls=(),
    )
    return AgentLoopResult(
        status=AgentLoopStatus.EXHAUSTED,
        reason_code="maximum_steps_reached",
        page_snapshot=snapshot,
        steps=(
            AgentLoopStep(
                sequence=1,
                decision=AgentDecision(
                    command=ClickVisualCommand(
                        action="click_visual",
                        control_id="control_1",
                        x_ratio=x_ratio,
                        y_ratio=0.5,
                    ),
                    rationale="Try a visible seat",
                    expected_result="The seat becomes selected",
                ),
                result=CommandExecutionResult(
                    succeeded=False,
                    reason_code=reason_code,
                ),
            ),
        ),
    )


def test_merge_agent_loop_results_preserves_actions_between_runs() -> None:
    previous = _result(x_ratio=0.2, reason_code="visual_control_unchanged")
    current = _result(x_ratio=0.8, reason_code="visual_control_changed")

    merged = merge_agent_loop_results(previous, current)

    assert merged.status is current.status
    assert merged.reason_code == current.reason_code
    assert [step.sequence for step in merged.steps] == [1, 2]
    assert [step.decision.command.x_ratio for step in merged.steps] == [0.2, 0.8]


def test_merge_agent_loop_results_bounds_and_renumbers_history() -> None:
    previous = _result(x_ratio=0.2, reason_code="visual_control_unchanged")
    current = _result(x_ratio=0.8, reason_code="visual_control_changed")

    merged = merge_agent_loop_results(previous, current, max_steps=1)

    assert len(merged.steps) == 1
    assert merged.steps[0].sequence == 1
    assert merged.steps[0].decision.command.x_ratio == 0.8


def test_merge_agent_loop_results_rejects_empty_history_limit() -> None:
    with pytest.raises(ValueError, match="max_steps must be at least one"):
        merge_agent_loop_results(
            None, _result(x_ratio=0.5, reason_code="failed"), max_steps=0
        )
