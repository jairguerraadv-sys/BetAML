#!/usr/bin/env bash
set -euo pipefail

REPO="jairguerraadv-sys/BetAML"
WORKFLOW="release-readiness.yml"

usage() {
  cat <<'EOF'
Uso:
  scripts/dispatch_release_readiness.sh --backup-reference TEXT --rollback-target TEXT --oncall-owner TEXT [opcoes]

Opcoes obrigatorias:
  --backup-reference TEXT       Ultimo backup valido (timestamp + bucket/caminho)
  --rollback-target TEXT        Revisao alvo de rollback
  --oncall-owner TEXT           Responsavel on-call pela janela

Opcoes adicionais:
  --repo OWNER/REPO             Repositorio GitHub (default: jairguerraadv-sys/BetAML)
  --e2e-username USER           Override de vars.E2E_USERNAME
  --capacity-users N            Usuarios Locust (default: 20)
  --capacity-spawn-rate N       Spawn rate do Locust (default: 5)
  --capacity-run-time DURATION  Duracao do capacity smoke (default: 120s)
  --capacity-min-rps N          Threshold minimo de req/s (default: 15)
  --capacity-min-event-rps N    Threshold minimo de eventos/s (default: 150)
  --capacity-max-p95-ms N       Threshold maximo de p95 (default: 2000)
  --capacity-max-failure-rate-pct N  Threshold maximo de falha percentual (default: 1)
  -h, --help                    Exibe esta ajuda
EOF
}

BACKUP_REFERENCE=""
ROLLBACK_TARGET=""
ONCALL_OWNER=""
E2E_USERNAME=""
CAPACITY_USERS="20"
CAPACITY_SPAWN_RATE="5"
CAPACITY_RUN_TIME="120s"
CAPACITY_MIN_RPS="15"
CAPACITY_MIN_EVENT_RPS="150"
CAPACITY_MAX_P95_MS="2000"
CAPACITY_MAX_FAILURE_RATE_PCT="1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --backup-reference) BACKUP_REFERENCE="$2"; shift 2 ;;
    --rollback-target) ROLLBACK_TARGET="$2"; shift 2 ;;
    --oncall-owner) ONCALL_OWNER="$2"; shift 2 ;;
    --e2e-username) E2E_USERNAME="$2"; shift 2 ;;
    --capacity-users) CAPACITY_USERS="$2"; shift 2 ;;
    --capacity-spawn-rate) CAPACITY_SPAWN_RATE="$2"; shift 2 ;;
    --capacity-run-time) CAPACITY_RUN_TIME="$2"; shift 2 ;;
    --capacity-min-rps) CAPACITY_MIN_RPS="$2"; shift 2 ;;
    --capacity-min-event-rps) CAPACITY_MIN_EVENT_RPS="$2"; shift 2 ;;
    --capacity-max-p95-ms) CAPACITY_MAX_P95_MS="$2"; shift 2 ;;
    --capacity-max-failure-rate-pct) CAPACITY_MAX_FAILURE_RATE_PCT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Argumento invalido: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$BACKUP_REFERENCE" || -z "$ROLLBACK_TARGET" || -z "$ONCALL_OWNER" ]]; then
  echo "--backup-reference, --rollback-target e --oncall-owner sao obrigatorios" >&2
  usage >&2
  exit 2
fi

if ! bash "$ROOT_DIR/scripts/check_github_workflow_sync.sh" --repo "$REPO" "$WORKFLOW" >/dev/null; then
  echo "release_readiness_dispatch=blocked workflow_sync=FAIL workflow=$WORKFLOW repo=$REPO" >&2
  bash "$ROOT_DIR/scripts/check_github_workflow_sync.sh" --repo "$REPO" "$WORKFLOW" >&2
  exit 1
fi

args=(
  workflow run "$WORKFLOW"
  --repo "$REPO"
  -f backup_reference="$BACKUP_REFERENCE"
  -f rollback_target="$ROLLBACK_TARGET"
  -f oncall_owner="$ONCALL_OWNER"
  -f capacity_users="$CAPACITY_USERS"
  -f capacity_spawn_rate="$CAPACITY_SPAWN_RATE"
  -f capacity_run_time="$CAPACITY_RUN_TIME"
  -f capacity_min_rps="$CAPACITY_MIN_RPS"
  -f capacity_min_event_rps="$CAPACITY_MIN_EVENT_RPS"
  -f capacity_max_p95_ms="$CAPACITY_MAX_P95_MS"
  -f capacity_max_failure_rate_pct="$CAPACITY_MAX_FAILURE_RATE_PCT"
)

if [[ -n "$E2E_USERNAME" ]]; then
  args+=( -f e2e_username="$E2E_USERNAME" )
fi

dispatch_err="$(mktemp)"
trap 'rm -f "$dispatch_err"' EXIT

if ! env -u GITHUB_TOKEN gh "${args[@]}" 2>"$dispatch_err"; then
  cat "$dispatch_err" >&2
  if grep -q 'Unexpected inputs provided' "$dispatch_err"; then
    echo "hint=workflow_remoto_desatualizado_em_relacao_ao_workspace" >&2
    echo "action=publique_no_GitHub_a_versao_atual_de_.github/workflows/${WORKFLOW}_antes_do_dispatch" >&2
  fi
  exit 1
fi

echo "release_readiness_dispatch=ok repo=$REPO workflow=$WORKFLOW"