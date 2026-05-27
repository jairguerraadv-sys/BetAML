#!/usr/bin/env python3
"""Detect forbidden dev/default secrets in tracked sensitive files.

Usage:
    python scripts/check_secret_hygiene.py
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import fnmatch
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]


FORBIDDEN_EXACT_TERMS = (
    "devpass",
    "minio123",
    "admin123",
    "analyst123",
    "superadmin123",
    "changeme",
    "dev-secret-change-me",
    "change-me",
    "app_devpass_change_me",
    "default-secret",
)


FORBIDDEN_EXACT_PATTERNS = [
    (term, re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])"))
    for term in FORBIDDEN_EXACT_TERMS
]


GF_ADMIN_PASSWORD_PATTERN = re.compile(
    r"(?i)GF_SECURITY_ADMIN_PASSWORD\s*[:=]\s*['\"]?admin123['\"]?"
)


WEAK_LITERAL_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:password|secret)\s*[:=]\s*['\"]?(password|secret)['\"]?(?:\s*(?:#.*)?)$"
)


HARD_CODED_SECRET_PATTERN = re.compile(
    r"(?i)([A-Z0-9_]*(?:SECRET|PASSWORD|TOKEN|KEY)[A-Z0-9_]*)\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?"
)


SENSITIVE_ROOT_PREFIXES = (
    "helm/",
    ".github/workflows/",
    "k8s/",
    "deploy/",
    "infra/",
)


ALLOWLIST_PREFIXES = (
    "docs/",
    "datasets/",
    "test_data/",
    "tests/",
)


ALLOWLIST_GLOBS = (
    "*.example",
    ".env",
    ".env.*",
    "infra/docker-compose*.yml",
    "infra/docker-compose*.yaml",
)


@dataclass
class Finding:
    file_path: str
    line_no: int
    term: str
    reason: str
    suggestion: str


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_allowlisted(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(prefix) for prefix in ALLOWLIST_PREFIXES):
        return True
    return any(fnmatch.fnmatch(normalized, pat) for pat in ALLOWLIST_GLOBS)


def is_sensitive_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if any(normalized.startswith(prefix) for prefix in SENSITIVE_ROOT_PREFIXES):
        return True

    lowered = normalized.lower()
    name = Path(normalized).name.lower()

    if name.startswith("values") and (name.endswith(".yml") or name.endswith(".yaml")):
        return True
    if "configmap" in name or "secret" in name:
        return True
    if any(token in lowered for token in ("/production", "-production", "/staging", "-staging", "/prod", "-prod")):
        return True
    return False


def redact(value: str) -> str:
    if len(value) <= 10:
        return value
    return f"{value[:4]}...{value[-3:]}"


def scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    if is_allowlisted(path) or not is_sensitive_path(path):
        return findings

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        for term, pattern in FORBIDDEN_EXACT_PATTERNS:
            if pattern.search(line):
                findings.append(
                    Finding(
                        file_path=path,
                        line_no=idx,
                        term=term,
                        reason="default/dev credential not allowed in sensitive tracked files",
                        suggestion="replace with existingSecret/secretKeyRef or environment variable sourced from secret manager",
                    )
                )

        if GF_ADMIN_PASSWORD_PATTERN.search(line):
            findings.append(
                Finding(
                    file_path=path,
                    line_no=idx,
                    term="GF_SECURITY_ADMIN_PASSWORD=admin123",
                    reason="default Grafana admin password is forbidden in tracked deploy files",
                    suggestion="use external secret and remove hardcoded default",
                )
            )

        weak = WEAK_LITERAL_ASSIGNMENT_PATTERN.search(line)
        if weak:
            findings.append(
                Finding(
                    file_path=path,
                    line_no=idx,
                    term=weak.group(1).lower(),
                    reason="weak literal credential value detected",
                    suggestion="replace with non-default value from secret manager",
                )
            )

        hardcoded = HARD_CODED_SECRET_PATTERN.search(line)
        if hardcoded:
            key_name = hardcoded.group(1)
            secret_value = hardcoded.group(2)
            if "$" in line or "${{" in line or "<" in line:
                continue
            if path.startswith(".github/workflows/") and secret_value.lower().startswith("ci-"):
                continue
            findings.append(
                Finding(
                    file_path=path,
                    line_no=idx,
                    term=f"{key_name}={redact(secret_value)}",
                    reason="potential hardcoded secret in sensitive tracked file",
                    suggestion="replace with secret reference and rotate if this value was used",
                )
            )

    return findings


def scan_repository() -> list[Finding]:
    findings: list[Finding] = []
    for rel_path in tracked_files():
        file_path = REPO_ROOT / rel_path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        findings.extend(scan_text(rel_path, text))
    return findings


def print_findings(findings: list[Finding]) -> None:
    print("ERROR: forbidden dev/default secret detected")
    for finding in findings:
        print("---")
        print(f"file: {finding.file_path}")
        print(f"line: {finding.line_no}")
        print(f"term: {finding.term}")
        print(f"reason: {finding.reason}")
        print(f"suggestion: {finding.suggestion}")


def main() -> int:
    findings = scan_repository()
    if findings:
        print_findings(findings)
        return 1
    print("OK: secret hygiene check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())