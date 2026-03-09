"""SQLAlchemy async engine + session factory."""
from __future__ import annotations

from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

# asyncpg driver → substitui postgresql:// por postgresql+asyncpg://
_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

# SQLite (testes unitários) não suporta pool_size/max_overflow
_is_sqlite = _url.startswith("sqlite")
_engine_kwargs: dict = {} if _is_sqlite else {"pool_size": 10, "max_overflow": 20}

engine = create_async_engine(_url, echo=False, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ContextVar que transporta o tenant_id corrente entre middleware e get_db
current_tenant_id: ContextVar[str | None] = ContextVar("current_tenant_id", default=None)


async def get_db() -> AsyncSession:  # type: ignore[return]
    """Dependency FastAPI: sessão async com RLS ativado para o tenant corrente."""
    async with AsyncSessionLocal() as session:
        tid = current_tenant_id.get()
        if tid:
            # SET LOCAL é escopo de transação no Postgres; usa-se SET (sessão) aqui
            # pois asyncpg reutiliza conexões do pool e cada request inicia nova sessão.
            await session.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": tid})
        yield session
