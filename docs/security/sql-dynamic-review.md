# SQL Dynamic Review (PR-10)

## Objetivo

Implementar o PR-10 `hardening/bandit-b608-review`: revisar SQL dinamico, remover skip global de B608 e documentar os casos aceitos.

## O que e B608

B608 do Bandit sinaliza possivel SQL injection quando queries sao construidas com strings dinamicas (f-string, `.format()`, concatenacao, etc.).

## Politica do projeto para SQL

- Permitido:
  - bind params (`:param`, `%(param)s`, `%s` com driver DB-API)
  - SQL estatico
  - identificadores apenas por allowlist fechada
- Proibido:
  - f-string com input externo em SQL
  - `.format()` com input externo em SQL
  - `%` formatting com input externo em SQL
  - concatenacao de SQL com input vindo de request

## Inventario revisado

| Arquivo | Linha/Função | Tipo | Risco | Decisão | Justificativa | Teste |
| ------- | ------------ | ---- | ----- | ------- | ------------- | ----- |
| services/api/routers/players.py | get_player_feature_history | NEEDS_FIX -> SAFE_STATIC_SQL | Medio | Corrigido | Query mudou para SQL estatico com bind params `%(tid)s/%(pid)s/%(days)s`; sem f-string | tests/security/test_sql_injection_guards.py |
| services/api/routers/players.py | erase_player_data (contagem por tabela) | NEEDS_FIX -> SAFE_IDENTIFIER_ALLOWLIST | Medio | Corrigido | Query dinamica removida; agora usa mapa estatico `_ERASURE_COUNT_SQL_BY_TABLE` + validacao por allowlist | tests/security/test_sql_injection_guards.py |
| services/api/main.py | set_config app.current_tenant | SAFE_BIND_PARAMS | Baixo | Mantido | SQL estatico com parametro bind `:tid` | N/A |
| services/stream_processor/main.py | set_config app.current_tenant | SAFE_BIND_PARAMS | Baixo | Mantido | SQL estatico com parametro bind `:tid` | N/A |
| scripts/clickhouse_backfill.py | backfill_transactions/backfill_alerts | SAFE_IDENTIFIER_ALLOWLIST | Baixo | Mantido | `.format()` usa apenas token interno fixo (`tenant_filter`) sem input de usuario; filtros usam bind params | Revisao manual |
| scripts/backfill_cpf_hmac.py | selects/updates batch | SAFE_BIND_PARAMS | Baixo | Mantido | `%s` via psycopg2 parametrizado (DB-API), sem interpolacao manual | Revisao manual |
| services/api/alembic/versions/*.py | migrations DDL | MIGRATION_STATIC_SQL | Baixo | Excluido do Bandit CI | Alembic excluido no workflow; sem input de usuario em runtime da API | Revisao manual |
| tests/security/test_rls_coverage.py | query com f-string em teste | TEST_ONLY | Baixo | Mantido | Contexto de teste para introspecao de tabela, fora caminho de producao | tests/security/test_rls_coverage.py |

## nosec B608 aceitos

Nenhum `# nosec B608` permanece no codigo de runtime apos este PR.

## Configuracao Bandit / CI

- Removido `B608` do skip global em `pyproject.toml`.
- CI roda Bandit com B608 ativo e skip explicito apenas para:
  - `B105`, `B107`, `B110`, `B112`, `B311`, `B405`
- CI exclui `services/api/alembic` e `services/api/seeds.py` do scan de runtime.

## Como rodar localmente

```bash
cd services/api
/Users/jairarantes/Desktop/BetAML/.venv/bin/bandit -r . \
  --skip B105,B107,B110,B112,B311,B405 \
  --exclude ./alembic,./seeds.py \
  -ll
```

```bash
python scripts/find_dynamic_sql.py
```

## Checklist para novos PRs com SQL

1. Verifique se toda entrada externa entra somente em bind params.
2. Se houver identificador dinamico (tabela/coluna/order by), exija allowlist fechada.
3. Rode `python scripts/find_dynamic_sql.py`.
4. Rode Bandit com B608 ativo.
5. Atualize este documento se surgir nova excecao aprovada.
