import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.due_mission_processor import DueMissionProcessingResult

MissionWorkerCycle = Callable[[], Awaitable["MissionWorkerCycleResult"]]
MissionWorkerErrorHandler = Callable[[Exception], Awaitable[None]]


class MissionWorkerCycleResult(BaseModel):
    recovered_mission_ids: list[UUID] = Field(default_factory=list)
    stale_failed_mission_ids: list[UUID] = Field(default_factory=list)
    processing: DueMissionProcessingResult


@dataclass(frozen=True, slots=True)
class MissionWorkerRunResult:
    completed_cycles: int
    failed_cycles: int


async def run_mission_worker[WorkerCycleResultT](
    process_cycle: Callable[[], Awaitable[WorkerCycleResultT]],
    *,
    poll_interval: timedelta,
    stop_event: asyncio.Event,
    on_result: Callable[[WorkerCycleResultT], Awaitable[None]],
    on_error: MissionWorkerErrorHandler,
    max_cycles: int | None = None,
) -> MissionWorkerRunResult:
    if poll_interval <= timedelta(0):
        raise ValueError("poll_interval must be greater than zero")
    if max_cycles is not None and max_cycles < 1:
        raise ValueError("max_cycles must be at least one")

    completed_cycles = 0
    failed_cycles = 0
    attempted_cycles = 0
    while not stop_event.is_set():
        try:
            result = await process_cycle()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            failed_cycles += 1
            await on_error(exc)
        else:
            completed_cycles += 1
            await on_result(result)

        attempted_cycles += 1
        if max_cycles is not None and attempted_cycles >= max_cycles:
            break
        await _wait_for_stop(stop_event, poll_interval)

    return MissionWorkerRunResult(
        completed_cycles=completed_cycles,
        failed_cycles=failed_cycles,
    )


async def _wait_for_stop(
    stop_event: asyncio.Event,
    poll_interval: timedelta,
) -> None:
    try:
        await asyncio.wait_for(
            stop_event.wait(),
            timeout=poll_interval.total_seconds(),
        )
    except TimeoutError:
        pass
