# Fase 2 - Item 19: Overview agregado de filing (KPI de tenant)

Data: 2026-05-22

## Escopo

- Expor uma visão executiva agregada do filing para o tenant.
- Complementar a fila operacional com métricas consolidadas para checkpoint diário.

## Mudanças

- Novo endpoint `GET /report-packages/filing-overview` em `services/api/routers/cases.py`.
- Métricas retornadas:
  - `total_cases_with_reports`
  - `requires_submission_count`
  - `missing_protocol_count`
  - `deadline_state_counts`
  - `oldest_pending_submission_days`
  - `top_breach_case_ids`
  - `truncated`
- Auditoria adicionada com ação `VIEW_REPORT_FILING_OVERVIEW`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `31 passed`.

## Cenários cobertos

- Agregação correta de contagens (pendência de submissão, protocolo e breach).
- Sinalização `truncated=true` quando o limite de varredura é alcançado.

## Resultado

- Operação passou a ter KPI executivo de filing em endpoint dedicado, reduzindo leitura manual de fila e acelerando triagem de risco regulatório.