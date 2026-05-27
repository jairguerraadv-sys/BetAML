# Incident Response

## Vazamento de segredo

1. Revogar ou rotacionar o segredo.
2. Identificar sistemas que consumiam o segredo.
3. Buscar uso indevido em audit logs, access logs e metricas.
4. Abrir postmortem com timeline, impacto e acao preventiva.

## Suspeita de vazamento de PII

1. Congelar retencao dos logs relevantes.
2. Identificar tenants, tabelas e objetos afetados.
3. Validar se houve cross-tenant, export indevido ou log com PII.
4. Acionar DPO/juridico do operador.

## Falha de ingestao

1. Checar `/health`, lag Kafka e taxa de DLQ.
2. Pausar conector se houver duplicidade ou payload malicioso.
3. Corrigir MappingConfig ou payload.
4. Reprocessar com lineage preservado.

## Backlog Kafka/DLQ

1. Medir consumer lag e DLQ rate.
2. Escalar consumers se CPU/memoria permitirem.
3. Separar erro sistemico de dados invalidos.
4. Reprocessar por janela e tenant.

## Rules/ML indisponivel

1. API continua recebendo ingestao se filas estiverem saudaveis.
2. Alertar operadores sobre atraso de scoring.
3. Reprocessar eventos afetados apos recuperacao.
