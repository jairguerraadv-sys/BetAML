#!/usr/bin/env bash
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: bash scripts/run-playwright.sh <playwright test args...>" >&2
  exit 1
fi

max_attempts="${PW_LAUNCH_RETRIES:-8}"
attempt=1
tmp_output="$(mktemp)"
trap 'rm -f "$tmp_output"' EXIT

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

  if grep -Eq 'browserType\.launch|Target page, context or browser has been closed|crashpad|setsockopt: Operation not permitted' "$tmp_output" && [ "$attempt" -lt "$max_attempts" ]; then
    echo
    echo "[playwright-wrapper] retry $((attempt + 1))/$max_attempts after launcher failure"
    echo
    attempt=$((attempt + 1))
    sleep 3
    continue
  fi

  exit "$status"
done

exit 1
