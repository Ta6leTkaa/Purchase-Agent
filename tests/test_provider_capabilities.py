from typing import cast

import pytest
from pydantic import ValidationError

from app.adapters.base import ProviderAdapter
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType
from app.domain.provider import ProviderOption, ReservationResult
from app.domain.provider_capability import ProviderCapability


class TrainOnlyAdapter(ProviderAdapter):
    @property
    def provider_id(self) -> str:
        return "train-only"

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset(
            {
                ProviderCapability(
                    mission_type=MissionType.TRAIN_TICKET,
                )
            }
        )

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        return []

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        raise NotImplementedError


def test_provider_capability_is_immutable_hashable_and_value_equal() -> None:
    capability = ProviderCapability(mission_type=MissionType.TRAIN_TICKET)

    assert capability == ProviderCapability(
        mission_type=MissionType.TRAIN_TICKET,
    )
    assert {capability} == {
        ProviderCapability(mission_type=MissionType.TRAIN_TICKET)
    }
    with pytest.raises(ValidationError):
        capability.mission_type = MissionType.TRAIN_TICKET


def test_adapter_supports_declared_mission_type_only() -> None:
    adapter = TrainOnlyAdapter()

    assert adapter.supports(MissionType.TRAIN_TICKET) is True
    assert adapter.supports(cast(MissionType, "unsupported")) is False
