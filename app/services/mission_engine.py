from datetime import datetime
from typing import Any
from uuid import UUID

from app.adapters import provider_registry
from app.adapters.registry import ProviderRegistry, UnknownProviderError
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionExecutionMode, MissionStatus
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
from app.services.mission_expiration import expire_mission_if_due
from app.services.mission_state_machine import MissionStateMachine
from app.services.provider_errors import (
    ProviderOperationError,
    UnsupportedExecutionModeError,
    UnsupportedMissionTypeError,
)
from app.services.provider_resolver import (
    AmbiguousProviderError,
    NoSupportingProviderError,
    ProviderResolver,
)
from app.services.rule_engine import evaluate_train_options

__all__ = ["UnsupportedMissionTypeError"]


class InvalidMissionConfirmationError(Exception):
    pass


class InvalidMissionCancellationError(Exception):
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
    if await expire_mission_if_due(
        mission,
        mission_repository,
        now,
    ):
        raise MissionNotReadyError("Mission has expired")
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
        UnsupportedExecutionModeError,
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

    try:
        options = await adapter.search_options(mission, identities)
    except ProviderOperationError as error:
        return await _fail_provider_operation(
            mission,
            mission_repository,
            state_machine,
            provider_id=adapter.provider_id,
            operation="search",
            retryable=error.retryable,
        )
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

    if mission.execution_mode is MissionExecutionMode.SEARCH_ONLY:
        state_machine.transition(mission, MissionStatus.completed)
        _add_event(
            mission,
            "mission_search_completed",
            "Mission completed without creating a reservation.",
            {"execution_mode": mission.execution_mode.value},
        )
        return await mission_repository.update(mission)

    if not is_processing_run:
        state_machine.transition(mission, MissionStatus.reserving)
    _add_event(mission, "reservation_started", "Reservation started.")

    try:
        reservation_result = await adapter.reserve_option(
            best.option,
            mission,
            idempotency_key=_reservation_idempotency_key(mission),
        )
    except ProviderOperationError as error:
        return await _fail_provider_operation(
            mission,
            mission_repository,
            state_machine,
            provider_id=adapter.provider_id,
            operation="reservation",
            retryable=error.retryable,
        )
    if not reservation_result.success:
        state_machine.transition(mission, MissionStatus.failed)
        _add_event(
            mission,
            "reservation_failed",
            "Reservation failed.",
            {"provider_id": adapter.provider_id},
        )
        return await mission_repository.update(mission)

    assert reservation_result.reservation_id is not None
    mission.reservation_id = reservation_result.reservation_id
    _add_event(
        mission,
        "reservation_succeeded",
        "Reservation succeeded.",
        {
            "reservation_id": reservation_result.reservation_id,
            "requires_confirmation": reservation_result.requires_confirmation,
        },
    )

    if (
        mission.execution_mode
        is MissionExecutionMode.REQUIRE_CONFIRMATION
    ):
        state_machine.transition(mission, MissionStatus.requires_confirmation)
        _add_event(
            mission,
            "waiting_for_user_confirmation",
            "Waiting for user confirmation.",
            {
                "execution_mode": mission.execution_mode.value,
                "provider_requires_confirmation": (
                    reservation_result.requires_confirmation
                ),
            },
        )
        return await mission_repository.update(mission)

    if reservation_result.requires_confirmation:
        _add_event(
            mission,
            "automatic_confirmation_started",
            "Automatic reservation confirmation started.",
            {"provider_id": adapter.provider_id},
        )
        try:
            confirmation_result = await adapter.confirm_reservation(
                reservation_result.reservation_id,
                mission,
                idempotency_key=_confirmation_idempotency_key(mission),
            )
        except ProviderOperationError as error:
            return await _fail_provider_operation(
                mission,
                mission_repository,
                state_machine,
                provider_id=adapter.provider_id,
                operation="automatic_confirmation",
                retryable=error.retryable,
            )
        if not confirmation_result.success:
            state_machine.transition(mission, MissionStatus.failed)
            _add_event(
                mission,
                "automatic_confirmation_failed",
                "Automatic reservation confirmation failed.",
                {"provider_id": adapter.provider_id},
            )
            return await mission_repository.update(mission)
        _add_event(
            mission,
            "automatic_confirmation_succeeded",
            "Automatic reservation confirmation succeeded.",
            {"provider_id": adapter.provider_id},
        )

    state_machine.transition(mission, MissionStatus.completed)
    _add_event(
        mission,
        "mission_completed",
        "Mission completed automatically.",
        {"execution_mode": mission.execution_mode.value},
    )

    return await mission_repository.update(mission)


