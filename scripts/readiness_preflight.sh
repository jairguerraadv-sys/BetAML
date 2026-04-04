#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"
API_URL="${API_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
EVIDENCE_OUT=""
HTTP_TIMEOUT_SEC="${HTTP_TIMEOUT_SEC:-90}"
SKIP_DOCKER=0
SKIP_HTTP=0
SKIP_ALEMBIC=0
SKIP_MIGRATION_DRY_RUN=0
SKIP_BACKUP_CONFIG=0

usage() {
  cat <<'EOF'
Uso:
  scripts/readiness_preflight.sh [opcoes]

Opcoes:
  --compose-file PATH        Caminho do docker-compose (default: infra/docker-compose.yml)
  --api-url URL              URL base da API (default: http://localhost:8000)
  --frontend-url URL         URL do frontend (default: http://localhost:3000)
  --http-timeout SEC         Tempo maximo para aguardar health HTTP (default: 90)
  --evidence-out PATH        Salva a saida completa em arquivo
  --skip-docker              Nao valida servicos do compose em execucao
  --skip-http                Nao valida probes HTTP
  --skip-alembic             Nao valida cadeia Alembic
  --skip-migration-dry-run   Nao executa dry-run de migracao SQL legada
  --skip-backup-config       Nao valida configuracao de backup Helm
  -h, --help                 Exibe esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --api-url)
      API_URL="$2"
      shift 2
      ;;
    --frontend-url)
      FRONTEND_URL="$2"
      shift 2
      ;;
    --http-timeout)
      HTTP_TIMEOUT_SEC="$2"
      shift 2
      ;;
    --evidence-out)
      EVIDENCE_OUT="$2"
      shift 2
      ;;
    --skip-docker)
      SKIP_DOCKER=1
      shift
      ;;
    --skip-http)
      SKIP_HTTP=1
      shift
      ;;
    --skip-alembic)
      SKIP_ALEMBIC=1
      shift
      ;;
    --skip-migration-dry-run)
      SKIP_MIGRATION_DRY_RUN=1
      shift
      ;;
    --skip-backup-config)
      SKIP_BACKUP_CONFIG=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Argumento invalido: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -n "$EVIDENCE_OUT" ]]; then
  mkdir -p "$(dirname "$EVIDENCE_OUT")"
  exec > >(tee "$EVIDENCE_OUT")
  exec 2>&1
fi

FAILURES=0

section() {
  printf '\n== %s ==\n' "$1"
}

pass() {
  printf '[PASS] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1"
  FAILURES=$((FAILURES + 1))
}

require_cmd() {
  local cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      fail "dependencia ausente: $cmd"
      return 1
    fi
  done
  return 0
}

wait_for_http() {
  local label="$1"
  local url="$2"
  local elapsed=0

  while [[ "$elapsed" -lt "$HTTP_TIMEOUT_SEC" ]]; do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      pass "$label respondendo em $url"
      return 0
    fi
    sleep 3
    elapsed=$((elapsed + 3))
  done

  fail "$label indisponivel em $url apos ${HTTP_TIMEOUT_SEC}s"
  return 1
}

section "Contexto"
echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "root_dir=$ROOT_DIR"
echo "compose_file=$COMPOSE_FILE"
echo "api_url=$API_URL"
echo "frontend_url=$FRONTEND_URL"

section "Dependencias"
require_cmd bash grep sed awk find sha256sum || true
if [[ "$SKIP_HTTP" -eq 0 ]]; then
  require_cmd curl || true
fi
if [[ "$SKIP_DOCKER" -eq 0 || "$SKIP_MIGRATION_DRY_RUN" -eq 0 ]]; then
  require_cmd docker || true
fi
if [[ "$SKIP_ALEMBIC" -eq 0 ]]; then
  require_cmd python || true
fi

if [[ "$SKIP_DOCKER" -eq 0 ]]; then
  section "Compose"
  if [[ ! -f "$COMPOSE_FILE" ]]; then
    fail "compose file nao encontrado: $COMPOSE_FILE"
  else
    mapfile -t running_services < <(docker compose -f "$COMPOSE_FILE" ps --services --status running 2>/dev/null || true)
    required_services=(postgres redis redpanda minio clickhouse api frontend stream-processor rules-engine ml-service)
    for service in "${required_services[@]}"; do
      if printf '%s\n' "${running_services[@]}" | grep -Fxq "$service"; then
        pass "servico em execucao: $service"
      else
        fail "servico fora de execucao: $service"
      fi
    done
  fi
fi

if [[ "$SKIP_HTTP" -eq 0 ]]; then
  section "HTTP"
  wait_for_http "api-live" "${API_URL%/}/health/live"
  wait_for_http "api-ready" "${API_URL%/}/health/ready"
  wait_for_http "frontend" "${FRONTEND_URL%/}/"
fi

if [[ "$SKIP_ALEMBIC" -eq 0 ]]; then
  section "Alembic"
  if (
    cd "$ROOT_DIR/services/api"
    python -m alembic -c alembic.ini heads >/dev/null
    python -m alembic -c alembic.ini history >/dev/null
  ); then
    pass "cadeia Alembic validada"
  else
    fail "falha ao validar cadeia Alembic"
  fi
fi

if [[ "$SKIP_MIGRATION_DRY_RUN" -eq 0 ]]; then
  section "Migracoes SQL"
  if "$ROOT_DIR/scripts/postgres_migrate_existing.sh" --dry-run >/dev/null; then
    pass "dry-run das migracoes legadas validado"
  else
    fail "dry-run das migracoes legadas falhou"
  fi
fi

if [[ "$SKIP_BACKUP_CONFIG" -eq 0 ]]; then
  section "Backup"
  if grep -Eq '^backup:' "$ROOT_DIR/helm/betaml/values.yaml" \
    && grep -Eq '^  enabled: true' "$ROOT_DIR/helm/betaml/values.yaml" \
    && grep -Eq '^  schedule: ' "$ROOT_DIR/helm/betaml/values.yaml" \
    && grep -Eq '^  bucket: ' "$ROOT_DIR/helm/betaml/values.yaml" \
    && [[ -f "$ROOT_DIR/helm/betaml/templates/backup-cronjob.yaml" ]]; then
    pass "configuracao de backup Helm presente e habilitada"
  else
    fail "configuracao de backup Helm incompleta"
  fi
fi

section "Resumo"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "readiness_preflight=PASS"
  exit 0
fi

echo "readiness_preflight=FAIL failures=$FAILURES"
exit 1