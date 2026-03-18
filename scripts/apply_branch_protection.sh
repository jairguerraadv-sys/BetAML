#!/usr/bin/env bash
set -euo pipefail

# Aplica branch protection na branch principal usando GitHub CLI.
# Requisitos:
#   - gh autenticado com token que tenha permissoes de admin no repo
#   - repositorio remoto acessivel
# Uso:
#   GITHUB_OWNER=jairguerraadv-sys GITHUB_REPO=BetAML ./scripts/apply_branch_protection.sh

OWNER="${GITHUB_OWNER:-jairguerraadv-sys}"
REPO="${GITHUB_REPO:-BetAML}"
BRANCH="${GITHUB_BRANCH:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI nao encontrado no PATH" >&2
  exit 1
fi

# Contextos de checks que devem ser obrigatorios para merge.
# Ajuste conforme evolucao dos workflows.
REQUIRED_CHECKS_JSON='[
  {"context":"Backend Tests (pytest)"},
  {"context":"Backend Lint (ruff)"},
  {"context":"Frontend TypeScript Check"},
  {"context":"Security Scan (bandit)"},
  {"context":"Docker Build Check"},
  {"context":"External Validation Endpoints (Docker)"},
  {"context":"Migration Idempotency Check"},
  {"context":"Alembic Baseline Check"}
]'

payload=$(cat <<JSON
{
  "required_status_checks": {
    "strict": true,
    "checks": $(echo "${REQUIRED_CHECKS_JSON}")
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 2,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": true
}
JSON
)

echo "Aplicando branch protection em ${OWNER}/${REPO}:${BRANCH}..."

gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${OWNER}/${REPO}/branches/${BRANCH}/protection" \
  --input <(printf '%s' "${payload}") >/dev/null

echo "Branch protection aplicada com sucesso."
