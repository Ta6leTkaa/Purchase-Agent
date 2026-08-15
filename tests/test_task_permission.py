from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.task import AgentTask, UserActionReason
from app.domain.task_permission import (
    BrowserAction,
    BrowserActionRequest,
    TaskPermissionPolicy,
)
from app.services.task_permission import evaluate_browser_action


def make_task(**overrides: object) -> AgentTask:
    values: dict[str, object] = {
        "id": uuid4(),
        "instruction": "Подготовь заказ к оплате",
        "target_url": "https://tickets.example/search",
        "person_ids": (uuid4(),),
        "created_at": datetime(2026, 8, 13, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return AgentTask.model_validate(values)


@pytest.mark.parametrize(
    "action",
    [
        BrowserAction.READ_PAGE,
        BrowserAction.FILL_BASIC_PROFILE,
        BrowserAction.SELECT_OPTION,
        BrowserAction.PREPARE_REVIEW,
    ],
)
def test_default_policy_allows_reversible_browser_work(
    action: BrowserAction,
) -> None:
    decision = evaluate_browser_action(
        make_task(),
        BrowserActionRequest(action=action),
    )

    assert decision.allowed
    assert not decision.requires_user


def test_navigation_is_limited_to_the_task_origin() -> None:
    same_origin = evaluate_browser_action(
        make_task(),
        BrowserActionRequest(
            action=BrowserAction.NAVIGATE,
            target_url="https://tickets.example/checkout",
        ),
    )
    cross_origin = evaluate_browser_action(
        make_task(),
        BrowserActionRequest(
            action=BrowserAction.NAVIGATE,
            target_url="https://payments.example/checkout",
        ),
    )

    assert same_origin.allowed
    assert not cross_origin.allowed
    assert cross_origin.requires_user
    assert cross_origin.reason == UserActionReason.CONFIRMATION_REQUIRED.value


@pytest.mark.parametrize(
    ("action_request", "reason"),
    [
        (
            BrowserActionRequest(action=BrowserAction.PAY),
            UserActionReason.PAYMENT_REQUIRED,
        ),
        (
            BrowserActionRequest(
                action=BrowserAction.SELECT_OPTION,
                creates_charge=True,
            ),
            UserActionReason.PAYMENT_REQUIRED,
        ),
        (
            BrowserActionRequest(action=BrowserAction.AUTHENTICATE),
            UserActionReason.AUTHENTICATION_REQUIRED,
        ),
        (
            BrowserActionRequest(action=BrowserAction.SOLVE_CAPTCHA),
            UserActionReason.CAPTCHA_REQUIRED,
        ),
        (
            BrowserActionRequest(action=BrowserAction.SUBMIT_ORDER),
            UserActionReason.CONFIRMATION_REQUIRED,
        ),
        (
            BrowserActionRequest(
                action=BrowserAction.SELECT_OPTION,
                reversible=False,
            ),
            UserActionReason.CONFIRMATION_REQUIRED,
        ),
    ],
)
def test_irreversible_or_protected_actions_always_return_to_the_user(
    action_request: BrowserActionRequest,
    reason: UserActionReason,
) -> None:
    decision = evaluate_browser_action(make_task(), action_request)

    assert not decision.allowed
    assert decision.requires_user
    assert decision.reason == reason.value


def test_sensitive_profile_data_requires_separate_approval_by_default() -> None:
    decision = evaluate_browser_action(
        make_task(),
        BrowserActionRequest(action=BrowserAction.FILL_SENSITIVE_PROFILE),
    )

    assert decision.requires_user
    assert decision.reason == (
        UserActionReason.SENSITIVE_DATA_APPROVAL_REQUIRED.value
    )


def test_user_can_preapprove_sensitive_data_for_a_task() -> None:
    task = make_task(
        permissions=TaskPermissionPolicy(
            require_approval_for_sensitive_data=False
        )
    )

    decision = evaluate_browser_action(
        task,
        BrowserActionRequest(action=BrowserAction.FILL_SENSITIVE_PROFILE),
    )

    assert decision.allowed
    assert not decision.requires_user


def test_free_reservation_is_opt_in() -> None:
    request = BrowserActionRequest(
        action=BrowserAction.CREATE_FREE_RESERVATION
    )

    default_decision = evaluate_browser_action(make_task(), request)
    opted_in_decision = evaluate_browser_action(
        make_task(
            permissions=TaskPermissionPolicy(allow_free_reservation=True)
        ),
        request,
    )

    assert default_decision.requires_user
    assert opted_in_decision.allowed


def test_policy_can_disable_ordinary_automation() -> None:
    task = make_task(
        permissions=TaskPermissionPolicy(
            allow_navigation=False,
            allow_reading_pages=False,
            allow_basic_profile_filling=False,
            allow_option_selection=False,
            allow_review_preparation=False,
        )
    )

    for request in (
        BrowserActionRequest(
            action=BrowserAction.NAVIGATE,
            target_url="https://tickets.example/search",
        ),
        BrowserActionRequest(action=BrowserAction.READ_PAGE),
        BrowserActionRequest(action=BrowserAction.FILL_BASIC_PROFILE),
        BrowserActionRequest(action=BrowserAction.SELECT_OPTION),
        BrowserActionRequest(action=BrowserAction.PREPARE_REVIEW),
    ):
        decision = evaluate_browser_action(task, request)
        assert not decision.allowed
        assert not decision.requires_user
