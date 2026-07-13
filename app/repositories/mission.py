from typing import Protocol
from uuid import UUID

from app.domain.mission import Mission


class MissionRepository(Protocol):
    def create(self, mission: Mission) -> Mission:
        ...

    def list(self) -> list[Mission]:
        ...

    def get(self, mission_id: UUID) -> Mission | None:
        ...

    def update(self, mission: Mission) -> Mission:
        ...

    def clear(self) -> None:
        ...
