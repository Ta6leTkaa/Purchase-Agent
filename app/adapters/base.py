from abc import ABC, abstractmethod

from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType
from app.domain.provider import (
    CancellationResult,
    ConfirmationResult,
    ProviderOption,
    ReservationResult,
)
from app.domain.provider_capability import ProviderCapability


class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Return a stable, non-empty identifier for this provider."""

        raise NotImplementedError

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[ProviderCapability]:
        """Return immutable mission capabilities declared by this adapter."""

        raise NotImplementedError

    def supports(self, mission_type: MissionType) -> bool:
        return any(
            capability.mission_type == mission_type
            for capability in self.capabilities
        )

    @abstractmethod
    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        """Return provider options or raise ProviderOperationError."""

        raise NotImplementedError

    @abstractmethod
    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> ReservationResult:
        """Reserve an option or raise ProviderOperationError."""

        raise NotImplementedError

    async def confirm_reservation(
        self,
        reservation_id: str,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> ConfirmationResult:
        """Confirm a reservation or raise ProviderOperationError."""

        raise NotImplementedError

    async def cancel_reservation(
        self,
        reservation_id: str,
        mission: Mission,
        *,
        idempotency_key: str,
    ) -> CancellationResult:
        """Cancel a reservation or raise ProviderOperationError."""

        raise NotImplementedError
