from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.agy_session import AgySession
from src.domain.interfaces.repositories import ISessionRepository


class SessionRepositoryImpl(ISessionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mint(
        self,
        thread_id: int,
        ttl_seconds: int,
        trigger_tg_message_id: int | None = None,
    ) -> str:
        key = secrets.token_urlsafe(24)
        self._session.add(
            AgySession(
                session_key=key,
                thread_id=thread_id,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
                trigger_tg_message_id=trigger_tg_message_id,
                sent_count=0,
            )
        )
        await self._session.flush()
        return key

    async def resolve(self, session_key: str) -> int | None:
        return (
            await self._session.execute(
                select(AgySession.thread_id).where(
                    AgySession.session_key == session_key,
                    AgySession.expires_at > func.now(),
                )
            )
        ).scalar_one_or_none()

    async def get_active(self, session_key: str) -> AgySession | None:
        return (
            await self._session.execute(
                select(AgySession).where(
                    AgySession.session_key == session_key,
                    AgySession.expires_at > func.now(),
                )
            )
        ).scalar_one_or_none()

    async def bump_sent_count(self, session_key: str, by: int) -> int:
        row = (
            await self._session.execute(
                update(AgySession)
                .where(AgySession.session_key == session_key)
                .values(sent_count=AgySession.sent_count + by)
                .returning(AgySession.sent_count)
            )
        ).scalar_one_or_none()
        return int(row or 0)

    async def delete(self, session_key: str) -> None:
        await self._session.execute(delete(AgySession).where(AgySession.session_key == session_key))

    async def purge_expired(self) -> int:
        result = await self._session.execute(
            delete(AgySession).where(AgySession.expires_at < func.now())
        )
        return int(getattr(result, "rowcount", 0) or 0)
