from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class BrowserAction(StrEnum):
    NAVIGATE = "navigate"
    READ_PAGE = "read_page"
    FILL_BASIC_PROFILE = "fill_basic_profile"
    FILL_SENSITIVE_PROFILE = "fill_sensitive_profile"
    SELECT_OPTION = "select_option"
    CREATE_FREE_RESERVATION = "create_free_reservation"
    SUBMIT_ORDER = "submit_order"
    AUTHENTICATE = "authenticate"
    SOLVE_CAPTCHA = "solve_captcha"
    PAY = "pay"


class TaskPermissionPolicy(BaseModel):
    """User-controlled limits for reversible browser automation."""

    model_config = ConfigDict(frozen=True)

    allow_navigation: bool = True
    allow_reading_pages: bool = True
    allow_basic_profile_filling: bool = True
    allow_option_selection: bool = True
    allow_free_reservation: bool = False
    require_approval_for_sensitive_data: bool = True
    require_approval_for_cross_origin_navigation: bool = True


class BrowserActionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: BrowserAction
    target_url: str | None = None
    creates_charge: bool = False
    reversible: bool = True


class PermissionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    requires_user: bool
    reason: str
