#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"
POSTGRES_SERVICE="postgres"
POSTGRES_USER="${POSTGRES_USER:-betaml}"
POSTGRES_DB="${POSTGRES_DB:-betaml_dev}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://127.0.0.1:9000}"
MINIO_USER="${MINIO_USER:-${MINIO_ROOT_USER:-minio}}"
MINIO_PASSWORD="${MINIO_PASSWORD:-${MINIO_ROOT_PASSWORD:-minio123}}"
BACKUP_BUCKET="${BACKUP_BUCKET:-betaml-backups}"
BACKUP_FILE=""
BACKUP_OBJECT=""
BACKUP_TIMESTAMP=""
RESTORE_DB="betaml_restore_drill_$(date -u +%Y%m%dT%H%M%SZ | tr -d ':')"
DROP_RESTORE_DB=0
SKIP_MINIO_CHECK=0
EVIDENCE_OUT=""
WORK_DIR=""

usage() {
  cat <<'EOF'
Uso:
  scripts/restore_drill.sh [opcoes]

Opcoes:
  --compose-file PATH        Caminho do docker-compose
  --postgres-service NAME    Servico Postgres no compose (default: postgres)
  --postgres-user USER       Usuario do Postgres (default: betaml)
  --postgres-db NAME         Banco principal usado para contexto (default: betaml_dev)
  --backup-file PATH         Dump local .sql.gz para restaurar
  --backup-object PATH       Objeto MinIO no formato bucket/prefix/file.sql.gz
  --backup-timestamp TS      Timestamp esperado no backup (ex.: 20260404T020000Z)
  --restore-db NAME          Nome do banco isolado para restore
  --drop-restore-db          Remove o banco isolado ao final em caso de sucesso
  --skip-minio-check         Nao valida a presenca dos artefatos espelhados no bucket
  --minio-endpoint URL       Endpoint HTTP do MinIO (default: http://127.0.0.1:9000)
  --minio-user USER          Usuario do MinIO
  --minio-password PASS      Senha do MinIO
  --backup-bucket NAME       Bucket de backups (default: betaml-backups)
  --evidence-out PATH        Salva a saida completa em arquivo
  -h, --help                 Exibe esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE="$2"
      shift 2
      ;;
    --postgres-service)
      POSTGRES_SERVICE="$2"
      shift 2
      ;;
    --postgres-user)
      POSTGRES_USER="$2"
      shift 2
      ;;
    --postgres-db)
      POSTGRES_DB="$2"
      shift 2
      ;;
    --backup-file)
      BACKUP_FILE="$2"
      shift 2
      ;;
    --backup-object)
      BACKUP_OBJECT="$2"
      shift 2
      ;;
    --backup-timestamp)
      BACKUP_TIMESTAMP="$2"
      shift 2
      ;;
    --restore-db)
      RESTORE_DB="$2"
      shift 2
      ;;
    --drop-restore-db)
      DROP_RESTORE_DB=1
      shift
      ;;
    --skip-minio-check)
      SKIP_MINIO_CHECK=1
      shift
      ;;
    --minio-endpoint)
      MINIO_ENDPOINT="$2"
      shift 2
      ;;
    --minio-user)
      MINIO_USER="$2"
      shift 2
      ;;
    --minio-password)
      MINIO_PASSWORD="$2"
      shift 2
      ;;
    --backup-bucket)
      BACKUP_BUCKET="$2"
      shift 2
      ;;
    --evidence-out)
      EVIDENCE_OUT="$2"
      shift 2
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

cleanup() {
  local exit_code=$?
  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
    rm -rf "$WORK_DIR"
  fi
  if [[ $exit_code -eq 0 && $DROP_RESTORE_DB -eq 1 ]]; then
    docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
      psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS \"${RESTORE_DB}\";" >/dev/null
    echo "restore_db_dropped=${RESTORE_DB}"
  fi
}
trap cleanup EXIT

require_cmd() {
  local cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "dependencia ausente: $cmd" >&2
      exit 1
    fi
  done
}

section() {
  printf '\n== %s ==\n' "$1"
}

mc_run() {
  docker run --rm \
    --add-host host.docker.internal:host-gateway \
    minio/mc:RELEASE.2024-10-29T15-34-59Z "$@"
}

mc_with_alias() {
  docker run --rm \
    --add-host host.docker.internal:host-gateway \
    --entrypoint /bin/sh \
    minio/mc:RELEASE.2024-10-29T15-34-59Z \
    -c 'mc alias set backup "$1" "$2" "$3" >/dev/null && shift 3 && mc "$@"' \
    sh "$MINIO_ENDPOINT" "$MINIO_USER" "$MINIO_PASSWORD" "$@"
}

normalize_minio_endpoint() {
  case "$MINIO_ENDPOINT" in
    http://*|https://*) ;;
    *) MINIO_ENDPOINT="http://${MINIO_ENDPOINT}" ;;
  esac

  case "$MINIO_ENDPOINT" in
    http://127.0.0.1:*|http://localhost:*|https://127.0.0.1:*|https://localhost:*)
      MINIO_ENDPOINT="${MINIO_ENDPOINT/127.0.0.1/host.docker.internal}"
      MINIO_ENDPOINT="${MINIO_ENDPOINT/localhost/host.docker.internal}"
      ;;
  esac
}

compose_service_running() {
  docker compose -f "$COMPOSE_FILE" ps --services --status running 2>/dev/null | grep -Fxq "$1"
}

extract_timestamp_from_name() {
  local name="$1"
  local base
  base="$(basename "$name")"
  base="${base#postgres_}"
  base="${base%.sql.gz}"
  printf '%s\n' "$base"
}

