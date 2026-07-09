from abc import ABC, abstractmethod

from app.domain.identity import Identity
from app.domain.mission import Mission
from app.domain.provider import ProviderOption, ReservationResult


class ProviderAdapter(ABC):
    provider_id: str

    @abstractmethod
    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        raise NotImplementedError

    @abstractmethod
    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        raise NotImplementedError
