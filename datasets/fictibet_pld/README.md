# FictiBet PLD Test Pack

Este pacote contem arquivos de ingestao para testar o BetAML com dados realistas de uma operacao de bet ficticia.

Objetivo:
- exercitar sinais de PLD/AML com cenarios de risco plausiveis;
- validar ingestao por arquivos e conectores (Gamma, Delta, Epsilon);
- cobrir transacoes, apostas e eventos de dispositivo usados no calculo de features e redes.

## Arquivos

- `01-fictibet-canonical-events.ndjson`
  - NDJSON canonico (misto de `TRANSACTION`, `BET`, `DEVICE_EVENT`).
  - Recomendado para teste principal end-to-end com `POST /ingest/file`.
- `02-fictibet-connector-gamma.xml`
  - Feed XML para `POST /ingest/connectors/gamma/parse`.
  - Inclui registros validos e invalidos para testar quarentena.
- `03-fictibet-connector-delta.ndjson`
  - Feed NDJSON no formato nativo Delta para `POST /ingest/connectors/delta/parse`.
  - Inclui linhas validas e invalidas.
- `04-fictibet-connector-epsilon-webhook.json`
  - Payload webhook Epsilon com eventos financeiros.
- `04-sign-epsilon.sh`
  - Script para gerar headers HMAC (`x-epsilon-signature` e `x-epsilon-timestamp`) para o arquivo Epsilon.
- `05-fictibet-transactions-smoke.csv`
  - CSV simples para smoke test rapido em `POST /ingest/file`.

## Cenarios de risco incluidos

- Structuring (fracionamento): `PLY-STR-001`
- Spike de deposito vs historico: `PLY-SPI-002`
- Round-tripping (deposito/saque rapido): `PLY-RTR-003`
- Rede por dispositivo/conta compartilhada: `PLY-NET-004`, `PLY-NET-005`, `PLY-PEP-006`
- PEP + apostas de alto risco + cashout: `PLY-PEP-006`
- Multi-currency + bonus + chargeback: `PLY-MULTI-007`
- Falhas operacionais (status `FAILED`) para feature de quality/fraud ops

## Dados Sintéticos CSV (gerados por generate.py)

Além dos arquivos originais acima, o pacote inclui CSVs gerados programaticamente
com `python datasets/fictibet_pld/generate.py` (seed fixa, reprodutível):

| Arquivo | Entidade | ~Rows | Descrição |
|---------|----------|-------|-----------|
| `players.csv` | PLAYER | 120 | 6 cenários: normal(80), renda-incomp(10), structuring(8), rede(12), bonus-abuse(5), PEP(5) |
| `transactions.csv` | TRANSACTION | 2.400 | Depósitos, saques, bônus, cashouts — 90 dias |
| `bets.csv` | BET | 2.000 | Sports, casino, slots — odds e stakes por cenário |
| `device_events.csv` | DEVICE_EVENT | 1.000 | Logins, apostas — rede compartilha device/IP |

Ingestão rápida:

```bash
python scripts/ingest_fictibet_pld.py            # tudo
python scripts/ingest_fictibet_pld.py --only players  # só jogadores
```

## Exemplo rapido de ingestao

Opção recomendada (pack completo em um comando):

```bash
scripts/ingest_fictibet_pack.sh
```

Variaveis uteis:

```bash
API_URL=http://localhost:8000 \
USERNAME=admin_a \
PASSWORD=admin123 \
EPSILON_WEBHOOK_SECRET=dev-secret-change-me \
scripts/ingest_fictibet_pack.sh
```

Opcao manual (endpoint por endpoint):

1. Login e token:

```bash
TOKEN=$(curl -sS -X POST http://localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"admin_a","password":"admin123"}' | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
```

2. Ingestao principal (NDJSON canonico):

```bash
curl -sS -X POST http://localhost:8000/ingest/file \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "file=@datasets/fictibet_pld/01-fictibet-canonical-events.ndjson;type=application/x-ndjson" \
  -F "source_system=BackofficeAlpha"
```

3. Connector Gamma (XML):

```bash
curl -sS -X POST http://localhost:8000/ingest/connectors/gamma/parse \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "entity_type=TRANSACTION" \
  -F "file=@datasets/fictibet_pld/02-fictibet-connector-gamma.xml;type=application/xml"
```

4. Connector Delta (NDJSON):

```bash
curl -sS -X POST http://localhost:8000/ingest/connectors/delta/parse \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "entity_type=TRANSACTION" \
  -F "file=@datasets/fictibet_pld/03-fictibet-connector-delta.ndjson;type=application/x-ndjson"
```

5. Connector Epsilon (Webhook HMAC):

```bash
./datasets/fictibet_pld/04-sign-epsilon.sh dev-secret-change-me datasets/fictibet_pld/04-fictibet-connector-epsilon-webhook.json
# Uso completo:
# ./datasets/fictibet_pld/04-sign-epsilon.sh <secret> <payload_file> [out_file] [timestamp_unix]

# O script imprime:
# - X_EPSILON_TIMESTAMP
# - X_EPSILON_SIGNATURE
```

```bash
source /tmp/epsilon_headers.env
curl -sS -X POST http://localhost:8000/ingest/webhook/epsilon \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "content-type: application/json" \
  -H "x-epsilon-timestamp: ${X_EPSILON_TIMESTAMP}" \
  -H "x-epsilon-signature: ${X_EPSILON_SIGNATURE}" \
  --data-binary @datasets/fictibet_pld/04-fictibet-connector-epsilon-webhook.json
```

## Observacoes

- Os identificadores de jogadores (`PLY-*`) sao externos/proprios para simular ambiente real.
- Os dados sao ficticios e nao contem PII real.
- O pack mistura eventos validos e invalidos para validar:
  - sucesso de ingestao;
  - quarentena (`ingest_errors`);
  - reprocessamento/replay;
  - sinais de features de risco.
