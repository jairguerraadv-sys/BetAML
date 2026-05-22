# Fase 2 - Item 17: Status operacional de filing do report package

Data: 2026-05-22

## Escopo

- Expor status operacional de filing por caso com semáforo de prazo regulatório.
- Tornar explícita a pendência de submissão e de registro de protocolo COAF.

## Mudanças

- Novo endpoint `GET /cases/{case_id}/report-filing-status` em `services/api/routers/cases.py`.
- O endpoint retorna, para o pacote mais recente do caso:
  - `requires_submission`
  - `protocol_registered`
  - `deadline_state` (`OK`, `WARNING`, `BREACH`, `NO_REPORT`)
  - `warnings[]`
  - dias desde criação/submissão do pacote.
- Auditoria adicionada com ação `VIEW_REPORT_FILING_STATUS`.

## Testes

- `pytest -q tests/unit/test_cases_module5.py`
- Resultado: `27 passed`.

## Cenários cobertos

- `FILE_SAR` sem submissão há mais de 30 dias => `BREACH`.
- pacote `FILED` sem `coaf_protocol_number` => `OK` com warning de protocolo pendente.

## Resultado

- A operação agora consegue verificar rapidamente o risco de prazo e o fechamento de protocolo no fluxo de filing regulatório por caso.