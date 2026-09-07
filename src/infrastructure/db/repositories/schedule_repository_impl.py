from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.scheduled_task import ScheduledTask
from src.domain.interfaces.repositories import IScheduleRepository

_ALLOWED_UPDATE_FIELDS = frozenset({"instruction", "cron", "run_at", "enabled"})


class ScheduleRepositoryImpl(IScheduleRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        thread_id: int,
        instruction: str,
        run_at: datetime,
        cron: str | None,
        created_by: str | None,
    ) -> ScheduledTask:
        task = ScheduledTask(
            thread_id=thread_id,
            instruction=instruction,
            run_at=run_at,
            cron=cron,
            created_by=created_by,
            enabled=True,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def list_for_thread(self, thread_id: int) -> list[ScheduledTask]:
        rows = (
            (
                await self._session.execute(
                    select(ScheduledTask)
                    .where(ScheduledTask.thread_id == thread_id)
                    .order_by(ScheduledTask.enabled.desc(), ScheduledTask.run_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def get_by_id(self, task_id: int) -> ScheduledTask | None:
        return await self._session.get(ScheduledTask, task_id)

    async def get_for_thread(self, thread_id: int, task_id: int) -> ScheduledTask | None:
        return (
            await self._session.execute(
                select(ScheduledTask).where(
                    ScheduledTask.id == task_id, ScheduledTask.thread_id == thread_id
                )
            )
        ).scalar_one_or_none()

    async def update_fields(
        self, thread_id: int, task_id: int, **fields: object
    ) -> ScheduledTask | None:
        # `cron` may legitimately be set to None (recurring -> one-off); every
        # other field is skipped when None ("not supplied").
        clean = {
            k: v
            for k, v in fields.items()
            if k in _ALLOWED_UPDATE_FIELDS and (v is not None or k == "cron")
        }
        if clean:
            await self._session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id == task_id, ScheduledTask.thread_id == thread_id)
                .values(**clean)
            )
            await self._session.flush()
        return await self.get_for_thread(thread_id, task_id)

    async def delete(self, thread_id: int, task_id: int) -> bool:
        result = await self._session.execute(
            delete(ScheduledTask).where(
                ScheduledTask.id == task_id, ScheduledTask.thread_id == thread_id
            )
        )
        return bool(getattr(result, "rowcount", 0) or 0)

    async def due(self, now: datetime, limit: int) -> list[ScheduledTask]:
        rows = (
            (
                await self._session.execute(
                    select(ScheduledTask)
                    .where(ScheduledTask.enabled.is_(True), ScheduledTask.run_at <= now)
                    .order_by(ScheduledTask.run_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def mark_ran(self, task_id: int, ran_at: datetime, next_run_at: datetime | None) -> None:
        await self._session.execute(
            update(ScheduledTask)
            .where(ScheduledTask.id == task_id)
            .values(
                last_run_at=ran_at,
                run_at=next_run_at if next_run_at is not None else ScheduledTask.run_at,
                enabled=next_run_at is not None,
            )
        )
