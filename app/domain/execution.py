from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ExecutionEvent(BaseModel):
    timestamp: datetime
    type: str
    message: str
    metadata: dict[str, Any] = {}
