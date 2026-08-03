from threading import Lock

from pydantic import BaseModel, Field


class HttpMetricsSnapshot(BaseModel):
    total_requests: int = Field(ge=0)
    in_flight_requests: int = Field(ge=0)
    requests_by_method: dict[str, int]
    responses_by_status_class: dict[str, int]
    average_duration_ms: float = Field(ge=0)
    max_duration_ms: float = Field(ge=0)


class HttpMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._total_requests = 0
        self._in_flight_requests = 0
        self._requests_by_method: dict[str, int] = {}
        self._responses_by_status_class: dict[str, int] = {}
        self._total_duration_ms = 0.0
        self._max_duration_ms = 0.0

    def start_request(self) -> None:
        with self._lock:
            self._in_flight_requests += 1

    def finish_request(
        self,
        *,
        method: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        status_class = f"{status_code // 100}xx"
        with self._lock:
            self._in_flight_requests = max(0, self._in_flight_requests - 1)
            self._total_requests += 1
            self._requests_by_method[method] = (
                self._requests_by_method.get(method, 0) + 1
            )
            self._responses_by_status_class[status_class] = (
                self._responses_by_status_class.get(status_class, 0) + 1
            )
            self._total_duration_ms += duration_ms
            self._max_duration_ms = max(self._max_duration_ms, duration_ms)

    def snapshot(self) -> HttpMetricsSnapshot:
        with self._lock:
            average = (
                self._total_duration_ms / self._total_requests
                if self._total_requests
                else 0.0
            )
            return HttpMetricsSnapshot(
                total_requests=self._total_requests,
                in_flight_requests=self._in_flight_requests,
                requests_by_method=dict(sorted(self._requests_by_method.items())),
                responses_by_status_class=dict(
                    sorted(self._responses_by_status_class.items())
                ),
                average_duration_ms=round(average, 3),
                max_duration_ms=round(self._max_duration_ms, 3),
            )

    def reset(self) -> None:
        with self._lock:
            self._total_requests = 0
            self._in_flight_requests = 0
            self._requests_by_method.clear()
            self._responses_by_status_class.clear()
            self._total_duration_ms = 0.0
            self._max_duration_ms = 0.0


http_metrics = HttpMetrics()
