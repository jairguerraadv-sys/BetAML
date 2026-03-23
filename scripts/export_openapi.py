from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
OUTPUT = ROOT / "docs" / "openapi.json"


def main() -> int:
    sys.path.insert(0, str(API_DIR))
    from main import app  # noqa: WPS433

    schema = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"OpenAPI exportado em {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
