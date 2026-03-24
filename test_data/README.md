# BetAML Test Data — Dataset Realista para Testes E2E

Este diretório contém dados simulados para testar o BetAML com cenários realistas.

## Estrutura

```
test_data/
├── README.md (este arquivo)
├── players/           # Dados de players
├── transactions/      # Transações financeiras (XML, NDJSON, CSV)
├── bets/             # Dados de apostas
├── devices/          # Eventos de dispositivo
└── results/          # Relatórios de teste
```

## Cenários Implementados

### 1. **Structuring (Estruturação)**
- **Players**: PLY-STRUCT-001, PLY-STRUCT-002
- **Padrão**: Múltiplos depósitos pequenos (R$ 600-800) em janela curta (6 horas)
- **Expected Alert**: STRUCTURING_DETECTED (HIGH severity)
- **Compliance**: COAF Res. 36/2021 Art. 6º

### 2. **Spike de Depósito**
- **Players**: PLY-SPIKE-001
- **Padrão**: Depósito 5x maior que média histórica (R$ 15.000 vs R$ 2.000)
- **Expected Alert**: ANOMALOUS_DEPOSIT (MEDIUM severity)
- **Trigger**: Anomaly Detection Model

### 3. **Network Clustering (Rede Suspeita)**
- **Players**: PLY-NETWORK-001, PLY-NETWORK-002, PLY-NETWORK-003
- **Padrão**: Múltiplos players compartilhando mesmo device_id
- **Expected**: Cluster detectado (size=3, suspicious)
- **Compliance**: COAF Res. 36/2021 Art. 6º (mulas)

### 4. **Reincidência**
- **Players**: PLY-RECUR-001 (aparente novo), PLY-ERASED-001 (histórico)
- **Padrão**: Device/IP/padrão temporal similar a conta já banida
- **Expected**: recurrence_score > 0.85
- **Compliance**: COAF Res. 36/2021 Art. 9º

### 5. **Operação Legítima**
- **Players**: PLY-NORMAL-001, PLY-NORMAL-002
- **Padrão**: Depósitos/apostas normais, variação esperada
- **Expected**: Sem alertas críticos, risk_score BAIXO

---

## Como Usar

### 1. Preparar o Ambiente

```bash
cd /workspaces/BetAML

# Iniciar stack (se não estiver rodando)
docker-compose up -d

# Aguardar healthcheck
docker-compose logs -f api | grep "healthy"
```

### 2. Registrar Tenant de Teste (se necessário)

```bash
# Via API
curl -X POST http://localhost:8000/admin/tenants \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <SUPER_ADMIN_TOKEN>" \
  -d '{
    "name": "TestOperator",
    "cnpj": "12.345.678/0001-99",
    "slug": "test-operator"
  }'
```

### 3. Criar MappingConfigs

```bash
# Para ConnectorGamma (XML)
python scripts/setup_mappings.py --connector gamma

# Para ConnectorDelta (NDJSON)
python scripts/setup_mappings.py --connector delta

# Para BackofficeAlpha (JSON)
python scripts/setup_mappings.py --connector alpha
```

### 4. Ingerir Dados de Teste

```bash
# Ingerir todas estruturas
python scripts/ingest_test_data.py --all

# Ou específico
python scripts/ingest_test_data.py --scenario structuring
python scripts/ingest_test_data.py --scenario spike
python scripts/ingest_test_data.py --scenario network
```

### 5. Validar Resultados

```bash
# Verificar alertas gerados
curl http://localhost:8000/alerts \
  -H "Authorization: Bearer <TOKEN>" | jq '.items[] | {id, type, severity, title}'

# Verificar features calculadas
curl "http://localhost:8000/feature-store/players/{playerId}/latest" \
  -H "Authorization: Bearer <TOKEN>" | jq '.features'

# Relatório consolidado
python scripts/validate_test_results.py
```

---

## Dados de Teste — Credenciais

### Seed Credentials (Dev)

**OperadorA:**
- Admin: `admin_a` / `admin123`
- Analyst: `analyst_a` / `analyst123`
- Auditor: `auditor_a` / `auditor123`

**OperadorB:**
- Admin: `admin_b` / `admin123`
- Analyst: `analyst_b` / `analyst123`
- Auditor: `auditor_b` / `auditor123`

**Tenant ID (OperadorA)**: Will be displayed após seed (check logs)

---

## Arquivos de Dados

| Arquivo | Formato | Cenário | Records |
|---------|---------|---------|---------|
| `transactions/structuring_gamma.xml` | XML | Structuring | 8 |
| `transactions/structuring_delta.ndjson` | NDJSON | Structuring | 8 |
| `transactions/spike_backoffice.json` | JSON | Spike | 5 |
| `transactions/network_combined.ndjson` | NDJSON | Network | 12 |
| `transactions/legit_sample.csv` | CSV | Normal | 20 |
| `bets/sports_bets.json` | JSON | Normal + Risky | 30 |
| `players/player_seed.json` | JSON | All scenarios | 15 |

---

## Métricas Esperadas (após ingestão)

### Structuring Scenario
```
Total events: 8
Alerts gerados: 1-2 (STRUCTURING_DETECTED)
Risk score: 0.75-0.95 (HIGH)
Features: deposit_velocity=3-5, structuring_score=0.80+
```

### Spike Scenario
```
Total events: 5
Alerts gerados: 1 (ANOMALOUS_DEPOSIT)
Risk score: 0.60-0.80 (MEDIUM-HIGH)
Features: deposit_sum_24h 5x baseline
```

### Network Scenario
```
Total events: 12
Clusters detectados: 1 (size=3)
suspicious_clusters: 1
Risk scores: 0.70-0.85 (HIGH)
```

### Reincidência Scenario
```
Total events: Múltiplos
recurrence_score: 0.85+
Flag: recurrence_suspect=True
Expected: Manual review ou auto-block
```

### Legítimo Scenario
```
Total events: 20+
Alerts: 0 ou muito baixo
Risk score: 0.15-0.35 (LOW)
Ações: Nenhuma (normal)
```

---

## Troubleshooting

### Erro: "Unknown connector type"
- Verifique `ALLOWED_SOURCE_SYSTEMS` em `routers/ingest.py`
- Confirme MappingConfig criado para o source_system

### Erro: "Invalid signature (ConnectorEpsilon)"
- Verifique `X-Epsilon-Signature` header
- Use script `scripts/sign_webhook.py` para gerar signature correta

### Sem alertas gerados
- Verifique se RuleDefinitions estão ativas
- Inspecione `alert_logs` table para erros de scoring
- Confirme que CanonicalEvents foram persistidos no Kafka

### Features não calculadas
- Verifique se `stream_processor` está rodando
- Inspecione logs: `docker-compose logs stream_processor`
- Confirme Redis conectado: `redis-cli ping`

---

## Próximas Etapas

1. **Customizar CPFs**: Edite `players/player_seed.json` com CPFs reais (validação)
2. **Ajustar timestamps**: Use `scripts/adjust_timestamps.py` para data atual
3. **Integrar BD antigo**: Importe dados históricos com `scripts/backfill_historical.py`
4. **Load testing**: Use `tests/load/locustfile.py` para testes de carga

---

**Data de criação**: 2026-03-24
**Últimas atualizações**: Seed com 5 cenários
**Status**: Production-ready para testes E2E
