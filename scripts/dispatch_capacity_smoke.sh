#!/usr/bin/env bash
set -euo pipefail

REPO="jairguerraadv-sys/BetAML"
WORKFLOW="capacity-smoke.yml"

usage() {
  cat <<'EOF'
Uso:
  scripts/dispatch_capacity_smoke.sh [opcoes]

Opcoes:
  --repo OWNER/REPO             Repositorio GitHub (default: jairguerraadv-sys/BetAML)
  --users N                     Usuarios Locust (default: 20)
  --spawn-rate N                Spawn rate do Locust (default: 5)
  --run-time DURATION           Duracao do teste (default: 120s)
  --min-rps N                   Threshold minimo de req/s (default: 15)
  --min-event-rps N             Threshold minimo de eventos/s (default: 150)
  --max-p95-ms N                Threshold maximo de p95 (default: 2000)
  --max-failure-rate-pct N      Threshold maximo de falha percentual (default: 1)
  -h, --help                    Exibe esta ajuda
EOF
}

USERS="20"
SPAWN_RATE="5"
RUN_TIME="120s"
MIN_RPS="15"
MIN_EVENT_RPS="150"
MAX_P95_MS="2000"
MAX_FAILURE_RATE_PCT="1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --users) USERS="$2"; shift 2 ;;
    --spawn-rate) SPAWN_RATE="$2"; shift 2 ;;
    --run-time) RUN_TIME="$2"; shift 2 ;;
    --min-rps) MIN_RPS="$2"; shift 2 ;;
    --min-event-rps) MIN_EVENT_RPS="$2"; shift 2 ;;
    --max-p95-ms) MAX_P95_MS="$2"; shift 2 ;;
    --max-failure-rate-pct) MAX_FAILURE_RATE_PCT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Argumento invalido: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! bash "$ROOT_DIR/scripts/check_github_workflow_sync.sh" --repo "$REPO" "$WORKFLOW" >/dev/null; then
  echo "capacity_smoke_dispatch=blocked workflow_sync=FAIL workflow=$WORKFLOW repo=$REPO" >&2
  bash "$ROOT_DIR/scripts/check_github_workflow_sync.sh" --repo "$REPO" "$WORKFLOW" >&2
  exit 1
fi

dispatch_err="$(mktemp)"
trap 'rm -f "$dispatch_err"' EXIT

if ! env -u GITHUB_TOKEN gh workflow run "$WORKFLOW" \
  --repo "$REPO" \
  -f locust_users="$USERS" \
  -f locust_spawn_rate="$SPAWN_RATE" \
  -f locust_run_time="$RUN_TIME" \
  -f min_rps="$MIN_RPS" \
  -f min_event_rps="$MIN_EVENT_RPS" \
  -f max_p95_ms="$MAX_P95_MS" \
  -f max_failure_rate_pct="$MAX_FAILURE_RATE_PCT" \
  2>"$dispatch_err"; then
  cat "$dispatch_err" >&2
  if grep -q 'Unexpected inputs provided' "$dispatch_err"; then
    echo "hint=workflow_remoto_desatualizado_em_relacao_ao_workspace" >&2
    echo "action=publique_no_GitHub_a_versao_atual_de_.github/workflows/${WORKFLOW}_antes_do_dispatch" >&2
  fi
  exit 1
fi

echo "capacity_smoke_dispatch=ok repo=$REPO workflow=$WORKFLOW"