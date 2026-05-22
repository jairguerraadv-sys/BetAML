# Fase 2 - Item 14: Report package com cadeia de custódia verificável

Data: 2026-05-22

## Escopo

- Tornar a cadeia de custódia do report package consultável por contrato de API.
- Validar integridade do hash do payload de forma determinística e auditável.

## Mudanças

- Novo endpoint `GET /cases/{case_id}/report-packages/{rp_id}/chain-of-custody` em `services/api/routers/cases.py`.
- O endpoint retorna:
  - hash armazenado (`report_payload_sha256`),
  - hash recalculado do payload sem `chain_of_custody`,
  - flag `integrity_ok`,
  - metadados de custódia (PDF/XML/protocolo COAF/tempos).
- Auditoria operacional adicionada com ação `VIEW_REPORT_CUSTODY`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `22 passed`.

## Evidência operacional

- Integridade positiva: cenário com hash armazenado igual ao recalculado (`integrity_ok=true`).
- Integridade negativa: cenário com hash divergente (`integrity_ok=false`).

## Resultado

- A cadeia de custódia deixou de ser apenas metadado persistido e passou a ter verificação explícita por API para suporte a auditoria e filing regulatório.