#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import psycopg2


@dataclass
class CheckResult:
    name: str
    value: int
    threshold: int
    ok: bool
    details: str


def _sync_db_url(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def _query_one(conn, sql: str) -> int:
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] or 0)


def run_checks(conn) -> list[CheckResult]:
    checks: list[tuple[str, str, int, str]] = [
        (
            "players_without_tenant",
            "SELECT COUNT(*) FROM players WHERE tenant_id IS NULL",
            0,
            "Players sem tenant_id devem ser zero.",
        ),
        (
            "alerts_invalid_status",
            "SELECT COUNT(*) FROM alerts WHERE status NOT IN ('OPEN','IN_REVIEW','CLOSED','FALSE_POSITIVE')",
            0,
            "Alertas devem ter status valido.",
        ),
        (
            "feature_snapshots_missing_version",
            "SELECT COUNT(*) FROM feature_snapshots WHERE feature_version IS NULL",
            0,
            "Snapshots de features devem ter feature_version.",
        ),
        (
            "unresolved_ingest_errors_24h",
            """
            SELECT COUNT(*)
            FROM ingest_errors
            WHERE resolved = false
              AND created_at < (now() - interval '24 hours')
            """,
            100,
            "Ingest errors antigos nao resolvidos devem ficar abaixo de 100.",
        ),
    ]

    results: list[CheckResult] = []
    for name, sql, threshold, details in checks:
        value = _query_one(conn, sql)
        ok = value <= threshold
        results.append(CheckResult(name=name, value=value, threshold=threshold, ok=ok, details=details))
    return results


def main() -> int:
    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@localhost:5432/betaml_dev")
    db_url = _sync_db_url(db_url)

    try:
        conn = psycopg2.connect(db_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[DQ] erro ao conectar no PostgreSQL: {exc}", file=sys.stderr)
        return 2

    try:
        results = run_checks(conn)
    finally:
        conn.close()

    failures = [r for r in results if not r.ok]

    print("[DQ] Data quality report")
    for r in results:
        status = "OK" if r.ok else "FAIL"
        print(f" - {r.name}: {status} (value={r.value}, threshold<={r.threshold})")

    if failures:
        print("[DQ] Falhas criticas detectadas:")
        for r in failures:
            print(f"   * {r.name}: {r.details}")
        return 1

    print("[DQ] Todos os checks passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
