import re
from datetime import date, time

from app.domain.task_intent import TaskIntent, TaskIntentIssue

_DATE_PATTERN = re.compile(
    r"(?<!\d)(?:(?P<year>20\d{2})[-./](?P<month>\d{1,2})[-./](?P<day>\d{1,2})|"
    r"(?P<day_first>\d{1,2})[./](?P<month_first>\d{1,2})[./](?P<year_last>20\d{2}))(?!\d)"
)
_TIME_AFTER_PATTERN = re.compile(
    r"(?:после|не раньше|after|from)\s+(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)",
    re.IGNORECASE,
)
_TIME_BEFORE_PATTERN = re.compile(
    r"(?:до|не позже|before|until)\s+(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)",
    re.IGNORECASE,
)
_QUOTED_TERM_PATTERN = re.compile(r"[«\"']([^»\"']{1,200})[»\"']")
_QUANTITY_PATTERN = re.compile(
    r"(?<!\d)(?P<count>\d{1,3})\s*(?:билет(?:а|ов)?|мест(?:о|а)?|"
    r"tickets?|seats?|rooms?|номер(?:а|ов)?)(?!\w)",
    re.IGNORECASE,
)
_WORD_QUANTITIES = {
    "один билет": 1,
    "одна комната": 1,
    "два билета": 2,
    "две комнаты": 2,
    "три билета": 3,
    "четыре билета": 4,
    "one ticket": 1,
    "two tickets": 2,
    "three tickets": 3,
    "four tickets": 4,
}


def extract_task_intent(
    instruction: str,
    *,
    participant_count: int,
) -> TaskIntent:
    requested_date, instruction_without_date, invalid_date = _extract_date(
        instruction
    )
    origin, destination = _extract_route(instruction_without_date)
    earliest_time = _extract_time(_TIME_AFTER_PATTERN, instruction)
    latest_time = _extract_time(_TIME_BEFORE_PATTERN, instruction)
    requested_quantity = _extract_quantity(instruction)
    issues: list[TaskIntentIssue] = []
    if invalid_date:
        issues.append(TaskIntentIssue.INVALID_DATE)
    if (
        earliest_time is not None
        and latest_time is not None
        and earliest_time > latest_time
    ):
        issues.append(TaskIntentIssue.INVALID_TIME_WINDOW)
    if (
        requested_quantity is not None
        and requested_quantity != participant_count
    ):
        issues.append(TaskIntentIssue.QUANTITY_MISMATCH)
    return TaskIntent(
        origin=origin,
        destination=destination,
        requested_date=requested_date,
        earliest_time=earliest_time,
        latest_time=latest_time,
        requested_quantity=requested_quantity,
        participant_count=participant_count,
        search_terms=tuple(_QUOTED_TERM_PATTERN.findall(instruction)),
        issues=tuple(issues),
    )


def _extract_date(value: str) -> tuple[date | None, str, bool]:
    match = _DATE_PATTERN.search(value)
    if match is None:
        return None, value, False
    try:
        if match.group("year") is not None:
            parsed = date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        else:
            parsed = date(
                int(match.group("year_last")),
                int(match.group("month_first")),
                int(match.group("day_first")),
            )
    except ValueError:
        return None, value, True
    return parsed, f"{value[:match.start()]} {value[match.end():]}", False


def _extract_route(value: str) -> tuple[str | None, str | None]:
    normalized = " ".join(value.split())
    patterns = (
        re.compile(
            r"(?:\bиз|\bот)\s+(?P<origin>[\w -]{2,100}?)\s+"
            r"(?:\bв|\bдо)\s+(?P<destination>[\w -]{2,100})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bfrom\s+(?P<origin>[\w -]{2,100}?)\s+to\s+"
            r"(?P<destination>[\w -]{2,100})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?P<origin>[А-ЯЁA-Z][\w-]*(?:\s+[А-ЯЁA-Z][\w-]*){0,2})"
            r"\s*(?:—|->)\s*"
            r"(?P<destination>[А-ЯЁA-Z][\w-]*"
            r"(?:\s+[А-ЯЁA-Z][\w-]*){0,2})"
        ),
    )
    for pattern in patterns:
        match = pattern.search(normalized)
        if match is not None:
            return (
                _trim_route_value(match.group("origin")),
                _trim_route_value(match.group("destination")),
            )
    destination_only = re.search(
        r"(?:авиабилет|билет|поездку|номер)\s+(?:в|до|to)\s+"
        r"(?P<destination>[А-ЯЁA-Z][\w-]*(?:\s+[А-ЯЁA-Z][\w-]*){0,2})",
        normalized,
        re.IGNORECASE,
    )
    if destination_only is not None:
        return None, _trim_route_value(destination_only.group("destination"))
    return None, None


def _trim_route_value(value: str) -> str:
    stop_words = re.compile(
        r"\s+(?:после|до|не раньше|не позже|на|для|after|before|for)\b",
        re.IGNORECASE,
    )
    return stop_words.split(value, maxsplit=1)[0].strip(" ,.;:—-")


def _extract_time(pattern: re.Pattern[str], value: str) -> time | None:
    match = pattern.search(value)
    if match is None:
        return None
    return time(int(match.group("hour")), int(match.group("minute")))


def _extract_quantity(value: str) -> int | None:
    match = _QUANTITY_PATTERN.search(value)
    if match is not None:
        count = int(match.group("count"))
        return count if 1 <= count <= 100 else None
    normalized = value.casefold()
    return next(
        (count for marker, count in _WORD_QUANTITIES.items() if marker in normalized),
        None,
    )
