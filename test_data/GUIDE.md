# BetAML End-to-End Test Suite

**Testes realistas completos com dados simulados** para validar o BetAML em produção.

## 📋 Visão Geral

Este diretório contém um **dataset completo com 5 cenários de teste realistas**:

1. **Structuring** (Estruturação) — Múltiplos depósitos pequenos para evitar detecção
2. **Spike** (Pico) — Depósito anormalmente grande vs. padrão histórico
3. **Network** (Rede) — 3 players usando mesmo dispositivo (possível fraude)
4. **Recurrence** (Reincidência) — Padrão similar a conta já banida
5. **Normal** (Legítimo) — Operações normais, sem flags

---

## 🚀 Quick Start

### 1. Preparar Ambiente

```bash
# Garantir que o stack está rodando
docker-compose up -d

# Aguardar health check
docker-compose logs -f api | grep "healthy"
```

### 2. Criar Tenant (se novo)

```bash
# Seed credentials usam COAF operator A
# Admin: admin_a / admin123
# Analyst: analyst_a / analyst123
# Auditor: auditor_a / auditor123
```

### 3. Ingerir Dados de Teste

```bash
# Todos os cenários
python scripts/ingest_test_data.py --all

# Ou específico
python scripts/ingest_test_data.py --scenario structuring
python scripts/ingest_test_data.py --scenario spike
python scripts/ingest_test_data.py --scenario network
```

### 4. Validar Resultados

```bash
# Verificar se os alertas foram gerados corretamente
python scripts/validate_test_results.py --all

# Gerar relatório HTML
python scripts/generate_test_report.py
```

---

## 📊 Dataset Realista

### Estrutura de Dados

```
test_data/
├── players/
│   └── player_seed.json          # 11 players com CPFs, profissões, renda
├── transactions/
│   ├── structuring_gamma.xml      # 4 depósitos XML (structuring)
│   ├── structuring_delta.ndjson   # 6 eventos NDJSON (structuring + bets)
│   ├── spike_backoffice.json      # 5 eventos JSON (spike)
│   ├── network_combined.ndjson    # 10 eventos NDJSON (network)
│   └── legit_sample.csv           # 20 eventos CSV (normal)
├── bets/
│   └── sports_bets.json           # 11 apostas com odds, markets, playoffs
├── devices/
│   └── device_events.json         # 10 eventos de dispositivo
└── results/
    └── test_report.html           # Relatório gerado
```

### Players por Cenário

| ID | Cenário | CPF | Renda Declarada | PEP | Status |
|----|---------|-----|-----------------|-----|--------|
| PLY-STRUCT-001 | Structuring | 123.456.789-01 | R$ 3.500 | ❌ | ACTIVE |
| PLY-STRUCT-002 | Structuring | 123.456.789-02 | R$ 2.500 | ❌ | ACTIVE |
| PLY-SPIKE-001 | Spike | 123.456.789-03 | R$ 5.000 | ❌ | ACTIVE |
| PLY-NETWORK-001 | Network | 123.456.789-04 | R$ 1.500 | ❌ | ACTIVE |
| PLY-NETWORK-002 | Network | 123.456.789-05 | R$ 2.000 | ❌ | ACTIVE |
| PLY-NETWORK-003 | Network | 123.456.789-06 | R$ 3.000 | ❌ | ACTIVE |
| PLY-RECUR-001 | Recurrence | 123.456.789-07 | R$ 4.000 | ❌ | ACTIVE |
| PLY-ERASED-001 | Recurrence (histórico) | 999.999.999-99 | null | ❌ | ERASED |
| PLY-NORMAL-001 | Normal | 123.456.789-08 | R$ 2.800 | ❌ | ACTIVE |
| PLY-NORMAL-002 | Normal | 123.456.789-09 | R$ 3.200 | ❌ | ACTIVE |
| PLY-PEP-001 | PEP (alto risco) | 123.456.789-10 | R$ 25.000 | ✅ | ACTIVE |

---

## 🔬 Cenários Detalhados

### 1. **Structuring** 🚩

**Padrão de Risco**: Múltiplos depósitos pequenos (R$ 600-800) em janela curta (3h)

