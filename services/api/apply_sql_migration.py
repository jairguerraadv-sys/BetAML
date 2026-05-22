"""Apply an idempotent SQL migration file using DATABASE_URL.

Used by docker-compose for local/dev stacks where Postgres volumes may already
exist, so docker-entrypoint init scripts will not run again.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python apply_sql_migration.py /path/to/migration.sql")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    sql_path = Path(sys.argv[1])
    sql = sql_path.read_text(encoding="utf-8")

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
