#!/usr/bin/env bash
set -euo pipefail

REPO="jairguerraadv-sys/BetAML"
REF="main"
EVIDENCE_OUT=""
WORKFLOWS=()

usage() {
  cat <<'EOF'
Uso:
  scripts/check_github_workflow_sync.sh [opcoes] [workflow.yml ...]

Sem argumentos posicionais, valida por padrao:
  - capacity-smoke.yml
  - release-readiness.yml

Opcoes:
  --repo OWNER/REPO         Repositorio GitHub (default: jairguerraadv-sys/BetAML)
  --ref REF                 Ref remota a comparar (default: main)
  --evidence-out PATH       Salva a saida completa em arquivo
  -h, --help                Exibe esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO="$2"
      shift 2
      ;;
    --ref)
      REF="$2"
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
      WORKFLOWS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#WORKFLOWS[@]} -eq 0 ]]; then
  WORKFLOWS=(capacity-smoke.yml release-readiness.yml)
fi

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

section "Contexto"
echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "repo=$REPO"
echo "ref=$REF"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

for workflow in "${WORKFLOWS[@]}"; do
  section "Workflow $workflow"
  local_path="/workspaces/BetAML/.github/workflows/$workflow"
  remote_path=".github/workflows/$workflow"
  remote_file="$work_dir/$workflow"

  if [[ ! -f "$local_path" ]]; then
    fail "arquivo local ausente: $local_path"
    continue
  fi

  if ! gh_safe api "repos/$REPO/contents/$remote_path?ref=$REF" --jq '.content' | tr -d '\n' | base64 -d > "$remote_file" 2>/dev/null; then
    fail "nao foi possivel ler workflow remoto: $remote_path@$REF"
    continue
  fi

  local_sha="$(sha256sum "$local_path" | awk '{print $1}')"
  remote_sha="$(sha256sum "$remote_file" | awk '{print $1}')"
  echo "local_sha256=$local_sha"
  echo "remote_sha256=$remote_sha"

  if cmp -s "$local_path" "$remote_file"; then
    pass "workflow sincronizado com o remoto"
  else
    fail "workflow remoto desatualizado em relacao ao workspace"
    echo "hint=publique_.github/workflows/${workflow}_no_ref_${REF}"
  fi
done

section "Resumo"
if [[ "$FAILURES" -eq 0 ]]; then
  echo "github_workflow_sync=PASS"
  exit 0
fi

echo "github_workflow_sync=FAIL failures=$FAILURES"
exit 1