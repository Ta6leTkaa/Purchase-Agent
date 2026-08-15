import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from app.domain.browser_page import BrowserControlKind, BrowserPageControl
from app.domain.identity import Identity
from app.domain.page_fill_plan import (
    IntentField,
    PageFieldBinding,
    PageFillPlan,
    PageIntentBinding,
    ProfileField,
)
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
_INTENT_FIELD_TERMS: dict[IntentField, tuple[str, ...]] = {
    IntentField.ORIGIN: (
        "origin",
        "departure city",
        "departure station",
        "from city",
        "откуда",
        "город отправления",
        "станция отправления",
    ),
    IntentField.DESTINATION: (
        "destination",
        "arrival city",
        "arrival station",
        "to city",
        "куда",
        "город прибытия",
        "станция прибытия",
    ),
    IntentField.REQUESTED_DATE: (
        "travel date",
        "departure date",
        "check in date",
        "date",
        "дата поездки",
        "дата отправления",
        "дата заезда",
        "дата",
    ),
    IntentField.EARLIEST_TIME: (
        "earliest time",
        "time from",
        "not earlier",
        "время от",
        "не раньше",
        "после",
    ),
    IntentField.LATEST_TIME: (
        "latest time",
        "time until",
        "not later",
        "время до",
        "не позже",
    ),
    IntentField.REQUESTED_QUANTITY: (
        "quantity",
        "ticket count",
        "guest count",
        "room count",
        "количество",
        "число билетов",
        "число гостей",
    ),
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
    intent_bindings: list[PageIntentBinding] = []
    bound_intent_fields: set[IntentField] = set()
    bound_search_terms: set[int] = set()
    unmatched_required: list[str] = []
    for control in snapshot.controls:
        profile_field = _detect_profile_field(control)
        if profile_field is not None:
            person_index = occurrences[profile_field]
            occurrences[profile_field] += 1
            if person_index < len(people):
                bindings.append(
                    PageFieldBinding(
                        control_id=control.control_id,
                        profile_field=profile_field,
                        person_id=people[person_index].id,
                        sensitive=profile_field is ProfileField.DOCUMENT_NUMBER,
                    )
                )
                continue
        intent_binding = _detect_intent_binding(
            task,
            control,
            bound_intent_fields,
            bound_search_terms,
        )
        if intent_binding is not None:
            intent_bindings.append(intent_binding)
            if intent_binding.intent_field is IntentField.SEARCH_TERM:
                assert intent_binding.search_term_index is not None
                bound_search_terms.add(intent_binding.search_term_index)
            else:
                bound_intent_fields.add(intent_binding.intent_field)
            continue
        if control.required and not control.disabled:
            unmatched_required.append(control.control_id)
    return PageFillPlan(
        snapshot_url=snapshot.url,
        created_at=now,
        bindings=tuple(bindings),
        intent_bindings=tuple(intent_bindings),
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


def _detect_intent_binding(
    task: AgentTask,
    control: BrowserPageControl,
    bound_fields: set[IntentField],
    bound_search_terms: set[int],
) -> PageIntentBinding | None:
    if task.intent is None or control.disabled:
        return None
    if control.kind in {BrowserControlKind.BUTTON, BrowserControlKind.LINK}:
        return None
    searchable = _normalize(f"{control.label} {control.field_name or ''}")
    available = {
        IntentField.ORIGIN: task.intent.origin,
        IntentField.DESTINATION: task.intent.destination,
        IntentField.REQUESTED_DATE: task.intent.requested_date,
        IntentField.EARLIEST_TIME: task.intent.earliest_time,
        IntentField.LATEST_TIME: task.intent.latest_time,
        IntentField.REQUESTED_QUANTITY: task.intent.requested_quantity,
    }
    for intent_field, terms in _INTENT_FIELD_TERMS.items():
        if (
            available[intent_field] is not None
            and intent_field not in bound_fields
            and any(_contains_term(searchable, term) for term in terms)
        ):
            return PageIntentBinding(
                control_id=control.control_id,
                intent_field=intent_field,
            )
    option_text = _normalize(" ".join(control.options))
    for index, term in enumerate(task.intent.search_terms):
        if index in bound_search_terms:
            continue
        normalized_term = _normalize(term)
        if normalized_term in searchable or normalized_term in option_text:
            return PageIntentBinding(
                control_id=control.control_id,
                intent_field=IntentField.SEARCH_TERM,
                search_term_index=index,
            )
    return None


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[_\-]+", " ", value.casefold()).split())


def _contains_term(searchable: str, term: str) -> bool:
    normalized_term = _normalize(term)
    if " " in normalized_term:
        return normalized_term in searchable
    return normalized_term in searchable.split()
