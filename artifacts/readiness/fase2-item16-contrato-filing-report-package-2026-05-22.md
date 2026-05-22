# Fase 2 - Item 16: Contrato operacional de filing do report package

Data: 2026-05-22

## Escopo

- Tornar explícito o contrato operacional de filing (submissão regulatória) por API.
- Remover ambiguidade sobre modo manual atual, maker-checker e campos obrigatórios de custódia.

## Mudanças

- Novo endpoint `GET /cases/{case_id}/report-filing-contract` em `services/api/routers/cases.py`.
- Retorno inclui:
  - `channel`/`mode` atuais (`MANUAL_PORTAL` / `manual`),
  - endpoints de submissão e registro de protocolo,
  - decisão obrigatória (`FILE_SAR`),
  - requisitos de maker-checker,
  - campos mínimos de cadeia de custódia esperados,
  - sinalização de indisponibilidade de submissão automática (`api_submission_available=false`).
- Auditoria adicionada com ação `VIEW_REPORT_FILING_CONTRACT`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `25 passed`.

## Resultado

- O processo de filing deixou de ser implícito no código de submissão e passou a ter contrato operacional explícito e auditável para uso em runbook/compliance.