import pytest
from pydantic import ValidationError

from app.domain.browser_command import (
    AgentDecision,
    ClickCommand,
    ClickVisualCommand,
    FillCommand,
    FillValueSource,
    OpenUrlCommand,
)


def test_decision_parses_discriminated_click_command() -> None:
    decision = AgentDecision.model_validate(
        {
            "command": {"action": "click", "control_id": "control_42"},
            "rationale": "  Вкладка соответствует нужной дате. ",
            "expected_result": " Появятся доступные сеансы. ",
        }
    )

    assert isinstance(decision.command, ClickCommand)
    assert decision.command.control_id == "control_42"
    assert decision.rationale == "Вкладка соответствует нужной дате."


def test_literal_fill_requires_bounded_explicit_value() -> None:
    command = FillCommand(
        action="fill",
        control_id="control_3",
        value_source=FillValueSource.LITERAL,
        literal_value="2026-08-17",
    )

    assert command.literal_value == "2026-08-17"
    with pytest.raises(ValidationError, match="literal fill requires"):
        FillCommand(
            action="fill",
            control_id="control_3",
            value_source=FillValueSource.LITERAL,
        )


def test_visual_click_is_bounded_to_inner_control_area() -> None:
    command = ClickVisualCommand(
        action="click_visual",
        control_id="control_7",
        x_ratio=0.5,
        y_ratio=0.75,
    )

    assert command.x_ratio == 0.5
    with pytest.raises(ValidationError):
        ClickVisualCommand(
            action="click_visual",
            control_id="control_7",
            x_ratio=1.0,
            y_ratio=0.5,
        )


def test_profile_fill_references_data_without_copying_value() -> None:
    command = FillCommand(
        action="fill",
        control_id="control_8",
        value_source=FillValueSource.FIRST_NAME,
    )

    assert command.literal_value is None
    with pytest.raises(ValidationError, match="must not contain"):
        FillCommand(
            action="fill",
            control_id="control_8",
            value_source=FillValueSource.DOCUMENT_NUMBER,
            literal_value="1234567890",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "run_javascript", "code": "document.body.remove()"},
        {"action": "click", "control_id": "#buy-now"},
        {"action": "wait", "seconds": 60},
        {"action": "scroll", "direction": "sideways"},
    ],
)
def test_decision_rejects_unknown_or_unbounded_commands(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(
            {
                "command": payload,
                "rationale": "Try an action",
                "expected_result": "Page changes",
            }
        )


def test_open_url_rejects_credentials_and_fragments() -> None:
    with pytest.raises(ValidationError, match="credentials"):
        OpenUrlCommand(
            action="open_url",
            url="https://user:password@example.com/",
        )
    with pytest.raises(ValidationError, match="fragment"):
        OpenUrlCommand(
            action="open_url",
            url="https://example.com/search#payment",
        )
