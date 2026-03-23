"""
tests/unit/test_module6_audit.py — Module 6 audit coverage tests.

Covers:
  - login success writes LOGIN audit
  - login wrong password writes LOGIN_FAILED and commits before raising 401
  - login unknown user raises 401 with no audit (no tenant available)
  - logout writes LOGOUT audit
  - compound rule DELETE writes DELETE_COMPOUND_RULE audit
  - player list DELETE writes DELETE_PLAYER_LIST audit
  - player list bulk_add writes BULK_ADD_LIST_ENTRIES audit
  - mappings create writes CREATE_MAPPING audit
  - mappings update writes UPDATE_MAPPING with before+after
  - erase_player_data zeros extra PII fields (birth_date, profession, etc.)
  - monthly report includes total_sar_reports key
  - monthly report includes true_positive_rate key
  - monthly report includes total_communications_generated key
  - report PDF export writes audit
"""
from __future__ import annotations

import sys
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id="u1", tenant_id="t1", role="ADMIN", username="admin"):
    u = MagicMock()
    u.id = user_id
    u.tenant_id = tenant_id
    u.role = role
    u.username = username
    return u


def _make_db():
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# auth.py — login / logout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_success_writes_audit():
    """Valid credentials must produce a LOGIN AuditLog entry."""
    from routers.auth import login, LoginRequest
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    db = _make_db()
    user_mock = MagicMock()
    user_mock.id = "u1"
    user_mock.tenant_id = "t1"
    user_mock.role = "AML_ANALYST"
    user_mock.password_hash = "hashed"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = user_mock
    db.execute = AsyncMock(return_value=result_mock)

    scope = {"type": "http", "method": "POST", "path": "/auth/login",
             "headers": [], "query_string": b"", "client": ("127.0.0.1", 1234)}
    request = StarletteRequest(scope)
    request.state.view_rate_limit = None  # satisfy slowapi wrapper

    with patch("routers.auth.verify_password", return_value=True), \
         patch("routers.auth.create_access_token", return_value="tok"), \
         patch("routers.auth.write_audit", AsyncMock()) as mock_audit, \
         patch("slowapi.Limiter._check_request_limit", MagicMock()):
        body = LoginRequest(username="admin", password="correct")
        await login(request=request, body=body, response=StarletteResponse(), db=db)

    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "LOGIN"


@pytest.mark.asyncio
async def test_login_failure_wrong_password_writes_audit():
    """Wrong password must produce a LOGIN_FAILED audit and commit before raise."""
    from routers.auth import login, LoginRequest
    from fastapi import HTTPException as FastAPIHTTPException
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    db = _make_db()
    user_mock = MagicMock()
    user_mock.id = "u1"
    user_mock.tenant_id = "t1"
    user_mock.role = "AML_ANALYST"
    user_mock.password_hash = "hashed"

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = user_mock
    db.execute = AsyncMock(return_value=result_mock)

    scope = {"type": "http", "method": "POST", "path": "/auth/login",
             "headers": [], "query_string": b"", "client": ("127.0.0.1", 1234)}
    request = StarletteRequest(scope)

    with patch("routers.auth.verify_password", return_value=False), \
         patch("routers.auth.write_audit", AsyncMock()) as mock_audit, \
         patch("slowapi.Limiter._check_request_limit", MagicMock()):
        body = LoginRequest(username="admin", password="wrong")
        with pytest.raises(FastAPIHTTPException) as exc_info:
            await login(request=request, body=body, response=StarletteResponse(), db=db)

    assert exc_info.value.status_code == 401
    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "LOGIN_FAILED"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_login_unknown_user_no_audit():
    """Non-existent user raises 401 without any audit (no tenant_id available)."""
    from routers.auth import login, LoginRequest
    from fastapi import HTTPException as FastAPIHTTPException
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    db = _make_db()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)

    scope = {"type": "http", "method": "POST", "path": "/auth/login",
             "headers": [], "query_string": b"", "client": ("127.0.0.1", 1234)}
    request = StarletteRequest(scope)

    with patch("routers.auth.write_audit", AsyncMock()) as mock_audit, \
         patch("slowapi.Limiter._check_request_limit", MagicMock()):
        body = LoginRequest(username="ghost", password="x")
        with pytest.raises(FastAPIHTTPException) as exc_info:
            await login(request=request, body=body, response=StarletteResponse(), db=db)

    assert exc_info.value.status_code == 401
    mock_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_writes_audit():
    """Logout must produce a LOGOUT AuditLog entry."""
    from routers.auth import logout
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    db = _make_db()
    current_user = _make_user()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/logout",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    }
    request = StarletteRequest(scope)

    with patch("routers.auth.revoke_token", AsyncMock()), \
         patch("routers.auth.revoke_refresh_token", AsyncMock()), \
         patch("routers.auth.write_audit", AsyncMock()) as mock_audit:
        await logout(request=request, response=StarletteResponse(), token="tok", current_user=current_user, db=db)

    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "LOGOUT"


