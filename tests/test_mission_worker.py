import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from app.services.due_mission_processor import DueMissionProcessingResult
from app.services.mission_worker import (
    MissionWorkerCycleResult,
    run_mission_worker,
)


def empty_cycle_result() -> MissionWorkerCycleResult:
    return MissionWorkerCycleResult(
        processing=DueMissionProcessingResult(processed_count=0)
    )


def test_worker_runs_bounded_cycles_and_reports_results() -> None:
    async def scenario() -> None:
        calls = 0
        results: list[MissionWorkerCycleResult] = []
        errors: list[Exception] = []

        async def process_cycle() -> MissionWorkerCycleResult:
            nonlocal calls
            calls += 1
            return MissionWorkerCycleResult(
                processing=DueMissionProcessingResult(
                    processed_count=1,
                    succeeded_mission_ids=[uuid4()],
                )
            )

        async def on_result(result: MissionWorkerCycleResult) -> None:
            results.append(result)

        async def on_error(error: Exception) -> None:
            errors.append(error)

        run_result = await run_mission_worker(
            process_cycle,
            poll_interval=timedelta(milliseconds=1),
            stop_event=asyncio.Event(),
            on_result=on_result,
            on_error=on_error,
            max_cycles=3,
        )

        assert calls == 3
        assert len(results) == 3
        assert errors == []
        assert run_result.completed_cycles == 3
        assert run_result.failed_cycles == 0

    asyncio.run(scenario())


def test_worker_continues_after_cycle_error() -> None:
    async def scenario() -> None:
        calls = 0
        results: list[MissionWorkerCycleResult] = []
        errors: list[Exception] = []

        async def process_cycle() -> MissionWorkerCycleResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("database unavailable")
            return empty_cycle_result()

        async def on_result(result: MissionWorkerCycleResult) -> None:
            results.append(result)

        async def on_error(error: Exception) -> None:
            errors.append(error)

        run_result = await run_mission_worker(
            process_cycle,
            poll_interval=timedelta(milliseconds=1),
            stop_event=asyncio.Event(),
            on_result=on_result,
            on_error=on_error,
            max_cycles=2,
        )

        assert len(errors) == 1
        assert isinstance(errors[0], ConnectionError)
        assert len(results) == 1
        assert run_result.completed_cycles == 1
        assert run_result.failed_cycles == 1

    asyncio.run(scenario())


def test_worker_stops_without_starting_cycle_when_already_signalled() -> None:
    async def scenario() -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        calls = 0

        async def process_cycle() -> MissionWorkerCycleResult:
            nonlocal calls
            calls += 1
            return empty_cycle_result()

        async def ignore_result(result: MissionWorkerCycleResult) -> None:
            del result

        async def ignore_error(error: Exception) -> None:
            del error

        result = await run_mission_worker(
            process_cycle,
            poll_interval=timedelta(seconds=1),
            stop_event=stop_event,
            on_result=ignore_result,
            on_error=ignore_error,
        )

        assert calls == 0
        assert result.completed_cycles == 0
        assert result.failed_cycles == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("poll_interval", "max_cycles"),
    [
        (timedelta(0), None),
        (timedelta(seconds=-1), None),
        (timedelta(seconds=1), 0),
    ],
)
def test_worker_rejects_invalid_configuration(
    poll_interval: timedelta,
    max_cycles: int | None,
) -> None:
    async def scenario() -> None:
        async def process_cycle() -> MissionWorkerCycleResult:
            return empty_cycle_result()

        async def ignore_result(result: MissionWorkerCycleResult) -> None:
            del result

        async def ignore_error(error: Exception) -> None:
            del error

        with pytest.raises(ValueError):
            await run_mission_worker(
                process_cycle,
                poll_interval=poll_interval,
                stop_event=asyncio.Event(),
                on_result=ignore_result,
                on_error=ignore_error,
                max_cycles=max_cycles,
            )

    asyncio.run(scenario())
