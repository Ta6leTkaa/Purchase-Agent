import asyncio
from datetime import date
from uuid import uuid4

from app.adapters.mock_train import MockTrainAdapter
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import ProviderOption
from app.services.rule_engine import evaluate_train_options


def make_mission(*, allow_adjacent_compartments: bool | None = True) -> Mission:
    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        participant_ids=[uuid4(), uuid4(), uuid4(), uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=4,
            must_be_same_compartment=True,
            min_lower_berths=2,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(
            allow_adjacent_compartments=allow_adjacent_compartments,
        ),
    )


def get_mock_options(mission: Mission) -> list[ProviderOption]:
    adapter = MockTrainAdapter()
    return asyncio.run(adapter.search_options(mission, []))


def test_variant_a_gets_highest_score_for_strict_train_constraints() -> None:
    mission = make_mission()
    scored_options = evaluate_train_options(get_mock_options(mission), mission)

    assert scored_options[0].option.train_number == "001A"
    assert scored_options[0].score == 100


def test_variant_c_is_valid_fallback_but_scores_below_variant_a() -> None:
    mission = make_mission(allow_adjacent_compartments=True)
    scored_options = evaluate_train_options(get_mock_options(mission), mission)
    scored_by_train = {
        scored.option.train_number: scored
        for scored in scored_options
    }

    variant_a = scored_by_train["001A"]
    variant_c = scored_by_train["003C"]

    assert not variant_c.violations
    assert "Seats are in adjacent compartments" in variant_c.reasons
    assert variant_c.score < variant_a.score


def test_variant_c_gets_violation_when_adjacent_fallback_is_disabled() -> None:
    mission = make_mission(allow_adjacent_compartments=False)
    scored_options = evaluate_train_options(get_mock_options(mission), mission)
    variant_c = next(
        scored
        for scored in scored_options
        if scored.option.train_number == "003C"
    )

    assert "Seats are not in the same compartment" in variant_c.violations


def test_variant_d_gets_near_toilet_violation_when_avoid_toilet_is_enabled() -> None:
    mission = make_mission()
    scored_options = evaluate_train_options(get_mock_options(mission), mission)
    variant_d = next(
        scored
        for scored in scored_options
        if scored.option.train_number == "004D"
    )

    assert "At least one seat is near toilet" in variant_d.violations


def test_valid_options_are_sorted_before_options_with_violations() -> None:
    mission = make_mission()
    scored_options = evaluate_train_options(get_mock_options(mission), mission)

    first_violation_index = next(
        index
        for index, scored in enumerate(scored_options)
        if scored.violations
    )

    assert all(
        not scored.violations
        for scored in scored_options[:first_violation_index]
    )
    assert all(scored.violations for scored in scored_options[first_violation_index:])


def test_scores_are_always_between_zero_and_one_hundred() -> None:
    mission = make_mission()
    scored_options = evaluate_train_options(get_mock_options(mission), mission)

    assert all(0 <= scored.score <= 100 for scored in scored_options)
