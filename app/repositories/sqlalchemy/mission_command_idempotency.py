from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.mission_command import MissionCommandReceiptModel
from app.services.mission_command_idempotency import (
    MissionCommandIdempotencyConflictError,
    MissionCommandInProgressError,
    MissionCommandType,
)


class SqlAlchemyMissionCommandIdempotencyStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin(
        self, *, key: str, mission_id: UUID, command: MissionCommandType
    ) -> UUID | None:
        inserted = await self._session.execute(
            insert(MissionCommandReceiptModel)
            .values(idempotency_key=key, mission_id=mission_id, command=command.value)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(MissionCommandReceiptModel.idempotency_key)
        )
        if inserted.scalar_one_or_none() is not None:
            return None
        receipt = await self._session.scalar(
            select(MissionCommandReceiptModel).where(
                MissionCommandReceiptModel.idempotency_key == key
            )
        )
        assert receipt is not None
        if receipt.mission_id != mission_id or receipt.command != command.value:
            raise MissionCommandIdempotencyConflictError
        if receipt.result_mission_id is None:
            raise MissionCommandInProgressError
        return receipt.result_mission_id

    async def complete(self, *, key: str, mission_id: UUID) -> None:
        await self._session.execute(
            update(MissionCommandReceiptModel)
            .where(MissionCommandReceiptModel.idempotency_key == key)
            .values(result_mission_id=mission_id)
        )

    async def abort(
        self,
        *,
        key: str,
        mission_id: UUID,
        command: MissionCommandType,
    ) -> None:
        await self._session.execute(
            delete(MissionCommandReceiptModel).where(
                MissionCommandReceiptModel.idempotency_key == key,
                MissionCommandReceiptModel.mission_id == mission_id,
                MissionCommandReceiptModel.command == command.value,
                MissionCommandReceiptModel.result_mission_id.is_(None),
            )
        )
