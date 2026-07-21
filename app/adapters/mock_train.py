from datetime import datetime, time, timedelta
from typing import Final
from uuid import uuid4

from app.adapters.base import ProviderAdapter
from app.domain.identity import Identity
from app.domain.mission import Mission, MissionType
from app.domain.provider_capability import ProviderCapability
from app.domain.provider import (
    ProviderOption,
    ProviderOptionType,
    ReservationResult,
    Seat,
    SeatBerth,
)


class MockTrainAdapter(ProviderAdapter):
    PROVIDER_ID: Final = "mock_train"
    _CAPABILITIES = frozenset(
        {
            ProviderCapability(
                mission_type=MissionType.TRAIN_TICKET,
            )
        }
    )

    @property
    def provider_id(self) -> str:
        return self.PROVIDER_ID

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return self._CAPABILITIES

    async def search_options(
        self,
        mission: Mission,
        identities: list[Identity],
    ) -> list[ProviderOption]:
        departure_at = datetime.combine(
            mission.payload.departure_date,
            time(hour=20),
        )
        arrival_at = departure_at + timedelta(hours=10)

        return [
            self._build_option(
                mission=mission,
                train_number="001A",
                total_price=28400,
                departure_at=departure_at,
                arrival_at=arrival_at,
                seats=[
                    self._seat(1, 1, 1, SeatBerth.lower),
                    self._seat(1, 1, 2, SeatBerth.upper),
                    self._seat(1, 1, 3, SeatBerth.lower),
                    self._seat(1, 1, 4, SeatBerth.upper),
                ],
            ),
            self._build_option(
                mission=mission,
                train_number="002B",
                total_price=25900,
                departure_at=departure_at + timedelta(minutes=30),
                arrival_at=arrival_at + timedelta(minutes=30),
                seats=[
                    self._seat(1, 2, 5, SeatBerth.lower),
                    self._seat(1, 2, 6, SeatBerth.upper),
                    self._seat(1, 2, 7, SeatBerth.upper),
                    self._seat(1, 2, 8, SeatBerth.upper),
                ],
            ),
            self._build_option(
                mission=mission,
                train_number="003C",
                total_price=24000,
                departure_at=departure_at + timedelta(hours=1),
                arrival_at=arrival_at + timedelta(hours=1),
                seats=[
                    self._seat(2, 3, 9, SeatBerth.lower),
                    self._seat(2, 3, 10, SeatBerth.upper),
                    self._seat(2, 4, 11, SeatBerth.lower),
                    self._seat(2, 4, 12, SeatBerth.upper),
                ],
            ),
            self._build_option(
                mission=mission,
                train_number="004D",
                total_price=22000,
                departure_at=departure_at + timedelta(hours=2),
                arrival_at=arrival_at + timedelta(hours=2),
                seats=[
                    self._seat(3, 5, 13, SeatBerth.lower, near_toilet=True),
                    self._seat(3, 5, 14, SeatBerth.upper),
                    self._seat(3, 5, 15, SeatBerth.lower),
                    self._seat(3, 5, 16, SeatBerth.upper),
                ],
            ),
        ]

    async def reserve_option(
        self,
        option: ProviderOption,
        mission: Mission,
    ) -> ReservationResult:
        return ReservationResult(
            success=True,
            reservation_id=f"mock-reservation-{option.id}",
            requires_confirmation=True,
            message="Mock reservation created. User confirmation required.",
        )

    def _build_option(
        self,
        *,
        mission: Mission,
        train_number: str,
        total_price: int,
        departure_at: datetime,
        arrival_at: datetime,
        seats: list[Seat],
    ) -> ProviderOption:
        return ProviderOption(
            id=uuid4(),
            type=ProviderOptionType.train_option,
            train_number=train_number,
            from_city=mission.payload.origin,
            to_city=mission.payload.destination,
            departure_at=departure_at,
            arrival_at=arrival_at,
            total_price=total_price,
            seats=seats,
        )

    def _seat(
        self,
        carriage_number: int,
        compartment_number: int,
        seat_number: int,
        berth: SeatBerth,
        *,
        near_toilet: bool = False,
    ) -> Seat:
        return Seat(
            carriage_number=carriage_number,
            compartment_number=compartment_number,
            seat_number=seat_number,
            berth=berth,
            near_toilet=near_toilet,
        )
