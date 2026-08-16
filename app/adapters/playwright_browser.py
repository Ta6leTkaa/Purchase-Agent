import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
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

from app.domain.browser_command import (
    AgentDecision,
    AskUserCommand,
    ClickCommand,
    CommandExecutionResult,
    FillCommand,
    FillValueSource,
    FinishCommand,
    GoBackCommand,
    OpenUrlCommand,
    ScrollCommand,
    SelectCommand,
    WaitCommand,
)
from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)
from app.domain.identity import Identity
from app.domain.page_fill_plan import IntentField, PageFillPlan, ProfileField
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
        if step.action is not BrowserAction.NAVIGATE and page.url == "about:blank":
            await page.goto(
                task.page_snapshot.url if task.page_snapshot else task.target_url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
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
            return await self._apply_task_intent(page, task)
        if step.action is BrowserAction.PREPARE_REVIEW:
            return await self._prepare_review(page, task)
        if step.action is BrowserAction.FILL_BASIC_PROFILE:
            return await self._fill_basic_profile(page, task)
        if step.action is BrowserAction.FILL_SENSITIVE_PROFILE:
            return await self._fill_sensitive_profile(page, task)
        return BrowserStepResult(
            succeeded=False,
            reason_code="browser_action_not_implemented",
        )

    async def execute_command(
        self,
        task: AgentTask,
        decision: AgentDecision,
        *,
        approved_sensitive: bool = False,
    ) -> CommandExecutionResult:
        """Execute one validated model command against the current page."""
        page = self._require_page()
        command = decision.command
        if isinstance(command, AskUserCommand):
            return CommandExecutionResult(
                succeeded=False,
                reason_code="user_input_required",
                requires_user=True,
            )
        if isinstance(command, FinishCommand):
            return CommandExecutionResult(
                succeeded=True,
                reason_code=f"finished_{command.outcome.value}",
                page_snapshot=(
                    await self._snapshot_page(page)
                    if page.url != "about:blank"
                    else None
                ),
                requires_user=command.outcome.value in {
                    "ready_for_user",
                    "needs_user",
                },
            )
        if page.url == "about:blank":
            await page.goto(
                task.page_snapshot.url if task.page_snapshot else task.target_url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
        if isinstance(command, OpenUrlCommand):
            if not _same_origin(command.url, task.target_url):
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="cross_origin_navigation_blocked",
                    requires_user=True,
                )
            response = await page.goto(
                command.url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            if response is None or response.status >= 400:
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="navigation_failed",
                )
            return await self._command_succeeded(page, "url_opened")
        if isinstance(command, GoBackCommand):
            await page.go_back(
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            if not _same_origin(page.url, task.target_url):
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="cross_origin_navigation_blocked",
                    requires_user=True,
                )
            return await self._command_succeeded(page, "went_back")
        if isinstance(command, ScrollCommand):
            delta = command.amount if command.direction == "down" else -command.amount
            await page.mouse.wheel(0, delta)
            return await self._command_succeeded(page, "page_scrolled")
        if isinstance(command, WaitCommand):
            await page.wait_for_timeout(command.seconds * 1_000)
            return await self._command_succeeded(page, "page_waited")

        snapshot = await self._snapshot_page(page)
        control_id = command.control_id
        control = next(
            (item for item in snapshot.controls if item.control_id == control_id),
            None,
        )
        if control is None:
            return CommandExecutionResult(
                succeeded=False,
                reason_code="control_not_found",
            )
        locator = page.locator(
            f'[data-purchase-agent-control="{control_id}"]'
        )
        if await locator.count() != 1 or not await locator.is_visible():
            return CommandExecutionResult(
                succeeded=False,
                reason_code="control_not_actionable",
            )
        if isinstance(command, ClickCommand):
            if control.kind not in {
                BrowserControlKind.BUTTON,
                BrowserControlKind.LINK,
                BrowserControlKind.CLICKABLE,
                BrowserControlKind.RADIO,
                BrowserControlKind.CHECKBOX,
            }:
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="control_not_clickable",
                )
            if any(
                term in _normalize_option(control.label)
                for term in _BLOCKED_BUTTON_TERMS
            ):
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="irreversible_click_requires_user",
                    page_snapshot=snapshot,
                    requires_user=True,
                )
            target = await locator.evaluate(_CLICK_TARGET_SCRIPT)
            if target is not None and not _same_origin(str(target), task.target_url):
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="cross_origin_navigation_blocked",
                    page_snapshot=snapshot,
                    requires_user=True,
                )
            await locator.click(timeout=self._timeout_ms)
            await page.wait_for_timeout(300)
            return await self._command_succeeded(page, "control_clicked")
        if isinstance(command, SelectCommand):
            if control.kind is not BrowserControlKind.SELECT:
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="control_not_select",
                )
            option = _unique_matching_option(control.options, command.option_text)
            if option is None:
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="select_option_not_unique",
                )
            await locator.select_option(label=option, timeout=self._timeout_ms)
            return await self._command_succeeded(page, "option_selected")
        if isinstance(command, FillCommand):
            if control.kind not in {
                BrowserControlKind.TEXT,
                BrowserControlKind.DATE,
                BrowserControlKind.EMAIL,
                BrowserControlKind.TEL,
                BrowserControlKind.NUMBER,
                BrowserControlKind.OTHER,
            }:
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="control_not_fillable",
                )
            if command.value_source is FillValueSource.DOCUMENT_NUMBER:
                if not approved_sensitive:
                    return CommandExecutionResult(
                        succeeded=False,
                        reason_code="sensitive_data_approval_required",
                        page_snapshot=snapshot,
                        requires_user=True,
                    )
            value = self._command_fill_value(command)
            if value is None:
                return CommandExecutionResult(
                    succeeded=False,
                    reason_code="fill_value_unavailable",
                )
            await locator.fill(value, timeout=self._timeout_ms)
            return await self._command_succeeded(page, "control_filled")
        return CommandExecutionResult(
            succeeded=False,
            reason_code="command_not_implemented",
        )

    def _command_fill_value(self, command: FillCommand) -> str | None:
        if command.value_source is FillValueSource.LITERAL:
            return command.literal_value
        if not self._identities:
            return None
        identity = self._identities[0]
        if command.value_source is FillValueSource.FIRST_NAME:
            return identity.first_name
        if command.value_source is FillValueSource.LAST_NAME:
            return identity.last_name
        if command.value_source is FillValueSource.BIRTH_DATE:
            return identity.birth_date.isoformat()
        if command.value_source is FillValueSource.DOCUMENT_NUMBER:
            return identity.documents[0].number if identity.documents else None
        return None

    async def _command_succeeded(
        self,
        page: Page,
        reason_code: str,
    ) -> CommandExecutionResult:
        return CommandExecutionResult(
            succeeded=True,
            reason_code=reason_code,
            page_snapshot=await self._snapshot_page(page),
        )

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Playwright browser runner is not started")
        return self._page

    async def _snapshot_page(self, page: Page) -> BrowserPageSnapshot:
        raw_controls: list[dict[str, Any]] = await page.locator(
            "body *"
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
                role=(str(item["role"]) if item.get("role") else None),
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
        restore_error = await self._ensure_mapped_page(page, task)
        if restore_error is not None:
            return BrowserStepResult(
                succeeded=False,
                reason_code=restore_error,
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

    async def _apply_task_intent(
        self,
        page: Page,
        task: AgentTask,
    ) -> BrowserStepResult:
        if task.intent is None or task.page_snapshot is None:
            return BrowserStepResult(
                succeeded=False,
                reason_code="task_intent_mapping_unavailable",
            )
        if task.intent.issues:
            return BrowserStepResult(
                succeeded=False,
                reason_code="task_intent_requires_clarification",
            )
        fill_plan = task.page_fill_plan or build_page_fill_plan(
            task, self._identities, utc_now()
        )
        if not fill_plan.intent_bindings:
            return await self._follow_matching_option(page, task, fill_plan)
        restore_error = await self._ensure_mapped_page(page, task)
        if restore_error is not None:
            return BrowserStepResult(
                succeeded=False,
                reason_code=restore_error,
                page_fill_plan=fill_plan,
            )
        controls = page.locator(_ANNOTATED_CONTROL_SELECTOR)
        visible_controls = [
            control for control in await controls.all() if await control.is_visible()
        ]
        snapshot_controls = {
            control.control_id: control for control in task.page_snapshot.controls
        }
        for binding in fill_plan.intent_bindings:
            index = int(binding.control_id.removeprefix("control_")) - 1
            if index >= len(visible_controls):
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_control_missing",
                    page_fill_plan=fill_plan,
                )
            control = snapshot_controls[binding.control_id]
            desired = _intent_value(
                task,
                binding.intent_field,
                binding.search_term_index,
            )
            if desired is None:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_intent_value_missing",
                    page_fill_plan=fill_plan,
                )
            locator = visible_controls[index]
            if control.kind is BrowserControlKind.SELECT:
                option = _unique_matching_option(control.options, desired)
                if option is None:
                    return BrowserStepResult(
                        succeeded=False,
                        reason_code="select_option_not_unique",
                        page_fill_plan=fill_plan,
                    )
                await locator.select_option(label=option, timeout=self._timeout_ms)
            elif control.kind in {
                BrowserControlKind.RADIO,
                BrowserControlKind.CHECKBOX,
            }:
                await locator.check(timeout=self._timeout_ms)
            elif control.kind in {
                BrowserControlKind.TEXT,
                BrowserControlKind.DATE,
                BrowserControlKind.NUMBER,
                BrowserControlKind.EMAIL,
                BrowserControlKind.TEL,
                BrowserControlKind.OTHER,
            }:
                await locator.fill(desired, timeout=self._timeout_ms)
            else:
                return BrowserStepResult(
                    succeeded=False,
                    reason_code="mapped_control_not_editable",
                    page_fill_plan=fill_plan,
                )
        return BrowserStepResult(
            succeeded=True,
            reason_code="task_intent_applied",
            page_fill_plan=fill_plan,
        )

    async def _follow_matching_option(
        self,
        page: Page,
        task: AgentTask,
        fill_plan: PageFillPlan,
    ) -> BrowserStepResult:
        match_index = _best_matching_link(
            task.page_snapshot.controls if task.page_snapshot else (),
            task.intent.search_terms if task.intent else (),
        )
        if match_index is None:
            return BrowserStepResult(
                succeeded=False,
                reason_code="task_intent_mapping_empty",
                page_fill_plan=fill_plan,
            )
        await self._snapshot_page(page)
        controls = page.locator(_ANNOTATED_CONTROL_SELECTOR)
        visible_controls = [
            control for control in await controls.all() if await control.is_visible()
        ]
        if match_index >= len(visible_controls):
            return BrowserStepResult(
                succeeded=False,
                reason_code="matched_option_missing",
                page_fill_plan=fill_plan,
            )
        link = visible_controls[match_index]
        target = await link.evaluate(_LINK_TARGET_SCRIPT)
        if target is None or not _same_origin(str(target), task.target_url):
            return BrowserStepResult(
                succeeded=False,
                reason_code="matched_option_cross_origin",
                page_fill_plan=fill_plan,
            )
        await link.click(timeout=self._timeout_ms)
        await page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        if not _same_origin(page.url, task.target_url):
            return BrowserStepResult(
                succeeded=False,
                reason_code="matched_option_cross_origin",
                page_fill_plan=fill_plan,
            )
        return BrowserStepResult(
            succeeded=True,
            reason_code="matching_option_opened",
            page_snapshot=await self._snapshot_page(page),
        )

    async def _ensure_mapped_page(
        self,
        page: Page,
        task: AgentTask,
    ) -> str | None:
        if task.page_snapshot is None:
            return "page_snapshot_missing"
        if page.url != task.page_snapshot.url:
            snapshot_url = urlsplit(task.page_snapshot.url)
            target_url = urlsplit(task.target_url)
            if (snapshot_url.scheme, snapshot_url.netloc) != (
                target_url.scheme,
                target_url.netloc,
            ):
                return "mapped_page_origin_changed"
            response = await page.goto(
                task.page_snapshot.url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            if response is None or response.status >= 400:
                return "mapped_page_restore_failed"
        if not await self._page_matches_snapshot(page, task):
            return "page_changed_since_mapping"
        return None

    async def _prepare_review(
        self,
        page: Page,
        task: AgentTask,
    ) -> BrowserStepResult:
        if task.page_snapshot is None:
            return BrowserStepResult(
                succeeded=False,
                reason_code="page_snapshot_missing",
            )
        if page.url != task.page_snapshot.url:
            if not _same_origin(task.page_snapshot.url, task.target_url):
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
        current_snapshot = await self._snapshot_page(page)
        candidates = [
            (index, control)
            for index, control in enumerate(current_snapshot.controls)
            if control.kind is BrowserControlKind.BUTTON
            and not control.disabled
            and _is_safe_review_button(control.label)
        ]
        if not candidates:
            return BrowserStepResult(
                succeeded=False,
                reason_code="review_action_not_available",
            )
        if len(candidates) != 1:
            return BrowserStepResult(
                succeeded=False,
                reason_code="review_button_not_unique",
            )
        index, _ = candidates[0]
        controls = page.locator(_ANNOTATED_CONTROL_SELECTOR)
        visible_controls = [
            control for control in await controls.all() if await control.is_visible()
        ]
        if index >= len(visible_controls):
            return BrowserStepResult(
                succeeded=False,
                reason_code="review_button_missing",
            )
        button = visible_controls[index]
        target = await button.evaluate(_FORM_TARGET_SCRIPT)
        if target is not None and not _same_origin(str(target), task.target_url):
            return BrowserStepResult(
                succeeded=False,
                reason_code="review_button_cross_origin",
            )
        await button.click(timeout=self._timeout_ms)
        await page.wait_for_load_state(
            "domcontentloaded",
            timeout=self._timeout_ms,
        )
        if not _same_origin(page.url, task.target_url):
            return BrowserStepResult(
                succeeded=False,
                reason_code="review_navigation_cross_origin",
            )
        return BrowserStepResult(
            succeeded=True,
            reason_code="review_page_opened",
            page_snapshot=await self._snapshot_page(page),
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
        controls = page.locator(_ANNOTATED_CONTROL_SELECTOR)
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
    role = str(item.get("role", "")).casefold()
    if tag == "select":
        return BrowserControlKind.SELECT
    if (
        tag == "button"
        or role == "button"
        or input_type in {"button", "submit", "reset"}
    ):
        return BrowserControlKind.BUTTON
    if tag == "a" or role == "link":
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
        if tag not in {"input", "textarea"}:
            return BrowserControlKind.CLICKABLE
        return BrowserControlKind.TEXT
    if role in {"tab", "option", "menuitem", "switch"} or item.get("clickable"):
        return BrowserControlKind.CLICKABLE
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


def _intent_value(
    task: AgentTask,
    field: IntentField,
    search_term_index: int | None,
) -> str | None:
    intent = task.intent
    if intent is None:
        return None
    if field is IntentField.ORIGIN:
        return intent.origin
    if field is IntentField.DESTINATION:
        return intent.destination
    if field is IntentField.REQUESTED_DATE:
        return intent.requested_date.isoformat() if intent.requested_date else None
    if field is IntentField.EARLIEST_TIME:
        return intent.earliest_time.strftime("%H:%M") if intent.earliest_time else None
    if field is IntentField.LATEST_TIME:
        return intent.latest_time.strftime("%H:%M") if intent.latest_time else None
    if field is IntentField.REQUESTED_QUANTITY:
        return str(intent.requested_quantity) if intent.requested_quantity else None
    if field is IntentField.SEARCH_TERM and search_term_index is not None:
        if search_term_index < len(intent.search_terms):
            return intent.search_terms[search_term_index]
    return None


def _unique_matching_option(options: tuple[str, ...], desired: str) -> str | None:
    normalized_desired = _normalize_option(desired)
    exact = [
        option
        for option in options
        if _normalize_option(option) == normalized_desired
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        option
        for option in options
        if normalized_desired in _normalize_option(option)
        or _normalize_option(option) in normalized_desired
    ]
    return partial[0] if len(partial) == 1 else None


def _best_matching_link(
    controls: tuple[BrowserPageControl, ...],
    search_terms: tuple[str, ...],
) -> int | None:
    if not search_terms:
        return None
    candidates: list[tuple[float, int]] = []
    for index, control in enumerate(controls):
        if control.kind is not BrowserControlKind.LINK or control.disabled:
            continue
        score = max(
            (_fuzzy_label_score(term, control.label) for term in search_terms),
            default=0.0,
        )
        if score >= 0.72:
            candidates.append((score, index))
    candidates.sort(reverse=True)
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.12:
        return None
    return candidates[0][1]


def _fuzzy_label_score(query: str, candidate: str) -> float:
    normalized_query = _normalize_fuzzy_text(query)
    normalized_candidate = _normalize_fuzzy_text(candidate)
    if not normalized_query or not normalized_candidate:
        return 0.0
    if normalized_query == normalized_candidate:
        return 1.0
    query_tokens = set(normalized_query.split())
    candidate_tokens = set(normalized_candidate.split())
    if query_tokens <= candidate_tokens:
        return 0.94
    overlap = len(query_tokens & candidate_tokens) / len(query_tokens)
    morphological_overlap = sum(
        max(
            SequenceMatcher(None, query_token, candidate_token).ratio()
            for candidate_token in candidate_tokens
        )
        >= 0.78
        for query_token in query_tokens
    ) / len(query_tokens)
    sequence = SequenceMatcher(
        None, normalized_query, normalized_candidate
    ).ratio()
    return max(overlap * 0.88, morphological_overlap * 0.9, sequence)


def _normalize_fuzzy_text(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold().replace("ё", "е")))


def _normalize_option(value: str) -> str:
    return " ".join(value.casefold().split())


def _is_safe_review_button(label: str) -> bool:
    normalized = _normalize_option(label)
    if any(term in normalized for term in _BLOCKED_BUTTON_TERMS):
        return False
    return any(term in normalized for term in _SAFE_REVIEW_BUTTON_TERMS)


def _same_origin(left: str, right: str) -> bool:
    left_url = urlsplit(left)
    right_url = urlsplit(right)
    return (left_url.scheme, left_url.netloc) == (
        right_url.scheme,
        right_url.netloc,
    )


_SAFE_REVIEW_BUTTON_TERMS = (
    "search",
    "find",
    "show",
    "continue",
    "next",
    "review",
    "proceed",
    "найти",
    "показать",
    "продолжить",
    "далее",
    "к результатам",
)
_BLOCKED_BUTTON_TERMS = (
    "pay",
    "payment",
    "purchase",
    "buy",
    "book",
    "confirm",
    "оплатить",
    "оплата",
    "купить",
    "забронировать",
    "подтвердить",
    "заказать",
    "оформить заказ",
)
_FORM_TARGET_SCRIPT = """
element => {
  const form = element.form || element.closest('form');
  const target = element.getAttribute('formaction') || (form && form.action);
  return target ? new URL(target, document.baseURI).href : null;
}
"""
_LINK_TARGET_SCRIPT = "element => element.href || null"
_CLICK_TARGET_SCRIPT = """
element => {
  const form = element.form || element.closest('form');
  const target =
    element.href ||
    element.getAttribute('formaction') ||
    (form && form.action);
  return target ? new URL(target, document.baseURI).href : null;
}
"""

_ANNOTATED_CONTROL_SELECTOR = "[data-purchase-agent-control]"


_CONTROL_INVENTORY_SCRIPT = """
elements => elements
  .filter(element => {
    if (element.getClientRects().length === 0) return false;
    const tag = element.tagName.toLowerCase();
    const role = (element.getAttribute('role') || '').toLowerCase();
    const native = ['input', 'select', 'textarea', 'button', 'a'].includes(tag);
    const semantic = [
      'button', 'link', 'tab', 'option', 'menuitem', 'switch'
    ].includes(role);
    const keyboard = element.tabIndex >= 0;
    const handler =
      typeof element.onclick === 'function' || element.hasAttribute('onclick');
    const pointer = getComputedStyle(element).cursor === 'pointer';
    return native || semantic || keyboard || handler || pointer;
  })
  .map(element => {
    const clean = value => (value || '').replace(/\\s+/g, ' ').trim().slice(0, 200);
    const label = clean(
      element.getAttribute('aria-label') ||
      (element.labels && element.labels[0] && element.labels[0].innerText) ||
      element.getAttribute('placeholder') ||
      element.innerText ||
      element.getAttribute('name') ||
      element.id
    );
    return {
      element,
      tag: element.tagName.toLowerCase(),
      type: element.getAttribute('type') || '',
      role: clean(element.getAttribute('role')) || null,
      name: clean(element.getAttribute('name')) || null,
      label,
      clickable:
        typeof element.onclick === 'function' ||
        element.hasAttribute('onclick') ||
        getComputedStyle(element).cursor === 'pointer',
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
  .filter(item => item.label)
  .slice(0, 300)
  .map((item, index) => {
    item.element.setAttribute('data-purchase-agent-control', `control_${index + 1}`);
    delete item.element;
    return item;
  })
"""