# ---------------------------------------------------------------------------
# compound_rules.py — DELETE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compound_rule_delete_writes_audit():
    """DELETE compound rule must write DELETE_COMPOUND_RULE audit before delete."""
    from routers.compound_rules import delete_compound_rule

    db = _make_db()
    row = MagicMock()
    row.name = "High-Risk Combo"
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=result_mock)
    db.delete = AsyncMock()

    with patch("routers.compound_rules.write_audit", AsyncMock()) as mock_audit:
        await delete_compound_rule(rule_id="r1", db=db, current_user=_make_user())

    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "DELETE_COMPOUND_RULE"
    assert args[4] == "CompoundRule"
    db.delete.assert_awaited()


# ---------------------------------------------------------------------------
# player_lists.py — DELETE + bulk_add
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_player_list_delete_writes_audit():
    """DELETE player list must write DELETE_PLAYER_LIST audit."""
    from routers.player_lists import delete_player_list

    db = _make_db()
    pl = MagicMock()
    pl.id = "list1"
    pl.name = "VIP Watchlist"
    pl.tenant_id = "t1"
    db.delete = AsyncMock()

    with patch("routers.player_lists._get_list_or_404", AsyncMock(return_value=pl)), \
         patch("routers.player_lists.write_audit", AsyncMock()) as mock_audit:
        await delete_player_list(list_id="list1", db=db, current_user=_make_user())

    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "DELETE_PLAYER_LIST"
    db.delete.assert_awaited()


@pytest.mark.asyncio
async def test_player_list_bulk_add_writes_audit():
    """bulk_add_list_entries must write BULK_ADD_LIST_ENTRIES audit after commit."""
    from routers.player_lists import bulk_add_list_entries

    db = _make_db()
    pl = MagicMock()
    pl.id = "list1"
    pl.tenant_id = "t1"

    class FakeBody:
        values = ["cpf1", "cpf2"]
        value_type = "CPF"

    with patch("routers.player_lists._get_list_or_404", AsyncMock(return_value=pl)), \
         patch("routers.player_lists.write_audit", AsyncMock()) as mock_audit:
        result = await bulk_add_list_entries(list_id="list1", body=FakeBody(), db=db, current_user=_make_user())

    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "BULK_ADD_LIST_ENTRIES"
    assert result["added"] == 2