**Expected Output**:
- ✅ Alerta: `STRUCTURING_DETECTED` (CRITICAL)
- ✅ Features: `deposit_velocity >= 3`, `structuring_score >= 0.70`
- ✅ RiskScore: 0.75-0.95 (HIGH)
- 📋 Compliance: COAF Res. 36/2021 Art. 6º (fracionamento)

**Arquivos**:
- `structuring_gamma.xml` — 4 eventos (ConnectorGamma)
- `structuring_delta.ndjson` — 6 eventos + 1 bet (ConnectorDelta)

---

### 2. **Spike** 📈

**Padrão de Risco**: Depósito 6x maior que baselinne (R$ 15K vs R$ 2.5K)

**Expected Output**:
- ✅ Alerta: `ANOMALOUS_DEPOSIT` (MEDIUM-HIGH)
- ✅ Features: `deposit_sum_24h = 17,500`
- ✅ RiskScore: 0.60-0.80
- 🤖 Trigger: ML Anomaly Model

**Arquivo**:
- `spike_backoffice.json` — 5 eventos (BackofficeAlpha)

---

### 3. **Network Clustering** 🕸️

**Padrão de Risco**: 3 players compartilhando MESMO device_id + IP

**Expected Output**:
- ✅ Cluster detectado: size=3 (suspeito)
- ✅ Features: `shared_device_score >= 0.5`, `cluster_id = <hash>`
- ✅ RiskScore: 0.70-0.85 por player
- 📋 Compliance: COAF Res. 36/2021 Art. 6º (mulas/redes)

**Arquivo**:
- `network_combined.ndjson` — 10 eventos com device compartilhado (ConnectorDelta)

---

### 4. **Recurrence** 🔄

**Padrão de Risco**: Player novo com padrão comportamental idêntico a conta já ERASED

**Expected Output**:
- ✅ Score: `recurrence_score >= 0.85` (muito similar)
- ✅ Flag: `recurrence_suspect = True`
- ✅ RiskScore: 0.80-0.95
- 📋 Compliance: COAF Res. 36/2021 Art. 9º (reincidência)

**Dados**:
- `PLY-RECUR-001` (novo) vs `PLY-ERASED-001` (histórico de risco)

---

### 5. **Normal** ✅

**Padrão Legítimo**: Operações ordinárias, apostas variadas, sem anomalias

**Expected Output**:
- ✅ Sem alertas CRITICAL
- ✅ RiskScore < 0.5 (LOW)
- ✅ Features normais (variação esperada)

**Arquivos**:
- `legit_sample.csv` — 20 transações (BackofficeAlpha)
- `sports_bets.json` — 11 apostas (SportsBook)

---

## 📈 Formatos de Dados

### ConnectorGamma (XML)

```xml
<transaction>
  <EventId>TXG-001</EventId>
  <PlayerId>PLY-STRUCT-001</PlayerId>
  <Type>DEPOSIT</Type>
  <Amount currency="BRL">650.00</Amount>
  <Timestamp>2026-03-24T14:00:00Z</Timestamp>
  <DeviceId>device-001</DeviceId>
  <Instrument>
    <Type>PIX</Type>
    <Token>pix-hash</Token>
  </Instrument>
</transaction>
```

### ConnectorDelta (NDJSON)

```json
{"id":"TXD-001","uid":"PLY-STRUCT-002","evt_type":"DEPOSIT","val":620.00,"ts":"2026-03-24T16:00:00Z","ccy":"BRL","device":"device-002","pay_method":"PIX"}
```

### BackofficeAlpha (JSON/CSV)

```json
{
  "event_id": "TXJ-SPIKE-001",
  "player_id": "PLY-SPIKE-001",
  "event_type": "DEPOSIT",
  "gross_amount": 15000.00,
  "event_time": "2026-03-24T11:00:00Z"
}
```

---

## 🧪 Scripts Auxiliares

### 1. `ingest_test_data.py`

Ingere dados dos arquivos JSON/XML/CSV para a API.

```bash
# Todos os cenários
python scripts/ingest_test_data.py --all

# Específico
python scripts/ingest_test_data.py --scenario structuring

# Com timeout customizado (segundos)
python scripts/ingest_test_data.py --scenario network --wait 60
```

