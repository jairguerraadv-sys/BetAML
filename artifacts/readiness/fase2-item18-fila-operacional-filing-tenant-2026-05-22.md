# Fase 2 - Item 18: Fila operacional de filing em nível de tenant

Data: 2026-05-22

## Escopo

- Expor visão única de pendências de filing para o tenant.
- Priorizar execução operacional por risco de prazo regulatório.

## Mudanças

- Novo endpoint `GET /report-packages/filing-queue` em `services/api/routers/cases.py`.
- A fila retorna itens com:
  - `deadline_state` (`BREACH`, `WARNING`, `OK`),
  - `requires_submission`,
  - `protocol_registered`,
  - dias desde criação/submissão,
  - `warnings[]`.
- Ordenação por criticidade e idade do pacote.
- Modo padrão deduplica por caso (última versão); `include_all_versions=true` habilita auditoria completa.
- Auditoria adicionada com ação `VIEW_REPORT_FILING_QUEUE`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `29 passed`.

## Cenários cobertos

- Priorização correta da fila (`BREACH` antes de `WARNING` e `OK`).
- Deduplicação por caso preservando a versão mais recente.

## Resultado

- Operações ganhou endpoint direto para triagem e priorização diária de filing sem inspeção manual caso a caso.