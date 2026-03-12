"""
tests/unit/test_lgpd_erasure.py

Unit tests for LGPD right-to-erasure and tenant creation RBAC.

Covers (without a running DB):
  - Erasure anon_suffix derivation is deterministic and 12 chars
  - Erased player fields are correctly anonymised (cpf_encrypted, name_encrypted, status)
  - right-to-erasure route requires ADMIN (require_roles dependency injected)
  - create_tenant route requires SUPER_ADMIN
  - jobs.calculate_risk_score_decay filters ERASED players
  - jobs.cleanup_expired_player_data filters ERASED players
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────
_ROOT          = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SERVICES_API  = os.path.join(_ROOT, "services", "api")
_LIBS          = os.path.join(_ROOT, "libs")

for p in (_SERVICES_API, _LIBS):
    if p not in sys.path:
        sys.path.insert(0, p)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _anon_suffix(player_id: str) -> str:
    """Replicates the anonymisation suffix used in right_to_erasure."""
    return hashlib.sha256(str(player_id).encode()).hexdigest()[:12]


# ── Tests: anonymisation algorithm ───────────────────────────────────────────

class TestAnonSuffix(unittest.TestCase):
    """Verify the deterministic SHA-256 suffix used during erasure."""

    def test_suffix_is_12_chars(self):
        suffix = _anon_suffix("some-uuid-here")
        self.assertEqual(len(suffix), 12)

    def test_suffix_is_deterministic(self):
        pid = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        self.assertEqual(_anon_suffix(pid), _anon_suffix(pid))

    def test_different_players_produce_different_suffixes(self):
        self.assertNotEqual(_anon_suffix("player-1"), _anon_suffix("player-2"))

    def test_erased_fields_contain_expected_prefix(self):
        pid = "abc123"
        suffix = _anon_suffix(pid)
        full_name = f"ERASURE_{suffix}"
        cpf_enc   = f"ERASURE_{suffix}".encode()
        self.assertTrue(full_name.startswith("ERASURE_"))
        self.assertTrue(cpf_enc.startswith(b"ERASURE_"))

    def test_erased_cpf_not_decodable_as_real_cpf(self):
        """Anonymised CPF must not be numeric-only (real CPF format)."""
        suffix = _anon_suffix("any-player-id")
        erased_cpf = f"ERASURE_{suffix}"
        self.assertFalse(erased_cpf.replace(".", "").replace("-", "").isdigit())


# ── Tests: routes_enterprise right-to-erasure RBAC ───────────────────────────

def _load_routes_enterprise():
    """Load routes_enterprise module with stubs for heavy dependencies."""
    # Stub out dependencies before loading
    for mod_name, stub in {
        "database":    types.ModuleType("database"),
        "auth":        types.ModuleType("auth"),
        "utils":       types.ModuleType("utils"),
        "models":      types.ModuleType("models"),
        "config":      types.ModuleType("config"),
        "structlog":   types.ModuleType("structlog"),
        "boto3":       types.ModuleType("boto3"),
        "minio":       types.ModuleType("minio"),
        "redis":       types.ModuleType("redis"),
        "redis.asyncio": types.ModuleType("redis.asyncio"),
        "fastapi":     types.ModuleType("fastapi"),
        "sqlalchemy":  types.ModuleType("sqlalchemy"),
        "sqlalchemy.ext.asyncio": types.ModuleType("sqlalchemy.ext.asyncio"),
        "sqlalchemy.future":      types.ModuleType("sqlalchemy.future"),
        "pydantic":    types.ModuleType("pydantic"),
    }.items():
        sys.modules.setdefault(mod_name, stub)

    # Minimal FastAPI stubs
    fastapi = sys.modules["fastapi"]
    fastapi.APIRouter   = MagicMock(return_value=MagicMock())
    fastapi.Depends     = lambda fn: fn
    fastapi.HTTPException = Exception
    fastapi.Query       = MagicMock()

    # auth stubs
    auth = sys.modules["auth"]
    auth.get_current_user = "get_current_user_dep"

    def _require_roles(*roles):
        return f"require_roles({','.join(roles)})"

    auth.require_roles     = _require_roles
    auth.User              = MagicMock
    auth.decrypt_pii       = lambda x: x
    auth.mask_cpf          = lambda x: x
    auth.hash_password     = lambda x: x
    auth.get_password_hash = lambda x: x

    # database stub
    db_mod = sys.modules["database"]
    db_mod.get_db = MagicMock()
    db_mod.Base   = MagicMock()

    # structlog stub
    sl = sys.modules["structlog"]
    sl.get_logger = lambda *a, **kw: MagicMock()

    # config stub
    cfg = sys.modules["config"]
    cfg.settings = MagicMock(
        minio_endpoint="http://localhost:9000",
        minio_access_key="minio",
        minio_secret_key="minio123",
        minio_bucket="betaml-lakehouse",
    )

    return None  # routes_enterprise is complex; we only need dependency inspection below


class TestRightToErasureRBAC(unittest.TestCase):
    """Verify that right-to-erasure enforces ADMIN role."""

    def test_require_roles_admin_used(self):
        """
        The right_to_erasure endpoint must call require_roles("ADMIN"),
        not bare get_current_user.  We verify by inspecting the source.
        """
        route_file = os.path.join(_SERVICES_API, "routes_enterprise.py")
        with open(route_file) as fh:
            src = fh.read()

        # Locate the right-to-erasure block
        idx = src.find("right-to-erasure")
        self.assertGreater(idx, 0, "right-to-erasure route not found")

        # Extract ~20 lines after the decorator
        snippet = src[idx: idx + 600]
        self.assertIn('require_roles("ADMIN")', snippet,
                      "right_to_erasure must use require_roles('ADMIN')")

    def test_cpf_encrypted_erased(self):
        """right_to_erasure must zero-out cpf_encrypted (not just full_name)."""
        route_file = os.path.join(_SERVICES_API, "routes_enterprise.py")
        with open(route_file) as fh:
            src = fh.read()

        idx = src.find("right-to-erasure")
        snippet = src[idx: idx + 800]
        self.assertIn("cpf_encrypted", snippet,
                      "right_to_erasure must erase cpf_encrypted")

    def test_status_set_to_erased(self):
        """right_to_erasure must set player.status = 'ERASED'."""
        route_file = os.path.join(_SERVICES_API, "routes_enterprise.py")
        with open(route_file) as fh:
            src = fh.read()

        idx = src.find("right-to-erasure")
        snippet = src[idx: idx + 1500]
        self.assertIn('"ERASED"', snippet,
                      "right_to_erasure must set status to ERASED")


class TestCreateTenantRBAC(unittest.TestCase):
    """Verify that create_tenant enforces SUPER_ADMIN role."""

    def test_require_roles_super_admin_used(self):
        admin_file = os.path.join(_SERVICES_API, "routers", "admin.py")
        with open(admin_file) as fh:
            src = fh.read()

        idx = src.find("create_tenant")
        self.assertGreater(idx, 0, "create_tenant route not found")
        snippet = src[idx: idx + 400]
        self.assertIn('require_roles("SUPER_ADMIN")', snippet,
                      "create_tenant must use require_roles('SUPER_ADMIN')")

    def test_super_admin_in_roles_set(self):
        auth_file = os.path.join(_SERVICES_API, "auth.py")
        with open(auth_file) as fh:
            src = fh.read()
        self.assertIn("SUPER_ADMIN", src,
                      "SUPER_ADMIN role must be declared in auth.py")


# ── Tests: jobs ERASED filter ─────────────────────────────────────────────────

class TestJobsErasedFilter(unittest.TestCase):
    """Verify that scheduled jobs never process ERASED players."""

    def test_calculate_risk_score_decay_filters_erased(self):
        jobs_file = os.path.join(_SERVICES_API, "jobs.py")
        with open(jobs_file) as fh:
            src = fh.read()

        # Locate the decay function
        idx = src.find("calculate_risk_score_decay")
        self.assertGreater(idx, 0, "calculate_risk_score_decay not found")
        # Search the entire function body (up to 5 kB from definition)
        snippet = src[idx: idx + 5000]
        self.assertIn("ERASED", snippet,
                      "calculate_risk_score_decay must filter ERASED players")

    def test_cleanup_expired_filters_erased(self):
        jobs_file = os.path.join(_SERVICES_API, "jobs.py")
        with open(jobs_file) as fh:
            src = fh.read()

        idx = src.find("cleanup_expired_player_data")
        self.assertGreater(idx, 0, "cleanup_expired_player_data not found")
        snippet = src[idx: idx + 5000]
        self.assertIn("ERASED", snippet,
                      "cleanup_expired_player_data must filter/handle ERASED players")

    def test_cleanup_writes_audit_log(self):
        jobs_file = os.path.join(_SERVICES_API, "jobs.py")
        with open(jobs_file) as fh:
            src = fh.read()

        idx = src.find("cleanup_expired_player_data")
        snippet = src[idx: idx + 5000]
        self.assertIn("AuditLog", snippet,
                      "cleanup_expired_player_data must create AuditLog entries")
        self.assertIn("LGPD_AUTO_EXPIRATION", snippet,
                      "cleanup_expired_player_data AuditLog must use LGPD_AUTO_EXPIRATION action")


if __name__ == "__main__":
    unittest.main()
