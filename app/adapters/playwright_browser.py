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
from app.domain.identity import Identity
from app.domain.page_fill_plan import PageFillPlan, ProfileField
from app.domain.task import AgentTask
from app.domain.task_permission import BrowserAction
from app.domain.task_plan import TaskPlanStep
from app.services.clock import utc_now
from app.services.page_field_mapper import build_page_fill_plan
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
        identities: tuple[Identity, ...] = (),
    ) -> None:
        self._timeout_ms = timeout_seconds * 1_000
        self._allow_local_network = allow_local_network
        self._identities = identities
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
        if step.action is BrowserAction.FILL_BASIC_PROFILE:
            return await self._fill_basic_profile(page, task)
        if step.action is BrowserAction.FILL_SENSITIVE_PROFILE:
            return await self._fill_sensitive_profile(page, task)
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

    async def _fill_basic_profile(
        self,
        page: Page,
        task: AgentTask,
    ) -> BrowserStepResult:
        if not self._identities or task.page_snapshot is None:
            return BrowserStepResult(
                succeeded=False,
                reason_code="basic_profile_mapping_unavailable",
            )
        fill_plan = task.page_fill_plan or build_page_fill_plan(
            task, self._identities, utc_now()
        )
        if not await self._page_matches_snapshot(page, task):
            return BrowserStepResult(
                succeeded=False,
                reason_code="page_changed_since_mapping",
            )
        return await self._fill_profile_bindings(
            page,
            fill_plan,
            sensitive=False,
        )

    async def _fill_sensitive_profile(
        self,
        page: Page,
        task: AgentTask,
    ) -> BrowserStepResult:
        if (
            not self._identities
            or task.page_snapshot is None
            or task.page_fill_plan is None
        ):
            return BrowserStepResult(
                succeeded=False,
                reason_code="sensitive_profile_mapping_unavailable",
            )
        if page.url != task.page_snapshot.url:
            snapshot_url = urlsplit(task.page_snapshot.url)
            target_url = urlsplit(task.target_url)
            if (snapshot_url.scheme, snapshot_url.netloc) != (
                target_url.scheme,
                target_url.netloc,
            ):
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_page_origin_changed",
                )
            response = await page.goto(
                task.page_snapshot.url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            if response is None or response.status >= 400:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_page_restore_failed",
                )
        if not await self._page_matches_snapshot(page, task):
            return BrowserStepResult(
                succeeded=False,
                reason_code="page_changed_since_mapping",
            )
        basic_result = await self._fill_profile_bindings(
            page,
            task.page_fill_plan,
            sensitive=False,
            allow_empty=True,
        )
        if not basic_result.succeeded:
            return basic_result
        return await self._fill_profile_bindings(
            page,
            task.page_fill_plan,
            sensitive=True,
        )

    async def _page_matches_snapshot(self, page: Page, task: AgentTask) -> bool:
        if task.page_snapshot is None:
            return False
        current_snapshot = await self._snapshot_page(page)
        return (
            current_snapshot.url == task.page_snapshot.url
            and current_snapshot.controls == task.page_snapshot.controls
        )

    async def _fill_profile_bindings(
        self,
        page: Page,
        fill_plan: PageFillPlan,
        *,
        sensitive: bool,
        allow_empty: bool = False,
    ) -> BrowserStepResult:
        identity_by_id = {identity.id: identity for identity in self._identities}
        bindings = tuple(
            binding
            for binding in fill_plan.bindings
            if binding.sensitive is sensitive
        )
        if not bindings:
            return BrowserStepResult(
                succeeded=allow_empty,
                reason_code=(
                    "profile_fields_not_required"
                    if allow_empty
                    else "profile_mapping_empty"
                ),
                page_fill_plan=fill_plan,
            )
        controls = page.locator("input, select, textarea, button, a[href]")
        visible_controls = [
            control for control in await controls.all() if await control.is_visible()
        ]
        for binding in bindings:
            identity = identity_by_id.get(binding.person_id)
            if identity is None:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_person_missing",
                    page_fill_plan=fill_plan,
                )
            index = int(binding.control_id.removeprefix("control_")) - 1
            if index >= len(visible_controls):
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_control_missing",
                    page_fill_plan=fill_plan,
                )
            value = _profile_value(identity, binding.profile_field)
            if value is None:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_profile_value_missing",
                    page_fill_plan=fill_plan,
                )
            await visible_controls[index].fill(value, timeout=self._timeout_ms)
        return BrowserStepResult(
            succeeded=True,
            reason_code=(
                "sensitive_profile_filled"
                if sensitive
                else "basic_profile_filled"
            ),
            page_fill_plan=fill_plan,
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


def _profile_value(identity: Identity, field: ProfileField) -> str | None:
    if field is ProfileField.FIRST_NAME:
        return identity.first_name
    if field is ProfileField.LAST_NAME:
        return identity.last_name
    if field is ProfileField.BIRTH_DATE:
        return identity.birth_date.isoformat()
    if field is ProfileField.DOCUMENT_NUMBER:
        return identity.documents[0].number if identity.documents else None
    return None


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
