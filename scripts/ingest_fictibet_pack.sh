#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATASET_DIR="${DATASET_DIR:-${ROOT_DIR}/datasets/fictibet_pld}"

API_URL="${API_URL:-http://localhost:8000}"
USERNAME="${USERNAME:-admin_a}"
PASSWORD="${PASSWORD:-admin123}"
SOURCE_SYSTEM="${SOURCE_SYSTEM:-BackofficeAlpha}"
EPSILON_WEBHOOK_SECRET="${EPSILON_WEBHOOK_SECRET:-dev-secret-change-me}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-120}"

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Tool obrigatoria nao encontrada: $1" >&2
    exit 1
  fi
}

json_get() {
  local json_payload="$1"
  local field_path="$2"
  python3 - "$json_payload" "$field_path" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
path = sys.argv[2].split(".")
value = payload
for part in path:
    if isinstance(value, dict):
        value = value.get(part)
    else:
        value = None
        break
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

wait_job_terminal() {
  local token="$1"
  local job_id="$2"
  local started
  started="$(date +%s)"

  while true; do
    local job_response
    job_response="$(curl -sS "${API_URL}/ingest/jobs/${job_id}" -H "Authorization: Bearer ${token}")"
    local status
    status="$(json_get "${job_response}" "status")"

    if [[ "${status}" == "DONE" || "${status}" == "PARTIAL" || "${status}" == "FAILED" ]]; then
      echo "${job_response}"
      return 0
    fi

    local now
    now="$(date +%s)"
    if (( now - started > WAIT_TIMEOUT_SECONDS )); then
      echo "Timeout aguardando job ${job_id}. Ultimo status: ${status}" >&2
      echo "${job_response}" >&2
      return 1
    fi
    sleep 2
  done
}

print_json() {
  python3 - "$1" <<'PY'
import json
import sys
print(json.dumps(json.loads(sys.argv[1]), indent=2, ensure_ascii=False))
PY
}

require_tool curl
require_tool python3
require_tool openssl

if [[ ! -d "${DATASET_DIR}" ]]; then
  echo "Dataset nao encontrado: ${DATASET_DIR}" >&2
  exit 1
fi

for file in \
  "01-fictibet-canonical-events.ndjson" \
  "02-fictibet-connector-gamma.xml" \
  "03-fictibet-connector-delta.ndjson" \
  "04-fictibet-connector-epsilon-webhook.json" \
  "04-sign-epsilon.sh"; do
  if [[ ! -f "${DATASET_DIR}/${file}" ]]; then
    echo "Arquivo obrigatorio ausente: ${DATASET_DIR}/${file}" >&2
    exit 1
  fi
done

echo "[fictibet] login em ${API_URL} com usuario ${USERNAME}"
LOGIN_RESPONSE="$(curl -sS -X POST "${API_URL}/auth/login" \
  -H "content-type: application/json" \
  -d "{\"username\":\"${USERNAME}\",\"password\":\"${PASSWORD}\"}")"
TOKEN="$(json_get "${LOGIN_RESPONSE}" "access_token")"

if [[ -z "${TOKEN}" || "${TOKEN}" == "None" ]]; then
  echo "Falha no login. Resposta:" >&2
  print_json "${LOGIN_RESPONSE}" >&2
  exit 1
fi

echo "[fictibet] ingestao canonical NDJSON (${SOURCE_SYSTEM})"
CANONICAL_RESPONSE="$(curl -sS -X POST "${API_URL}/ingest/file" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "source_system=${SOURCE_SYSTEM}" \
  -F "file=@${DATASET_DIR}/01-fictibet-canonical-events.ndjson;type=application/x-ndjson")"
CANONICAL_JOB_ID="$(json_get "${CANONICAL_RESPONSE}" "job_id")"
echo "  job canonical: ${CANONICAL_JOB_ID}"
CANONICAL_FINAL="$(wait_job_terminal "${TOKEN}" "${CANONICAL_JOB_ID}")"
echo "  status canonical final:"
print_json "${CANONICAL_FINAL}"

echo "[fictibet] parse ConnectorGamma (XML)"
GAMMA_RESPONSE="$(curl -sS -X POST "${API_URL}/ingest/connectors/gamma/parse" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "entity_type=TRANSACTION" \
  -F "file=@${DATASET_DIR}/02-fictibet-connector-gamma.xml;type=application/xml")"
GAMMA_JOB_ID="$(json_get "${GAMMA_RESPONSE}" "job_id")"
echo "  job gamma: ${GAMMA_JOB_ID}"
echo "  resumo gamma:"
print_json "${GAMMA_RESPONSE}"

echo "[fictibet] parse ConnectorDelta (NDJSON)"
DELTA_RESPONSE="$(curl -sS -X POST "${API_URL}/ingest/connectors/delta/parse" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "entity_type=TRANSACTION" \
  -F "file=@${DATASET_DIR}/03-fictibet-connector-delta.ndjson;type=application/x-ndjson")"
DELTA_JOB_ID="$(json_get "${DELTA_RESPONSE}" "job_id")"
echo "  job delta: ${DELTA_JOB_ID}"
echo "  resumo delta:"
print_json "${DELTA_RESPONSE}"

echo "[fictibet] webhook ConnectorEpsilon (HMAC)"
HEADERS_FILE="/tmp/fictibet_epsilon_headers_$$.env"
"${DATASET_DIR}/04-sign-epsilon.sh" "${EPSILON_WEBHOOK_SECRET}" "${DATASET_DIR}/04-fictibet-connector-epsilon-webhook.json" "${HEADERS_FILE}"
# shellcheck disable=SC1090
source "${HEADERS_FILE}"

EPSILON_RESPONSE="$(curl -sS -X POST "${API_URL}/ingest/webhook/epsilon" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "content-type: application/json" \
  -H "x-epsilon-timestamp: ${X_EPSILON_TIMESTAMP}" \
  -H "x-epsilon-signature: ${X_EPSILON_SIGNATURE}" \
  --data-binary "@${DATASET_DIR}/04-fictibet-connector-epsilon-webhook.json")"
EPSILON_JOB_ID="$(json_get "${EPSILON_RESPONSE}" "job_id")"
echo "  job epsilon: ${EPSILON_JOB_ID}"
echo "  resumo epsilon:"
print_json "${EPSILON_RESPONSE}"

echo "[fictibet] status final dos jobs (job detail)"
for job_id in "${CANONICAL_JOB_ID}" "${GAMMA_JOB_ID}" "${DELTA_JOB_ID}" "${EPSILON_JOB_ID}"; do
  if [[ -z "${job_id}" ]]; then
    continue
  fi
  JOB_DETAIL="$(curl -sS "${API_URL}/ingest/jobs/${job_id}" -H "Authorization: Bearer ${TOKEN}")"
  echo "  job ${job_id}:"
  print_json "${JOB_DETAIL}"
done

echo "[fictibet] ok: pack ingerido com sucesso"
