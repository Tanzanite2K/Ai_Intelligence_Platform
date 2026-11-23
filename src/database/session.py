"""Async engine/session factory (PostgreSQL via asyncpg in prod, SQLite for local/tests)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.config.settings import get_settings
from src.database.models import Base


class Database:
    def __init__(self, url: str | None = None) -> None:
        s = get_settings()
        self.url = url or s.database_url
        kwargs: dict = {"pool_pre_ping": True}
        if self.url.startswith("postgresql"):
            kwargs.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow)
        self.engine: AsyncEngine = create_async_engine(self.url, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    async def dispose(self) -> None:
        await self.engine.dispose()
