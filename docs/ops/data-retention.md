# Retencao de Dados

- Audit logs: manter conforme obrigacao regulatoria e contrato do operador.
- Report packages: manter payload, PDF/XML, protocolo e cadeia de custodia pelo periodo regulatorio aplicavel.
- MinIO bronze: reter bruto pelo tempo necessario para replay e auditoria.
- DLQ: reter ate triagem e reprocessamento, com TTL minimo operacional.
- Redis: cache transiente; nao e sistema de registro.
- ClickHouse: reter historico analitico conforme custo e necessidade de investigacao.

Qualquer apagamento LGPD deve preservar evidencias regulatoriamente necessarias e registrar audit log.
