# Fase 2 - Item 20: Hotlist acionável de filing (tenant)

Data: 2026-05-22

## Escopo

- Fechar o gap entre KPI agregado e execução operacional diária.
- Expor uma lista priorizada apenas com pendências acionáveis de filing.

## Mudanças

- Novo endpoint `GET /report-packages/filing-hotlist` em `services/api/routers/cases.py`.
- Retorno inclui somente itens com ação pendente:
  - `SUBMIT_REPORT` (report ainda não submetido)
  - `REGISTER_PROTOCOL` (report submetido sem protocolo)
- Priorização operacional aplicada:
  - `BREACH` -> `WARNING` -> pendência de protocolo.
- Auditoria adicionada com ação `VIEW_REPORT_FILING_HOTLIST`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `33 passed`.

## Cenários cobertos

- Hotlist retorna apenas casos acionáveis e ordenados por prioridade de risco.
- Deduplicação por caso preserva a versão mais recente quando `include_all_versions=false`.

## Resultado

- Operação ganhou endpoint único para execução imediata de pendências regulatórias, reduzindo tempo de triagem entre overview e atuação.