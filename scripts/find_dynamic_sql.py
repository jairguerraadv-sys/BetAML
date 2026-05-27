#!/usr/bin/env python3
"""Lista ocorrencias potenciais de SQL dinamico para revisao manual (B608)."""
from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = [
    REPO_ROOT / "services" / "api",
    REPO_ROOT / "services" / "stream_processor",
    REPO_ROOT / "services" / "rules_engine",
    REPO_ROOT / "scripts",
    REPO_ROOT / "tests",
]
SKIP_DIR_NAMES = {"__pycache__", ".next", "node_modules", ".venv"}
PATTERNS = [
    re.compile(r"text\(f\"", re.IGNORECASE),
    re.compile(r"execute\(f\"", re.IGNORECASE),
    re.compile(r"exec_driver_sql\(f\"", re.IGNORECASE),
    re.compile(r"\.format\(", re.IGNORECASE),
    re.compile(r"SELECT\s+.*\{", re.IGNORECASE),
    re.compile(r"UPDATE\s+.*\{", re.IGNORECASE),
    re.compile(r"INSERT\s+.*\{", re.IGNORECASE),
    re.compile(r"DELETE\s+.*\{", re.IGNORECASE),
    re.compile(r"ALTER\s+.*\{", re.IGNORECASE),
]


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for base in TARGET_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix not in {".py", ".sql"}:
                continue
            files.append(path)
    return sorted(files)


def main() -> int:
    matches = 0
    for path in iter_source_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(REPO_ROOT)
        for idx, line in enumerate(lines, start=1):
            for pattern in PATTERNS:
                if pattern.search(line):
                    print(f"{rel}:{idx}: {line.strip()}")
                    matches += 1
                    break

    if matches == 0:
        print("No potential dynamic SQL patterns found")
    else:
        print(f"\nTotal potential matches: {matches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
