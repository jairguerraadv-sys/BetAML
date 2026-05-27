# Checklist de Producao

- [ ] `ENVIRONMENT=production`.
- [ ] Nenhum segredo em default.
- [ ] `EXTERNAL_VALIDATION_PROVIDER` real configurado.
- [ ] RLS validado por `pytest tests/security`.
- [ ] RBAC validado e documentado.
- [ ] Backups Postgres/MinIO/ClickHouse testados.
- [ ] Restore drill executado.
- [ ] Alertas Prometheus/Grafana ativos.
- [ ] DLQ e replay testados.
- [ ] Report package com cadeia de custodia validada.
- [ ] Runbooks publicados para suporte.
- [ ] SSO/MFA e break-glass definidos.
- [ ] Plano de resposta a PII aprovado pelo DPO/juridico.
- [ ] Linguagem comercial revisada para nao prometer compliance automatico.
