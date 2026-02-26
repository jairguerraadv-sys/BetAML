# BetAML: Resumo Executivo

## 1. O Produto

**BetAML** é uma plataforma **SaaS multi-tenant "nível banco"** de detecção de anomalias e conformidade (PLD/FT) exclusivamente projetada para operadores de apostas de quota fixa no Brasil.

### Missão
Fornecer detecção de fraude, lavagem de dinheiro e financiamento do terrorismo através de:
- Análise em tempo real
- Regras parametrizáveis por operador
- Machine Learning com explicabilidade
- Conformidade com LGPD e legislação brasileira

### Diferencial
- ✅ **Não engessado**: regras customizáveis por tenant (DSL próprio)
- ✅ **Big Data ready**: processa 10k-100k eventos/segundo
- ✅ **Enterprise-ready**: multi-tenant, RBAC, auditoria completa
- ✅ **Explicável**: cada alerta inclui drivers e evidências
- ✅ **Escalável**: event-driven, stream processing, data lakehouse

---

## 2. Arquitetura (Camadas)

### Camada 1: Entrada Universal (API Layer)
- Recebe dados de múltiplos backoffices (CSV, JSON, REST)
- Valida e aplica mapeamentos (transformações) parametrizáveis
- Publica eventos brutos em Kafka
- RBAC, autenticação JWT, isolamento de tenant

### Camada 2: Event Bus (Kafka)
- Central de eventos: 11 tópicos por domínio
- Retenção por tipo (raw: 30d, canonical: 365d, alerts: 90d)
- Escalável: particionado por tenant/player/entity

### Camada 3: Data Lakehouse (MinIO)
- **Bronze**: raw payloads como recebidos
- **Silver**: eventos canônicos normalizados
- **Gold**: features agregadas (24h/7d/30d)
- Particionado por tenant, data, tipo, source

### Camada 4: Feature Store
- **Online (Redis)**: cache de features (TTL 1h)
- **Offline (Parquet)**: histórico completo (365d)
- 15+ features: financeiras, comportamentais, baseline, correlações

### Camada 5: Stream Processing
- **Stream Processor**: computa features contínuamente
- **Rules Engine**: avalia DSL contra eventos + features
- **ML Service**: scoring em tempo real + treino diário

### Camada 6: Analytics (OLAP)
- **ClickHouse**: queries rápidas para dashboards e investigações
- Tabelas: alertas, eventos, agregados, case timeline
- TTL automático por retenção

### Camada 7: Workflow (OLTP)
- **PostgreSQL**: tenants, users, RBAC, rules, cases, audit
- Isolamento lógico por tenant
- Auditoria completa (AuditLog)

### Camada 8: Frontend (Next.js)
- Dashboards (KPIs, séries, top players)
- Alertas (grid filtrado, detalhes, evidências)
- Casos (timeline, notas, evidências, report generation)
- Regras (CRUD, editor DSL, simulação)
- Mappings (CRUD, teste)

---

## 3. Fluxos Principais (User Stories)

### US1: Operador carrega transações diárias
```
Backoffice → POST /ingest/file (CSV) 
  → API valida + cria IngestJob 
  → raw.transactions (Kafka) 
  → MinIO Bronze 
  → Normaliza → canonical.transactions 
  → Stream Processor 
  → features.player_daily 
  → Redis + MinIO Gold
  → Rules Engine avalia DSL 
  → scoring.alerts 
  → ClickHouse + PostgreSQL
  → Dashboard: operador vê alertas em tempo real
```

### US2: Analista investiga alerta
```
Frontend: Alertas → clica em alerta HIGH
  → exibe: dados do jogador, transações, regras disparadas, features
  → clica "Criar Caso"
  → abre caso, add notas, upload evidências
  → decide: SAR/SAT/CLOSE
  → gera ReportPackage (JSON)
  → exporta para compartilhar com órgão regulador
```

### US3: Admin configura regra nova
```
Admin → POST /rules
  → DSL: "features.zscore_current_deposit_vs_baseline > 3.0 AND features.deposit_sum_24h > 5000"
  → salva em Postgres
  → clica "Simular" → testa com histórico
  → ativa (status ACTIVE)
  → Rules Engine carrega em cache
  → próximos eventos já usam nova regra
```

### US4: ML treina modelo diário
```
Batch job (noite): 
  → lê features Gold (últimos 90 dias)
  → treina IsolationForest
  → salva em model_registry
  → publica novo modelo
  → Stream Processor começa a usar
  → anomalyScore gerado para cada transação
  → pode gerar alertas (severity AUTO)
```

