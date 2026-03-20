# BetAML - Auditoria Consolidada e Auditoria das Ferramentas de PLD

Data: 2026-03-20
Escopo: consolidacao de auditorias historicas + avaliacao tecnica das ferramentas de PLD/FT
Status executivo: READY FOR STAGING / CONDITIONAL FOR PRODUCTION

## 1. Consolidacao documental

Este documento substitui os relatorios historicos de auditoria/readiness que ficaram sobrepostos ao longo da evolucao do projeto.

### Fontes consolidadas
- docs/audit-2026-03-17.md
- docs/implementation-report-2026-03-17.md
- docs/security-remediation-plan.md
- docs/final-readiness-2026-03-20.md

### Documentos mantidos como canonicos (operacionais e funcionais)
- docs/go-live-checklist.md
- docs/ops-guide.md
- docs/runbook-incidentes.md
- docs/security-secrets-management.md
- docs/branch-protection.md
- docs/slo-sli.md
- docs/analyst-guide.md
- docs/aml-scorecard.md
- docs/ml-trainer-implementation.md

## 2. Sumario de readiness

### 2.1 Fechamentos tecnicos concluídos
- Refresh token rotation com JTI e revogacao.
- Scoring hibrido (regras + ML + rede) com persistencia de composite score.
- Materializacao OLTP de eventos canonicos (financial_transactions, bets, device_events).
- Consistencia de decisao em report packages (payload + coluna decision).
- Agregacao SAR retrocompativel (decision coluna + payload).
- Hardening de RLS com FORCE RLS em tabelas tenant-scoped criticas.
- Imutabilidade de audit_logs com trigger de bloqueio de UPDATE/DELETE.
- Backfill diario e data quality alerting agendados via APScheduler.
- Request-ID no contexto de ingestao e headers Kafka em best-effort.
- Branch protection reforcado em main (checks strict, reviews e code owners).

### 2.2 Evidencias de validacao
- Unit tests focados: 33 passed.
- Seguranca multi-tenant: 10 passed, 27 skipped.
- Integracao stream processor e endpoints: testes passando em staging local.
- Validacao de banco: migration v18 aplicada, policies e trigger ativos.

### 2.3 Pendencias para producao (nao-codigo)
1. Secret manager externo ativo com rotacao e trilha de auditoria.
2. TLS/Ingress produtivo e hardening de transporte.
3. Load test sustentado com SLO aprovado.
4. Janela formal de rollout/rollback com aprovacao de Operacoes + Compliance.

## 3. Auditoria das ferramentas de PLD (AML/CTF)

Escala de maturidade:
- A: pronto para producao
- B: pronto para staging com pequenos ajustes operacionais
- C: funcional, mas com gap relevante para producao

### 3.1 Ingestao de dados
- Componentes: API ingest, conectores, Kafka raw/canonical, controles de erro e replay.
- Estado: B
- Pontos fortes:
  - Suporte a ingestao por evento, batch, arquivo e websocket.
  - DLQ/retries, deduplicacao, trilha de ingest_errors, replay operacional.
  - Suporte a correlacao com request_id.
- Gap residual:
  - Governance operacional de capacidade em pico (teste sustentado em producao).

### 3.2 Motor de regras (DSL + regras compostas)
- Componentes: parser DSL, regras simples/compostas, pesos e severidade.
- Estado: A
- Pontos fortes:
  - Linguagem expressiva, macros e regras compostas.
  - Execucao event-driven com evidencias e contexto de features.
  - Persistencia consistente de alertas e trilha analitica.
- Gap residual:
  - Recalibracao periodica de thresholds por dominio de negocio.

### 3.3 Feature store e engenharia de features
- Componentes: Redis online, snapshots offline, enriquecimento no stream.
- Estado: A
- Pontos fortes:
  - Modelo online/offline com consistencia operacional.
  - Backfill e metricas de populacao/qualidade.
- Gap residual:
  - Governanca de drift formal com ritos mensais (processo, nao codigo).

### 3.4 Camada de ML e anomalia
- Componentes: ML service, model registry, champion/challenger, feedback loop.
- Estado: B
- Pontos fortes:
  - Infra de score online, registry e trilha de inferencia.
  - Integracao com rules engine no composite score.
- Gap residual:
  - Politica formal de promocao/reversao de modelos para producao.

### 3.5 Gestao de alertas e casos
- Componentes: triagem, labeling, workflows de caso, SLA, atribuicao.
- Estado: A
- Pontos fortes:
  - Fluxo completo analista-operacao (alerta -> caso -> decisao).
  - Labels e feedback para melhoria de deteccao.
- Gap residual:
  - Nenhum gap tecnico bloqueador identificado.

### 3.6 Reportes regulatorios (COAF/SAR)
- Componentes: report package, exportacoes, agregacao mensal.
- Estado: A
- Pontos fortes:
  - Consistencia de decisao regulatoria corrigida.
  - Agregacao mensal robusta para historico legado e atual.
- Gap residual:
  - Validacao final de layout e processo junto ao time de compliance juridico.

### 3.7 Auditoria, trilha e isolamento multi-tenant
- Componentes: audit_logs, RLS/FORCE RLS, branch protection.
- Estado: A
- Pontos fortes:
  - Isolamento multi-tenant forte em banco.
  - Trilha de auditoria imutavel.
  - Governanca de merge reforcada em repositiorio.
- Gap residual:
  - Nenhum gap tecnico bloqueador identificado.

### 3.8 Seguranca de segredos e transporte
- Componentes: config guards, secrets docs, politica de deploy.
- Estado: C
- Pontos fortes:
  - Guards contra defaults em ambientes nao-dev.
  - Guia de migracao para secret manager pronto.
- Gap residual (bloqueador):
  - Integracao efetiva com secret manager externo + TLS produtivo.

## 4. Parecer final de auditoria PLD

### 4.1 Conclusao tecnica
As ferramentas nucleares de PLD (ingestao, deteccao, triagem, casos, reporting e trilha) estao implementadas e operacionais, com maturidade A/B na maior parte dos dominios.

### 4.2 Conclusao de go-live
- Staging: aprovado.
- Producao: aprovacao condicional, dependente de concluir os 4 itens operacionais da secao 2.3.

## 5. Plano final objetivo (D-3, D-1, D0, D+1)

### D-3
- Ativar secret manager e validar rotacao inicial.
- Validar certificados e roteamento TLS fim-a-fim.

### D-1
- Executar load test com perfil realista e comparar contra SLO/SLI.
- Ensaiar rollback tecnico e operacional (runbook).

### D0
- Publicacao controlada por janela.
- Monitoracao reforcada (erros, latencia, backlog, ingest_errors, alert volume).

### D+1
- Revisao de estabilidade e relatorio de aceite.
- Registro formal de readiness assinado por Engenharia, Operacoes e Compliance.
