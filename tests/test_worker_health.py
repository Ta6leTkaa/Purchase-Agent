from datetime import UTC, datetime, timedelta

from app.domain.worker_health import (
    WorkerHeartbeat,
    WorkerKind,
    evaluate_worker_health,
)

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


def test_fresh_heartbeat_is_healthy() -> None:
    health = evaluate_worker_health(
        WorkerHeartbeat(
            worker_kind=WorkerKind.MISSION,
            instance_id="mission-worker-1",
            started_at=NOW - timedelta(hours=1),
            heartbeat_at=NOW - timedelta(seconds=10),
            last_success_at=NOW - timedelta(seconds=10),
        ),
        NOW,
        timedelta(seconds=60),
    )

    assert health.healthy
    assert health.heartbeat_age_seconds == 10
    assert health.consecutive_failures == 0


def test_stale_heartbeat_is_unhealthy_even_without_failures() -> None:
    health = evaluate_worker_health(
        WorkerHeartbeat(
            worker_kind=WorkerKind.NOTIFICATION,
            instance_id="notification-worker-1",
            started_at=NOW - timedelta(hours=1),
            heartbeat_at=NOW - timedelta(seconds=61),
            last_success_at=NOW - timedelta(seconds=61),
        ),
        NOW,
        timedelta(seconds=60),
    )

    assert not health.healthy
    assert health.heartbeat_age_seconds == 61


def test_fresh_failing_worker_is_unhealthy() -> None:
    health = evaluate_worker_health(
        WorkerHeartbeat(
            worker_kind=WorkerKind.MISSION,
            instance_id="mission-worker-1",
            started_at=NOW - timedelta(hours=1),
            heartbeat_at=NOW,
            last_success_at=NOW - timedelta(seconds=5),
            consecutive_failures=1,
        ),
        NOW,
        timedelta(seconds=60),
    )

    assert not health.healthy
