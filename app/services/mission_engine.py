from datetime import datetime
from typing import Any
from uuid import UUID

from app.adapters import provider_registry
from app.adapters.registry import UnknownProviderError
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionStatus
from app.domain.provider_resolution import (
    ProviderResolutionFailedEventPayload,
    ProviderResolutionFailureReason,
    ProviderResolvedEventPayload,
    ProviderSelectionMode,
    create_provider_resolution_snapshot,
)
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.clock import utc_now
from app.services.mission_errors import MissionNotFoundError
from app.services.mission_state_machine import MissionStateMachine
from app.services.provider_errors import UnsupportedMissionTypeError
from app.services.provider_resolver import (
    AmbiguousProviderError,
    NoSupportingProviderError,
    ProviderResolver,
)
from app.services.rule_engine import evaluate_train_options

__all__ = ["UnsupportedMissionTypeError"]


class InvalidMissionConfirmationError(Exception):
    pass


class InvalidMissionRunError(Exception):
    pass


class MissionNotReadyError(Exception):
    pass


async def run_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    identity_repository: IdentityRepository,
    provider_resolver: ProviderResolver | None = None,
    current_time: datetime | None = None,
    allow_processing: bool = False,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError

    allowed_statuses = {MissionStatus.created, MissionStatus.waiting}
    if allow_processing:
        allowed_statuses.add(MissionStatus.processing)

    if mission.status not in allowed_statuses:
        message = (
            "Mission cannot be started from status "
            f"'{mission.status.value}'"
        )
        raise InvalidMissionRunError(message)

    now = current_time or utc_now()
    if mission.status is MissionStatus.waiting and _is_scheduled_for_future(
        mission,
        now,
    ):
        raise MissionNotReadyError("Mission is scheduled for a future time")

    resolver = provider_resolver or ProviderResolver(provider_registry)
    try:
        adapter = resolver.resolve(mission)
    except (
        UnknownProviderError,
        UnsupportedMissionTypeError,
        NoSupportingProviderError,
        AmbiguousProviderError,
    ) as error:
        _add_event(
            mission,
            "provider_resolution_failed",
            "Provider resolution failed.",
            _provider_resolution_failure_payload(mission, error),
        )
        await mission_repository.update(mission)
        raise
    mission.resolved_provider_id = adapter.provider_id
    selection_mode = (
        ProviderSelectionMode.explicit
        if mission.provider_id is not None
        else ProviderSelectionMode.automatic
    )
    resolution_payload = ProviderResolvedEventPayload(
        provider_id=adapter.provider_id,
        mission_type=mission.mission_type,
        selection_mode=selection_mode,
        snapshot=create_provider_resolution_snapshot(
            mission=mission,
            resolved_provider_id=adapter.provider_id,
            candidate_provider_ids=(adapter.provider_id,),
        ),
    )
    _add_event(
        mission,
        "provider_resolved",
        "Provider resolved for mission execution.",
        resolution_payload.model_dump(mode="json"),
    )
    await mission_repository.update(mission)

    state_machine = MissionStateMachine()
    is_processing_run = mission.status is MissionStatus.processing
    if not is_processing_run:
        state_machine.transition(mission, MissionStatus.running)

    identities = await _get_participants(mission, identity_repository)
    if len(identities) != len(mission.participant_ids):
        state_machine.transition(mission, MissionStatus.failed)
        _add_event(
            mission,
            "participant_missing",
            "At least one mission participant was not found.",
        )
        return await mission_repository.update(mission)

    _add_event(mission, "mission_started", "Mission started.")

    if not is_processing_run:
        state_machine.transition(mission, MissionStatus.searching)
    _add_event(mission, "search_started", "Provider option search started.")

    options = await adapter.search_options(mission, identities)
    _add_event(
        mission,
        "options_found",
        "Provider options found.",
        {"count": len(options)},
    )

    scored_options = evaluate_train_options(options, mission)
    best = next(
        (
            scored_option
            for scored_option in scored_options
            if not scored_option.violations
        ),
        None,
    )
    if best is None:
        state_machine.transition(mission, MissionStatus.failed)
        _add_event(mission, "no_valid_option_found", "No valid option found.")
        return await mission_repository.update(mission)

    if not is_processing_run:
        state_machine.transition(mission, MissionStatus.option_found)
    mission.best_option = best.option
    _add_event(
        mission,
        "best_option_selected",
        "Best option selected.",
        {
            "score": best.score,
            "reasons": best.reasons,
        },
    )

    if not is_processing_run:
        state_machine.transition(mission, MissionStatus.reserving)
    _add_event(mission, "reservation_started", "Reservation started.")

    reservation_result = await adapter.reserve_option(best.option, mission)
    if not reservation_result.success:
        state_machine.transition(mission, MissionStatus.failed)
        _add_event(
            mission,
            "reservation_failed",
            "Reservation failed.",
            {"message": reservation_result.message},
        )
        return await mission_repository.update(mission)

    if reservation_result.requires_confirmation:
        state_machine.transition(mission, MissionStatus.requires_confirmation)
        _add_event(
            mission,
            "waiting_for_user_confirmation",
            "Waiting for user confirmation.",
        )
    else:
        state_machine.transition(mission, MissionStatus.completed)
        _add_event(mission, "mission_completed", "Mission completed.")

    return await mission_repository.update(mission)


