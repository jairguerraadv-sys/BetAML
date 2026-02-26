"""ClickHouse writer for the stream_processor service."""

from __future__ import annotations

import logging
from typing import Any

from clients.clickhouse_client import ClickHouseClient

logger = logging.getLogger(__name__)


class ClickHouseWriter:
    """Writes canonical events and player features to ClickHouse.

    All write methods swallow connection errors so a single bad write cannot
    crash the consumer loop.  Errors are logged at ERROR level.
    """

    def __init__(self, host: str, user: str, password: str, database: str) -> None:
        self._db = database
        self._client = ClickHouseClient(
            host=host,
            user=user,
            password=password,
            database=database,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _table(self, name: str) -> str:
        return f"{self._db}.{name}"

    def _safe_insert(self, table: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        try:
            self._client.insert(self._table(table), records)
        except Exception as exc:
            logger.error("ClickHouse insert into %s failed: %s", table, exc)

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    def write_transactions(self, records: list[dict[str, Any]]) -> None:
        """Insert transaction records into the ``transactions`` table."""
        self._safe_insert("transactions", records)

    def write_bets(self, records: list[dict[str, Any]]) -> None:
        """Insert bet records into the ``bets`` table."""
        self._safe_insert("bets", records)

    def write_player_features(self, records: list[dict[str, Any]]) -> None:
        """Insert feature snapshot records into the ``player_features`` table."""
        self._safe_insert("player_features", records)

    def write_alerts(self, records: list[dict[str, Any]]) -> None:
        """Insert alert records into the ``alerts`` table."""
        self._safe_insert("alerts", records)

    # ------------------------------------------------------------------
    # Read helpers (used by consumers to build feature windows)
    # ------------------------------------------------------------------

    def _fetch_rows(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute *query* and return rows as a list of column-name dicts."""
        try:
            rows = self._client.execute(query, params)
            if not rows:
                return []
            # clickhouse-driver returns list[tuple]; use WITH NAMES syntax if
            # available, otherwise fall back to positional column extraction.
            # We use SELECT * with LIMIT so we need the column names separately.
            return rows  # caller responsible for column mapping
        except Exception as exc:
            logger.error("ClickHouse query failed: %s", exc)
            return []

    def fetch_player_transactions(
        self, tenant_id: str, player_id: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """Return the last *days* days of transactions for *player_id* as dicts."""
        try:
            query = (
                f"SELECT id, tenant_id, player_id, player_cpf, transaction_type, "
                f"amount, currency, method, status, payment_instrument, occurred_at "
                f"FROM {self._table('transactions')} "
                f"WHERE tenant_id = %(tenant_id)s "
                f"  AND player_id = %(player_id)s "
                f"  AND occurred_at >= now() - toIntervalDay(%(days)s) "
                f"ORDER BY occurred_at ASC"
            )
            rows = self._client.execute(
                query,
                {"tenant_id": tenant_id, "player_id": player_id, "days": days},
            )
            columns = [
                "id", "tenant_id", "player_id", "player_cpf", "transaction_type",
                "amount", "currency", "method", "status", "payment_instrument", "occurred_at",
            ]
            return [dict(zip(columns, row)) for row in rows]
        except Exception as exc:
            logger.error("fetch_player_transactions failed for player %s: %s", player_id, exc)
            return []

    def fetch_player_bets(
        self, tenant_id: str, player_id: str, days: int = 30
    ) -> list[dict[str, Any]]:
        """Return the last *days* days of bets for *player_id* as dicts."""
        try:
            query = (
                f"SELECT id, tenant_id, player_id, player_cpf, external_bet_id, "
                f"stake_amount, odds, potential_payout, settled_payout, "
                f"market_type, sport, event_id, selection, channel, placed_at, settled_at "
                f"FROM {self._table('bets')} "
                f"WHERE tenant_id = %(tenant_id)s "
                f"  AND player_id = %(player_id)s "
                f"  AND placed_at >= now() - toIntervalDay(%(days)s) "
                f"ORDER BY placed_at ASC"
            )
            rows = self._client.execute(
                query,
                {"tenant_id": tenant_id, "player_id": player_id, "days": days},
            )
            columns = [
                "id", "tenant_id", "player_id", "player_cpf", "external_bet_id",
                "stake_amount", "odds", "potential_payout", "settled_payout",
                "market_type", "sport", "event_id", "selection", "channel", "placed_at", "settled_at",
            ]
            return [dict(zip(columns, row)) for row in rows]
        except Exception as exc:
            logger.error("fetch_player_bets failed for player %s: %s", player_id, exc)
            return []

    def close(self) -> None:
        self._client.close()
