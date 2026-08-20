import pytest

from app.mvp_smoke import _validated_base_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://localhost:8000/", "http://localhost:8000"),
        ("https://agent.example.com", "https://agent.example.com"),
    ],
)
def test_mvp_smoke_accepts_absolute_base_url(value: str, expected: str) -> None:
    assert _validated_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "localhost:8000",
        "file:///tmp/demo.html",
        "https://user:secret@example.com",
        "https://example.com?next=/admin",
        "https://example.com#fragment",
    ],
)
def test_mvp_smoke_rejects_unsafe_base_url(value: str) -> None:
    with pytest.raises(ValueError):
        _validated_base_url(value)