**Output**:
```
✓ API healthy at http://localhost:8000
SCENARIO: STRUCTURING
──────────────────────
  Ingestando structuring_gamma.xml como ConnectorGamma...
  ✓ Ingestão iniciada: job_id=abc123, status=PROCESSING

RESUMO DE INGESTÃO
──────────────────
✓ Sucesso: 2/2
```

---

### 2. `validate_test_results.py`

Valida que cenários geraram alertas/features esperados.

```bash
# Todos os cenários
python scripts/validate_test_results.py

# Específico
python scripts/validate_test_results.py --scenario structuring
```

**Output**:
```
VALIDANDO: STRUCTURING
──────────────────────
Checkando PLY-STRUCT-001...
  ✓ Alerta de structuring detectado
  ✓ deposit_velocity > 2.0
  ✓ structuring_score > 0.7

RESUMO DE VALIDAÇÃO
───────────────────
✓ Passed: 18/20 (90.0%)
⚠ Warnings: 2/20
```

---

### 3. `generate_test_report.py`

Gera relatório HTML consolidado.

```bash
python scripts/generate_test_report.py

# Custom output
python scripts/generate_test_report.py --output my_report.html
```

**Saída**: `test_data/results/test_report.html`
(Abra no navegador para visualizar!)

---

## 🎯 Fluxo Completo de Teste

```bash
#!/bin/bash
set -e

echo "1. Iniciando stack..."
docker-compose up -d
sleep 10

echo "2. Ingestando dados..."
python scripts/ingest_test_data.py --all --wait 45

echo "3. Validando resultados..."
python scripts/validate_test_results.py

echo "4. Gerando relatório..."
python scripts/generate_test_report.py

echo "✓ TESTES COMPLETOS!"
echo "📊 Relatório: test_data/results/test_report.html"
```

---

## 🔐 Credentials (Seed Data)

```
Tenant: OperadorA
───────────────────
Admin:    admin_a    / admin123
Analyst:  analyst_a  / analyst123
Auditor:  auditor_a  / auditor123

API Key: betaml_v2_test_key_dummy (para scripts)
```

---

## 📋 Checklist de Validação

Após rodar os testes, verificar:

- [ ] **Structuring scenario**: 1-2 alertas CRITICAL gerados
- [ ] **Spike scenario**: 1 alerta MEDIUM/HIGH para depósito anormal
- [ ] **Network scenario**: 3 players em mesmo cluster
- [ ] **Recurrence scenario**: score >= 0.85 para padrão similar
- [ ] **Normal scenario**: RiskScore < 0.5, sem alertas críticos
- [ ] **Features** calculadas (deposit_velocity, structuring_score, etc.)
- [ ] **Job tracking** (ingest jobs com status DONE/PARTIAL)
- [ ] **Audit logs** registrando todas as ações

---

## 🐛 Troubleshooting

### "API not healthy"
```bash
docker-compose ps                    # Verificar status
docker-compose logs api              # Ver erros
docker-compose logs stream_processor # Consumer
```

### "Unknown source_system"
```bash
# Verificar que MappingConfig foi criado
curl http://localhost:8000/mappings -H "Authorization: Bearer <TOKEN>" | jq
```

### "Sem alertas gerados"
```bash
# Verificar que RuleDefinitions estão ativas
curl http://localhost:8000/rules -H "Authorization: Bearer <TOKEN>" | jq '.[] | {id, title, active}'

# Inspecionar tabela alert_logs para erros de scoring
docker-compose exec postgres psql -U betaml betaml_dev -c "SELECT * FROM alert_logs LIMIT 5;"
```

### "Features não calculadas"
```bash
# Verificar que stream_processor está processando
docker-compose logs stream_processor | grep "processed"

# Testar redis
docker-compose exec redis redis-cli KEYS "player:*"
```

---

## 📚 Referências

- **COAF**: Resolução 36/2021 (typologias AML)
- **LGPD**: Lei 13.709/2018 (proteção de dados)
- **Bacen**: Circular 3.978/2020 (requisitos prudenciais)

---

**Data**: 2026-03-24
**Status**: Production-ready ✅
**Scenarios**: 5 (structuring, spike, network, recurrence, normal)
**Total Events**: 50+
**Coverage**: All major AML/FT patterns
