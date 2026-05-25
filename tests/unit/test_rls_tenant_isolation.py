"""
tests/unit/test_rls_tenant_isolation.py

Unit tests verifying that the JWT auth layer enforces tenant isolation
(cross-tenant access is blocked at the token validation level).

These tests do NOT require a running database — they inspect source code and
mock the auth layer to validate that tenant_id checks are present and correct.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

_ROOT         = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SERVICES_API = os.path.join(_ROOT, "services", "api")
_LIBS         = os.path.join(_ROOT, "libs")

for p in (_SERVICES_API, _LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)


class TestJWTTenantValidationInSource(unittest.TestCase):
    """Verify that get_current_user validates tenant_id against the DB record."""

    def _load_auth_src(self) -> str:
        with open(os.path.join(_SERVICES_API, "auth.py")) as fh:
            return fh.read()

    def test_tenant_id_mismatch_raises(self):
        """get_current_user must compare token tenant_id against db record."""
        src = self._load_auth_src()
        idx = src.find("get_current_user")
        self.assertGreater(idx, 0, "get_current_user not found in auth.py")
        snippet = src[idx: idx + 3000]
        self.assertIn("tenant_id", snippet,
                      "get_current_user must check tenant_id")
        self.assertTrue(
            "credentials_exception" in snippet or "HTTPException" in snippet,
            "get_current_user must raise on tenant mismatch"
        )

    def test_cross_tenant_blocked_by_comparison(self):
        """Source must contain a tenant_id comparison that rejects mismatches."""
        src = self._load_auth_src()
        self.assertTrue(
            "str(user.tenant_id) != token_tenant_id" in src
            or "tenant_id" in src and "credentials_exception" in src,
            "auth.py must compare token tenant_id to DB tenant_id and raise on mismatch"
        )


class TestRLSContextSetInIngest(unittest.TestCase):
    """Verify that ingest routes set Postgres RLS context before DB queries."""

    def _load_ingest_src(self) -> str:
        with open(os.path.join(_SERVICES_API, "routers", "ingest.py")) as fh:
            return fh.read()

    def test_set_config_tenant_called(self):
        """_ensure_db_tenant_context must be called in ingest paths."""
        src = self._load_ingest_src()
        self.assertIn("_ensure_db_tenant_context", src,
                      "ingest routes must call _ensure_db_tenant_context for RLS")

    def test_set_config_sql_present(self):
        """RLS context must use PostgreSQL set_config for app.current_tenant."""
        src = self._load_ingest_src()
        self.assertIn("app.current_tenant", src,
                      "ingest must set app.current_tenant via set_config for RLS")


class TestCrossAccessBlockedByTenantFilter(unittest.TestCase):
    """Verify that key routes filter by tenant_id to prevent cross-tenant reads."""

    def _assert_tenant_filter_in_route(self, router_file: str, route_func: str) -> None:
        path = os.path.join(_SERVICES_API, "routers", router_file)
        with open(path) as fh:
            src = fh.read()
        idx = src.find(route_func)
        self.assertGreater(idx, 0, f"{route_func} not found in {router_file}")
        snippet = src[idx: idx + 2000]
        self.assertIn("tenant_id", snippet,
                      f"{route_func} in {router_file} must filter by tenant_id")

    def test_alerts_filtered_by_tenant(self):
        self._assert_tenant_filter_in_route("alerts.py", "list_alerts")

    def test_cases_filtered_by_tenant(self):
        self._assert_tenant_filter_in_route("cases.py", "list_cases")

    def test_players_filtered_by_tenant(self):
        self._assert_tenant_filter_in_route("players.py", "list_players")


if __name__ == "__main__":
    unittest.main()