discover_latest_backup_object() {
  normalize_minio_endpoint
  mc_with_alias ls "backup/${BACKUP_BUCKET}/postgres" | awk '{print $NF}' | grep '^postgres_.*\.sql\.gz$' | sort | tail -n 1
}

download_backup_object() {
  local object_path="$1"
  local target_path="$2"

  normalize_minio_endpoint

  # Evita bind mount de diretórios temporários não compartilhados no macOS.
  docker run --rm \
    --add-host host.docker.internal:host-gateway \
    --entrypoint /bin/sh \
    minio/mc:RELEASE.2024-10-29T15-34-59Z \
    -c 'mc alias set backup "$1" "$2" "$3" >/dev/null && mc cat "backup/$4"' \
    sh "$MINIO_ENDPOINT" "$MINIO_USER" "$MINIO_PASSWORD" "$object_path" > "$target_path"
}

validate_backup_object_exists() {
  local object_path="$1"
  normalize_minio_endpoint
  mc_with_alias stat "backup/${object_path}" >/dev/null
}

section "Contexto"
echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "compose_file=$COMPOSE_FILE"
echo "postgres_service=$POSTGRES_SERVICE"
echo "postgres_user=$POSTGRES_USER"
echo "postgres_db=$POSTGRES_DB"
echo "restore_db=$RESTORE_DB"
echo "backup_bucket=$BACKUP_BUCKET"

section "Dependencias"
require_cmd bash docker gzip gunzip awk sed grep mktemp

section "Compose"
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "compose file nao encontrado: $COMPOSE_FILE" >&2
  exit 1
fi
if ! compose_service_running "$POSTGRES_SERVICE"; then
  echo "servico postgres fora de execucao: $POSTGRES_SERVICE" >&2
  exit 1
fi
echo "postgres_service_running=$POSTGRES_SERVICE"
if [[ $SKIP_MINIO_CHECK -eq 0 ]]; then
  if ! compose_service_running minio; then
    echo "servico minio fora de execucao" >&2
    exit 1
  fi
  echo "minio_service_running=minio"
fi

section "Backup Source"
WORK_DIR="$(mktemp -d)"
if [[ -n "$BACKUP_FILE" ]]; then
  if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "backup file nao encontrado: $BACKUP_FILE" >&2
    exit 1
  fi
  BACKUP_FILE="$(cd "$(dirname "$BACKUP_FILE")" && pwd)/$(basename "$BACKUP_FILE")"
  echo "backup_source=local"
  echo "backup_file=$BACKUP_FILE"
else
  if [[ -z "$BACKUP_OBJECT" ]]; then
    latest_file="$(discover_latest_backup_object || true)"
    if [[ -z "$latest_file" ]]; then
      echo "nenhum backup encontrado em ${BACKUP_BUCKET}/postgres" >&2
      exit 1
    fi
    BACKUP_OBJECT="${BACKUP_BUCKET}/postgres/${latest_file}"
  fi
  BACKUP_FILE="$WORK_DIR/$(basename "$BACKUP_OBJECT")"
  download_backup_object "$BACKUP_OBJECT" "$BACKUP_FILE"
  echo "backup_source=minio"
  echo "backup_object=$BACKUP_OBJECT"
  echo "backup_file=$BACKUP_FILE"
fi

if [[ -z "$BACKUP_TIMESTAMP" ]]; then
  BACKUP_TIMESTAMP="$(extract_timestamp_from_name "$BACKUP_FILE")"
fi
echo "backup_timestamp=$BACKUP_TIMESTAMP"

section "Restore"
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"${RESTORE_DB}\";" >/dev/null
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
  psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE \"${RESTORE_DB}\";" >/dev/null
echo "restore_db_created=${RESTORE_DB}"

gunzip -c "$BACKUP_FILE" | docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
  psql -U "$POSTGRES_USER" -d "$RESTORE_DB" -v ON_ERROR_STOP=1 >/dev/null
echo "restore_import=ok"

section "Evidence"
echo "table_counts="
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
  psql -U "$POSTGRES_USER" -d "$RESTORE_DB" -At -F '|' -v ON_ERROR_STOP=1 <<'SQL'
SELECT 'players', count(*) FROM players
UNION ALL
SELECT 'alerts', count(*) FROM alerts
UNION ALL
SELECT 'cases', count(*) FROM cases
ORDER BY 1;
SQL

if [[ $SKIP_MINIO_CHECK -eq 0 ]]; then
  section "MinIO Artifacts"
  validate_backup_object_exists "${BACKUP_BUCKET}/postgres/postgres_${BACKUP_TIMESTAMP}.sql.gz"
  echo "postgres_backup_object=ok"
  if mc_with_alias ls "backup/${BACKUP_BUCKET}/minio/${BACKUP_TIMESTAMP}" >/tmp/betaml_restore_drill_minio_ls.txt 2>/dev/null \
    && [[ -s /tmp/betaml_restore_drill_minio_ls.txt ]]; then
    echo "minio_artifact_mirror=ok"
    sed -n '1,20p' /tmp/betaml_restore_drill_minio_ls.txt
    rm -f /tmp/betaml_restore_drill_minio_ls.txt
  else
    rm -f /tmp/betaml_restore_drill_minio_ls.txt
    echo "minio artifact mirror nao encontrado para timestamp ${BACKUP_TIMESTAMP}" >&2
    exit 1
  fi
fi

section "Resumo"
echo "restore_drill=PASS"
echo "restore_db_retained=${RESTORE_DB}"