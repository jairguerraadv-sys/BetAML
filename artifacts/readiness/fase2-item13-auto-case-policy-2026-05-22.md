# Fase 2 - Item 13: Política de auto-case explícita

Data: 2026-05-22

## Escopo

- Expor um contrato operacional único para auto-case.
- Tornar o `rules_engine` o materializador oficial.
- Mostrar o status do `alert_processor` legado sem duplicar a lógica de materialização.

## Mudanças

- Novo endpoint `GET /admin/auto-case-policy` em [services/api/routers/admin.py](../../services/api/routers/admin.py).
- Novo contrato `AutoCasePolicyOut` com thresholds, gatilhos por severidade e flags de legado.
- Documentação atualizada em [services/api/README.md](../../services/api/README.md) e [docs/ops-guide.md](../../docs/ops-guide.md).

## Validação

- `pytest -q tests/unit/test_module8.py` passou com 17 testes verdes.

## Conclusão

- A política de auto-case agora tem contrato explícito e um único materializador oficial para operação normal.