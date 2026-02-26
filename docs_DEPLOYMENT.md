# BetAML Deployment & Getting Started Guide

## 🚀 Quick Start (5 minutos)

### Pré-requisitos
```bash
# macOS
brew install docker docker-compose git

# Linux (Ubuntu/Debian)
sudo apt-get install docker.io docker-compose git

# Windows
# Download Docker Desktop: https://www.docker.com/products/docker-desktop
```

### 1. Clonar Repositório
```bash
git clone https://github.com/your-org/betaml.git
cd betaml
```

### 2. Iniciar Infraestrutura
```bash
# Subir todos os serviços
docker-compose -f infra/docker-compose.yml up -d

# Verificar status
docker-compose -f infra/docker-compose.yml ps

# Aguardar ~30 segundos para tudo ficar pronto
sleep 30
```

### 3. Verificar Saúde
```bash
# API health
curl http://localhost:8000/health

# Postgres
docker-compose -f infra/docker-compose.yml exec postgres psql -U betaml -d betaml_dev -c "SELECT count(*) FROM tenants;"

# Redis
docker-compose -f infra/docker-compose.yml exec redis redis-cli ping

# Kafka (via kafka-ui)
open http://localhost:8080
```

### 4. Acessar Aplicação
- **Frontend**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **MinIO**: http://localhost:9001 (admin / minio123)
- **Kafka UI**: http://localhost:8080
- **ClickHouse**: http://localhost:8123 (opcional)

### 5. Login Padrão (DEV)
```
Usuário: admin_a
Senha: admin123
Tenant: OperadorA
```

---

## 📊 Seed Data

Ao iniciar, os scripts criam automaticamente:

### Tenants
- **OperadorA**: Teste
- **OperadorB**: Teste

### Usuários (3 por tenant)
- `admin_a` / `admin123` (ADMIN)
- `analyst_a` / `analyst123` (AML_ANALYST)
- `auditor_a` / `auditor123` (AUDITOR)

### Dados Sintéticos
- 50 players/tenant
- 200 transações de teste
- Cenários suspeitos:
  - Structuring (múltiplos depósitos pequenos)
  - Spike (depósito alto vs baseline)
  - Dispositivo compartilhado
  - Saque rápido pós-depósito

### Regras Padrão
- 5 regras pré-configuradas (ACTIVE)
- DSL já validada
- Severidade configurada

---

## 🧪 Teste Manual: Ingestão → Alertas

### 1. Criar arquivo de teste
```bash
cat > test_transactions.csv << 'EOF'
externalTransactionId,playerId,type,amount,method,status,occurredAt
TXN-001,player-001,DEPOSIT,10000,PIX,SETTLED,2024-02-26T10:00:00Z
TXN-002,player-001,DEPOSIT,10000,PIX,SETTLED,2024-02-26T10:05:00Z
TXN-003,player-001,WITHDRAWAL,18000,PIX,SETTLED,2024-02-26T10:30:00Z
EOF
```

### 2. Upload via API
```bash
TOKEN=$(curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin_a","password":"admin123"}' \
  | jq -r .access_token)

curl -X POST http://localhost:8000/ingest/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test_transactions.csv" \
  -F "sourceSystem=BackofficeAlpha" \
  -F "mappingConfigId=<default-mapping-uuid>"
```

### 3. Acompanhar Processamento
```bash
# Verifique jobs de ingestão
curl http://localhost:8000/ingest/jobs \
  -H "Authorization: Bearer $TOKEN" | jq .

# Aguarde ~10 segundos para processamento

# Verifique alertas gerados
curl "http://localhost:8000/alerts?severity=HIGH" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 4. Visualizar no Frontend
1. Acesse http://localhost:3000
2. Login com `admin_a` / `admin123`
3. Vá para **Alertas**
4. Filtre por **Severity = HIGH**
5. Clique no alerta para ver evidências

---

## 📝 Configurações Importantes

### `infra/docker-compose.yml`

**Variáveis de Ambiente:**
```yaml
api:
  environment:
    DATABASE_URL: postgresql://betaml:devpass@postgres:5432/betaml_dev
    REDIS_URL: redis://:devpass@redis:6379/0
    KAFKA_BOOTSTRAP_SERVERS: kafka:29092
    JWT_SECRET: dev-secret-change-me  # MUDE EM PRODUÇÃO
    ENVIRONMENT: development  # dev, staging, production
```

### Escalabilidade Local
Para simular carga maior:

```yaml
# Aumentar resources
postgres:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G

kafka:
  environment:
    KAFKA_LOG_SEGMENT_BYTES: 104857600  # 100MB (default 1GB)
    KAFKA_NUM_PARTITIONS: 12  # Aumentar para paralelismo
```

---

## 🐛 Troubleshooting

### Containers não sobem
```bash
# Verificar se portas estão disponíveis
lsof -i :5432  # Postgres
lsof -i :9092  # Kafka
lsof -i :6379  # Redis

# Liberar porto
kill -9 $(lsof -t -i :5432)

# Limpar volumes e tentar novamente
docker-compose -f infra/docker-compose.yml down -v
docker-compose -f infra/docker-compose.yml up -d
```

### Alertas não aparecem
```bash
# Verificar logs do rules_engine
docker-compose -f infra/docker-compose.yml logs -f rules_engine

