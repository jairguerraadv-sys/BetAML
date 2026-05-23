#!/usr/bin/env bash
set -euo pipefail

PREFLIGHT_EVIDENCE=""
RESTORE_EVIDENCE=""
CAPACITY_EVIDENCE=""
JUNIT_DIR=""
BACKUP_REFERENCE=""
ROLLBACK_TARGET=""
ONCALL_OWNER=""
EVIDENCE_OUT=""
MAX_BACKUP_AGE_HOURS=24
SKIP_BACKUP_AGE_CHECK=0
EXTERNAL_PROVIDER="${EXTERNAL_VALIDATION_PROVIDER:-}"
ALLOW_MOCK_PROVIDER=0

usage() {
  cat <<'EOF'
Uso:
  scripts/release_decision_gate.sh [opcoes]

Opcoes:
  --preflight-evidence PATH      Log do readiness_preflight.sh
  --restore-evidence PATH        Log do restore_drill.sh
  --capacity-evidence PATH       Evidencia do validate_slo.py
  --junit-dir PATH               Diretorio com XMLs JUnit do Playwright
  --backup-reference TEXT        Referencia do ultimo backup valido
  --rollback-target TEXT         Revisao alvo de rollback
  --oncall-owner TEXT            Responsavel on-call pela janela
  --external-provider NAME       Provider externo/KYC ativo no corte (ex.: idwall)
  --allow-mock-provider          Permite mock provider (somente para ambiente controlado)
  --max-backup-age-hours N       Idade maxima permitida do backup em horas (default: 24)
  --skip-backup-age-check        Nao valida idade do backup_reference
  --evidence-out PATH            Salva a decisao completa em arquivo
  -h, --help                     Exibe esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preflight-evidence)
      PREFLIGHT_EVIDENCE="$2"
      shift 2
      ;;
    --restore-evidence)
      RESTORE_EVIDENCE="$2"
      shift 2
      ;;
    --capacity-evidence)
      CAPACITY_EVIDENCE="$2"
      shift 2
      ;;
    --junit-dir)
      JUNIT_DIR="$2"
      shift 2
      ;;
    --backup-reference)
      BACKUP_REFERENCE="$2"
      shift 2
      ;;
    --rollback-target)
      ROLLBACK_TARGET="$2"
      shift 2
      ;;
    --oncall-owner)
      ONCALL_OWNER="$2"
      shift 2
      ;;
    --external-provider)
      EXTERNAL_PROVIDER="$2"
      shift 2
      ;;
    --allow-mock-provider)
      ALLOW_MOCK_PROVIDER=1
      shift
      ;;
    --max-backup-age-hours)
      MAX_BACKUP_AGE_HOURS="$2"
      shift 2
      ;;
    --skip-backup-age-check)
      SKIP_BACKUP_AGE_CHECK=1
      shift
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

require_file() {
  local path="$1"
  local label="$2"
  if [[ -f "$path" ]]; then
    pass "$label presente"
  else
    fail "$label ausente: $path"
  fi
}

require_non_empty() {
  local value="$1"
  local label="$2"
  if [[ -n "${value// }" ]]; then
    pass "$label informado"
  else
    fail "$label nao informado"
  fi
}

