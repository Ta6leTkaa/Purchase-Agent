from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.adapters.registry import UnknownProviderError
from app.domain.mission import Mission, MissionType
from app.domain.provider_resolution import (
    ProviderResolutionFailureReason,
    ProviderResolutionPreviewOutcome,
    ProviderSelectionMode,
    selection_mode_for,
)
from app.repositories.mission import MissionRepository
from app.services.mission_errors import MissionNotFoundError
from app.services.provider_errors import UnsupportedMissionTypeError
from app.services.provider_resolver import (
    AmbiguousProviderError,
    NoSupportingProviderError,
    ProviderResolver,
)


class ProviderResolutionPreview(BaseModel):
    """Read-only result of resolving a mission against the current registry."""

    model_config = ConfigDict(frozen=True)

    mission_id: UUID
    mission_type: MissionType
    selection_mode: ProviderSelectionMode
    requested_provider_id: str | None
    outcome: ProviderResolutionPreviewOutcome
    resolved_provider_id: str | None
    candidate_provider_ids: tuple[str, ...]
    failure_reason: ProviderResolutionFailureReason | None

    @model_validator(mode="after")
    def validate_outcome(self) -> "ProviderResolutionPreview":
        expected_mode = selection_mode_for(self.requested_provider_id)
        if self.selection_mode is not expected_mode:
            raise ValueError("selection mode does not match requested provider")

        if self.outcome is ProviderResolutionPreviewOutcome.resolved:
            if (
                self.resolved_provider_id is None
                or self.failure_reason is not None
                or self.candidate_provider_ids
                != (self.resolved_provider_id,)
            ):
                raise ValueError("resolved preview is invalid")
            return self

        if self.resolved_provider_id is not None:
            raise ValueError("failed preview must not have a resolved provider")
        if self.failure_reason is None:
            raise ValueError("failed preview must have a failure reason")
        if self.outcome.value != self.failure_reason.value:
            raise ValueError("preview outcome does not match failure reason")

        if self.outcome in {
            ProviderResolutionPreviewOutcome.unknown_provider,
            ProviderResolutionPreviewOutcome.unsupported_mission_type,
        }:
            if self.requested_provider_id is None or self.candidate_provider_ids:
                raise ValueError("explicit provider failure preview is invalid")
        elif self.outcome is ProviderResolutionPreviewOutcome.no_supporting_provider:
            if self.requested_provider_id is not None or self.candidate_provider_ids:
                raise ValueError("no-supporting-provider preview is invalid")
        elif (
            self.requested_provider_id is not None
            or len(self.candidate_provider_ids) < 2
        ):
            raise ValueError("ambiguous-provider preview is invalid")
        return self


class PreviewMissionProviderResolution:
    def __init__(
        self,
        mission_repository: MissionRepository,
        provider_resolver: ProviderResolver,
    ) -> None:
        self._mission_repository = mission_repository
        self._provider_resolver = provider_resolver

    async def execute(self, mission_id: UUID) -> ProviderResolutionPreview:
        mission = await self._mission_repository.get(mission_id)
        if mission is None:
            raise MissionNotFoundError

        try:
            adapter = self._provider_resolver.resolve(mission)
        except UnknownProviderError:
            return _failure_preview(
                mission,
                ProviderResolutionPreviewOutcome.unknown_provider,
                ProviderResolutionFailureReason.unknown_provider,
            )
        except UnsupportedMissionTypeError:
            return _failure_preview(
                mission,
                ProviderResolutionPreviewOutcome.unsupported_mission_type,
                ProviderResolutionFailureReason.unsupported_mission_type,
            )
        except NoSupportingProviderError:
            return _failure_preview(
                mission,
                ProviderResolutionPreviewOutcome.no_supporting_provider,
                ProviderResolutionFailureReason.no_supporting_provider,
            )
        except AmbiguousProviderError as error:
            return _failure_preview(
                mission,
                ProviderResolutionPreviewOutcome.ambiguous_provider,
                ProviderResolutionFailureReason.ambiguous_provider,
                candidate_provider_ids=error.provider_ids,
            )

        return ProviderResolutionPreview(
            mission_id=mission.id,
            mission_type=mission.mission_type,
            selection_mode=selection_mode_for(mission.provider_id),
            requested_provider_id=mission.provider_id,
            outcome=ProviderResolutionPreviewOutcome.resolved,
            resolved_provider_id=adapter.provider_id,
            candidate_provider_ids=(adapter.provider_id,),
            failure_reason=None,
        )


def _failure_preview(
    mission: Mission,
    outcome: ProviderResolutionPreviewOutcome,
    failure_reason: ProviderResolutionFailureReason,
    *,
    candidate_provider_ids: tuple[str, ...] = (),
) -> ProviderResolutionPreview:
    return ProviderResolutionPreview(
        mission_id=mission.id,
        mission_type=mission.mission_type,
        selection_mode=selection_mode_for(mission.provider_id),
        requested_provider_id=mission.provider_id,
        outcome=outcome,
        resolved_provider_id=None,
        candidate_provider_ids=candidate_provider_ids,
        failure_reason=failure_reason,
    )
