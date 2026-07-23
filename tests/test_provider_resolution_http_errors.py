from app.api.exception_handlers import map_provider_resolution_error
from app.adapters.registry import UnknownProviderError
from app.domain.mission import MissionType
from app.services.provider_errors import UnsupportedMissionTypeError
from app.services.provider_resolver import (
    AmbiguousProviderError,
    NoSupportingProviderError,
)


def test_maps_unknown_provider_error() -> None:
    mapped = map_provider_resolution_error(UnknownProviderError("missing"))

    assert mapped.status_code == 422
    assert mapped.code == "unknown_provider"
    assert mapped.details == {"provider_id": "missing"}


def test_maps_unsupported_mission_type_error() -> None:
    mapped = map_provider_resolution_error(
        UnsupportedMissionTypeError("provider_a", MissionType.TRAIN_TICKET)
    )

    assert mapped.status_code == 422
    assert mapped.code == "unsupported_mission_type"
    assert mapped.details == {
        "provider_id": "provider_a",
        "mission_type": "train_ticket",
    }


def test_maps_no_supporting_provider_error() -> None:
    mapped = map_provider_resolution_error(
        NoSupportingProviderError(MissionType.TRAIN_TICKET)
    )

    assert mapped.status_code == 409
    assert mapped.code == "no_supporting_provider"
    assert mapped.details == {"mission_type": "train_ticket"}


def test_maps_ambiguous_provider_error_in_registration_order() -> None:
    mapped = map_provider_resolution_error(
        AmbiguousProviderError(
            MissionType.TRAIN_TICKET,
            ("provider_b", "provider_a"),
        )
    )

    assert mapped.status_code == 409
    assert mapped.code == "ambiguous_provider"
    assert mapped.details["candidate_provider_ids"] == [
        "provider_b",
        "provider_a",
    ]
