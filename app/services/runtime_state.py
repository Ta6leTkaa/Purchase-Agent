from datetime import datetime
from enum import StrEnum
from threading import Lock

from pydantic import BaseModel


class RuntimeTrafficState(StrEnum):
    accepting = "accepting"
    draining = "draining"


class RuntimeStateSnapshot(BaseModel):
    traffic_state: RuntimeTrafficState
    accepting_traffic: bool
    draining_since: datetime | None


class RuntimeState:
    def __init__(self) -> None:
        self._lock = Lock()
        self._draining_since: datetime | None = None

    def begin_draining(self, current_time: datetime) -> RuntimeStateSnapshot:
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise ValueError("current_time must be timezone-aware")
        with self._lock:
            if self._draining_since is None:
                self._draining_since = current_time
            return self._snapshot_unlocked()

    def resume(self) -> RuntimeStateSnapshot:
        with self._lock:
            self._draining_since = None
            return self._snapshot_unlocked()

    def snapshot(self) -> RuntimeStateSnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> RuntimeStateSnapshot:
        accepting = self._draining_since is None
        return RuntimeStateSnapshot(
            traffic_state=(
                RuntimeTrafficState.accepting
                if accepting
                else RuntimeTrafficState.draining
            ),
            accepting_traffic=accepting,
            draining_since=self._draining_since,
        )


runtime_state = RuntimeState()
