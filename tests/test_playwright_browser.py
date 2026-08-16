import ipaddress
from datetime import UTC, date, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from playwright.async_api import Page

from app.adapters.playwright_browser import (
    PlaywrightBrowserStepRunner,
    _best_matching_link,
    _fuzzy_label_score,
    _is_safe_review_button,
    _unique_matching_option,
    validate_browser_url,
)
from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)
from app.domain.identity import Document, DocumentType, Identity
from app.domain.task import AgentTask
from app.domain.task_intent import TaskIntent
from app.domain.task_permission import BrowserAction
from app.domain.task_plan import TaskPlanStep

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


async def public_resolver(
    host: str, port: int
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return {ipaddress.ip_address("93.184.216.34")}


async def private_resolver(
    host: str, port: int
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return {ipaddress.ip_address("10.0.0.5")}


@pytest.mark.asyncio
async def test_browser_url_guard_allows_public_hosts() -> None:
    await validate_browser_url(
        "https://example.com/path",
        allow_local_network=False,
        resolver=public_resolver,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/internal",
    ],
)
async def test_browser_url_guard_rejects_non_public_addresses(url: str) -> None:
    with pytest.raises(ValueError, match="non-public"):
        await validate_browser_url(url, allow_local_network=False)


@pytest.mark.asyncio
async def test_browser_url_guard_rejects_domains_resolving_to_private_ip() -> None:
    with pytest.raises(ValueError, match="non-public"):
        await validate_browser_url(
            "https://internal.example/",
            allow_local_network=False,
            resolver=private_resolver,
        )


@pytest.mark.asyncio
async def test_local_development_can_explicitly_allow_local_address() -> None:
    await validate_browser_url(
        "http://127.0.0.1:8080/",
        allow_local_network=True,
    )


@pytest.mark.asyncio
async def test_driver_opens_and_reads_a_page() -> None:
    page_mock = MagicMock()
    page = cast(Page, page_mock)
    response = MagicMock(status=200)
    page_mock.goto = AsyncMock(return_value=response)
    body = MagicMock()
    body.count = AsyncMock(return_value=1)
    body.inner_text = AsyncMock(return_value="Example page")
    controls = MagicMock()
    controls.evaluate_all = AsyncMock(
        return_value=[
            {
                "tag": "input",
                "type": "text",
                "name": "first_name",
                "label": "First name",
                "required": True,
                "disabled": False,
                "options": [],
            }
        ]
    )
    page_mock.locator = MagicMock(
        side_effect=lambda selector: body if selector == "body" else controls
    )
    page_mock.url = "https://example.com/form"
    page_mock.title = AsyncMock(return_value="Example form")
    runner = PlaywrightBrowserStepRunner()
    runner._page = page
    task = AgentTask(
        id=uuid4(),
        instruction="Проверить страницу",
        target_url="https://example.com/",
        person_ids=(uuid4(),),
        created_at=NOW,
    )

    opened = await runner.run(
        task,
        TaskPlanStep(
            step_id="open",
            action=BrowserAction.NAVIGATE,
            summary="Open",
            target_url="https://example.com/",
        ),
    )
    inspected = await runner.run(
        task,
        TaskPlanStep(
            step_id="read",
            action=BrowserAction.READ_PAGE,
            summary="Read",
        ),
    )

    assert opened.succeeded
    assert inspected.succeeded
    assert inspected.page_snapshot is not None
    assert inspected.page_snapshot.title == "Example form"
    assert inspected.page_snapshot.controls[0].field_name == "first_name"
    assert "value" not in inspected.page_snapshot.controls[0].model_dump()


@pytest.mark.asyncio
async def test_driver_fails_closed_for_unmapped_option_selection() -> None:
    runner = PlaywrightBrowserStepRunner()
    runner._page = cast(Page, MagicMock())
    task = AgentTask(
        id=uuid4(),
        instruction="Выбрать билет",
        target_url="https://example.com/",
        person_ids=(uuid4(),),
        created_at=NOW,
    )

    result = await runner.run(
        task,
        TaskPlanStep(
            step_id="select",
            action=BrowserAction.SELECT_OPTION,
            summary="Select",
        ),
    )

    assert not result.succeeded
    assert result.reason_code == "task_intent_mapping_unavailable"


@pytest.mark.asyncio
async def test_driver_applies_unambiguous_task_intent_without_submitting() -> None:
    person = Identity(
        id=uuid4(),
        display_name="Ivan Petrov",
        first_name="Ivan",
        last_name="Petrov",
        birth_date=date(1990, 1, 2),
    )
    snapshot_controls = (
        BrowserPageControl(
            control_id="control_1",
            kind=BrowserControlKind.SELECT,
            label="Destination",
            options=("Moscow", "Kazan"),
        ),
        BrowserPageControl(
            control_id="control_2",
            kind=BrowserControlKind.DATE,
            label="Travel date",
        ),
        BrowserPageControl(
            control_id="control_3",
            kind=BrowserControlKind.NUMBER,
            label="Ticket count",
        ),
        BrowserPageControl(
            control_id="control_4",
            kind=BrowserControlKind.RADIO,
            label="Evening train",
        ),
        BrowserPageControl(
            control_id="control_5",
            kind=BrowserControlKind.BUTTON,
            label="Search",
        ),
    )
    raw_controls = [
        {
            "tag": (
                "select"
                if control.kind is BrowserControlKind.SELECT
                else (
                    "button"
                    if control.kind is BrowserControlKind.BUTTON
                    else "input"
                )
            ),
            "type": (
                "submit"
                if control.kind is BrowserControlKind.BUTTON
                else control.kind.value
            ),
            "name": "",
            "label": control.label,
            "required": False,
            "disabled": False,
            "options": list(control.options),
        }
        for control in snapshot_controls
    ]
    locators = [MagicMock() for _ in snapshot_controls]
    for locator in locators:
        locator.is_visible = AsyncMock(return_value=True)
        locator.fill = AsyncMock()
        locator.check = AsyncMock()
        locator.select_option = AsyncMock()
        locator.click = AsyncMock()
        locator.evaluate = AsyncMock(return_value="https://example.com/form")
    controls = MagicMock()
    controls.all = AsyncMock(return_value=locators)
    controls.evaluate_all = AsyncMock(return_value=raw_controls)
    page_mock = MagicMock()
    page = cast(Page, page_mock)
    page_mock.locator = MagicMock(return_value=controls)
    page_mock.url = "https://example.com/form"
    page_mock.title = AsyncMock(return_value="Intent form")
    page_mock.wait_for_load_state = AsyncMock()
    task = AgentTask(
        id=uuid4(),
        instruction="Buy one ticket to Kazan",
        target_url="https://example.com/",
        person_ids=(person.id,),
        intent=TaskIntent(
            destination="Kazan",
            requested_date=date(2026, 10, 4),
            requested_quantity=1,
            participant_count=1,
            search_terms=("Evening train",),
        ),
        page_snapshot=BrowserPageSnapshot(
            url=page_mock.url,
            captured_at=NOW,
            controls=snapshot_controls,
        ),
        created_at=NOW,
    )
    runner = PlaywrightBrowserStepRunner(identities=(person,))
    runner._page = page

    result = await runner.run(
        task,
        TaskPlanStep(
            step_id="choose_option",
            action=BrowserAction.SELECT_OPTION,
            summary="Apply intent",
        ),
    )

    assert result.succeeded
    assert result.reason_code == "task_intent_applied"
    locators[0].select_option.assert_awaited_once_with(
        label="Kazan", timeout=30_000
    )
    locators[1].fill.assert_awaited_once_with("2026-10-04", timeout=30_000)
    locators[2].fill.assert_awaited_once_with("1", timeout=30_000)
    locators[3].check.assert_awaited_once_with(timeout=30_000)

    review = await runner.run(
        task,
        TaskPlanStep(
            step_id="open_review",
            action=BrowserAction.PREPARE_REVIEW,
            summary="Open review",
        ),
    )

    assert review.succeeded
    assert review.page_snapshot is not None
    locators[4].click.assert_awaited_once_with(timeout=30_000)


def test_option_matching_rejects_ambiguous_partial_matches() -> None:
    assert (
        _unique_matching_option(
            ("Kazan central", "Kazan airport"),
            "Kazan",
        )
        is None
    )
    assert _unique_matching_option(("Moscow", "Kazan"), "kazan") == "Kazan"


def test_fuzzy_movie_matching_ignores_punctuation_and_allows_short_title() -> None:
    controls = (
        BrowserPageControl(
            control_id="control_1",
            kind=BrowserControlKind.LINK,
            label="Последний богатырь. Колобок",
        ),
        BrowserPageControl(
            control_id="control_2",
            kind=BrowserControlKind.LINK,
            label="Смешарики сквозь вселенные",
        ),
    )

    assert _fuzzy_label_score(
        "Последний богатырь Колобок", "Последний богатырь. Колобок"
    ) == 1.0
    assert _best_matching_link(controls, ("Колобок",)) == 0


def test_fuzzy_movie_matching_refuses_ambiguous_short_title() -> None:
    controls = (
        BrowserPageControl(
            control_id="control_1",
            kind=BrowserControlKind.LINK,
            label="Колобок возвращается",
        ),
        BrowserPageControl(
            control_id="control_2",
            kind=BrowserControlKind.LINK,
            label="Колобок в космосе",
        ),
    )

    assert _best_matching_link(controls, ("Колобок",)) is None


@pytest.mark.parametrize(
    "label",
    ["Pay", "Continue to payment", "Купить", "Подтвердить заказ"],
)
def test_review_button_filter_rejects_irreversible_labels(label: str) -> None:
    assert not _is_safe_review_button(label)


@pytest.mark.asyncio
async def test_driver_fills_basic_fields_but_skips_document_values() -> None:
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
    page_mock = MagicMock()
    page = cast(Page, page_mock)
    first_name = MagicMock()
    first_name.fill = AsyncMock()
    passport = MagicMock()
    passport.fill = AsyncMock()
    first_name.is_visible = AsyncMock(return_value=True)
    passport.is_visible = AsyncMock(return_value=True)
    controls = MagicMock()
    controls.all = AsyncMock(return_value=[first_name, passport])
    controls.evaluate_all = AsyncMock(
        return_value=[
            {
                "tag": "input",
                "type": "text",
                "name": "",
                "label": "First name",
                "required": False,
                "disabled": False,
                "options": [],
            },
            {
                "tag": "input",
                "type": "text",
                "name": "",
                "label": "Passport number",
                "required": False,
                "disabled": False,
                "options": [],
            },
        ]
    )
    page_mock.locator = MagicMock(return_value=controls)
    page_mock.url = "https://example.com/form"
    page_mock.title = AsyncMock(return_value="Untitled page")
    task = AgentTask(
        id=uuid4(),
        instruction="Купить билет на поезд",
        target_url="https://example.com/",
        person_ids=(person.id,),
        page_snapshot=BrowserPageSnapshot(
            url=page_mock.url,
            captured_at=NOW,
            controls=(
                BrowserPageControl(
                    control_id="control_1",
                    kind=BrowserControlKind.TEXT,
                    label="First name",
                ),
                BrowserPageControl(
                    control_id="control_2",
                    kind=BrowserControlKind.TEXT,
                    label="Passport number",
                ),
            ),
        ),
        created_at=NOW,
    )
    runner = PlaywrightBrowserStepRunner(identities=(person,))
    runner._page = page

    result = await runner.run(
        task,
        TaskPlanStep(
            step_id="fill_people",
            action=BrowserAction.FILL_BASIC_PROFILE,
            summary="Fill basic profile",
        ),
    )

    assert result.succeeded
    assert result.page_fill_plan is not None
    first_name.fill.assert_awaited_once_with("Ivan", timeout=30_000)
    passport.fill.assert_not_awaited()

    approved_task = task.model_copy(
        update={"page_fill_plan": result.page_fill_plan}
    )
    sensitive_result = await runner.run(
        approved_task,
        TaskPlanStep(
            step_id="fill_documents",
            action=BrowserAction.FILL_SENSITIVE_PROFILE,
            summary="Fill approved document",
        ),
    )

    assert sensitive_result.succeeded
    passport.fill.assert_awaited_once_with("1234567890", timeout=30_000)
