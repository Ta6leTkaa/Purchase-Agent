from datetime import date, time

from app.domain.task_intent import TaskIntentIssue
from app.services.task_intent_parser import extract_task_intent


def test_extracts_russian_route_date_time_quantity_and_title() -> None:
    intent = extract_task_intent(
        "Купить 2 билета из Москвы в Казань 20.09.2026 после 18:30 "
        "до 22:00 на «Вечерний поезд»",
        participant_count=2,
    )

    assert intent.origin == "Москвы"
    assert intent.destination == "Казань"
    assert intent.requested_date == date(2026, 9, 20)
    assert intent.earliest_time == time(18, 30)
    assert intent.latest_time == time(22, 0)
    assert intent.requested_quantity == 2
    assert intent.participant_count == 2
    assert intent.search_terms == ("Вечерний поезд",)


def test_extracts_english_route_and_iso_date() -> None:
    intent = extract_task_intent(
        "Book two tickets from London to Edinburgh 2026-10-04 after 09:15",
        participant_count=2,
    )

    assert intent.origin == "London"
    assert intent.destination == "Edinburgh"
    assert intent.requested_date == date(2026, 10, 4)
    assert intent.requested_quantity == 2


def test_extracts_dash_route_without_leading_instruction_words() -> None:
    intent = extract_task_intent(
        "Купить билет Москва — Нижний Новгород 15.10.2026",
        participant_count=1,
    )

    assert intent.origin == "Москва"
    assert intent.destination == "Нижний Новгород"


def test_extracts_russian_month_date_and_rolls_past_date_forward() -> None:
    upcoming = extract_task_intent(
        "Купить билет в кино на 22 августа",
        participant_count=1,
        reference_date=date(2026, 8, 16),
    )
    next_year = extract_task_intent(
        "Купить билет в кино на 10 августа",
        participant_count=1,
        reference_date=date(2026, 8, 16),
    )

    assert upcoming.requested_date == date(2026, 8, 22)
    assert next_year.requested_date == date(2027, 8, 10)


def test_extracts_unquoted_cinema_title_without_exact_punctuation() -> None:
    full = extract_task_intent(
        "Купи билет на фильм Последний богатырь Колобок на 22 августа",
        participant_count=1,
        reference_date=date(2026, 8, 16),
    )
    short = extract_task_intent(
        "Купи билет в кино на Колобок на 22 августа",
        participant_count=1,
        reference_date=date(2026, 8, 16),
    )

    assert full.search_terms == ("Последний богатырь Колобок",)
    assert short.search_terms == ("Колобок",)


def test_invalid_or_ambiguous_values_are_left_unset() -> None:
    intent = extract_task_intent(
        "Купить билеты когда-нибудь 31.02.2026",
        participant_count=3,
    )

    assert intent.requested_date is None
    assert intent.requested_quantity is None
    assert intent.origin is None
    assert intent.destination is None
    assert intent.participant_count == 3
    assert intent.issues == (TaskIntentIssue.INVALID_DATE,)


def test_reports_quantity_and_time_window_conflicts() -> None:
    intent = extract_task_intent(
        "Купить 4 билета после 22:00 до 18:00",
        participant_count=2,
    )

    assert intent.issues == (
        TaskIntentIssue.INVALID_TIME_WINDOW,
        TaskIntentIssue.QUANTITY_MISMATCH,
    )
