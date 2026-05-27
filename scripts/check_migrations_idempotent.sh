#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$ROOT_DIR/services/api"

if [[ -x "$ROOT_DIR/.venv/bin/alembic" ]]; then
  ALEMBIC_BIN="$ROOT_DIR/.venv/bin/alembic"
else
  ALEMBIC_BIN="alembic"
fi

cd "$API_DIR"

echo "[idempotency] using alembic: $ALEMBIC_BIN"
echo "[idempotency] running first upgrade"
"$ALEMBIC_BIN" upgrade head

echo "[idempotency] running second upgrade"
"$ALEMBIC_BIN" upgrade head

echo "[idempotency] current revision"
"$ALEMBIC_BIN" current
