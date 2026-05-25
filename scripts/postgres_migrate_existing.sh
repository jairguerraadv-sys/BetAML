#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${ROOT_DIR}/infra/docker-compose.yml}"
PG_SERVICE="${PG_SERVICE:-postgres}"
PG_DB="${PG_DB:-betaml_dev}"
PG_USER="${PG_USER:-betaml}"
DRY_RUN=0

usage() {
  cat <<'EOF'
Uso:
  scripts/postgres_migrate_existing.sh [--dry-run]

Opcoes via variavel de ambiente:
  COMPOSE_FILE  Caminho do docker-compose (default: infra/docker-compose.yml)
  PG_SERVICE    Nome do servico Postgres no compose (default: postgres)
  PG_DB         Nome do banco (default: betaml_dev)
  PG_USER       Usuario do banco (default: betaml)

Exemplos:
  scripts/postgres_migrate_existing.sh
  COMPOSE_FILE=infra/docker-compose.yml PG_DB=betaml_dev scripts/postgres_migrate_existing.sh --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Argumento invalido: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker nao encontrado no PATH" >&2
  exit 1
fi

if ! docker compose -f "${COMPOSE_FILE}" ps --services 2>/dev/null | grep -Fxq "${PG_SERVICE}"; then
  echo "Nao foi possivel acessar o servico ${PG_SERVICE} no compose ${COMPOSE_FILE}" >&2
  exit 1
fi

run_psql() {
  docker compose -f "${COMPOSE_FILE}" exec -T "${PG_SERVICE}" \
    psql -v ON_ERROR_STOP=1 -U "${PG_USER}" -d "${PG_DB}" "$@"
}

sql_bool() {
  local query="$1"
  local value
  value="$(run_psql -tAc "$query" | tr -d '[:space:]')"
  [[ "${value}" == "t" ]]
}

version_from_filename() {
  local file="$1"
  local version
  version="${file#migration_v}"
  version="${version%.sql}"
  echo "${version}"
}

probe_applied_version() {
  local version="$1"
  case "${version}" in
    2)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ingest_errors')"
      ;;
    3)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='financial_transactions')"
      ;;
    4)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='report_packages' AND column_name='pdf_path')"
      ;;
    5)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='compound_rules' AND column_name='logic')"
      ;;
    6)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='scoring_configs' AND column_name='low_threshold')"
      ;;
    7)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='feature_snapshots' AND column_name='snapshot_date')"
      ;;
    8)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='notifications' AND column_name='is_read')"
      ;;
    9)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='notifications' AND column_name='reference_type')"
      ;;
    10)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='feature_snapshots' AND column_name='feature_version')"
      ;;
    11)
      sql_bool "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname='idx_alerts_tenant_status_created')"
      ;;
    12)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='alerts' AND column_name='label_note')"
      ;;
    13)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='tenants' AND column_name='cnpj')"
      ;;
    14)
      sql_bool "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND indexname='idx_notifications_user_unread')"
      ;;
    15)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='refresh_token_jti')"
      ;;
    16)
      sql_bool "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='scoring_configs' AND column_name='ml_challenger_pct')"
      ;;
    17)
      # v17 remove o constraint legado players_status_check (que bloqueava status='ERASED').
      # Considera aplicada quando o constraint NAO existe (idempotente).
      sql_bool "SELECT CASE WHEN to_regclass('public.players') IS NULL THEN false ELSE NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='players_status_check') END"
      ;;
    30)
      sql_bool "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='case_events_event_type_check' AND pg_get_constraintdef(oid) ILIKE '%REPORT_SUBMITTED%')"
      ;;
    31)
      sql_bool "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='report_packages_status_check' AND pg_get_constraintdef(oid) ILIKE '%FILED%')"
      ;;
    *)
      return 1
      ;;
  esac
}

run_psql <<'SQL'
CREATE TABLE IF NOT EXISTS public.schema_migrations (
  filename   text PRIMARY KEY,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

MIGRATIONS=()
while IFS= read -r migration_path; do
  [[ -n "${migration_path}" ]] && MIGRATIONS+=("${migration_path}")
done < <(find "${ROOT_DIR}/infra" -maxdepth 1 -type f -name 'migration_v*.sql' -print | sort -V)

sha256_file() {
  local file_path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file_path" | awk '{print $1}'
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file_path" | awk '{print $1}'
    return
  fi
  echo "ERRO: sha256sum/shasum nao encontrado" >&2
  exit 1
}

if [[ ${#MIGRATIONS[@]} -eq 0 ]]; then
  echo "Nenhuma migration encontrada em ${ROOT_DIR}/infra" >&2
  exit 1
fi

echo "Aplicando migrations em ${PG_DB} (${PG_SERVICE})"
for migration in "${MIGRATIONS[@]}"; do
  file="$(basename "${migration}")"
  version="$(version_from_filename "${file}")"
  checksum="$(sha256_file "${migration}")"

  existing_checksum="$(run_psql -tAc "SELECT checksum FROM public.schema_migrations WHERE filename = '${file}'" | tr -d '[:space:]')"

  if [[ -n "${existing_checksum}" ]]; then
    if [[ "${existing_checksum}" != "${checksum}" ]]; then
      echo "ERRO: ${file} ja aplicada com checksum diferente." >&2
      echo "      registrado=${existing_checksum}" >&2
      echo "      atual=${checksum}" >&2
      exit 1
    fi
    echo "SKIP  ${file} (ja aplicada)"
    continue
  fi

  if probe_applied_version "${version}"; then
    if [[ ${DRY_RUN} -eq 1 ]]; then
      echo "MARK  ${file} (probe: ja aplicada)"
    else
      echo "MARK  ${file} (probe: ja aplicada)"
      run_psql -c "INSERT INTO public.schema_migrations (filename, checksum) VALUES ('${file}', '${checksum}')"
    fi
    continue
  fi

  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "PLAN  ${file}"
    continue
  fi

  echo "APPLY ${file}"
  run_psql < "${migration}"
  run_psql -c "INSERT INTO public.schema_migrations (filename, checksum) VALUES ('${file}', '${checksum}')"
done

if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "Dry-run concluido. Nenhuma migration foi aplicada."
else
  echo "Migracoes aplicadas com sucesso."
fi
