from datetime import datetime
from typing import Any
from uuid import UUID

from app.adapters import get_adapter
from app.domain.execution import ExecutionEvent
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionStatus
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.services.mission_state_machine import MissionStateMachine
from app.services.rule_engine import evaluate_train_options


class MissionNotFoundError(Exception):
    pass


class InvalidMissionConfirmationError(Exception):
    pass


class InvalidMissionRunError(Exception):
    pass


async def run_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    identity_repository: IdentityRepository,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError

    if mission.status not in {MissionStatus.created, MissionStatus.waiting}:
        message = (
            "Mission cannot be started from status "
            f"'{mission.status.value}'"
        )
        raise InvalidMissionRunError(message)

    state_machine = MissionStateMachine()
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

    adapter = get_adapter(mission.provider)

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
    mission.execution_log.append(
        ExecutionEvent(
            timestamp=datetime.now(),
            type=event_type,
            message=message,
            metadata=metadata or {},
        )
    )
