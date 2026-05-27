from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services" / "api"
DEFAULT_OUTPUT = ROOT / "docs" / "openapi.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exporta OpenAPI da API BetAML")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Caminho de saída do openapi.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = args.output.resolve()

    sys.path.insert(0, str(API_DIR))
    from main import app  # noqa: WPS433

    schema = app.openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(schema, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI exportado em {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
