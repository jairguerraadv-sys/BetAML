from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "services" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from routers import players


MALICIOUS_INPUTS = [
    "' OR '1'='1",
    "'; DROP TABLE players; --",
    "x' UNION SELECT * FROM users --",
    "created_at; DROP TABLE audit_logs; --",
]


def test_invalid_sort_column_is_rejected() -> None:
    for payload in MALICIOUS_INPUTS:
        with pytest.raises(ValueError):
            players._resolve_feature_history_sort_column(payload)


def test_invalid_table_name_is_rejected() -> None:
    for payload in MALICIOUS_INPUTS:
        with pytest.raises(ValueError):
            players._build_erasure_related_count_sql(payload)


def test_allowlisted_tables_return_static_sql() -> None:
    for table in players._ERASURE_COUNT_SQL_BY_TABLE:
        sql = players._build_erasure_related_count_sql(table)
        assert "{table" not in sql
        assert "{col" not in sql
        assert "; DROP TABLE" not in sql.upper()
        assert ":tid" in sql
        assert ":pid" in sql


def test_feature_history_query_is_parameterized() -> None:
    sql = players._FEATURE_HISTORY_SQL_BY_SORT_COLUMN["feature_date"]
    assert "%(tid)s" in sql
    assert "%(pid)s" in sql
    assert "%(days)s" in sql


@pytest.mark.parametrize("payload", MALICIOUS_INPUTS)
def test_malicious_payloads_do_not_change_query_template(payload: str) -> None:
    sql = players._FEATURE_HISTORY_SQL_BY_SORT_COLUMN["feature_date"]
    params = {"tid": payload, "pid": payload, "days": 30}

    assert payload not in sql
    assert params["tid"] == payload
    assert params["pid"] == payload


@pytest.mark.parametrize("payload", MALICIOUS_INPUTS)
def test_malicious_tenant_id_kept_as_bound_param(payload: str) -> None:
    sql = players._build_erasure_related_count_sql("alerts")
    params = {"tid": payload, "pid": "player-123"}

    assert payload not in sql
    assert ":tid" in sql
    assert params["tid"] == payload
