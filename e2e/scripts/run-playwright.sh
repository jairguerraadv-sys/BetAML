#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$ROOT_DIR/.env.e2e" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env.e2e"
  set +a
fi

if [ "$#" -eq 0 ]; then
  echo "Usage: bash scripts/run-playwright.sh <playwright test args...>" >&2
  exit 1
fi

max_attempts="${PW_LAUNCH_RETRIES:-8}"
attempt=1
tmp_output="$(mktemp)"
trap 'rm -f "$tmp_output"' EXIT

require_stack_health="${PW_REQUIRE_STACK_HEALTH:-1}"
health_timeout_sec="${PW_HEALTH_TIMEOUT_SEC:-60}"
base_url="${BASE_URL:-http://localhost:3000}"
api_url="${E2E_API_URL:-http://localhost:8000}"
capture_docker_diagnostics="${PW_CAPTURE_DOCKER_DIAGNOSTICS:-1}"
diagnostic_log_lines="${PW_DIAGNOSTIC_LOG_LINES:-120}"
compose_file="${PW_COMPOSE_FILE:-$ROOT_DIR/../infra/docker-compose.yml}"

check_url() {
  local url="$1"
  curl -fsS --max-time 3 "$url" >/dev/null 2>&1
}

capture_stack_diagnostics() {
  local reason="$1"

  if [ "$capture_docker_diagnostics" != "1" ]; then
    return 0
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "[playwright-wrapper] docker nao encontrado, ignorando diagnostico (${reason})"
    return 0
  fi

  if [ ! -f "$compose_file" ]; then
    echo "[playwright-wrapper] compose file nao encontrado em $compose_file, ignorando diagnostico (${reason})"
    return 0
  fi

  echo
  echo "[playwright-wrapper] diagnostic snapshot (${reason})"
  echo "[playwright-wrapper] compose file: $compose_file"
  echo

  set +e
  docker compose -f "$compose_file" ps || true
  echo
  echo "[playwright-wrapper] logs api (tail=${diagnostic_log_lines})"
  docker compose -f "$compose_file" logs --tail "$diagnostic_log_lines" api || true
  echo
  echo "[playwright-wrapper] logs frontend (tail=${diagnostic_log_lines})"
  docker compose -f "$compose_file" logs --tail "$diagnostic_log_lines" frontend || true
  set -e
}

wait_for_stack_health() {
  local elapsed=0
  local frontend_ready=0
  local api_ready=0
  local api_live_url="${api_url%/}/health/live"
  local api_health_url="${api_url%/}/health"

  while [ "$elapsed" -lt "$health_timeout_sec" ]; do
    if check_url "$base_url"; then
      frontend_ready=1
    else
      frontend_ready=0
    fi

    if check_url "$api_live_url" || check_url "$api_health_url"; then
      api_ready=1
    else
      api_ready=0
    fi

    if [ "$frontend_ready" -eq 1 ] && [ "$api_ready" -eq 1 ]; then
      return 0
    fi

    sleep 2
    elapsed=$((elapsed + 2))
  done

  echo "[playwright-wrapper] preflight timeout after ${health_timeout_sec}s"
  echo "[playwright-wrapper] frontend: $base_url"
  echo "[playwright-wrapper] api: $api_live_url (fallback $api_health_url)"
  return 1
}

slug="suite"
for arg in "$@"; do
  case "$arg" in
    -*) ;;
    *)
      slug="$arg"
      break
      ;;
  esac
done
slug="$(printf '%s' "$slug" | sed -E 's#[^A-Za-z0-9._-]+#-#g')"
slug="${slug#-}"
slug="${slug%-}"
if [ -z "$slug" ]; then
  slug="suite"
fi

html_output_dir="${PLAYWRIGHT_HTML_OUTPUT_DIR:-playwright-report}"
resolved_html_output_dir=""
if [ -n "$html_output_dir" ]; then
  resolved_html_output_dir="${html_output_dir}/${slug}"
  mkdir -p "$resolved_html_output_dir"
fi

junit_output_dir="${PLAYWRIGHT_JUNIT_OUTPUT_DIR:-test-results}"
resolved_junit_output_name=""
if [ -n "$junit_output_dir" ]; then
  mkdir -p "$junit_output_dir"
  junit_output_name="${PLAYWRIGHT_JUNIT_OUTPUT_NAME:-results.xml}"
  case "$junit_output_name" in
    *.*)
      base_name="${junit_output_name%.*}"
      ext_name="${junit_output_name##*.}"
      resolved_junit_output_name="${base_name}-${slug}.${ext_name}"
      ;;
    *)
      resolved_junit_output_name="${junit_output_name}-${slug}.xml"
      ;;
  esac
fi

while [ "$attempt" -le "$max_attempts" ]; do
  if [ "$require_stack_health" = "1" ]; then
    if ! wait_for_stack_health; then
      capture_stack_diagnostics "preflight-timeout-attempt-${attempt}"
      if [ "$attempt" -lt "$max_attempts" ]; then
        echo "[playwright-wrapper] retry $((attempt + 1))/$max_attempts after stack preflight failure"
        attempt=$((attempt + 1))
        sleep 3
        continue
      fi
      exit 1
    fi
  fi

  env_vars=()
  if [ -n "$resolved_html_output_dir" ]; then
    env_vars+=("PLAYWRIGHT_HTML_OUTPUT_DIR=$resolved_html_output_dir")
  fi
  if [ -n "$junit_output_dir" ]; then
    env_vars+=("PLAYWRIGHT_JUNIT_OUTPUT_DIR=$junit_output_dir")
  fi
  if [ -n "$resolved_junit_output_name" ]; then
    env_vars+=("PLAYWRIGHT_JUNIT_OUTPUT_NAME=$resolved_junit_output_name")
  fi

  set +e
  if [ "${#env_vars[@]}" -gt 0 ]; then
    env "${env_vars[@]}" npm run test -- "$@" >"$tmp_output" 2>&1
  else
    npm run test -- "$@" >"$tmp_output" 2>&1
  fi
  status="$?"
  set -e

  cat "$tmp_output"

  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  if grep -Eq 'browserType\.launch|Target page, context or browser has been closed|crashpad|setsockopt: Operation not permitted|ERR_CONNECTION_REFUSED|net::ERR_CONNECTION_REFUSED|Timed out waiting for navigation|Timeout [0-9]+ms exceeded.*beforeEach' "$tmp_output" && [ "$attempt" -lt "$max_attempts" ]; then
    capture_stack_diagnostics "transient-failure-attempt-${attempt}"
    echo
    echo "[playwright-wrapper] retry $((attempt + 1))/$max_attempts after transient environment failure"
    echo
    attempt=$((attempt + 1))
    sleep 3
    continue
  fi

  exit "$status"
done

exit 1
