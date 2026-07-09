from pydantic import BaseModel

from app.domain.mission import Mission
from app.domain.provider import ProviderOption, SeatBerth


class ScoredOption(BaseModel):
    option: ProviderOption
    score: int
    reasons: list[str] = []
    violations: list[str] = []


def evaluate_train_options(
    options: list[ProviderOption],
    mission: Mission,
) -> list[ScoredOption]:
    scored_options = [
        _evaluate_train_option(option, mission)
        for option in options
    ]
    return sorted(
        scored_options,
        key=lambda scored_option: (
            bool(scored_option.violations),
            -scored_option.score,
        ),
    )


def _evaluate_train_option(
    option: ProviderOption,
    mission: Mission,
) -> ScoredOption:
    score = 0
    reasons: list[str] = []
    violations: list[str] = []

    same_compartment = _is_same_compartment(option)
    if same_compartment:
        score += 40
        reasons.append("All seats are in the same compartment")

    if mission.constraints.must_be_same_compartment is True and not same_compartment:
        if (
            mission.fallback_rules.allow_adjacent_compartments is True
            and _is_adjacent_compartments(option)
        ):
            score += 20
            reasons.append("Seats are in adjacent compartments")
        else:
            violations.append("Seats are not in the same compartment")

    min_lower_berths = mission.constraints.min_lower_berths
    if min_lower_berths is not None and _count_lower_berths(option) >= min_lower_berths:
        score += 20
        reasons.append("Required lower berths count is satisfied")

    max_total_price = mission.constraints.max_total_price
    if max_total_price is not None and option.total_price <= max_total_price:
        score += 20
        reasons.append("Option is within budget")

    if mission.constraints.avoid_toilet is True and _has_near_toilet_seat(option):
        score -= 30
        violations.append("At least one seat is near toilet")

    if len(option.seats) == mission.constraints.passengers_count:
        score += 20
        reasons.append("Passenger count matches seats count")

    return ScoredOption(
        option=option,
        score=max(0, min(score, 100)),
        reasons=reasons,
        violations=violations,
    )


def _is_same_compartment(option: ProviderOption) -> bool:
    compartments = {
        (seat.carriage_number, seat.compartment_number)
        for seat in option.seats
    }
    return len(compartments) == 1


def _is_adjacent_compartments(option: ProviderOption) -> bool:
    compartments = {
        (seat.carriage_number, seat.compartment_number)
        for seat in option.seats
    }
    if len(compartments) != 2:
        return False

    first, second = sorted(compartments)
    return first[0] == second[0] and abs(first[1] - second[1]) == 1


def _count_lower_berths(option: ProviderOption) -> int:
    return sum(seat.berth is SeatBerth.lower for seat in option.seats)


def _has_near_toilet_seat(option: ProviderOption) -> bool:
    return any(seat.near_toilet for seat in option.seats)
