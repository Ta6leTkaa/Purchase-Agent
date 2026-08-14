import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    Request,
    Route,
    async_playwright,
)

from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)
from app.domain.task import AgentTask
from app.domain.task_permission import BrowserAction
from app.domain.task_plan import TaskPlanStep
from app.services.clock import utc_now
from app.services.task_executor import BrowserStepResult

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
HostResolver = Callable[[str, int], Awaitable[set[IPAddress]]]


class PlaywrightBrowserStepRunner:
    """A minimal real browser driver; ambiguous form actions fail closed."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        allow_local_network: bool = False,
    ) -> None:
        self._timeout_ms = timeout_seconds * 1_000
        self._allow_local_network = allow_local_network
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> "PlaywrightBrowserStepRunner":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        context = await self._browser.new_context(
            accept_downloads=False,
            java_script_enabled=True,
        )
        await context.route("**/*", self._guard_request)
        self._page = await context.new_page()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def run(self, task: AgentTask, step: TaskPlanStep) -> BrowserStepResult:
        page = self._require_page()
        if step.action is BrowserAction.NAVIGATE:
            if step.target_url is None:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="navigation_target_missing",
                )
            response = await page.goto(
                step.target_url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            if response is None:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="navigation_no_response",
                )
            if response.status >= 400:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="navigation_http_error",
                )
            return BrowserStepResult(
                succeeded=True,
                reason_code="page_opened",
            )
        if step.action is BrowserAction.READ_PAGE:
            body = page.locator("body")
            if await body.count() != 1:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="page_body_missing",
                )
            await body.inner_text(timeout=self._timeout_ms)
            snapshot = await self._snapshot_page(page)
            return BrowserStepResult(
                succeeded=True,
                reason_code="page_inspected",
                page_snapshot=snapshot,
            )
        if step.action is BrowserAction.SELECT_OPTION:
            return BrowserStepResult(
                succeeded=False,
                reason_code="option_selection_requires_mapping",
            )
        if step.action in {
            BrowserAction.FILL_BASIC_PROFILE,
            BrowserAction.FILL_SENSITIVE_PROFILE,
        }:
            return BrowserStepResult(
                succeeded=False,
                reason_code="form_mapping_required",
            )
        return BrowserStepResult(
            succeeded=False,
            reason_code="browser_action_not_implemented",
        )

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Playwright browser runner is not started")
        return self._page

    async def _snapshot_page(self, page: Page) -> BrowserPageSnapshot:
        raw_controls: list[dict[str, Any]] = await page.locator(
            "input, select, textarea, button, a[href]"
        ).evaluate_all(_CONTROL_INVENTORY_SCRIPT)
        controls = tuple(
            BrowserPageControl(
                control_id=f"control_{index}",
                kind=_control_kind(item),
                label=str(
                    item.get("label")
                    or f"Unlabelled {item.get('tag', 'control')}"
                ),
                field_name=(str(item["name"]) if item.get("name") else None),
                required=bool(item.get("required")),
                disabled=bool(item.get("disabled")),
                options=tuple(str(option) for option in item.get("options", ())),
            )
            for index, item in enumerate(raw_controls[:300], start=1)
        )
        return BrowserPageSnapshot(
            url=page.url,
            title=await page.title() or "Untitled page",
            captured_at=utc_now(),
            controls=controls,
        )

    async def _guard_request(self, route: Route, request: Request) -> None:
        try:
            await validate_browser_url(
                request.url,
                allow_local_network=self._allow_local_network,
            )
        except ValueError:
            await route.abort("blockedbyclient")
            return
        await route.continue_()


async def validate_browser_url(
    url: str,
    *,
    allow_local_network: bool,
    resolver: HostResolver | None = None,
) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("browser URL must use HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("browser URL must not contain credentials")
    host = parsed.hostname.casefold()
    addresses: set[IPAddress]
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        resolve = resolver or _resolve_host
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await resolve(host, port)
    if not addresses:
        raise ValueError("browser URL host did not resolve")
    if not allow_local_network and any(not address.is_global for address in addresses):
        raise ValueError("browser URL resolves to a non-public address")


async def _resolve_host(
    host: str,
    port: int,
) -> set[IPAddress]:
    records = await asyncio.to_thread(
        socket.getaddrinfo,
        host,
        port,
        type=socket.SOCK_STREAM,
    )
    return {ipaddress.ip_address(record[4][0]) for record in records}


def _control_kind(item: dict[str, Any]) -> BrowserControlKind:
    tag = str(item.get("tag", "")).casefold()
    input_type = str(item.get("type", "")).casefold()
    if tag == "select":
        return BrowserControlKind.SELECT
    if tag == "button" or input_type in {"button", "submit", "reset"}:
        return BrowserControlKind.BUTTON
    if tag == "a":
        return BrowserControlKind.LINK
    if input_type == "radio":
        return BrowserControlKind.RADIO
    if input_type == "checkbox":
        return BrowserControlKind.CHECKBOX
    if input_type == "date":
        return BrowserControlKind.DATE
    if input_type == "email":
        return BrowserControlKind.EMAIL
    if input_type == "tel":
        return BrowserControlKind.TEL
    if input_type == "number":
        return BrowserControlKind.NUMBER
    if tag == "textarea" or input_type in {"", "text", "search"}:
        return BrowserControlKind.TEXT
    return BrowserControlKind.OTHER


_CONTROL_INVENTORY_SCRIPT = """
elements => elements
  .filter(element => element.getClientRects().length > 0)
  .slice(0, 300)
  .map(element => {
    const clean = value => (value || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
    const label = clean(
      element.getAttribute('aria-label') ||
      (element.labels && element.labels[0] && element.labels[0].innerText) ||
      element.getAttribute('placeholder') ||
      (
        (element.tagName === 'BUTTON' || element.tagName === 'A') &&
        element.innerText
      ) ||
      element.getAttribute('name') ||
      element.id
    );
    return {
      tag: element.tagName.toLowerCase(),
      type: element.getAttribute('type') || '',
      name: clean(element.getAttribute('name')) || null,
      label,
      required:
        element.required === true ||
        element.getAttribute('aria-required') === 'true',
      disabled:
        element.disabled === true ||
        element.getAttribute('aria-disabled') === 'true',
      options: element.tagName === 'SELECT'
        ? Array.from(element.options)
            .slice(0, 100)
            .map(option => clean(option.textContent))
            .filter(Boolean)
        : []
    };
  })
"""
