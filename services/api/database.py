"""SQLAlchemy async engine + session factory."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

# asyncpg driver → substitui postgresql:// por postgresql+asyncpg://
_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(_url, pool_size=10, max_overflow=20, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session