validate_backup_reference_age() {
  local reference="$1"
  local max_age_hours="$2"

  if ! [[ "$max_age_hours" =~ ^[0-9]+$ ]]; then
    fail "max_backup_age_hours invalido: $max_age_hours"
    return
  fi

  local ts
  ts="$(printf '%s' "$reference" | grep -Eo '[0-9]{8}T[0-9]{6}Z' | head -n1 || true)"
  if [[ -z "$ts" ]]; then
    fail "backup_reference sem timestamp no formato YYYYMMDDTHHMMSSZ"
    return
  fi

  local epoch_parser=""
  if command -v python3 >/dev/null 2>&1; then
    epoch_parser="python3"
  elif command -v python >/dev/null 2>&1; then
    epoch_parser="python"
  else
    fail "python/python3 ausente para validar idade do backup_reference"
    return
  fi

  local backup_epoch now_epoch age_hours
  backup_epoch="$($epoch_parser - <<PY
from datetime import datetime, timezone
print(int(datetime.strptime("$ts", "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()))
PY
)"
  now_epoch="$($epoch_parser - <<'PY'
from datetime import datetime, timezone
print(int(datetime.now(timezone.utc).timestamp()))
PY
)"

  if ! [[ "$backup_epoch" =~ ^[0-9]+$ ]] || ! [[ "$now_epoch" =~ ^[0-9]+$ ]]; then
    fail "falha ao calcular epoch do backup_reference"
    return
  fi

  if (( now_epoch < backup_epoch )); then
    fail "backup_reference possui timestamp no futuro: $ts"
    return
  fi

  age_hours=$(( (now_epoch - backup_epoch) / 3600 ))
  if (( age_hours > max_age_hours )); then
    fail "backup_reference excede idade maxima: ${age_hours}h > ${max_age_hours}h"
    return
  fi

  pass "backup_reference com idade valida (${age_hours}h <= ${max_age_hours}h)"
}

validate_external_provider() {
  local provider_raw="$1"
  local allow_mock="$2"
  local provider

  provider="$(printf '%s' "$provider_raw" | tr '[:upper:]' '[:lower:]' | xargs)"
  if [[ -z "$provider" ]]; then
    fail "external_provider vazio"
    return
  fi

  if [[ "$provider" == "mock" || "$provider" == "mock_identity" ]]; then
    if [[ "$allow_mock" -eq 1 ]]; then
      pass "external_provider mock permitido por override (--allow-mock-provider)"
    else
      fail "external_provider mock nao permitido para go/no-go formal"
    fi
    return
  fi

  pass "external_provider real informado: $provider"
}

validate_junit_file() {
  local xml_path="$1"
  local label="$2"

  if [[ ! -f "$xml_path" ]]; then
    fail "$label ausente: $xml_path"
    return
  fi

  local failures errors
  failures="$(grep -o 'failures="[0-9]\+"' "$xml_path" | head -n1 | sed 's/[^0-9]//g')"
  errors="$(grep -o 'errors="[0-9]\+"' "$xml_path" | head -n1 | sed 's/[^0-9]//g')"
  failures="${failures:-0}"
  errors="${errors:-0}"

  if [[ "$failures" == "0" && "$errors" == "0" ]]; then
    pass "$label sem falhas"
  else
    fail "$label com falhas/errors failures=${failures} errors=${errors}"
  fi
}

validate_junit_suite() {
  local junit_dir="$1"
  local suite_prefix="$2"
  local label="$3"
  local before_failures="$FAILURES"
  local files=()
  local file_path

  if [[ ! -d "$junit_dir" ]]; then
    fail "$label ausente: diretorio nao encontrado em $junit_dir"
    return
  fi

  while IFS= read -r file_path; do
    files+=("$file_path")
  done < <(find "$junit_dir" -maxdepth 1 -type f \( -name "${suite_prefix}.xml" -o -name "${suite_prefix}-*.xml" \) | sort)

  if [[ "${#files[@]}" -eq 0 ]]; then
    fail "$label ausente: nenhum XML encontrado com prefixo ${suite_prefix} em $junit_dir"
    return
  fi

  for file_path in "${files[@]}"; do
    validate_junit_file "$file_path" "$label ($(basename "$file_path"))"
  done

  if [[ "$FAILURES" -eq "$before_failures" ]]; then
    pass "$label aprovado com ${#files[@]} arquivo(s) JUnit"
  fi
}

section "Contexto"
echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "backup_reference=$BACKUP_REFERENCE"
echo "rollback_target=$ROLLBACK_TARGET"
echo "oncall_owner=$ONCALL_OWNER"
echo "external_provider=$EXTERNAL_PROVIDER"
echo "capacity_evidence=$CAPACITY_EVIDENCE"

section "Metadados obrigatorios"
require_non_empty "$BACKUP_REFERENCE" "backup_reference"
require_non_empty "$ROLLBACK_TARGET" "rollback_target"
require_non_empty "$ONCALL_OWNER" "oncall_owner"
require_non_empty "$PREFLIGHT_EVIDENCE" "preflight_evidence"
require_non_empty "$RESTORE_EVIDENCE" "restore_evidence"
require_non_empty "$CAPACITY_EVIDENCE" "capacity_evidence"
require_non_empty "$JUNIT_DIR" "junit_dir"
require_non_empty "$EXTERNAL_PROVIDER" "external_provider"

if [[ "$SKIP_BACKUP_AGE_CHECK" -eq 0 ]]; then
  validate_backup_reference_age "$BACKUP_REFERENCE" "$MAX_BACKUP_AGE_HOURS"
else
  pass "validacao de idade do backup_reference ignorada (--skip-backup-age-check)"
fi

validate_external_provider "$EXTERNAL_PROVIDER" "$ALLOW_MOCK_PROVIDER"

section "Evidencias locais"
if [[ -n "${PREFLIGHT_EVIDENCE// }" ]]; then
  require_file "$PREFLIGHT_EVIDENCE" "artifact-readiness-preflight"
fi
if [[ -n "${RESTORE_EVIDENCE// }" ]]; then
  require_file "$RESTORE_EVIDENCE" "artifact-readiness-restore-drill"
fi
if [[ -n "${CAPACITY_EVIDENCE// }" ]]; then
  require_file "$CAPACITY_EVIDENCE" "artifact-readiness-capacity-smoke"
fi

if [[ -f "$PREFLIGHT_EVIDENCE" ]]; then
  if grep -q 'readiness_preflight=PASS' "$PREFLIGHT_EVIDENCE"; then
    pass "preflight operacional aprovado"
  else
    fail "preflight operacional nao aprovado"
  fi
fi

if [[ -f "$RESTORE_EVIDENCE" ]]; then
  if grep -q 'restore_drill=PASS' "$RESTORE_EVIDENCE"; then
    pass "restore drill aprovado"
  else
    fail "restore drill nao aprovado"
  fi
fi

if [[ -f "$CAPACITY_EVIDENCE" ]]; then
  if grep -q 'load_slo=PASS' "$CAPACITY_EVIDENCE"; then
    pass "capacity smoke aprovado"
  else
    fail "capacity smoke nao aprovado"
  fi
fi

section "E2E critico"
if [[ -n "${JUNIT_DIR// }" ]]; then
  validate_junit_suite "$JUNIT_DIR" "readiness-smoke" "smoke suite"
  validate_junit_suite "$JUNIT_DIR" "readiness-extended" "extended suite"
  validate_junit_suite "$JUNIT_DIR" "readiness-security" "security suite"
fi

section "Resumo"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "release_go_no_go=GO"
  exit 0
fi

echo "release_go_no_go=NO_GO failures=$FAILURES"
exit 1