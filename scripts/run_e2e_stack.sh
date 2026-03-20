#!/usr/bin/env bash
set -euo pipefail

# Runs local E2E integration tests that require the Docker stack.
# Usage:
#   scripts/run_e2e_stack.sh
#   API_URL=http://localhost:8000 ML_URL=http://localhost:8001 scripts/run_e2e_stack.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"

API_URL="${API_URL:-http://localhost:8000}"
ML_URL="${ML_URL:-http://localhost:8001}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker nao encontrado no PATH" >&2
  exit 1
fi

pushd "${ROOT_DIR}" >/dev/null

echo "[e2e] subindo stack: ${COMPOSE_FILE}"
docker compose -f "${COMPOSE_FILE}" up -d --build

echo "[e2e] aguardando health da API e ML service"
for i in {1..60}; do
  if curl -fsS "${API_URL}/health" >/dev/null 2>&1 && curl -fsS "${ML_URL}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
  if [[ $i -eq 60 ]]; then
    echo "[e2e] timeout aguardando health endpoints" >&2
    docker compose -f "${COMPOSE_FILE}" ps
    exit 1
  fi
done

export TEST_STACK_UP=1
export API_URL
export ML_URL

echo "[e2e] rodando pytest integration"
/workspaces/BetAML/.venv-1/bin/python -m pytest -q tests/integration/ -v --tb=short

popd >/dev/null
