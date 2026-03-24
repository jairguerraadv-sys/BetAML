"""
clickhouse_backfill.py — Popula tabelas ClickHouse com dados históricos do PostgreSQL.

Uso:
  python scripts/clickhouse_backfill.py --days 90
  python scripts/clickhouse_backfill.py --days 30 --tenant-id <uuid>

O script copia:
  - financial_transactions  → ClickHouse.transactions_raw
  - bets                    → ClickHouse.bets_raw
  - alerts                  → ClickHouse.alerts_raw
  - feature_snapshots       → ClickHouse.player_features_daily
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "api"))

import structlog
from clickhouse_driver import Client as ClickHouseClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import settings

logger = structlog.get_logger(__name__)

_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(_url, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)


def get_ch_client() -> ClickHouseClient:
    return ClickHouseClient(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        database=settings.clickhouse_db,
    )


async def backfill_transactions(days: int, tenant_id: str | None, ch: ClickHouseClient) -> int:
    """Copia financial_transactions do Postgres para ClickHouse."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    count = 0

    async with Session() as db:
        stmt = text("""
            SELECT id, tenant_id, player_id, transaction_type, amount,
                   currency, status, payment_method, transaction_timestamp
            FROM financial_transactions
            WHERE transaction_timestamp >= :cutoff
            {:tenant_filter}
            ORDER BY transaction_timestamp
        """.format(tenant_filter="AND tenant_id = :tenant_id" if tenant_id else ""))

        params = {"cutoff": cutoff}
        if tenant_id:
            params["tenant_id"] = tenant_id

        result = await db.execute(stmt, params)
        rows = result.fetchall()

        if rows:
            ch.execute(
                """
                INSERT INTO transactions_raw
                (id, tenant_id, player_id, transaction_type, amount,
                 currency, status, payment_method, occurred_at)
                VALUES
                """,
                [
                    {
                        "id": str(r.id),
                        "tenant_id": str(r.tenant_id),
                        "player_id": str(r.player_id),
                        "transaction_type": r.transaction_type or "",
                        "amount": float(r.amount or 0),
                        "currency": r.currency or "BRL",
                        "status": r.status or "",
                        "payment_method": r.payment_method or "",
                        "occurred_at": r.transaction_timestamp,
                    }
                    for r in rows
                ],
            )
            count = len(rows)

    logger.info("backfill_transactions_done", count=count, days=days)
    return count


async def backfill_alerts(days: int, tenant_id: str | None, ch: ClickHouseClient) -> int:
    """Copia alerts do Postgres para ClickHouse."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    count = 0

    async with Session() as db:
        stmt = text("""
            SELECT id, tenant_id, player_id, rule_id, severity, status,
                   risk_score, label, created_at
            FROM alerts
            WHERE created_at >= :cutoff
            {:tenant_filter}
            ORDER BY created_at
        """.format(tenant_filter="AND tenant_id = :tenant_id" if tenant_id else ""))

        params = {"cutoff": cutoff}
        if tenant_id:
            params["tenant_id"] = tenant_id

        result = await db.execute(stmt, params)
        rows = result.fetchall()

        if rows:
            ch.execute(
                """
                INSERT INTO alerts_raw
                (id, tenant_id, player_id, rule_id, severity, status, risk_score, label, created_at)
                VALUES
                """,
                [
                    {
                        "id": str(r.id),
                        "tenant_id": str(r.tenant_id),
                        "player_id": str(r.player_id) if r.player_id else "",
                        "rule_id": str(r.rule_id) if r.rule_id else "",
                        "severity": r.severity or "LOW",
                        "status": r.status or "OPEN",
                        "risk_score": float(r.risk_score or 0),
                        "label": r.label or "",
                        "created_at": r.created_at,
                    }
                    for r in rows
                ],
            )
            count = len(rows)

    logger.info("backfill_alerts_done", count=count, days=days)
    return count


async def main(days: int, tenant_id: str | None) -> None:
    logger.info("clickhouse_backfill_started", days=days, tenant_id=tenant_id)
    ch = get_ch_client()

    try:
        total_tx = await backfill_transactions(days, tenant_id, ch)
        total_alerts = await backfill_alerts(days, tenant_id, ch)
        logger.info(
            "clickhouse_backfill_completed",
            transactions=total_tx,
            alerts=total_alerts,
        )
        print(f"Backfill concluido: {total_tx} transacoes, {total_alerts} alertas")
    except Exception as exc:
        logger.error("clickhouse_backfill_failed", error=str(exc))
        print(f"Erro no backfill: {exc}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ClickHouse backfill from PostgreSQL")
    parser.add_argument("--days", type=int, default=90, help="Number of days to backfill (default: 90)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Limit to specific tenant UUID")
    args = parser.parse_args()

    asyncio.run(main(args.days, args.tenant_id))
