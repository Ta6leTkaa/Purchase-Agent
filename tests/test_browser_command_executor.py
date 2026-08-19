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
    control.fill = AsyncMock()
    control.select_option = AsyncMock()
    inventory = MagicMock()
    raw_control = {
        "tag": tag,
        "type": control_type,
        "role": role,
        "name": None,
        "label": label,
        "clickable": True,
        "required": False,
        "disabled": False,
        "checked": False if control_type in {"radio", "checkbox"} else None,
        "selected": False if role == "tab" else None,
        "expanded": None,
        "pressed": None,
        "options": options or [],
    }
    inventory.evaluate_all = AsyncMock(return_value=[raw_control])
    body = MagicMock()
    body.inner_text = AsyncMock(return_value=f"Tickets\n{label}")

    def mark_clicked(**kwargs: object) -> None:
        raw_control["selected"] = True if role == "tab" else None
        raw_control["checked"] = (
            True if control_type in {"radio", "checkbox"} else None
        )
        body.inner_text.return_value = f"Tickets\n{label}\nSelected"

    control.click = AsyncMock(side_effect=mark_clicked)

    def locate(selector: str) -> MagicMock:
        if selector == "body *":
            return inventory
        if selector == "body":
            return body
        return control

    page_mock.locator = MagicMock(
        side_effect=locate
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
async def test_executor_clicks_bounded_point_inside_canvas() -> None:
    page, control = _page_with_control(tag="canvas", label="Схема мест")
    control.bounding_box = AsyncMock(
        return_value={"x": 100, "y": 200, "width": 400, "height": 300}
    )
    control.screenshot = AsyncMock(side_effect=[b"before", b"after"])
    page.mouse.click = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision(
            {
                "action": "click_visual",
                "control_id": "control_1",
                "x_ratio": 0.25,
                "y_ratio": 0.5,
            }
        ),
    )

    assert result.succeeded
    assert result.reason_code == "visual_control_clicked"
    page.mouse.click.assert_awaited_once_with(200, 350)
    control.click.assert_not_awaited()
    assert control.screenshot.await_count == 2


@pytest.mark.asyncio
async def test_executor_clicks_bounded_point_inside_svg_map() -> None:
    page, control = _page_with_control(tag="svg", label="Схема зала")
    control.bounding_box = AsyncMock(
        return_value={"x": 50, "y": 100, "width": 500, "height": 400}
    )
    control.screenshot = AsyncMock(side_effect=[b"before", b"after"])
    page.mouse.click = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision(
            {
                "action": "click_visual",
                "control_id": "control_1",
                "x_ratio": 0.5,
                "y_ratio": 0.25,
            }
        ),
    )

    assert result.succeeded
    assert result.reason_code == "visual_control_clicked"
    page.mouse.click.assert_awaited_once_with(300, 200)


@pytest.mark.asyncio
async def test_executor_reports_visual_click_that_does_not_change_canvas() -> None:
    page, control = _page_with_control(tag="canvas", label="Схема мест")
    control.bounding_box = AsyncMock(
        return_value={"x": 100, "y": 200, "width": 400, "height": 300}
    )
    control.screenshot = AsyncMock(side_effect=[b"same", b"same"])
    page.mouse.click = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision(
            {
                "action": "click_visual",
                "control_id": "control_1",
                "x_ratio": 0.5,
                "y_ratio": 0.5,
            }
        ),
    )

    assert not result.succeeded
    assert result.reason_code == "visual_control_unchanged"
    assert result.page_snapshot is not None
    page.mouse.click.assert_awaited_once_with(300, 350)


@pytest.mark.asyncio
async def test_executor_rejects_visual_click_for_regular_control() -> None:
    page, control = _page_with_control(tag="button", label="Выбрать")
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision(
            {
                "action": "click_visual",
                "control_id": "control_1",
                "x_ratio": 0.5,
                "y_ratio": 0.5,
            }
        ),
    )

    assert not result.succeeded
    assert result.reason_code == "visual_control_required"
    control.click.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_blocks_visual_click_on_payment_canvas() -> None:
    page, control = _page_with_control(tag="canvas", label="Оплатить заказ")
    control.bounding_box = AsyncMock(
        return_value={"x": 0, "y": 0, "width": 400, "height": 300}
    )
    page.mouse.click = AsyncMock()
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision(
            {
                "action": "click_visual",
                "control_id": "control_1",
                "x_ratio": 0.5,
                "y_ratio": 0.5,
            }
        ),
    )

    assert not result.succeeded
    assert result.requires_user
    assert result.reason_code == "irreversible_click_requires_user"
    page.mouse.click.assert_not_awaited()


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
async def test_executor_reports_click_that_does_not_change_page() -> None:
    page, control = _page_with_control(tag="button", label="Показать сеансы")
    control.click = AsyncMock()
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "click", "control_id": "control_1"}),
    )

    assert not result.succeeded
    assert result.reason_code == "page_unchanged_after_click"
    assert result.page_snapshot is not None


