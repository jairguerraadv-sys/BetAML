# Fase 2 - Item 15: Reconciliação E2E evento -> alerta -> caso -> report

Data: 2026-05-22

## Escopo

- Expor um contrato único para verificar reconciliação ponta-a-ponta.
- Evidenciar gaps de encadeamento com diagnóstico direto por estágio.

## Mudanças

- Novo endpoint `GET /cases/{case_id}/reconciliation` em `services/api/routers/cases.py`.
- O endpoint valida três estágios:
  - `event_to_alert`
  - `alert_to_case`
  - `case_to_report_package`
- Resposta inclui:
  - `all_stages_ok`
  - `gaps[]`
  - detalhes por estágio (`source_event_ids`, alertas vinculados, report package reconciliado).
- Auditoria operacional adicionada com ação `VIEW_CASE_RECONCILIATION`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `24 passed`.

## Cobertura nova

- Cenário íntegro: todos estágios reconciliados (`all_stages_ok=true`).
- Cenário com falhas: ausência de evento de origem e de report package (`gaps` preenchido).

## Resultado

- A reconciliação evento->alerta->caso->reporte agora é verificável por API com sinalização objetiva de completude e lacunas.