---

## 4. Dados & Features

### Entrada (Canônico)
```
PLAYER: cpf, nome, birthDate, pepFlag, renda declarada, profissão
TRANSACTION: playerId, amount, type (DEPOSIT/WITHDRAWAL/CHARGEBACK), method, status, instrument
BET: playerId, stakeAmount, odds, potentialPayout, sport, channel, timestamp
DEVICE_EVENT: deviceId, playerId, ip, userAgent, geoCountry
```

### Features Computadas
```
Financeiras (24h/7d/30d):
- deposit_sum, deposit_count
- withdrawal_sum, withdrawal_count
- ratio_withdrawal_to_deposit

Comportamentais:
- bet_stake_sum, bet_count
- new_payment_instrument_flag
- new_device_flag

Baseline:
- baseline_avg_daily_deposit
- baseline_stddev_deposit
- zscore_current_vs_baseline

Correlações:
- shared_device_count (quantos CPFs no mesmo device)
- shared_bank_account_count (quantos players por holderDocument)
```

### Alertas Gerados
```
Tipo: RULE (de DSL) | ANOMALY (de ML) | COMPOSITE (ambos)
Severidade: LOW | MEDIUM | HIGH | CRITICAL
Status: NEW | ACK | CLOSED | ESCALATED
Evidence: features, thresholds, rule versions, anomaly drivers
```

---

## 5. Regras Padrão (12)

1. **Spike vs Baseline**: zscore > 3.0 (anomalia)
2. **Structuring**: 5+ depósitos pequenos em 24h
3. **Saque rápido**: saque logo após depósito
4. **Instrumento novo + valor alto**: novo cartão com transação grande
5. **PEP com desvio**: PEP com zscore > 2.0
6. **Conta compartilhada**: 3+ CPFs na mesma conta
7. **Device compartilhado**: 3+ dispositivos no mesmo IP
8. **Alta razão saque/depósito**: > 90% de retorno
9. **Spike de apostas**: 3x aumento em 7d
10. **Múltiplos chargebacks**: 3+ em 30d
11. **Falhas + sucesso**: tentativas falhas seguidas de transação grande
12. **Round-tripping**: depósito → aposta mínima → saque

---

## 6. Integração: Como Funciona

### Fase 1: Setup (T+0)
```
✓ Tenant configurado no Postgres
✓ 3 usuários criados (ADMIN, ANALYST, AUDITOR)
✓ MappingConfig criado (BackofficeAlpha → Canônico)
✓ 5 regras ativadas (padrão)
✓ ML model treinado (synthetic data)
✓ Usuário acessa frontend
```

### Fase 2: Ingestão (T+1)
```
✓ Operador upload CSV diário
✓ API aplica mapping
✓ Eventos em Kafka
✓ Features computadas em 30 segundos
✓ Alertas gerados em 60 segundos
✓ Dashboard atualizado
```

### Fase 3: Análise (T+2 a T+30)
```
✓ Analista triage alertas
✓ Investiga casos
✓ Coleta evidências
✓ Gera ReportPackage
✓ Submete (integração futura com sistemas externos)
```

---

## 7. Compliance & Segurança

### LGPD
- ✅ Mascaramento de PII em frontend (CPF: XXX.XXX.XX9)
- ✅ Criptografia em repouso (pgcrypto) para campos sensíveis
- ✅ AuditLog de todos acessos
- ✅ Data retention policies por tenant (configurável)
- ✅ Direito ao esquecimento (soft delete com backup)

### Auditoria
- ✅ Todas mudanças em RuleDefinition logadas
- ✅ Todas ações em Alerts/Cases logadas
- ✅ ReportPackage com timestamp + quem gerou + hash
- ✅ Imutabilidade de alertas (append-only)

### RBAC
- ADMIN: criar users, editar regras, acessar auditoria
- AML_ANALYST: triage alertas, criar casos, adicionar notas
- AUDITOR: somente leitura, acesso irrestrito a AuditLog

### Multi-tenancy
- ✅ tenant_id SEMPRE do JWT (nunca cliente)
- ✅ Row-level security (opcional RLS no Postgres)
- ✅ Isolamento lógico em todos dados

---

## 8. Performance & Escalabilidade

