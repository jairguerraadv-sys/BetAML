from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "services" / "api"
LIBS_DIR = ROOT / "libs"

for path in (str(API_DIR), str(LIBS_DIR)):
    while path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, str(LIBS_DIR))
sys.path.insert(0, str(API_DIR))

models_module = sys.modules.get("models")
models_source = getattr(models_module, "__file__", "") or ""
if models_source.endswith("libs/models.py"):
    sys.modules.pop("models", None)
    sys.modules.pop("database", None)

API_MAIN_PATH = API_DIR / "main.py"
spec = importlib.util.spec_from_file_location("betaml_api_main_contract_tests", API_MAIN_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load API app module from {API_MAIN_PATH}")

api_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_main)
app = api_main.app


@pytest.fixture(scope="module")
def openapi_schema() -> dict:
    return app.openapi()


def _operation(schema: dict, path: str, method: str) -> dict:
    return schema["paths"][path][method]


def test_openapi_generates_without_error(openapi_schema: dict) -> None:
    assert "openapi" in openapi_schema
    assert "paths" in openapi_schema


def test_critical_paths_exist(openapi_schema: dict) -> None:
    paths = openapi_schema["paths"]
    assert "/cases" in paths
    assert "/cases/{case_id}" in paths
    assert "/alerts" in paths
    assert "/alerts/{alert_id}" in paths
    assert "/reports/monthly-summary" in paths
    assert "/report-packages/{rp_id}" in paths


def test_critical_operations_have_ids_and_success_responses(openapi_schema: dict) -> None:
    checks = [
        ("/cases", "get", "200"),
        ("/cases", "post", "201"),
        ("/cases/{case_id}", "get", "200"),
        ("/alerts", "get", "200"),
        ("/alerts/{alert_id}", "get", "200"),
        ("/reports/monthly-summary", "get", "200"),
        ("/report-packages/{rp_id}", "get", "200"),
    ]

    for path, method, success_code in checks:
        operation = _operation(openapi_schema, path, method)
        assert operation.get("operationId"), f"operationId ausente em {method.upper()} {path}"
        assert success_code in operation.get("responses", {}), f"response {success_code} ausente em {method.upper()} {path}"


def test_request_bodies_exist_where_expected(openapi_schema: dict) -> None:
    create_case = _operation(openapi_schema, "/cases", "post")
    generate_monthly = _operation(openapi_schema, "/reports/monthly-summary", "post")

    assert "requestBody" in create_case
    assert "requestBody" in generate_monthly


def test_critical_schemas_present(openapi_schema: dict) -> None:
    schemas = openapi_schema.get("components", {}).get("schemas", {})
    for schema_name in [
        "CaseSummaryOut",
        "CaseDetailOut",
        "CaseCreate",
        "AlertsListOut",
        "AlertDetailOut",
        "MonthlyReportOut",
        "ReportPackageDetailOut",
    ]:
        assert schema_name in schemas, f"Schema crítico ausente: {schema_name}"


def test_critical_schemas_avoid_unbounded_additional_properties(openapi_schema: dict) -> None:
    schemas = openapi_schema.get("components", {}).get("schemas", {})
    for schema_name in ["CaseSummaryOut", "CaseDetailOut", "AlertDetailOut", "ReportPackageDetailOut"]:
        schema = schemas.get(schema_name, {})
        assert schema.get("additionalProperties") is not True


def test_required_fields_for_critical_schemas(openapi_schema: dict) -> None:
    schemas = openapi_schema.get("components", {}).get("schemas", {})

    case_summary_required = set(schemas["CaseSummaryOut"].get("required", []))
    assert {"id", "title", "status", "reference_number", "created_at"}.issubset(case_summary_required)

    case_detail_required = set(schemas["CaseDetailOut"].get("required", []))
    assert {"id", "title", "status", "alerts", "timeline", "evidence_files"}.issubset(case_detail_required)

    alert_detail_required = set(schemas["AlertDetailOut"].get("required", []))
    assert {"id", "title", "status", "alert_type", "evidence", "created_at"}.issubset(alert_detail_required)

    report_required = set(schemas["ReportPackageDetailOut"].get("required", []))
    assert {"id", "case_id", "status", "format", "created_at"}.issubset(report_required)
