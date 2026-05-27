#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${PYTEST_BIN:-}" ]]; then
  read -r -a PYTEST_CMD <<<"${PYTEST_BIN}"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTEST_CMD=("$ROOT_DIR/.venv/bin/python" -m pytest)
elif [[ -x "$ROOT_DIR/.venv-1/bin/pytest" ]]; then
  PYTEST_CMD=("$ROOT_DIR/.venv-1/bin/pytest")
else
  PYTEST_CMD=(python -m pytest)
fi

cd "$ROOT_DIR"

INCLUDE_REMAINDER=false
CRITICAL_COVERAGE=false
PASSTHROUGH_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --include-remainder)
      INCLUDE_REMAINDER=true
      ;;
    --critical-coverage)
      CRITICAL_COVERAGE=true
      ;;
    *)
      PASSTHROUGH_ARGS+=("$arg")
      ;;
  esac
done

COMMON_ARGS=()
COVERAGE_SOURCE_ARGS=()
FINAL_ONLY_ARGS=()
if [[ "${#PASSTHROUGH_ARGS[@]}" -gt 0 ]]; then
  for arg in "${PASSTHROUGH_ARGS[@]}"; do
    case "$arg" in
      --cov=*)
        COVERAGE_SOURCE_ARGS+=("$arg")
        ;;
      --cov-report=*|--cov-fail-under=*)
        FINAL_ONLY_ARGS+=("$arg")
        ;;
      *)
        COMMON_ARGS+=("$arg")
        ;;
    esac
  done
fi

CRITICAL_INCLUDE="services/api/auth.py,services/api/config.py,services/api/database.py,services/api/models.py,services/api/routers/auth.py,services/api/routers/audit.py,services/api/routers/ingest.py,services/api/routers/ml.py,services/api/routers/reports.py"

if [[ "$CRITICAL_COVERAGE" == "true" ]]; then
  if [[ "${#COVERAGE_SOURCE_ARGS[@]}" -eq 0 ]]; then
    COVERAGE_SOURCE_ARGS+=("--cov=services/api")
  fi
  if [[ "${#FINAL_ONLY_ARGS[@]}" -eq 0 ]]; then
    FINAL_ONLY_ARGS+=("--cov-report=term-missing")
    FINAL_ONLY_ARGS+=("--cov-report=xml:coverage.xml")
    FINAL_ONLY_ARGS+=("--cov-report=json:artifacts/coverage/critical-api-coverage.json")
  fi
fi

BATCH_1=(
  tests/unit/test_cases.py
  tests/unit/test_cases_module5.py
  tests/unit/test_ingest_core.py
  tests/unit/test_ingest_extended.py
  tests/unit/test_mapping.py
  tests/unit/test_connectors.py
  tests/unit/test_feature_store.py
  tests/unit/test_feature_store_runtime.py
  tests/unit/test_features.py
  tests/unit/test_stream_processor.py
  tests/unit/test_rules.py
  tests/unit/test_dsl.py
  tests/unit/test_dsl_arithmetic.py
  tests/unit/test_dsl_macros.py
  tests/unit/test_rules_engine.py
  tests/unit/test_rules_engine_runtime.py
  tests/unit/test_rules_engine_contract.py
  tests/unit/test_compound_rules_routes.py
  tests/unit/test_player_lists_routes.py
  tests/unit/test_rate_limit_role.py
)

BATCH_2=(
  tests/unit/test_ml_routes.py
  tests/unit/test_ml_explainability.py
  tests/unit/test_ml_service.py
  tests/unit/test_ml_trainer.py
  tests/unit/test_reports.py
  tests/unit/test_module6_audit.py
  tests/unit/test_audit.py
  tests/unit/test_notifications.py
  tests/unit/test_stats.py
  tests/unit/test_search_internal.py
  tests/unit/test_module7.py
  tests/unit/test_infra_resilience.py
  tests/unit/test_rate_limit_role.py
)

BATCHES=(BATCH_1 BATCH_2)

CRITICAL_FILES=("${BATCH_1[@]}" "${BATCH_2[@]}")

is_critical_file() {
  local candidate="$1"
  local file
  for file in "${CRITICAL_FILES[@]}"; do
    if [[ "$file" == "$candidate" ]]; then
      return 0
    fi
  done
  return 1
}

if [[ "$INCLUDE_REMAINDER" == "true" ]]; then
  REMAINDER=()
  while IFS= read -r file; do
    if ! is_critical_file "$file"; then
      REMAINDER+=("$file")
    fi
  done < <(find tests -type f -name 'test_*.py' | sort)
  if [[ "${#REMAINDER[@]}" -gt 0 ]]; then
    BATCHES+=(REMAINDER)
  fi
fi

run_batch() {
  local label="$1"
  local append_coverage="$2"
  local is_final="$3"
  shift 3

  local args=("${COMMON_ARGS[@]}")
  if [[ "${#COVERAGE_SOURCE_ARGS[@]}" -gt 0 ]]; then
    args+=("${COVERAGE_SOURCE_ARGS[@]}")
    if [[ "$append_coverage" == "true" ]]; then
      args+=("--cov-append")
    fi
    if [[ "$is_final" == "true" ]]; then
      args+=("${FINAL_ONLY_ARGS[@]}")
    fi
  fi

  echo "[critical-unit] $label"
  DEBUG="${DEBUG:-false}" "${PYTEST_CMD[@]}" "$@" "${args[@]}"
}

for i in "${!BATCHES[@]}"; do
  batch_name="${BATCHES[$i]}"
  append_coverage=false
  is_final=false
  if [[ "$i" -gt 0 ]]; then
    append_coverage=true
  fi
  if [[ "$i" -eq "$((${#BATCHES[@]} - 1))" ]]; then
    is_final=true
  fi
  # shellcheck disable=SC1083,SC2178
  eval "batch_files=(\"\${${batch_name}[@]}\")"
  batch_label="$(printf '%s' "$batch_name" | tr '[:upper:]' '[:lower:]')"
  run_batch "$batch_label" "$append_coverage" "$is_final" "${batch_files[@]}"
done

if [[ "$CRITICAL_COVERAGE" == "true" ]]; then
  echo "[critical-unit] enforcing critical API coverage >= 70%"
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    COVERAGE_CMD=("$ROOT_DIR/.venv/bin/python" -m coverage)
  else
    COVERAGE_CMD=(python -m coverage)
  fi
  "${COVERAGE_CMD[@]}" report --include="$CRITICAL_INCLUDE" --fail-under=70
fi
