from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.storage.memory import InMemoryIdentityRepository, InMemoryMissionRepository

identity_repository = InMemoryIdentityRepository()
mission_repository = InMemoryMissionRepository()


def get_identity_repository() -> IdentityRepository:
    return identity_repository


def get_mission_repository() -> MissionRepository:
    return mission_repository
