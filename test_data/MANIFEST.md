# Test Data Manifest — O que foi criado

## 📁 Estrutura Completa

```
test_data/
├── README.md                           # Overview dos datasets
├── GUIDE.md                            # Guia completo de uso
├── MANIFEST.md                         # Este arquivo
├── run_tests.sh                        # Script principal de execução
│
├── players/
│   └── player_seed.json               # 11 players (structuring, spike, network, recurrence, normal, PEP)
│
├── transactions/
│   ├── structuring_gamma.xml          # 4 eventos XML (ConnectorGamma)
│   ├── structuring_delta.ndjson       # 6 eventos NDJSON (ConnectorDelta) + 1 bet
│   ├── spike_backoffice.json          # 5 eventos JSON (BackofficeAlpha) — spike 6x
│   ├── network_combined.ndjson        # 10 eventos NDJSON (ConnectorDelta) — shared device
│   └── legit_sample.csv               # 20 eventos CSV (BackofficeAlpha) — normal
│
├── bets/
│   └── sports_bets.json               # 11 apostas (SportsBook) — stakes 100-5000 BRL
│
├── devices/
│   └── device_events.json             # 10 login events — shared-device pattern
│
└── results/
    └── (gerado após testes)           # test_report.html
```

## 📊 Dados por Cenário

### 1️⃣ STRUCTURING
- **Players**: PLY-STRUCT-001, PLY-STRUCT-002
- **Eventos**: 8 depósitos (R$ 600-800) em 3h
- **Formatos**: XML (Gamma), NDJSON (Delta)
- **Expected Alert**: STRUCTURING_DETECTED (CRITICAL)
- **Risk Score**: 0.75-0.95

### 2️⃣ SPIKE
- **Player**: PLY-SPIKE-001
- **Eventos**: 1 depósito grande (R$ 15K) + 2 apostas
- **Formato**: JSON (BackofficeAlpha)
- **Expected Alert**: ANOMALOUS_DEPOSIT (HIGH)
- **Risk Score**: 0.60-0.80

### 3️⃣ NETWORK
- **Players**: PLY-NETWORK-001/002/003 (3 em 1 cluster)
- **Eventos**: 10 transações + apostas (shared device)
- **Formato**: NDJSON (Delta)
- **Expected**: cluster_id = 1, size=3, suspicious
- **Risk Score**: 0.70-0.85

### 4️⃣ RECURRENCE
- **Players**: PLY-RECUR-001 (novo), PLY-ERASED-001 (histórico)
- **Padrão**: Similar device/IP/temporal
- **Expected**: recurrence_score >= 0.85
- **Risk Score**: 0.80-0.95

### 5️⃣ NORMAL + PEP
- **Players**: PLY-NORMAL-001/002, PLY-PEP-001
- **Eventos**: 20 transações legítimas
- **Formatos**: CSV, JSON
- **Expected**: Sem alertas críticos, RiskScore < 0.5

## 🎯 Total de Dados

| Type | Count | Sources |
|------|-------|---------|
| Players | 11 | player_seed.json |
| Transactions | 41 | 5 arquivos |
| Bets | 11 | sports_bets.json |
| Device Events | 10 | device_events.json |
| **TOTAL EVENTS** | **62** | **9 arquivos** |

## 🔍 Metadados Inclusos

### Players
- ✅ CPF (formato válido, não real)
- ✅ Nome completo
- ✅ Data de nascimento
- ✅ Renda mensal declarada
- ✅ Profissão
- ✅ Flag PEP (Public Exposed Person)
- ✅ Status (ACTIVE, ERASED)

### Transactions
- ✅ external_transaction_id (único por source)
- ✅ Player ID (cross-reference)
- ✅ Tipo (DEPOSIT, WITHDRAWAL, CHARGEBACK, BET)
- ✅ Valor (BRL, valores realistas)
- ✅ Timestamp ISO8601 (com timezone Z)
- ✅ Device ID (fingerprint)
- ✅ Método de pagamento (PIX, TED, CARD, etc.)
- ✅ IP address (class C private)

### Bets
- ✅ external_bet_id
- ✅ Stake amount (R$ 50-5000)
- ✅ Odds (1.50-3.50)
- ✅ Potential/settled payout
- ✅ Sport category (SOCCER, BASKETBALL, TENNIS)
- ✅ Market type (1X2, OVER_UNDER, MONEYLINE)
- ✅ Status (OPEN, SETTLED, WIN, LOSS)

