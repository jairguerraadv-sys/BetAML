#!/usr/bin/env bash
set -euo pipefail

REPO="jairguerraadv-sys/BetAML"
EVIDENCE_OUT=""

usage() {
  cat <<'EOF'
Uso:
  scripts/check_github_actions_readiness.sh [opcoes]

Opcoes:
  --repo OWNER/REPO         Repositorio GitHub (default: jairguerraadv-sys/BetAML)
  --evidence-out PATH       Salva a saida completa em arquivo
  -h, --help                Exibe esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --evidence-out) EVIDENCE_OUT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Argumento invalido: $1" >&2; usage >&2; exit 2 ;;
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

gh_safe() {
  env -u GITHUB_TOKEN gh "$@"
}

check_workflow() {
  local workflow="$1"
  if gh_safe workflow view "$workflow" --repo "$REPO" >/dev/null 2>&1; then
    pass "workflow presente: $workflow"
  else
    fail "workflow ausente ou inacessivel: $workflow"
  fi
}

check_variable() {
  local name="$1"
  if gh_safe variable list --repo "$REPO" | awk '{print $1}' | grep -Fxq "$name"; then
    pass "variable presente: $name"
  else
    fail "variable ausente: $name"
  fi
}

check_secret() {
  local name="$1"
  if gh_safe secret list --repo "$REPO" | awk '{print $1}' | grep -Fxq "$name"; then
    pass "secret presente: $name"
  else
    fail "secret ausente: $name"
  fi
}

section "Contexto"
echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "repo=$REPO"

section "Workflows obrigatorios"
check_workflow capacity-smoke.yml
check_workflow release-readiness.yml

section "Repo variables obrigatorias"
check_variable E2E_USERNAME

section "Repo secrets obrigatorios"
check_secret E2E_PASSWORD

section "Resumo"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "github_actions_readiness=PASS"
  exit 0
fi

echo "github_actions_readiness=FAIL failures=$FAILURES"
echo "hint_set_variable=env -u GITHUB_TOKEN gh variable set E2E_USERNAME --repo $REPO --body '<usuario>'"
echo "hint_set_secret=printf '%s' '<senha>' | env -u GITHUB_TOKEN gh secret set E2E_PASSWORD --repo $REPO"
exit 1