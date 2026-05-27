#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


FORBIDDEN = "/workspaces/BetAML"
CHECK_SUFFIXES = {".md", ".rst", ".txt", ".toml", ".yml", ".yaml"}
SKIP_DIRS = {".git", ".venv", ".venv-1", "node_modules", ".pytest_cache", "artifacts"}


def iter_candidate_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in CHECK_SUFFIXES:
            yield path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings: list[str] = []
    for path in iter_candidate_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN in line:
                findings.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")

    if findings:
        print("Forbidden absolute workspace links found:", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("Documentation links check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