@pytest.mark.asyncio
async def test_executor_follows_same_origin_popup() -> None:
    page, control = _page_with_control(tag="button", label="Выбрать сеанс")
    popup, _ = _page_with_control(tag="button", label="Выбрать места")
    popup.url = "https://tickets.example.com/session/42"
    popup.wait_for_load_state = AsyncMock()
    handlers: dict[str, object] = {}
    page.on = MagicMock(
        side_effect=lambda event, handler: handlers.update({event: handler})
    )

    def open_popup(**kwargs: object) -> None:
        handler = handlers["popup"]
        assert callable(handler)
        handler(popup)

    control.click = AsyncMock(side_effect=open_popup)
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "click", "control_id": "control_1"}),
    )

    assert result.succeeded
    assert result.page_snapshot is not None
    assert result.page_snapshot.url.endswith("/session/42")
    assert runner._page is popup


@pytest.mark.asyncio
async def test_executor_closes_cross_origin_popup() -> None:
    page, control = _page_with_control(tag="button", label="Продолжить")
    popup, _ = _page_with_control(tag="button", label="External")
    popup.url = "https://evil.example/checkout"
    popup.wait_for_load_state = AsyncMock()
    popup.close = AsyncMock()
    handlers: dict[str, object] = {}
    page.on = MagicMock(
        side_effect=lambda event, handler: handlers.update({event: handler})
    )

    def open_popup(**kwargs: object) -> None:
        handler = handlers["popup"]
        assert callable(handler)
        handler(popup)

    control.click = AsyncMock(side_effect=open_popup)
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "click", "control_id": "control_1"}),
    )

    assert not result.succeeded
    assert result.requires_user
    assert result.reason_code == "cross_origin_popup_blocked"
    popup.close.assert_awaited_once()
    assert runner._page is page


@pytest.mark.asyncio
async def test_executor_clicks_control_inside_same_origin_frame() -> None:
    page, _ = _page_with_control(tag="button", label="Main action")
    frame = MagicMock()
    frame.url = "https://tickets.example.com/widget/sessions"
    frame_control = MagicMock()
    frame_control.count = AsyncMock(return_value=1)
    frame_control.is_visible = AsyncMock(return_value=True)
    frame_control.evaluate = AsyncMock(return_value=None)
    frame_inventory = MagicMock()
    frame_inventory.evaluate_all = AsyncMock(
        return_value=[
            {
                "tag": "button",
                "type": "button",
                "role": None,
                "name": None,
                "label": "18:00",
                "clickable": True,
                "required": False,
                "disabled": False,
                "options": [],
            }
        ]
    )
    frame_body = MagicMock()
    frame_body.inner_text = AsyncMock(return_value="Колобок\n18:00")

    def click_frame(**kwargs: object) -> None:
        frame_body.inner_text.return_value = "Колобок\n18:00\nВыбор мест"

    frame_control.click = AsyncMock(side_effect=click_frame)

    def locate_frame(selector: str) -> MagicMock:
        if selector == "body *":
            return frame_inventory
        if selector == "body":
            return frame_body
        return frame_control

    frame.locator = MagicMock(side_effect=locate_frame)
    external_frame = MagicMock()
    external_frame.url = "https://payments.example.net/widget"
    page.frames = [page, frame, external_frame]
    runner = PlaywrightBrowserStepRunner()
    runner._page = page

    result = await runner.execute_command(
        _task(),
        _decision({"action": "click", "control_id": "control_2"}),
    )

    assert result.succeeded
    assert result.page_snapshot is not None
    assert result.page_snapshot.controls[1].frame_index == 1
    assert result.page_snapshot.controls[1].frame_url == frame.url
    assert "[Embedded frame 1]" in result.page_snapshot.visible_text
    frame_control.click.assert_awaited_once()
    external_frame.locator.assert_not_called()


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