async def confirm_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError

    if mission.status is not MissionStatus.requires_confirmation:
        message = (
            "Mission cannot be confirmed from status "
            f"{mission.status.value}"
        )
        raise InvalidMissionConfirmationError(message)

    state_machine = MissionStateMachine()
    state_machine.transition(mission, MissionStatus.completed)
    _add_event(mission, "mission_confirmed", "Mission confirmed by user")
    _add_event(mission, "mission_completed", "Mission completed")

    return await mission_repository.update(mission)


async def _get_participants(
    mission: Mission,
    identity_repository: IdentityRepository,
) -> list[Identity]:
    identities: list[Identity] = []
    for participant_id in mission.participant_ids:
        identity = await identity_repository.get(participant_id)
        if identity is not None:
            identities.append(identity)
    return identities


def _add_event(
    mission: Mission,
    event_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    mission.record_event(
        timestamp=utc_now(),
        event_type=event_type,
        message=message,
        metadata=metadata,
    )


def _is_scheduled_for_future(
    mission: Mission,
    current_time: datetime,
) -> bool:
    return (
        mission.scheduled_at is not None
        and mission.scheduled_at > current_time
    )


def _provider_resolution_failure_payload(
    mission: Mission,
    error: (
        UnknownProviderError
        | UnsupportedMissionTypeError
        | NoSupportingProviderError
        | AmbiguousProviderError
    ),
) -> dict[str, object]:
    if isinstance(error, UnknownProviderError):
        reason = ProviderResolutionFailureReason.unknown_provider
        requested_provider_id = mission.provider_id
        candidate_provider_ids: tuple[str, ...] = ()
    elif isinstance(error, UnsupportedMissionTypeError):
        reason = ProviderResolutionFailureReason.unsupported_mission_type
        requested_provider_id = mission.provider_id
        candidate_provider_ids = ()
    elif isinstance(error, NoSupportingProviderError):
        reason = ProviderResolutionFailureReason.no_supporting_provider
        requested_provider_id = None
        candidate_provider_ids = ()
    else:
        reason = ProviderResolutionFailureReason.ambiguous_provider
        requested_provider_id = None
        candidate_provider_ids = error.provider_ids

    return ProviderResolutionFailedEventPayload(
        reason=reason,
        mission_type=mission.mission_type,
        requested_provider_id=requested_provider_id,
        candidate_provider_ids=candidate_provider_ids,
    ).model_dump(mode="json")
