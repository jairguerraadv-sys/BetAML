"""ClickHouse client wrapper using clickhouse-driver."""

from __future__ import annotations

import logging
from typing import Any, Optional

from clickhouse_driver import Client

logger = logging.getLogger(__name__)


class ClickHouseClient:
    """Thin wrapper around :class:`clickhouse_driver.Client`.

    Parameters
    ----------
    host:
        ClickHouse server host (default ``localhost``).
    port:
        Native TCP port (default ``9000``).
    database:
        Default database (default ``default``).
    user:
        Database user (default ``default``).
    password:
        Database password (default empty string).
    settings:
        Additional driver settings dict passed to :class:`clickhouse_driver.Client`.
    client:
        Inject a pre-built :class:`clickhouse_driver.Client` instance for
        testing.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9000,
        database: str = "default",
        user: str = "default",
        password: str = "",
        settings: Optional[dict[str, Any]] = None,
        client: Optional[Client] = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = Client(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                settings=settings or {},
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        query: str,
        params: Optional[dict[str, Any]] = None,
    ) -> list[Any]:
        """Execute an arbitrary *query* and return the result rows.

        Parameters
        ----------
        query:
            SQL string (may contain ``%(name)s`` placeholders when *params* is
            supplied).
        params:
            Optional parameter dict for safe value substitution.

        Returns
        -------
        list
            Rows returned by the server (empty list for DDL / DML statements).
        """
        logger.debug("Executing ClickHouse query: %.200s", query)
        return self._client.execute(query, params or {})

    def insert(self, table: str, records: list[dict[str, Any]]) -> int:
        """Bulk-insert *records* into *table*.

        The column names are derived from the keys of the **first** record.
        All records must have the same set of keys.

        Parameters
        ----------
        table:
            Fully-qualified table name (e.g. ``default.transactions``).
        records:
            List of row dicts.  All dicts must have identical key sets.

        Returns
        -------
        int
            Number of rows inserted (mirrors ``clickhouse_driver`` return).

        Raises
        ------
        ValueError
            When *records* is empty.
        """
        if not records:
            raise ValueError("records must not be empty")

        columns = list(records[0].keys())
        rows = [[row[col] for col in columns] for row in records]
        col_list = ", ".join(columns)
        query = f"INSERT INTO {table} ({col_list}) VALUES"
        logger.debug("Inserting %d rows into %s", len(rows), table)
        return self._client.execute(query, rows)

    def ping(self) -> bool:
        """Return ``True`` if the server responds to a simple query."""
        try:
            self._client.execute("SELECT 1")
            return True
        except Exception:
            return False

    def close(self) -> None:
        """Disconnect from the server."""
        self._client.disconnect()

    def __enter__(self) -> "ClickHouseClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