# ---------------------------------------------------------------------------
# mappings.py — CREATE_MAPPING
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mappings_create_writes_audit():
    """create_mapping must write CREATE_MAPPING audit after commit."""
    from routers.mappings import create_mapping, MappingCreate

    db = _make_db()
    mc_obj = MagicMock()
    mc_obj.id = "m1"
    mc_obj.name = "Test Mapping"
    mc_obj.version_number = 1
    mc_obj.is_current = True
    mc_obj.source_system = "SYS_A"
    mc_obj.entity_type = "Transaction"

    db.execute = AsyncMock(return_value=MagicMock())
    db.add = MagicMock()
    db.refresh = AsyncMock()

    body = MappingCreate(
        name="Test Mapping",
        source_system="SYS_A",
        entity_type="Transaction",
        format="json",
        config_json={"fields": []},
    )
    user = _make_user()

    # Patch update (sqlalchemy DML) to avoid real table dependency
    update_mock = MagicMock(return_value=MagicMock())

    with patch("routers.mappings.write_audit", AsyncMock()) as mock_audit, \
         patch("routers.mappings._parse_config_payload", return_value={"fields": []}), \
         patch("routers.mappings.validate_mapping_targets_against_canonical_schema", return_value={"valid": True}), \
         patch("routers.mappings._next_version_number", AsyncMock(return_value=1)), \
         patch("routers.mappings.update", update_mock), \
         patch("routers.mappings.MappingConfig", return_value=mc_obj):
        await create_mapping(body=body, current_user=user, db=db)

    mock_audit.assert_awaited_once()
    args = mock_audit.call_args[0]
    assert args[3] == "CREATE_MAPPING"


# ---------------------------------------------------------------------------
# mappings.py — UPDATE_MAPPING (create_new_mapping_version)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mappings_update_writes_audit_with_before_after():
    """create_new_mapping_version must write UPDATE_MAPPING with before and after."""
    from routers.mappings import create_new_mapping_version, MappingPatch

    db = _make_db()
    existing = MagicMock()
    existing.id = "m1"
    existing.tenant_id = "t1"
    existing.name = "Old Mapping"
    existing.version_number = 1
    existing.source_system = "SYS_A"
    existing.entity_type = "Transaction"
    existing.config_json = {}
    existing.parent_id = None
    existing.is_current = True

    new_row = MagicMock()
    new_row.id = "m2"
    new_row.name = "New Mapping"
    new_row.version_number = 2
    new_row.is_current = True

    db.get = AsyncMock(return_value=existing)
    db.execute = AsyncMock(return_value=MagicMock())
    db.refresh = AsyncMock()

    body = MappingPatch(name="New Mapping", format="json", config_json={})
    user = _make_user()

    update_mock = MagicMock(return_value=MagicMock())

    with patch("routers.mappings.write_audit", AsyncMock()) as mock_audit, \
         patch("routers.mappings._parse_config_payload", return_value={}), \
         patch("routers.mappings.validate_mapping_targets_against_canonical_schema", return_value={"valid": True}), \
         patch("routers.mappings._next_version_number", AsyncMock(return_value=2)), \
         patch("routers.mappings.update", update_mock), \
         patch("routers.mappings.MappingConfig", return_value=new_row):
        await create_new_mapping_version(mapping_id="m1", body=body, current_user=user, db=db)

    mock_audit.assert_awaited_once()
    assert db.refresh.await_count == 0
    args = mock_audit.call_args[0]
    assert args[3] == "UPDATE_MAPPING"
    kwargs = mock_audit.call_args[1]
    assert "before" in kwargs
    assert "after" in kwargs


# ---------------------------------------------------------------------------
# players.py — erase_player_data zeros extra PII fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_erase_player_data_zeros_extra_pii_fields():
    """erase_player_data must null birth_date, profession, declared_income_monthly, registered_since."""
    from routers.players import erase_player_data

    db = _make_db()
    p = MagicMock()
    p.tenant_id = "t1"
    p.status = "ACTIVE"
    p.birth_date = "1990-01-01"
    p.profession = "Engineer"
    p.declared_income_monthly = 5000
    p.registered_since = "2020-01-01"

    db.get = AsyncMock(return_value=p)

    with patch("routers.players.write_audit", AsyncMock()), \
         patch("routers.players.encrypt_pii", return_value=b"ENCRYPTED"):
        await erase_player_data(
            player_id="00000000-0000-0000-0000-000000000001",
            reason="LGPD request",
            db=db,
            current_user=_make_user(),
        )

    assert p.birth_date is None
    assert p.profession is None
    assert p.declared_income_monthly is None
    assert p.registered_since is None
    assert p.status == "ERASED"


