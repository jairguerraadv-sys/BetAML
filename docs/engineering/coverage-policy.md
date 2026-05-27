# Coverage Policy (PR-02)

## Objetivo

Elevar a proteção contra regressão em código crítico sem bloquear a esteira por acoplamento com módulos legados ainda em hardening.

## Baseline medido

- Baseline global (`services/api` em `tests/unit`): 57%
- Baseline crítico antigo (incluindo `routers/cases.py` e `routers/alerts.py`): 66%
- Gate crítico atual (escopo PR-02): 73%

## Gate crítico vigente

Threshold:

- `coverage >= 70%`

Comando canônico:

```bash
JWT_SECRET='test-secret-only-for-unit-tests' \
ENVIRONMENT='test' \
PII_ENCRYPTION_KEY='test-pii-encryption-key-32bytes!!' \
DATABASE_URL='sqlite+aiosqlite:///:memory:' \
REDIS_URL='redis://localhost:6379/0' \
KAFKA_BOOTSTRAP_SERVERS='localhost:9092' \
bash scripts/run_critical_unit_batches.sh --critical-coverage --include-remainder -q --tb=short
```

Runner/CI:

- `scripts/run_critical_unit_batches.sh --critical-coverage`
- `.github/workflows/ci.yml` (job `backend-tests`)

## Módulos cobertos pelo gate crítico

- `services/api/auth.py`
- `services/api/config.py`
- `services/api/database.py`
- `services/api/models.py`
- `services/api/routers/auth.py`
- `services/api/routers/audit.py`
- `services/api/routers/ingest.py`
- `services/api/routers/ml.py`
- `services/api/routers/reports.py`

## Fora do gate (por enquanto)

- `services/api/routers/cases.py`
- `services/api/routers/alerts.py`
- `services/api/routers/players.py`

Motivo:

- Alta superfície legada e baixo baseline histórico ainda em recuperação.
- Inclusão imediata desses módulos no gate 70 derrubaria a esteira sem sinal de regressão nova.
- Estratégia adotada: reforçar testes incrementais e reintroduzir no gate em ondas controladas.

## Política de skips e evolução

- Skips devem ser explícitos, com justificativa técnica e prazo de revisão.
- Não usar exclusões silenciosas em `.coveragerc` para esconder regressão funcional.
- Expansão planejada:
  - Fase 1: manter gate crítico em 70 (PR-02).
  - Fase 2: reincluir `cases` e `alerts` após aumento sustentado de testes (>65% por módulo).
  - Fase 3: elevar gate crítico para 75 e depois 80.

## Evidência de execução local (PR-02)

Com `.coverage` gerada por `pytest tests/unit --cov=services/api`, o comando abaixo passou:

```bash
python -m coverage report \
  --include='services/api/auth.py,services/api/config.py,services/api/database.py,services/api/models.py,services/api/routers/auth.py,services/api/routers/audit.py,services/api/routers/ingest.py,services/api/routers/ml.py,services/api/routers/reports.py' \
  --fail-under=70
```

Resultado consolidado: `TOTAL ... 73%`.
