"""Database session factory for the rules_engine service.

Uses synchronous psycopg2 so consumers (which run in threads) can do
straightforward blocking DB calls without an async event loop.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def create_session_factory(database_url: str):
    """Return a ``(SessionFactory, Engine)`` tuple for *database_url*.

    Converts ``asyncpg`` URLs to ``psycopg2`` automatically so the same
    DATABASE_URL env-var used by the async API service can be passed here.
    """
    sync_url = (
        database_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("asyncpg://", "postgresql+psycopg2://")
    )
    engine = create_engine(sync_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return factory, engine
