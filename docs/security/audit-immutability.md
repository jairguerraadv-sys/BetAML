# Audit Logs Immutability (PR-08)

## Objetivo

Garantir trilha de auditoria append-only em nivel de banco para `audit_logs`.
Nenhum papel de aplicacao (incluindo `SUPER_ADMIN`) pode alterar ou apagar eventos
ja persistidos.

## Implementacao

### Migration Alembic

- Arquivo: `services/api/alembic/versions/20260526_000003_audit_logs_immutability.py`
- Revisao: `20260526_000003`
- Down revision: `20260526_000002`

A migration:

1. Cria/atualiza a funcao `prevent_audit_logs_mutation()`.
2. Remove nomes de trigger legados para manter idempotencia.
3. Cria trigger canonico `trg_prevent_audit_logs_mutation` com:
   - `BEFORE UPDATE OR DELETE ON audit_logs`
   - `FOR EACH ROW EXECUTE FUNCTION prevent_audit_logs_mutation()`

## Garantias de seguranca

- `INSERT` continua permitido sob RLS e tenant context valido.
- `UPDATE` bloqueado no banco por trigger (erro de imutabilidade).
- `DELETE` bloqueado no banco por trigger (erro de imutabilidade).
- Sem bypass por papel de aplicacao: claim `SUPER_ADMIN` nao altera enforcement SQL.
- Isolamento tenant permanece em vigor via policies RLS (`USING false` para
  UPDATE/DELETE + policies de SELECT/INSERT por tenant).

## Testes de regressao

Arquivo: `tests/security/test_audit_immutability.py`

Cobertura:

- Trigger existe em catalogo (`pg_trigger`/`pg_proc`).
- `INSERT` permitido em `audit_logs` com tenant context.
- `UPDATE` bloqueado por trigger.
- `DELETE` bloqueado por trigger.
- Linha criada por usuario com role `SUPER_ADMIN` tambem nao pode ser mutada.
- Tenant B nao le log do Tenant A.
- Sem tenant context, `INSERT` falha por RLS.

## Validacao local

```bash
cd services/api
alembic upgrade head
alembic current

cd ../..
TEST_STACK_UP=1 pytest tests/security/test_audit_immutability.py -q
TEST_STACK_UP=1 pytest tests/security -q
python scripts/check_secret_hygiene.py
```

## Observacoes

- A trilha de auditoria fica tecnicamente imutavel no banco, sem dependencia de
  bloqueios em camada de API/RBAC.
- Esta mudanca complementa os bloqueios anteriores de policy RLS (`USING false`
  em UPDATE/DELETE para `audit_logs`) com defesa adicional por trigger.
