#!/usr/bin/env bash
set -euo pipefail

SECRET="${1:-dev-secret-change-me}"
PAYLOAD_FILE="${2:-datasets/fictibet_pld/04-fictibet-connector-epsilon-webhook.json}"
OUT_FILE="${3:-/tmp/epsilon_headers.env}"
TIMESTAMP="${4:-$(date +%s)}"

if [[ ! -f "${PAYLOAD_FILE}" ]]; then
  echo "Payload file not found: ${PAYLOAD_FILE}" >&2
  exit 1
fi

# Preserve payload bytes exactly as they will be sent via --data-binary.
# Using command substitution would strip trailing newlines and break signature checks.
SIGNATURE_HEX="$(
  {
    printf '%s.' "${TIMESTAMP}"
    cat "${PAYLOAD_FILE}"
  } | openssl dgst -sha256 -hmac "${SECRET}" -hex | awk '{print $2}'
)"

cat > "${OUT_FILE}" <<EOF
X_EPSILON_TIMESTAMP=${TIMESTAMP}
X_EPSILON_SIGNATURE=sha256=${SIGNATURE_HEX}
EOF

echo "Headers saved to ${OUT_FILE}"
echo "x-epsilon-timestamp: ${TIMESTAMP}"
echo "x-epsilon-signature: sha256=${SIGNATURE_HEX}"