# Verificar se Kafka tem dados
docker-compose -f infra/docker-compose.yml exec kafka \
  kafka-console-consumer --bootstrap-server localhost:29092 \
  --topic canonical.transactions --from-beginning --max-messages 5

# Verificar Redis features
docker-compose -f infra/docker-compose.yml exec redis \
  redis-cli KEYS "operador*"
```

### API não responde
```bash
# Verificar logs
docker-compose -f infra/docker-compose.yml logs api

# Verificar database
docker-compose -f infra/docker-compose.yml exec postgres \
  psql -U betaml -d betaml_dev -c "SELECT * FROM tenants;"

# Reiniciar
docker-compose -f infra/docker-compose.yml restart api
```

### Kafka com lag alto
```bash
# Verificar consumer lag
docker-compose -f infra/docker-compose.yml exec kafka \
  kafka-consumer-groups --bootstrap-server localhost:29092 \
  --group stream-processor --describe

# Aumentar partições se necessário
docker-compose -f infra/docker-compose.yml exec kafka \
  kafka-topics --bootstrap-server localhost:29092 \
  --alter --topic canonical.transactions --partitions 6
```

---

## 🏭 Production Deployment

### Prerequisites
- Kubernetes 1.24+
- Prometheus + Grafana (observabilidade)
- ELK Stack (logging) OU Datadog/CloudWatch
- AWS VPC / GCP VPC com security groups
- RDS PostgreSQL (managed) OU Postgres em k8s
- MSK / Confluent Cloud (managed Kafka)
- S3 para lakehouse (ou GCS / Azure Blob)
- ECR / GCR para container registry

### Helm Charts (Sketch)
```bash
helm repo add betaml https://helm.betaml.io
helm install betaml/betaml \
  --namespace aml \
  --values values-prod.yaml
```

### Security Checklist
- [ ] JWT_SECRET mude para valor aleatório 256-bit
- [ ] PostgreSQL com replicação e backup automático
- [ ] Redis com replicação (sentinel ou cluster)
- [ ] Kafka com replicação (broker > 3)
- [ ] TLS/SSL em todas as conexões
- [ ] Network policies isolam serviços
- [ ] Secrets armazenados em Vault/Secrets Manager
- [ ] Audit logging para todas ações administrativas
- [ ] Rate limiting em APIs públicas
- [ ] DDoS protection (WAF)

### Monitoramento
```yaml
# Prometheus
prometheus:
  scrape_configs:
    - job_name: 'betaml-api'
      static_configs:
        - targets: ['api:8000']
    - job_name: 'betaml-rules'
      static_configs:
        - targets: ['rules_engine:8080']  # /metrics

# Grafana Dashboards
- Alert Rate (alerts/min)
- Rule Latency (P50, P95, P99)
- Feature Computation Time
- Kafka Consumer Lag
- PostgreSQL connections & queries
- ClickHouse query performance
```

### Retention Policies
```sql
-- Bronze: 30 dias
ALTER TABLE bronze_events MODIFY TTL occurred_at + INTERVAL 30 DAY;

-- Silver: 365 dias
ALTER TABLE silver_events MODIFY TTL occurred_at + INTERVAL 365 DAY;

-- Alerts: 90 dias
ALTER TABLE scoring_alerts MODIFY TTL created_at + INTERVAL 90 DAY;

-- Audit Logs: 7 anos (compliance)
ALTER TABLE audit_logs MODIFY TTL created_at + INTERVAL 7 YEAR;
```

### Backup & Disaster Recovery
```bash
# Daily backup (PostgreSQL)
0 2 * * * pg_dump -Fc betaml_dev | aws s3 cp - s3://betaml-backups/pg-$(date +%Y%m%d).dump

# Weekly backup (MinIO)
0 3 * * 0 aws s3 sync s3://betaml-lakehouse s3://betaml-backups/lake-$(date +%Y%m%d)/ --delete

# Test restore monthly
aws s3 cp s3://betaml-backups/pg-$(date -d '30 days ago' +%Y%m%d).dump - | pg_restore -Fc -d betaml_restore
```

---

## 📚 Documentação Completa

- [ARCHITECTURE.md](./docs/ARCHITECTURE.md): Diagramas e design
- [DSL_GUIDE.md](./docs/DSL_GUIDE.md): Linguagem de regras
- [LGPD_COMPLIANCE.md](./docs/LGPD_COMPLIANCE.md): Dados pessoais
- [API_REFERENCE.md](./docs/API_REFERENCE.md): Endpoints completos
- [MIGRATION_GUIDE.md](./docs/MIGRATION_GUIDE.md): Dados existentes

---

## 🤝 Suporte

- **Issues:** https://github.com/your-org/betaml/issues
- **Wiki:** https://github.com/your-org/betaml/wiki
- **Email:** support@betaml.io
- **Slack:** https://betaml.slack.com

---

## 📄 Licença

Proprietário. Todos os direitos reservados © 2024 BetAML.

---

**Última atualização:** 26/02/2024
**Versão MVP:** 1.0.0
