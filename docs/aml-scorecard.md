# BetAML - Scorecard AML Operacional

## Endpoint

- Rota: `GET /admin/kpis/aml`
- Perfis: `ADMIN`, `AUDITOR`, `AML_ANALYST`
- Janela: ultimos 30 dias

## KPIs retornados

- `alerts_open`: alertas em aberto no tenant.
- `alerts_in_review`: alertas em revisao.
- `alerts_labeled_30d`: alertas etiquetados na janela.
- `true_positive_rate_30d_percent`: taxa de true positive sobre alertas etiquetados.
- `false_positive_rate_30d_percent`: taxa de false positive sobre alertas etiquetados.
- `cases_open`: casos em aberto ou em revisao.
- `cases_overdue`: casos com SLA vencido.
- `sla_breach_rate_open_cases_percent`: percentual de casos abertos em breach de SLA.
- `avg_case_resolution_hours_30d`: media de horas entre criacao e fechamento de casos.

## Exemplo

```bash
curl -s http://localhost:8000/admin/kpis/aml \
  -H "Authorization: Bearer <TOKEN>" | jq
```

## Uso recomendado

- Acompanhar diariamente a triagem AML.
- Medir degradacao de qualidade de modelo/regras via crescimento de false positive.
- Usar `cases_overdue` e `sla_breach_rate_open_cases_percent` para capacidade da operacao.
