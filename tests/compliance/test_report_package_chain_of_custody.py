from __future__ import annotations

from datetime import UTC, datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


def _sha256_json(value: dict) -> str:
    from routers.cases import _sha256_json as real_hash

    return real_hash(value)


def _user():
    user = MagicMock()
    user.id = "u1"
    user.tenant_id = "t1"
    user.role = "AML_ANALYST"
    user.roles = None
    return user


def _case():
    case = MagicMock()
    case.id = "case-1"
    case.tenant_id = "t1"
    return case


def _report_package(payload: dict):
    rp = MagicMock()
    rp.id = "rp-1"
    rp.case_id = "case-1"
    rp.tenant_id = "t1"
    rp.status = "DRAFT"
    rp.decision = "FILE_SAR"
    rp.payload = payload
    rp.pdf_path = None
    rp.xml_path = None
    rp.xml_sha256 = None
    rp.coaf_protocol_number = None
    rp.filed_at = None
    return rp


def _db(case_obj, rp_obj):
    db = AsyncMock()
    db.commit = AsyncMock()

    async def get(_model, pk):
        return case_obj if str(pk) == "case-1" else rp_obj

    db.get = AsyncMock(side_effect=get)
    return db


@pytest.mark.asyncio
async def test_chain_of_custody_integrity_ok_for_untampered_payload():
    from routers.cases import get_report_package_chain_of_custody

    payload_core = {
        "schema_version": "1.0",
        "decision": "FILE_SAR",
        "source_lineage": {"event_id": "evt-1", "alert_id": "alert-1", "case_id": "case-1"},
        "export_formats": ["json", "pdf"],
    }
    payload = dict(payload_core)
    payload["chain_of_custody"] = {
        "report_payload_sha256": _sha256_json(payload_core),
        "hash_scope": "payload_excluding_chain_of_custody",
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": "u1",
    }

    with patch("routers.cases.write_audit", AsyncMock()) as audit:
        result = await get_report_package_chain_of_custody(
            case_id="case-1",
            rp_id="rp-1",
            current_user=_user(),
            db=_db(_case(), _report_package(payload)),
        )

    assert result["chain_of_custody"]["integrity_ok"] is True
    assert result["chain_of_custody"]["report_payload_sha256"] == _sha256_json(payload_core)
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_chain_of_custody_detects_unauthorized_payload_change():
    from routers.cases import get_report_package_chain_of_custody

    original_core = {"schema_version": "1.0", "decision": "FILE_SAR", "amount": 100}
    tampered = {"schema_version": "1.0", "decision": "NO_ACTION", "amount": 100}
    tampered["chain_of_custody"] = {
        "report_payload_sha256": _sha256_json(original_core),
        "hash_scope": "payload_excluding_chain_of_custody",
    }

    with patch("routers.cases.write_audit", AsyncMock()):
        result = await get_report_package_chain_of_custody(
            case_id="case-1",
            rp_id="rp-1",
            current_user=_user(),
            db=_db(_case(), _report_package(tampered)),
        )

    assert result["chain_of_custody"]["integrity_ok"] is False


def test_report_package_router_exposes_expected_export_and_filing_routes():
    from routers.cases import router

    routes = {getattr(route, "path", ""): getattr(route, "methods", set()) for route in router.routes}

    assert "/cases/{case_id}/report-package/json" in routes
    assert "/cases/{case_id}/report-package/pdf" in routes
    assert "/cases/{case_id}/report-package/xml" in routes
    assert "/cases/{case_id}/report-package/submit" in routes
    assert "/cases/{case_id}/report-packages/{rp_id}/protocol-number" in routes
