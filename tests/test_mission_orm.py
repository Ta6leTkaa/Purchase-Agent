from datetime import date, datetime, timezone
from uuid import uuid4

from app.db.base import Base
from app.db.models.mission import mission_from_model, mission_to_model
from app.domain.execution import ExecutionEvent
from app.domain.mission import (
    FallbackRules,
    Mission,
    MissionStatus,
    MissionType,
    TrainConstraints,
)
from app.domain.provider import (
    ProviderOption,
    ProviderOptionType,
    Seat,
    SeatBerth,
)

_USE_DEFAULT_BEST_OPTION = object()


def test_metadata_contains_missions_table() -> None:
    assert "missions" in Base.metadata.tables


def test_mission_to_model_saves_core_fields() -> None:
    mission = make_mission()

    model = mission_to_model(mission)

    assert model.id == mission.id
    assert model.type == "train_trip"
    assert model.mission_type == "train_ticket"
    assert model.provider_id is None
    assert model.resolved_provider_id is None
    assert model.status == "requires_confirmation"
    assert model.participant_ids == [
        str(participant_id)
        for participant_id in mission.participant_ids
    ]
    assert model.constraints["travel_date"] == "2026-08-01"
    assert model.best_option is not None
    assert model.best_option["train_number"] == "001A"


def test_mission_from_model_restores_domain_mission() -> None:
    mission = make_mission()
    model = mission_to_model(mission)

    restored_mission = mission_from_model(model)

    assert restored_mission == mission
    assert restored_mission.mission_type is MissionType.TRAIN_TICKET


def test_provider_id_survives_mapper_round_trip() -> None:
    mission = make_mission()
    mission.provider_id = "mock_train"

    model = mission_to_model(mission)
    restored_mission = mission_from_model(model)

    assert model.provider_id == "mock_train"
    assert restored_mission.provider_id == "mock_train"


def test_resolved_provider_id_survives_mapper_round_trip() -> None:
    mission = make_mission()
    mission.resolved_provider_id = "mock_train"

    model = mission_to_model(mission)
    restored_mission = mission_from_model(model)

    assert model.resolved_provider_id == "mock_train"
    assert restored_mission.resolved_provider_id == "mock_train"


def test_execution_log_survives_round_trip() -> None:
    mission = make_mission()
    model = mission_to_model(mission)

    restored_mission = mission_from_model(model)

    assert len(restored_mission.execution_log) == 1
    assert restored_mission.execution_log[0].type == "mission_started"


def test_best_option_survives_round_trip() -> None:
    mission = make_mission()
    model = mission_to_model(mission)

    restored_mission = mission_from_model(model)

    assert restored_mission.best_option is not None
    assert restored_mission.best_option.train_number == "001A"


def test_scheduled_at_survives_round_trip() -> None:
    scheduled_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    mission = make_mission(scheduled_at=scheduled_at)
    model = mission_to_model(mission)

    restored_mission = mission_from_model(model)

    assert model.scheduled_at == scheduled_at
    assert restored_mission.scheduled_at == scheduled_at


def test_claimed_at_survives_round_trip() -> None:
    claimed_at = datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)
    mission = make_mission(
        status=MissionStatus.processing,
        claimed_at=claimed_at,
    )
    model = mission_to_model(mission)

    restored_mission = mission_from_model(model)

    assert model.claimed_at == claimed_at
    assert restored_mission.claimed_at == claimed_at
    assert restored_mission.status is MissionStatus.processing


def test_execution_attempts_survives_round_trip() -> None:
    mission = make_mission()
    mission.execution_attempts = 2

    model = mission_to_model(mission)
    restored_mission = mission_from_model(model)

    assert model.execution_attempts == 2
    assert restored_mission.execution_attempts == 2


def test_max_execution_attempts_survives_round_trip() -> None:
    mission = make_mission()
    mission.max_execution_attempts = 5

    model = mission_to_model(mission)
    restored_mission = mission_from_model(model)

    assert model.max_execution_attempts == 5
    assert restored_mission.max_execution_attempts == 5


def test_legacy_processing_without_claimed_at_can_be_restored() -> None:
    mission = make_mission(status=MissionStatus.created)
    model = mission_to_model(mission)
    model.status = MissionStatus.processing.value
    model.claimed_at = None

    restored_mission = mission_from_model(model)

    assert restored_mission.status is MissionStatus.processing
    assert restored_mission.claimed_at is None


def test_none_best_option_survives_round_trip() -> None:
    mission = make_mission(best_option=None)
    model = mission_to_model(mission)

    restored_mission = mission_from_model(model)

    assert restored_mission.best_option is None


def make_mission(
    best_option: ProviderOption | None | object = _USE_DEFAULT_BEST_OPTION,
    scheduled_at: datetime | None = None,
    claimed_at: datetime | None = None,
    status: MissionStatus = MissionStatus.requires_confirmation,
) -> Mission:
    if best_option is _USE_DEFAULT_BEST_OPTION:
        best_option = make_provider_option()

    return Mission(
        id=uuid4(),
        type=MissionType.train_trip,
        title="Moscow to Saint Petersburg",
        status=status,
        participant_ids=[uuid4(), uuid4()],
        provider="mock_train",
        constraints=TrainConstraints(
            from_city="Moscow",
            to_city="Saint Petersburg",
            travel_date=date(2026, 8, 1),
            passengers_count=2,
            must_be_same_compartment=True,
            min_lower_berths=1,
            max_total_price=30000,
            avoid_toilet=True,
        ),
        fallback_rules=FallbackRules(
            allow_adjacent_compartments=True,
        ),
        scheduled_at=scheduled_at,
        claimed_at=claimed_at,
        execution_log=[
            ExecutionEvent(
                timestamp=datetime(2026, 7, 13, 10, 0),
                type="mission_started",
                message="Mission started.",
                metadata={"source": "test"},
            )
        ],
        best_option=normalize_best_option(best_option),
    )


def normalize_best_option(
    best_option: ProviderOption | None | object,
) -> ProviderOption | None:
    if isinstance(best_option, ProviderOption):
        return best_option
    return None


def make_provider_option() -> ProviderOption:
    return ProviderOption(
        id=uuid4(),
        type=ProviderOptionType.train_option,
        train_number="001A",
        from_city="Moscow",
        to_city="Saint Petersburg",
        departure_at=datetime(2026, 8, 1, 20, 0),
        arrival_at=datetime(2026, 8, 2, 6, 0),
        total_price=14200,
        seats=[
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=1,
                berth=SeatBerth.lower,
            ),
            Seat(
                carriage_number=1,
                compartment_number=1,
                seat_number=2,
                berth=SeatBerth.upper,
            ),
        ],
    )