async def confirm_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    registry: ProviderRegistry | None = None,
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

    if _requires_provider_confirmation(mission):
        assert mission.resolved_provider_id is not None
        assert mission.reservation_id is not None
        confirmation_registry = registry or provider_registry
        try:
            adapter = confirmation_registry.get(mission.resolved_provider_id)
        except UnknownProviderError as exc:
            raise InvalidMissionConfirmationError(
                "The provider for this reservation is unavailable"
            ) from exc

        _add_event(
            mission,
            "confirmation_started",
            "Reservation confirmation started.",
        )
        try:
            confirmation_result = await adapter.confirm_reservation(
                mission.reservation_id,
                mission,
                idempotency_key=_confirmation_idempotency_key(mission),
            )
        except ProviderOperationError as error:
            return await _fail_provider_operation(
                mission,
                mission_repository,
                MissionStateMachine(),
                provider_id=adapter.provider_id,
                operation="confirmation",
                retryable=error.retryable,
            )

        if not confirmation_result.success:
            state_machine = MissionStateMachine()
            state_machine.transition(mission, MissionStatus.failed)
            _add_event(
                mission,
                "confirmation_failed",
                "Reservation confirmation failed.",
                {"provider_id": adapter.provider_id},
            )
            return await mission_repository.update(mission)

        _add_event(
            mission,
            "confirmation_succeeded",
            "Reservation confirmed.",
            {"provider_id": adapter.provider_id},
        )

    state_machine = MissionStateMachine()
    state_machine.transition(mission, MissionStatus.completed)
    _add_event(mission, "mission_confirmed", "Mission confirmed by user")
    _add_event(mission, "mission_completed", "Mission completed")

    return await mission_repository.update(mission)


async def cancel_mission(
    mission_id: UUID,
    mission_repository: MissionRepository,
    registry: ProviderRegistry | None = None,
) -> Mission:
    mission = await mission_repository.get(mission_id)
    if mission is None:
        raise MissionNotFoundError

    if mission.status not in {
        MissionStatus.created,
        MissionStatus.waiting,
        MissionStatus.paused,
        MissionStatus.requires_confirmation,
    }:
        raise InvalidMissionCancellationError(
            "Mission cannot be cancelled from status "
            f"'{mission.status.value}'"
        )

    if mission.status is MissionStatus.requires_confirmation:
        cancellation_succeeded = await _cancel_provider_reservation(
            mission,
            mission_repository,
            registry or provider_registry,
        )
        if not cancellation_succeeded:
            return mission

    MissionStateMachine().transition(mission, MissionStatus.cancelled)
    _add_event(mission, "mission_cancelled", "Mission cancelled.")
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


async def _fail_provider_operation(
    mission: Mission,
    mission_repository: MissionRepository,
    state_machine: MissionStateMachine,
    *,
    provider_id: str,
    operation: str,
    retryable: bool,
) -> Mission:
    state_machine.transition(mission, MissionStatus.failed)
    _add_event(
        mission,
        "provider_operation_failed",
        "Provider operation failed.",
        {
            "provider_id": provider_id,
            "operation": operation,
            "retryable": retryable,
        },
    )
    return await mission_repository.update(mission)


async def _cancel_provider_reservation(
    mission: Mission,
    mission_repository: MissionRepository,
    registry: ProviderRegistry,
) -> bool:
    try:
        requires_provider_confirmation = _requires_provider_confirmation(mission)
    except InvalidMissionConfirmationError as exc:
        raise InvalidMissionCancellationError(str(exc)) from exc
    if not requires_provider_confirmation:
        return True
    assert mission.resolved_provider_id is not None
    assert mission.reservation_id is not None
    try:
        adapter = registry.get(mission.resolved_provider_id)
    except UnknownProviderError as exc:
        raise InvalidMissionCancellationError(
            "The provider for this reservation is unavailable"
        ) from exc

    _add_event(
        mission,
        "cancellation_started",
        "Reservation cancellation started.",
    )
    try:
        cancellation_result = await adapter.cancel_reservation(
            mission.reservation_id,
            mission,
            idempotency_key=_cancellation_idempotency_key(mission),
        )
    except ProviderOperationError as error:
        await _fail_provider_operation(
            mission,
            mission_repository,
            MissionStateMachine(),
            provider_id=adapter.provider_id,
            operation="cancellation",
            retryable=error.retryable,
        )
        return False

    if not cancellation_result.success:
        MissionStateMachine().transition(mission, MissionStatus.failed)
        _add_event(
            mission,
            "cancellation_failed",
            "Reservation cancellation failed.",
            {"provider_id": adapter.provider_id},
        )
        await mission_repository.update(mission)
        return False

    _add_event(
        mission,
        "cancellation_succeeded",
        "Reservation cancelled.",
        {"provider_id": adapter.provider_id},
    )
    return True


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


def _reservation_idempotency_key(mission: Mission) -> str:
    return f"mission:{mission.id}"


def _confirmation_idempotency_key(mission: Mission) -> str:
    return f"mission:{mission.id}:confirmation"


def _cancellation_idempotency_key(mission: Mission) -> str:
    return f"mission:{mission.id}:cancellation"


def _requires_provider_confirmation(mission: Mission) -> bool:
    if mission.resolved_provider_id is None and mission.reservation_id is None:
        return False
    if mission.resolved_provider_id is None or mission.reservation_id is None:
        raise InvalidMissionConfirmationError(
            "Mission reservation metadata is incomplete"
        )
    return True


def _provider_resolution_failure_payload(
    mission: Mission,
    error: (
        UnknownProviderError
        | UnsupportedMissionTypeError
        | UnsupportedExecutionModeError
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
    elif isinstance(error, UnsupportedExecutionModeError):
        reason = ProviderResolutionFailureReason.unsupported_execution_mode
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