# ---------------------------------------------------------------------------
# reports.py — total_sar_reports and true_positive_rate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_monthly_report_includes_sar_count():
    """Monthly report must include total_sar_reports key."""
    from routers.reports import _build_monthly_report
    from datetime import timezone

    db = AsyncMock()
    executed_statements = []
    # Return row-like mocks for all queries
    empty_result = MagicMock()
    empty_result.all.return_value = []
    empty_result.scalar_one.return_value = 0
    empty_result.scalar.return_value = 3  # 3 SAR communications

    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    scalar_result.scalar_one.return_value = 0

    call_count = 0

    async def _execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        executed_statements.append(stmt)
        stmt_text = str(stmt)
        r = MagicMock()
        r.all.return_value = []
        r.scalar_one.return_value = 0
        r.scalar.return_value = 3 if "CAST(:tenant_id AS uuid)" in stmt_text else 0
        r.scalars.return_value = r
        return r

    db.execute = AsyncMock(side_effect=_execute)

    from_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    to_dt = datetime(2025, 1, 31, tzinfo=timezone.utc)
    result = await _build_monthly_report("t1", from_dt, to_dt, db)

    assert "total_sar_reports" in result
    sar_stmt = next(str(stmt) for stmt in executed_statements if "CAST(:tenant_id AS uuid)" in str(stmt))
    assert "CAST(:tenant_id AS uuid)" in sar_stmt
    assert "IN ('REPORT', 'FILE_SAR')" in sar_stmt
    assert result["total_sar_reports"] == 3


@pytest.mark.asyncio
async def test_monthly_report_includes_true_positive_rate():
    """Monthly report must include true_positive_rate key."""
    from routers.reports import _build_monthly_report
    from datetime import timezone

    db = AsyncMock()

    async def _execute(stmt, *a, **kw):
        r = MagicMock()
        r.all.return_value = []
        r.scalar_one.return_value = 0
        r.scalar.return_value = 0
        r.scalars.return_value = r
        return r

    db.execute = AsyncMock(side_effect=_execute)

    from_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    to_dt = datetime(2025, 1, 31, tzinfo=timezone.utc)
    result = await _build_monthly_report("t1", from_dt, to_dt, db)

    assert "true_positive_rate" in result


@pytest.mark.asyncio
async def test_monthly_report_includes_generated_communications():
    """Monthly report must include total_communications_generated key."""
    from routers.reports import _build_monthly_report
    from datetime import timezone

    db = AsyncMock()

    async def _execute(stmt, *a, **kw):
        r = MagicMock()
        r.all.return_value = []
        r.scalar_one.return_value = 0
        r.scalar.return_value = 4 if "report_packages" not in str(stmt).lower() else 2
        r.scalars.return_value = r
        return r

    db.execute = AsyncMock(side_effect=_execute)

    from_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    to_dt = datetime(2025, 1, 31, tzinfo=timezone.utc)
    result = await _build_monthly_report("t1", from_dt, to_dt, db)

    assert "total_communications_generated" in result


@pytest.mark.asyncio
async def test_download_report_pdf_writes_audit():
    """PDF export must generate EXPORT_REPORT_PDF audit entry."""
    from routers.cases import download_report_pdf
    import tempfile

    db = _make_db()
    rp = MagicMock()
    rp.tenant_id = "t1"
    rp.case_id = "case-1"
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(b"%PDF-1.4 fake")
        handle.flush()
        rp.pdf_path = handle.name
        db.get = AsyncMock(return_value=rp)

        with patch("routers.cases.write_audit", AsyncMock()) as mock_audit:
            response = await download_report_pdf(
                case_id="case-1",
                rp_id="rp-1",
                current_user=_make_user(),
                db=db,
            )

            assert response.media_type == "application/pdf"
            mock_audit.assert_awaited_once()
            assert mock_audit.call_args[0][3] == "EXPORT_REPORT_PDF"
