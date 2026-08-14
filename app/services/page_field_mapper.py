import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from app.domain.browser_page import BrowserControlKind, BrowserPageControl
from app.domain.identity import Identity
from app.domain.page_fill_plan import PageFieldBinding, PageFillPlan, ProfileField
from app.domain.task import AgentTask

_FIELD_TERMS: dict[ProfileField, tuple[str, ...]] = {
    ProfileField.FIRST_NAME: ("first name", "firstname", "given name", "имя"),
    ProfileField.LAST_NAME: (
        "last name",
        "lastname",
        "surname",
        "family name",
        "фамилия",
    ),
    ProfileField.BIRTH_DATE: (
        "birth date",
        "date of birth",
        "birthday",
        "дата рождения",
        "рождение",
    ),
    ProfileField.DOCUMENT_NUMBER: (
        "document number",
        "passport number",
        "passport",
        "номер документа",
        "номер паспорта",
        "паспорт",
    ),
}
_IGNORED_KINDS = {
    BrowserControlKind.BUTTON,
    BrowserControlKind.LINK,
    BrowserControlKind.CHECKBOX,
    BrowserControlKind.RADIO,
    BrowserControlKind.SELECT,
}


def build_page_fill_plan(
    task: AgentTask,
    identities: Sequence[Identity],
    now: datetime,
) -> PageFillPlan:
    snapshot = task.page_snapshot
    if snapshot is None:
        raise ValueError("task has no captured page")
    identity_by_id = {identity.id: identity for identity in identities}
    people = tuple(identity_by_id[person_id] for person_id in task.person_ids)
    occurrences: defaultdict[ProfileField, int] = defaultdict(int)
    bindings: list[PageFieldBinding] = []
    unmatched_required: list[str] = []
    for control in snapshot.controls:
        profile_field = _detect_profile_field(control)
        if profile_field is None:
            if control.required and not control.disabled:
                unmatched_required.append(control.control_id)
            continue
        person_index = occurrences[profile_field]
        occurrences[profile_field] += 1
        if person_index >= len(people):
            if control.required:
                unmatched_required.append(control.control_id)
            continue
        bindings.append(
            PageFieldBinding(
                control_id=control.control_id,
                profile_field=profile_field,
                person_id=people[person_index].id,
                sensitive=profile_field is ProfileField.DOCUMENT_NUMBER,
            )
        )
    return PageFillPlan(
        snapshot_url=snapshot.url,
        created_at=now,
        bindings=tuple(bindings),
        unmatched_required_controls=tuple(unmatched_required),
    )


def _detect_profile_field(control: BrowserPageControl) -> ProfileField | None:
    if control.disabled or control.kind in _IGNORED_KINDS:
        return None
    searchable = _normalize(f"{control.label} {control.field_name or ''}")
    for profile_field, terms in _FIELD_TERMS.items():
        if any(term in searchable for term in terms):
            return profile_field
    return None


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", value.casefold()).split())
