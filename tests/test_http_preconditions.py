import pytest

from app.services.http_preconditions import if_none_match_matches


@pytest.mark.parametrize(
    "header",
    [
        '"4"',
        'W/"4"',
        '"3", "4", "5"',
        'W/"3", W/"4"',
        "*",
    ],
)
def test_if_none_match_uses_weak_comparison(header: str) -> None:
    assert if_none_match_matches(header, '"4"')


@pytest.mark.parametrize("header", [None, "", '"3"', 'w/"4"', "invalid"])
def test_if_none_match_rejects_non_matching_values(header: str | None) -> None:
    assert not if_none_match_matches(header, '"4"')
