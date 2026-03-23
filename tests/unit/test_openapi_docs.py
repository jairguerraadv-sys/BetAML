from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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

if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

API_MAIN_PATH = API_DIR / "main.py"
spec = importlib.util.spec_from_file_location("betaml_api_main_for_tests", API_MAIN_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load API app module from {API_MAIN_PATH}")
api_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_main)
app = api_main.app


def test_openapi_contains_required_tags() -> None:
    schema = app.openapi()
    tags = {tag["name"] for tag in schema.get("tags", [])}
    assert {"auth", "ingest", "rules", "features", "alerts", "cases", "reports", "admin", "audit"} <= tags


def test_openapi_contains_core_paths() -> None:
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/auth/login" in paths
    assert "/ingest/jobs/{job_id}/reprocess" in paths
    assert "/rules/{rule_id}/simulate" in paths
    assert "/feature-store/players/{player_id}/history" in paths
    assert "/cases/{case_id}/report-package" in paths
