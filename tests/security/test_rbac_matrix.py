from __future__ import annotations

import inspect
import os
import sys

import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


ROLES = [
    "Operador_Analista",
    "Operador_Gestor",
    "Operador_AdminTecnico",
    "BetAML_SuperAdmin",
    "ADMIN",
    "AML_ANALYST",
    "AUDITOR",
    "SUPER_ADMIN",
]

RBAC_MATRIX = {
    "ingest:write": {
        "allow": {"Operador_AdminTecnico", "BetAML_SuperAdmin", "ADMIN", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_Gestor", "AML_ANALYST", "AUDITOR"},
    },
    "alerts:write": {
        "allow": {"Operador_Analista", "Operador_Gestor", "BetAML_SuperAdmin", "ADMIN", "AML_ANALYST", "SUPER_ADMIN"},
        "deny": {"Operador_AdminTecnico", "AUDITOR"},
    },
    "cases:write": {
        "allow": {"Operador_Analista", "Operador_Gestor", "BetAML_SuperAdmin", "ADMIN", "AML_ANALYST", "SUPER_ADMIN"},
        "deny": {"Operador_AdminTecnico", "AUDITOR"},
    },
    "reports:write": {
        "allow": {"Operador_Analista", "Operador_Gestor", "BetAML_SuperAdmin", "ADMIN", "AML_ANALYST", "SUPER_ADMIN"},
        "deny": {"Operador_AdminTecnico", "AUDITOR"},
    },
    "rules:write": {
        "allow": {"Operador_Gestor", "BetAML_SuperAdmin", "ADMIN", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_AdminTecnico", "AML_ANALYST", "AUDITOR"},
    },
    "mappings:write": {
        "allow": {"Operador_AdminTecnico", "BetAML_SuperAdmin", "ADMIN", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_Gestor", "AML_ANALYST", "AUDITOR"},
    },
    "users:write": {
        "allow": {"Operador_AdminTecnico", "BetAML_SuperAdmin", "ADMIN", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_Gestor", "AML_ANALYST", "AUDITOR"},
    },
    "tenants:admin": {
        "allow": {"BetAML_SuperAdmin", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_Gestor", "Operador_AdminTecnico", "ADMIN", "AML_ANALYST", "AUDITOR"},
    },
    "audit:read": {
        "allow": set(ROLES),
        "deny": set(),
    },
    "players:erase": {
        "allow": {"Operador_Gestor", "BetAML_SuperAdmin", "ADMIN", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_AdminTecnico", "AML_ANALYST", "AUDITOR"},
    },
    "model_registry:promote": {
        "allow": {"BetAML_SuperAdmin", "SUPER_ADMIN"},
        "deny": {"Operador_Analista", "Operador_Gestor", "Operador_AdminTecnico", "ADMIN", "AML_ANALYST", "AUDITOR"},
    },
}


def _user_with_role(role: str):
    user = MagicMock()
    user.role = role
    user.roles = [role] if role.startswith("Operador_") or role == "BetAML_SuperAdmin" else None
    return user


def _has_permission(user, permission: str) -> bool:
    from auth import _PERMISSIONS, get_effective_roles

    for role in get_effective_roles(user):
        permissions = _PERMISSIONS.get(role, frozenset())
        if "*" in permissions or permission in permissions:
            return True
    return False


@pytest.mark.parametrize("permission,expectation", RBAC_MATRIX.items())
def test_rbac_permission_matrix(permission: str, expectation: dict[str, set[str]]):
    for role in expectation["allow"]:
        assert _has_permission(_user_with_role(role), permission), f"{role} should allow {permission}"
    for role in expectation["deny"]:
        assert not _has_permission(_user_with_role(role), permission), f"{role} should deny {permission}"


def test_auditor_is_read_only_in_permission_map():
    from auth import _PERMISSIONS, get_effective_roles

    auditor_permissions: set[str] = set()
    for role in get_effective_roles(_user_with_role("AUDITOR")):
        auditor_permissions.update(_PERMISSIONS.get(role, frozenset()))

    assert all(not perm.endswith(":write") and not perm.endswith(":admin") for perm in auditor_permissions)
    assert "*" not in auditor_permissions


@pytest.mark.asyncio
async def test_require_permission_denies_analyst_for_admin_action():
    from auth import require_permission

    checker = require_permission("users:write")
    with pytest.raises(HTTPException) as exc:
        await checker(current_user=_user_with_role("Operador_Analista"))
    assert exc.value.status_code == 403


def test_mutating_route_guards_match_rbac_documentation():
    from routers import admin, alerts, cases, ingest, mappings, ml, players, rules

    sources = {
        "ingest": inspect.getsource(ingest),
        "alerts": inspect.getsource(alerts),
        "cases": inspect.getsource(cases),
        "mappings": inspect.getsource(mappings),
        "rules": inspect.getsource(rules),
        "admin": inspect.getsource(admin),
        "players": inspect.getsource(players),
        "ml": inspect.getsource(ml),
    }

    assert "get_ingest_principal" in sources["ingest"]
    assert "require_role_any([AppRole.ANALISTA, AppRole.GESTOR" in sources["alerts"]
    assert "require_role_any([AppRole.ANALISTA, AppRole.GESTOR" in sources["cases"]
    assert "require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])" in sources["mappings"]
    assert "require_role(AppRole.GESTOR)" in sources["rules"]
    assert "require_role(AppRole.SUPER_ADMIN)" in sources["admin"]
    assert "erase_player_data" in sources["players"] and "AppRole.ANALISTA" not in sources["players"].split("async def erase_player_data", 1)[1].split("async def", 1)[0]
    assert "promote_model" in sources["ml"] and "AppRole.SUPER_ADMIN" in sources["ml"]
