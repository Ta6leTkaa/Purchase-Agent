import pytest

from app.services.fuzzy_matching import fuzzy_text_score, normalize_fuzzy_text


@pytest.mark.parametrize(
    ("query", "candidate", "minimum"),
    [
        ("Колобок", "Последний богатырь. Колобок", 0.94),
        ("Последний богатырь Колобок", "Последний богатырь. Колобок", 1.0),
        ("колобка", "Последний богатырь. Колобок", 0.9),
        ("Spider Man", "Spider-Man: New Day", 0.94),
    ],
)
def test_fuzzy_text_score_tolerates_user_title_variations(
    query: str, candidate: str, minimum: float
) -> None:
    assert fuzzy_text_score(query, candidate) >= minimum


def test_fuzzy_text_score_keeps_unrelated_candidates_low() -> None:
    assert fuzzy_text_score("Колобок", "Человек-паук") < 0.4


def test_normalize_fuzzy_text_ignores_case_punctuation_and_yo() -> None:
    assert normalize_fuzzy_text("  Ёлки-Иголки! ") == "елки иголки"
