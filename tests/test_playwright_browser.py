import ipaddress
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from playwright.async_api import Page

from app.adapters.playwright_browser import (
    PlaywrightBrowserStepRunner,
    validate_browser_url,
)
from app.domain.task import AgentTask
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
    page_mock.locator = MagicMock(return_value=body)
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
    assert result.reason_code == "option_selection_requires_mapping"
