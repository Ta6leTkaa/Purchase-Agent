from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from playwright.async_api import Page

from app.adapters.playwright_browser import PlaywrightBrowserStepRunner
from app.domain.browser_command import AgentDecision
from app.domain.identity import Document, DocumentType, Identity
from app.domain.task import AgentTask

NOW = datetime(2026, 8, 16, 12, tzinfo=UTC)


def _decision(command: dict[str, object]) -> AgentDecision:
    return AgentDecision.model_validate(
        {
            "command": command,
            "rationale": "This advances the task",
            "expected_result": "The page changes",
        }
    )


def _task() -> AgentTask:
    return AgentTask(
        id=uuid4(),
        instruction="Купить билет",
        target_url="https://tickets.example.com/search",
        person_ids=(uuid4(),),
        created_at=NOW,
    )


def _page_with_control(
    *,
    tag: str,
    label: str,
    role: str | None = None,
    control_type: str = "",
    options: list[str] | None = None,
) -> tuple[Page, MagicMock]:
    page_mock = MagicMock()
    page = cast(Page, page_mock)
    control = MagicMock()
    control.is_visible = AsyncMock(return_value=True)
    control.count = AsyncMock(return_value=1)
    control.evaluate = AsyncMock(return_value=None)
    control.click = AsyncMock()
    control.fill = AsyncMock()
    control.select_option = AsyncMock()
    inventory = MagicMock()
    inventory.evaluate_all = AsyncMock(
        return_value=[
            {
                "tag": tag,
                "type": control_type,
                "role": role,
                "name": None,
                "label": label,
                "clickable": True,
                "required": False,
                "disabled": False,
                "options": options or [],
            }
        ]
    )
    page_mock.locator = MagicMock(
        side_effect=lambda selector: inventory if selector == "body *" else control
    )
    page_mock.url = "https://tickets.example.com/search"
    page_mock.title = AsyncMock(return_value="Tickets")
    page_mock.wait_for_timeout = AsyncMock()
    return page, control


@pytest.mark.asyncio
async def test_executor_clicks_custom_interactive_control() -> None:
    page, control = _page_with_control(tag="div", role="tab", label="Завтра")
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "click", "control_id": "control_1"}),
    )

    assert result.succeeded
    assert result.reason_code == "control_clicked"
    control.click.assert_awaited_once()
    assert result.page_snapshot is not None


@pytest.mark.asyncio
async def test_executor_blocks_irreversible_click() -> None:
    page, control = _page_with_control(
        tag="button",
        label="Оплатить заказ",
    )
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "click", "control_id": "control_1"}),
    )

    assert not result.succeeded
    assert result.requires_user
    assert result.reason_code == "irreversible_click_requires_user"
    control.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_blocks_cross_origin_open_url() -> None:
    page, _ = _page_with_control(tag="button", label="Continue")
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "open_url", "url": "https://evil.example/"}),
    )

    assert not result.succeeded
    assert result.requires_user
    assert result.reason_code == "cross_origin_navigation_blocked"
    page.goto.assert_not_called()


@pytest.mark.asyncio
async def test_executor_resolves_profile_value_without_model_receiving_it() -> None:
    person = Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 2),
        documents=[
            Document(
                id=uuid4(),
                type=DocumentType.internal_passport,
                number="1234567890",
            )
        ],
    )
    page, control = _page_with_control(
        tag="input",
        label="First name",
        control_type="text",
    )
    runner = PlaywrightBrowserStepRunner(identities=(person,))
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision(
            {
                "action": "fill",
                "control_id": "control_1",
                "value_source": "first_name",
            }
        ),
    )

    assert result.succeeded
    control.fill.assert_awaited_once_with("Ivan", timeout=30_000)


@pytest.mark.asyncio
async def test_executor_requires_approval_before_document_fill() -> None:
    person = Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 2),
        documents=[
            Document(
                id=uuid4(),
                type=DocumentType.internal_passport,
                number="1234567890",
            )
        ],
    )
    page, control = _page_with_control(
        tag="input",
        label="Passport",
        control_type="text",
    )
    runner = PlaywrightBrowserStepRunner(identities=(person,))
    runner._page = page
    decision = _decision(
        {
            "action": "fill",
            "control_id": "control_1",
            "value_source": "document_number",
        }
    )

    blocked = await runner.execute_command(_task(), decision)
    approved = await runner.execute_command(
        _task(),
        decision,
        approved_sensitive=True,
    )

    assert not blocked.succeeded
    assert blocked.requires_user
    control.fill.assert_awaited_once_with("1234567890", timeout=30_000)
    assert approved.succeeded