### Throughput (production, single region)
- **Eventos**: 10k-100k/seg (Kafka)
- **Alertas**: 1k-10k/seg (Rules Engine)
- **Queries**: 100 req/seg (ClickHouse)
- **Latência**: <30s ingestão → alerta (P95)

### Escaling
- **Horizontal**: adicione brokers Kafka, Stream Processor workers, Rules Engine workers
- **Vertical**: aumentar CPU/RAM de PostgreSQL, ClickHouse, Redis

### Storage
- Bronze: 30d (caro, com compressão)
- Silver: 365d (parquet, comprimido)
- Alerts: 90d (ClickHouse)
- Audit: 2555d (7 anos, compliance)

---

## 9. MVP Entregáveis

### Código
- ✅ 6 serviços: API, Stream Processor, Rules Engine, ML Service, Frontend, Infra
- ✅ Docker Compose para ambiente local
- ✅ Testes unitários (DSL parser, transforms)
- ✅ Testes integração (ingestão → alerta)

### Documentação
- ✅ README completo (quick start, troubleshooting)
- ✅ ARCHITECTURE.md (diagramas, fluxos)
- ✅ DSL_GUIDE.md (12 regras, exemplos, syntax)
- ✅ DEPLOYMENT.md (quick start local + prod checklist)
- ✅ OpenAPI/Swagger gerado automaticamente
- ✅ Diagrama de entity-relationship

### Dados
- ✅ 2 tenants de teste
- ✅ 6 usuários (3 por tenant)
- ✅ 50 players por tenant
- ✅ 200 transações de teste
- ✅ Cenários suspeitos pré-configurados
- ✅ 5 regras padrão (ACTIVE)

### Infra
- ✅ Docker Compose (local dev)
- ✅ PostgreSQL com schema
- ✅ Redis configurado
- ✅ Kafka/Redpanda 7 tópicos
- ✅ MinIO com buckets
- ✅ ClickHouse com tabelas
- ✅ Health checks

---

## 10. Roadmap Pós-MVP

### v1.1 (Semana 1-2)
- [ ] Integração com sistemas externos (API callbacks)
- [ ] Dashboard avançado (Grafana)
- [ ] Alertas por email/Slack
- [ ] Exportação de relatórios (PDF)

### v1.2 (Semana 3-4)
- [ ] Clustering de casos (correlação temporal)
- [ ] Network analysis (relacionamentos entre players)
- [ ] Regras com machine learning (auto-generation)
- [ ] Versioning completo de regras + A/B testing

### v2.0 (Mês 2)
- [ ] Integração LGPD (DPA, consentimento)
- [ ] Multi-region deployment (sync entre regiões)
- [ ] Advanced analytics (cohort analysis, churn prediction)
- [ ] Mobile app (iOS/Android)

---

## 11. Custos de Operação (Estimativa AWS)

### Dev/Staging (t3.medium)
- EC2: ~$70/mês
- RDS Postgres: ~$100/mês
- ElastiCache Redis: ~$50/mês
- MSK Kafka: ~$300/mês
- S3: ~$50/mês
- **Total: ~$570/mês**

### Production (m5.xlarge, multi-az)
- EC2 (4 nodes): ~$2000/mês
- RDS Postgres (multi-az): ~$500/mês
- ElastiCache Redis (cluster): ~$300/mês
- MSK Kafka (3 brokers): ~$900/mês
- S3 (compliance storage): ~$200/mês
- ClickHouse (managed): ~$400/mês
- ALB, NAT, CloudWatch: ~$300/mês
- **Total: ~$4600/mês** (excl. suporte)

---

## 12. Sucesso Metrics

### SLA Target
- ✅ Uptime: 99.9% (< 43 minutos/mês inatividade)
- ✅ Latência P95: < 30s (ingestão → alerta)
- ✅ Alert accuracy: > 85% (TP / (TP + FP))

### KPIs
- Alertas/dia por tenant (deve crescer com dados)
- True positive rate (investigação manual)
- Tempo para investigar caso (média)
- Casos SAR gerados/mês
- Conformidade: 100% auditoria

---

## 13. Próximos Passos

1. **Hoje**: Revisar arquitetura, DSL, dados
2. **Semana 1**: Teste local completo, feedback
3. **Semana 2**: Refinamentos, testes penetração
4. **Semana 3-4**: Deploy staging, treinamento
5. **Mês 2**: Go-live production

---

**BetAML v1.0.0-MVP**
**Data:** 26/02/2024
**Equipe:** Arquitetura de Software + Full-Stack Engineering
