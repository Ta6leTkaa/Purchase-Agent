from datetime import UTC, date, datetime
from uuid import uuid4

from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)
from app.domain.identity import Identity
from app.domain.page_fill_plan import ProfileField
from app.domain.task import AgentTask
from app.services.page_field_mapper import build_page_fill_plan

NOW = datetime(2026, 8, 14, 15, tzinfo=UTC)


def make_identity(name: str) -> Identity:
    first_name, last_name = name.split()
    return Identity(
        id=uuid4(),
        display_name=name,
        first_name=first_name,
        last_name=last_name,
        birth_date=date(1990, 1, 1),
    )


def test_mapper_assigns_repeated_profile_fields_in_selected_people_order() -> None:
    first = make_identity("Иван Иванов")
    second = make_identity("Анна Петрова")
    controls = (
        BrowserPageControl(
            control_id="control_1",
            kind=BrowserControlKind.TEXT,
            label="Имя пассажира 1",
            required=True,
        ),
        BrowserPageControl(
            control_id="control_2",
            kind=BrowserControlKind.TEXT,
            label="Имя пассажира 2",
            required=True,
        ),
        BrowserPageControl(
            control_id="control_3",
            kind=BrowserControlKind.TEXT,
            label="Номер паспорта",
        ),
    )
    task = AgentTask(
        id=uuid4(),
        instruction="Купить билеты",
        target_url="https://tickets.example.com/",
        person_ids=(first.id, second.id),
        page_snapshot=BrowserPageSnapshot(
            url="https://tickets.example.com/form",
            captured_at=NOW,
            controls=controls,
        ),
        created_at=NOW,
    )

    plan = build_page_fill_plan(task, [second, first], NOW)

    assert [binding.person_id for binding in plan.bindings[:2]] == [
        first.id,
        second.id,
    ]
    assert plan.bindings[0].profile_field is ProfileField.FIRST_NAME
    assert plan.bindings[2].sensitive is True
    assert "value" not in plan.model_dump_json()


def test_mapper_reports_required_fields_it_cannot_identify() -> None:
    person = make_identity("Иван Иванов")
    task = AgentTask(
        id=uuid4(),
        instruction="Купить билет",
        target_url="https://tickets.example.com/",
        person_ids=(person.id,),
        page_snapshot=BrowserPageSnapshot(
            url="https://tickets.example.com/form",
            captured_at=NOW,
            controls=(
                BrowserPageControl(
                    control_id="control_1",
                    kind=BrowserControlKind.TEXT,
                    label="Промокод владельца",
                    required=True,
                ),
            ),
        ),
        created_at=NOW,
    )

    plan = build_page_fill_plan(task, [person], NOW)

    assert plan.bindings == ()
    assert plan.unmatched_required_controls == ("control_1",)
