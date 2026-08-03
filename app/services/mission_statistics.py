from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.mission import MissionStatus


class MissionStatistics(BaseModel):
    generated_at: datetime
    total_missions: int = Field(ge=0)
    missions_by_status: dict[MissionStatus, int]
    due_missions: int = Field(ge=0)
    expired_pending_missions: int = Field(ge=0)
    stale_processing_missions: int = Field(ge=0)
    exhausted_waiting_missions: int = Field(ge=0)
    claim_timeout_seconds: int = Field(ge=1)