### Device Events
- ✅ external_event_id
- ✅ Device ID (compartilhado em network scenario)
- ✅ action (LOGIN, LOGOUT, DEPOSIT_ATTEMPT)
- ✅ Device type (MOBILE_IOS, ANDROID, DESKTOP)
- ✅ IP address
- ✅ Country code (BR)
- ✅ User agent

## 📋 Scripts Auxiliares

### 1. ingest_test_data.py (8.6 KB)
Ingere arquivos JSON/XML/NDJSON/CSV para API.

**Uso**:
```bash
python scripts/ingest_test_data.py --scenario all
python scripts/ingest_test_data.py --scenario structuring --wait 60
```

**Features**:
- ✅ Health check API
- ✅ Ingestão por cenário
- ✅ Aguarda processamento assíncrono
- ✅ Sumário de sucesso/falha

### 2. validate_test_results.py (9.3 KB)
Valida que os cenários geraram alertas/features esperados.

**Uso**:
```bash
python scripts/validate_test_results.py --scenario structuring
python scripts/validate_test_results.py  # all scenarios
```

**Checks**:
- ✅ Structuring: alerta + features de velocity
- ✅ Spike: alerta + deposit elevado
- ✅ Network: cluster detection + shared device
- ✅ Normal: ausência de alertas críticos

### 3. generate_test_report.py (11 KB)
Gera relatório HTML consolidado com métricas.

**Uso**:
```bash
python scripts/generate_test_report.py
python scripts/generate_test_report.py --output my_report.html
```

**Output**: HTML interativo com:
- Chart de alertas por severidade
- Tabela de ingest jobs
- Timeline de eventos
- Tempo de geração

### 4. run_tests.sh (Bash executable)
Script principal que:
1. Verifica health API
2. Ingere dados
3. Valida resultados
4. Gera relatório

**Uso**:
```bash
./test_data/run_tests.sh                 # all scenarios
./test_data/run_tests.sh structuring 60  # específico + 60s timeout
```

## 🚀 Como Usar

### Quick Start (3 commands)

```bash
# 1. Ingerir tudo
python scripts/ingest_test_data.py --all

# 2. Validar
python scripts/validate_test_results.py

# 3. Relatório
python scripts/generate_test_report.py
```

### Full Run com Script

```bash
./test_data/run_tests.sh all
```

### Por Cenário

```bash
./test_data/run_tests.sh structuring 45
./test_data/run_tests.sh spike
./test_data/run_tests.sh network
```

## ✅ Checklist de Validação Manual

Após rodar testes, verificar no dashboard:

- [ ] **Structuring**: 2 alertas CRITICAL gerados
- [ ] **Spike**: 1 alerta HIGH para PLY-SPIKE-001
- [ ] **Network**: 3 players em cluster (cluster_id != null)
- [ ] **Features**: deposit_velocity, structuring_score calculados
- [ ] **Jobs**: Status DONE com 0 erros
- [ ] **Risk Scores**: Visualization correcta no player detail
- [ ] **Audit Logs**: Eventos de ingestão registrados
- [ ] **PEP Monitor**: PLY-PEP-001 com flag ativa

## 🐛 Debugging

Se algum cenário falhar:

```bash
# Ver logs do ingest job
curl http://localhost:8000/ingest/jobs?status=FAILED | jq

# Alertas de um player
curl http://localhost:8000/alerts?player_id=PLY-STRUCT-001 | jq '.items[] | {id, type, title, severity}'

# Features de um player
curl http://localhost:8000/feature-store/players/PLY-SPIKE-001/latest | jq '.features'
```

## 📚 Conformidade

✅ Todos os dados são **fictícios** mas realistas:
- CPFs: formato válido mas numéros aleatórios
- Nomes: genéricos, sem PII real
- Valores: BRL (Real), quantidades realistas
- Timestamps: ISO8601 com timezone Z

✅ Padrões cobrem **COAF typologies**:
- Structuring (Art. 6º)
- Money laundering (Art. 6º)
- Network (Art. 6º — mulas)
- Recurrence (Art. 9º)

✅ **LGPD compliant**:
- CPFs não encriptados apenas em dados de entrada
- Será encriptado no DB (bcrypt + AES)
- ERASURE data incluído (PLY-ERASED-001)

---

**Data**: 2026-03-24  
**Status**: ✅ Production-ready  
**Total Files**: 9 (datasets) + 4 (scripts)  
**Total Events**: 62 realistas  
**Compliance**: COAF + LGPD  
