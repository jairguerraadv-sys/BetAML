# BetAML - Branch Protection

## Objetivo

Bloquear merge em `main` sem qualidade minima de CI, revisao e historico linear.

## Script de aplicacao

Use o script abaixo com permissao admin no repositorio:

```bash
chmod +x scripts/apply_branch_protection.sh
GITHUB_OWNER=jairguerraadv-sys GITHUB_REPO=BetAML ./scripts/apply_branch_protection.sh
```

## Regras aplicadas

- Status checks obrigatorios (backend, lint, frontend, seguranca, docker, migracao, alembic).
- `strict=true` (branch precisa estar atualizada com `main`).
- 2 aprovacoes obrigatorias em PR.
- Requer resolucao de conversas.
- Bloqueia force-push e delete de branch protegida.
- Exige historico linear.

## Observacoes

- O workflow de readiness continua manual para gate final de release.
- Branch protection governa merge de codigo; readiness governa publicacao.
