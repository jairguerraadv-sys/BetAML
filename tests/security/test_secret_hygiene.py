from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_scanner_module():
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "check_secret_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_secret_hygiene", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sensitive_file_with_admin123_fails():
    scanner = _load_scanner_module()
    findings = scanner.scan_text(
        "helm/betaml/values-staging.yaml",
        "E2E_ADMIN_PASSWORD: admin123\n",
    )
    assert findings
    assert any(f.term == "admin123" for f in findings)


def test_helm_values_with_devpass_fails():
    scanner = _load_scanner_module()
    findings = scanner.scan_text(
        "helm/betaml/templates/configmap.yaml",
        "DATABASE_URL: postgresql://betaml:devpass@localhost:5432/db\n",
    )
    assert findings
    assert any(f.term == "devpass" for f in findings)


def test_env_example_with_devpass_is_allowed():
    scanner = _load_scanner_module()
    findings = scanner.scan_text(
        ".env.example",
        "POSTGRES_PASSWORD=devpass\n",
    )
    assert findings == []


def test_docs_local_example_is_allowed():
    scanner = _load_scanner_module()
    findings = scanner.scan_text(
        "docs/ops-guide.md",
        "Exemplo local: admin123\n",
    )
    assert findings == []


def test_staging_file_with_changeme_fails():
    scanner = _load_scanner_module()
    findings = scanner.scan_text(
        "deploy/staging/configmap.yaml",
        "JWT_SECRET: changeme\n",
    )
    assert findings
    assert any(f.term == "changeme" for f in findings)


def test_output_redacts_long_hardcoded_secret():
    scanner = _load_scanner_module()
    line = "JWT_SECRET: abcdefghijklmnopqrstuvwxyz1234567890\n"
    findings = scanner.scan_text("helm/betaml/values.yaml", line)
    assert findings
    rendered = [f.term for f in findings]
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in "\n".join(rendered)
    assert any("JWT_SECRET=" in value for value in rendered)