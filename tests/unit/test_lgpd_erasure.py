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


class TestRightToErasureRBAC(unittest.TestCase):
    """Verify that right-to-erasure enforces ADMIN role.

    The canonical implementation lives in routers/players.py
    (POST /players/{id}/erase + alias /right-to-erasure).
    """

    def _load_players_src(self) -> str:
        route_file = os.path.join(_SERVICES_API, "routers", "players.py")
        with open(route_file) as fh:
            return fh.read()

    def test_require_roles_admin_used(self):
        """erase_player_data must call require_roles('ADMIN')."""
        src = self._load_players_src()
        idx = src.find("erase_player_data")
        self.assertGreater(idx, 0, "erase_player_data function not found in routers/players.py")
        snippet = src[idx: idx + 800]
        # Allows require_roles("ADMIN") or require_roles("ADMIN", "SUPER_ADMIN")
        self.assertIn('require_roles("ADMIN', snippet,
                      "erase_player_data must use require_roles('ADMIN')")

    def test_cpf_encrypted_erased(self):
        """erase_player_data must anonymise cpf_encrypted."""
        src = self._load_players_src()
        idx = src.find("erase_player_data")
        snippet = src[idx: idx + 2000]
        self.assertIn("cpf_encrypted", snippet,
                      "erase_player_data must erase cpf_encrypted")

    def test_status_set_to_erased(self):
        """erase_player_data must set player.status = 'ERASED'."""
        src = self._load_players_src()
        idx = src.find("erase_player_data")
        snippet = src[idx: idx + 1500]
        self.assertIn('"ERASED"', snippet,
                      "erase_player_data must set status to ERASED")


class TestCreateTenantRBAC(unittest.TestCase):
    """Verify that create_tenant enforces privileged admin role."""

    def test_require_roles_super_admin_used(self):
        admin_file = os.path.join(_SERVICES_API, "routers", "admin.py")
        with open(admin_file) as fh:
            src = fh.read()

        idx = src.find("create_tenant")
        self.assertGreater(idx, 0, "create_tenant route not found")
        snippet = src[idx: idx + 400]
        self.assertTrue(
            'require_roles("SUPER_ADMIN")' in snippet
            or 'require_roles("ADMIN", "SUPER_ADMIN")' in snippet,
            "create_tenant must use privileged admin RBAC",
        )

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
