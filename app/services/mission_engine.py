from datetime import datetime
from typing import Any
from uuid import UUID

from app.adapters import get_adapter
from app.domain.execution import ExecutionEvent
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionStatus
from app.services.rule_engine import evaluate_train_options
from app.storage.memory import store


class MissionNotFoundError(Exception):
    pass


async def run_mission(mission_id: UUID) -> Mission:
    mission = store.get_mission(mission_id)
    if mission is None:
        raise MissionNotFoundError

    identities = _get_participants(mission)
    if len(identities) != len(mission.participant_ids):
        mission.status = MissionStatus.failed
        _add_event(
            mission,
            "participant_missing",
            "At least one mission participant was not found.",
        )
        return store.update_mission(mission)

    mission.status = MissionStatus.running
    _add_event(mission, "mission_started", "Mission started.")

    adapter = get_adapter(mission.provider)

    mission.status = MissionStatus.searching
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
        mission.status = MissionStatus.failed
        _add_event(mission, "no_valid_option_found", "No valid option found.")
        return store.update_mission(mission)

    mission.status = MissionStatus.option_found
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

    mission.status = MissionStatus.reserving
    _add_event(mission, "reservation_started", "Reservation started.")

    reservation_result = await adapter.reserve_option(best.option, mission)
    if not reservation_result.success:
        mission.status = MissionStatus.failed
        _add_event(
            mission,
            "reservation_failed",
            "Reservation failed.",
            {"message": reservation_result.message},
        )
        return store.update_mission(mission)

    if reservation_result.requires_confirmation:
        mission.status = MissionStatus.requires_confirmation
        _add_event(
            mission,
            "waiting_for_user_confirmation",
            "Waiting for user confirmation.",
        )
    else:
        mission.status = MissionStatus.completed
        _add_event(mission, "mission_completed", "Mission completed.")

    return store.update_mission(mission)


def _get_participants(mission: Mission) -> list[Identity]:
    identities: list[Identity] = []
    for participant_id in mission.participant_ids:
        identity = store.get_identity(participant_id)
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
