import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import TextIO

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import async_session_maker
from app.repositories.identity import IdentityRepository
from app.repositories.mission import MissionRepository
from app.repositories.sqlalchemy.identity import SqlAlchemyIdentityRepository
from app.repositories.sqlalchemy.mission import SqlAlchemyMissionRepository
from app.services.clock import utc_now
from app.services.due_mission_processor import (
    DueMissionProcessingResult,
    process_due_missions,
)


async def process_due_command(
    limit: int,
    *,
    session_maker: async_sessionmaker[AsyncSession] | None = None,
    mission_repository: MissionRepository | None = None,
    identity_repository: IdentityRepository | None = None,
    current_time: datetime | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr
    now = current_time or utc_now()

    try:
        if mission_repository is not None and identity_repository is not None:
            result = await process_due_missions(
                mission_repository,
                identity_repository,
                now,
                limit=limit,
            )
        else:
            result = await _process_due_with_database_session(
                session_maker or async_session_maker,
                now,
                limit,
            )
    except Exception:
        error_output.write("Infrastructure error while processing due missions.\n")
        return 1

    output.write(result.model_dump_json() + "\n")
    if result.failed_mission_ids:
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(asyncio.run(process_due_command(args.limit)))


async def _process_due_with_database_session(
    session_maker: async_sessionmaker[AsyncSession],
    current_time: datetime,
    limit: int,
) -> DueMissionProcessingResult:
    async with session_maker() as session:
        try:
            result = await process_due_missions(
                SqlAlchemyMissionRepository(session),
                SqlAlchemyIdentityRepository(session),
                current_time,
                limit=limit,
            )
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Purchase Agent command line tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    process_due_parser = subparsers.add_parser(
        "process-due",
        help="Run one processing cycle for due missions.",
    )
    process_due_parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=100,
        help="Maximum number of due missions to process, from 1 to 500.",
    )

    return parser


def _parse_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc

    if limit < 1 or limit > 500:
        raise argparse.ArgumentTypeError("limit must be between 1 and 500")
    return limit


if __name__ == "__main__":
    main()
