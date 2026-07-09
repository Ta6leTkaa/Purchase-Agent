from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.domain.mission import Mission
from app.storage.memory import store

router = APIRouter(prefix="/missions", tags=["missions"])


@router.post("")
def create_mission(mission: Mission) -> Mission:
    return store.create_mission(mission)


@router.get("")
def list_missions() -> list[Mission]:
    return store.list_missions()


@router.get("/{mission_id}")
def get_mission(mission_id: UUID) -> Mission:
    mission = store.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission
