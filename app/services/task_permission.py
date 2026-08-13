from urllib.parse import urlsplit, urlunsplit

from app.domain.task import AgentTask, UserActionReason
from app.domain.task_permission import (
    BrowserAction,
    BrowserActionRequest,
    PermissionDecision,
)


def evaluate_browser_action(
    task: AgentTask,
    request: BrowserActionRequest,
) -> PermissionDecision:
    """Decide whether a browser step is safe to execute without the user."""
    policy = task.permissions
    if request.action is BrowserAction.PAY or request.creates_charge:
        return _user(UserActionReason.PAYMENT_REQUIRED)
    if request.action is BrowserAction.SOLVE_CAPTCHA:
        return _user(UserActionReason.CAPTCHA_REQUIRED)
    if request.action is BrowserAction.AUTHENTICATE:
        return _user(UserActionReason.AUTHENTICATION_REQUIRED)
    if request.action is BrowserAction.SUBMIT_ORDER or not request.reversible:
        return _user(UserActionReason.CONFIRMATION_REQUIRED)
    if request.action is BrowserAction.FILL_SENSITIVE_PROFILE:
        if policy.require_approval_for_sensitive_data:
            return _user(UserActionReason.SENSITIVE_DATA_APPROVAL_REQUIRED)
        return _allowed("sensitive_data_preapproved")
    if request.action is BrowserAction.NAVIGATE:
        if not policy.allow_navigation:
            return _denied("navigation_not_allowed")
        if request.target_url is None:
            return _denied("navigation_target_required")
        target_origin = _origin(request.target_url)
        if target_origin is None:
            return _denied("invalid_navigation_target")
        if (
            target_origin != task.target_origin
            and policy.require_approval_for_cross_origin_navigation
        ):
            return _user(UserActionReason.CONFIRMATION_REQUIRED)
        return _allowed("navigation_allowed")
    if request.action is BrowserAction.READ_PAGE:
        return (
            _allowed("page_reading_allowed")
            if policy.allow_reading_pages
            else _denied("page_reading_not_allowed")
        )
    if request.action is BrowserAction.FILL_BASIC_PROFILE:
        return (
            _allowed("basic_profile_filling_allowed")
            if policy.allow_basic_profile_filling
            else _denied("basic_profile_filling_not_allowed")
        )
    if request.action is BrowserAction.SELECT_OPTION:
        return (
            _allowed("option_selection_allowed")
            if policy.allow_option_selection
            else _denied("option_selection_not_allowed")
        )
    if request.action is BrowserAction.CREATE_FREE_RESERVATION:
        return (
            _allowed("free_reservation_allowed")
            if policy.allow_free_reservation
            else _user(UserActionReason.CONFIRMATION_REQUIRED)
        )
    return _denied("unsupported_action")


def _origin(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), "", "", "")
    )


def _allowed(reason: str) -> PermissionDecision:
    return PermissionDecision(allowed=True, requires_user=False, reason=reason)


def _denied(reason: str) -> PermissionDecision:
    return PermissionDecision(allowed=False, requires_user=False, reason=reason)


def _user(reason: UserActionReason) -> PermissionDecision:
    return PermissionDecision(
        allowed=False,
        requires_user=True,
        reason=reason.value,
    )
