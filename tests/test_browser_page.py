from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.browser_page import (
    BrowserControlKind,
    BrowserPageControl,
    BrowserPageSnapshot,
)

NOW = datetime(2026, 8, 14, 14, tzinfo=UTC)


def test_page_snapshot_keeps_structure_without_control_values() -> None:
    control = BrowserPageControl(
        control_id="control_1",
        kind=BrowserControlKind.SELECT,
        label="  Город   отправления ",
        field_name="origin",
        role="listbox",
        required=True,
        options=(" Москва ", "Санкт-Петербург", "Москва"),
    )
    snapshot = BrowserPageSnapshot(
        url="https://tickets.example.com/search",
        title="  Поиск   билетов ",
        captured_at=NOW,
        controls=(control,),
    )

    assert snapshot.title == "Поиск билетов"
    assert control.label == "Город отправления"
    assert control.options == ("Москва", "Санкт-Петербург")
    assert control.role == "listbox"
    assert "value" not in control.model_dump()


def test_page_control_rejects_arbitrary_value_or_html_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        BrowserPageControl.model_validate(
            {
                "control_id": "control_1",
                "kind": "text",
                "label": "Имя",
                "value": "Иван",
                "html": "<input value='Иван'>",
            }
        )


def test_page_control_rejects_blank_label() -> None:
    with pytest.raises(ValidationError, match="label must not be blank"):
        BrowserPageControl(
            control_id="control_1",
            kind=BrowserControlKind.TEXT,
            label="   ",
        )


def test_page_snapshot_bounds_control_inventory() -> None:
    controls = tuple(
        BrowserPageControl(
            control_id=f"control_{index}",
            kind=BrowserControlKind.TEXT,
            label=f"Field {index}",
        )
        for index in range(1, 302)
    )

    with pytest.raises(ValidationError):
        BrowserPageSnapshot(
            url="https://example.com/",
            captured_at=NOW,
            controls=controls,
        )
