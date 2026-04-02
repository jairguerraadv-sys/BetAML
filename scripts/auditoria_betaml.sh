#!/usr/bin/env bash
# =============================================================================
# auditoria_betaml.sh — Auditoria rápida pós-deploy BetAML (~2 min)
#
# Cobre o caminho completo de um analista PLD:
#   health → frontend → login → ingestão CSV → alertas → auto-case
#   → compatibilidade renda/volume → ReportPackage JSON/XML COAF
#   → isolamento de tenant
#
# Uso:
#   bash scripts/auditoria_betaml.sh
#
# Variáveis de ambiente (todas têm padrão):
#   API_URL          — http://localhost:8000
#   FRONTEND_URL     — http://localhost:3000
#   ADMIN_A_USER     — admin_a         (tenant operador_a)
#   ADMIN_A_PASS     — admin123
#   ADMIN_B_USER     — admin_b         (tenant operador_b)
#   ADMIN_B_PASS     — admin123
#   TENANT_A_SLUG    — operador_a
#   TENANT_B_SLUG    — operador_b
#   INGEST_CSV       — tests/data/transacoes_reais_exemplo.csv
#   AUDIT_LOG        — /tmp/auditoria_betaml_<timestamp>.log
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

API_URL="${API_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
ADMIN_A_USER="${ADMIN_A_USER:-admin_a}"
ADMIN_A_PASS="${ADMIN_A_PASS:-admin123}"
ADMIN_B_USER="${ADMIN_B_USER:-admin_b}"
ADMIN_B_PASS="${ADMIN_B_PASS:-admin123}"
TENANT_A_SLUG="${TENANT_A_SLUG:-operador_a}"
TENANT_B_SLUG="${TENANT_B_SLUG:-operador_b}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INGEST_CSV="${INGEST_CSV:-${ROOT_DIR}/tests/data/transacoes_reais_exemplo.csv}"
AUDIT_LOG="${AUDIT_LOG:-/tmp/auditoria_betaml_$(date +%Y%m%d_%H%M%S).log}"

FAIL_COUNT=0

# ── Helpers ───────────────────────────────────────────────────────────────────
log()    { echo -e "$*" | tee -a "${AUDIT_LOG}"; }
ok()     { log "${GREEN}[OK]${NC}   $*"; }
fail()   { log "${RED}[FAIL]${NC} $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn()   { log "${YELLOW}[WARN]${NC} $*"; }
check()  { log "${BLUE}→ Checando${NC} $*"; }
section(){ log ""; log "──────────────────────────────────────────────────"; log "  $*"; log "──────────────────────────────────────────────────"; }

require_cmd() {
  for cmd in "$@"; do
    if ! command -v "${cmd}" >/dev/null 2>&1; then
      log "${RED}Dependência ausente: ${cmd}. Instale e reexecute.${NC}"
      exit 1
    fi
  done
}

require_cmd curl jq

# ── Garante CSV de ingestão ───────────────────────────────────────────────────
ensure_csv() {
  if [[ ! -f "${INGEST_CSV}" ]]; then
    warn "CSV não encontrado em ${INGEST_CSV} — gerando arquivo sintético"
    mkdir -p "$(dirname "${INGEST_CSV}")"
    cat > "${INGEST_CSV}" << 'CSV'
transaction_id,account_id,player_id,amount,currency,merchant,country,timestamp
TXN-AUD-001,ACC-100,PLY-001,5000.00,BRL,Casino Alpha,BR,2026-01-10T10:00:00Z
TXN-AUD-002,ACC-100,PLY-001,8000.00,BRL,Casino Alpha,BR,2026-01-10T10:05:00Z
TXN-AUD-003,ACC-101,PLY-002,12000.00,BRL,Betfair,BR,2026-01-10T11:00:00Z
TXN-AUD-004,ACC-101,PLY-002,15000.00,BRL,Betfair,BR,2026-01-10T11:05:00Z
TXN-AUD-005,ACC-102,PLY-003,500.00,BRL,Casino Alpha,BR,2026-01-10T12:00:00Z
TXN-AUD-006,ACC-103,PLY-004,75000.00,BRL,OverseasBet,PY,2026-01-10T13:00:00Z
TXN-AUD-007,ACC-104,PLY-005,2000.00,BRL,CasinoLocal,BR,2026-01-10T14:00:00Z
TXN-AUD-008,ACC-105,PLY-006,3500.00,BRL,Casino Alpha,BR,2026-01-10T15:00:00Z
TXN-AUD-009,ACC-105,PLY-006,4000.00,BRL,Casino Alpha,BR,2026-01-10T15:30:00Z
TXN-AUD-010,ACC-106,PLY-007,20000.00,BRL,PokerRoom,UY,2026-01-10T16:00:00Z
CSV
    ok "CSV sintético criado em ${INGEST_CSV}"
  else
    ok "CSV encontrado: ${INGEST_CSV}"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────

log ""
log "╔══════════════════════════════════════════════════════════╗"
log "║       BetAML – Auditoria Rápida Pós-Deploy               ║"
log "║       $(date '+%Y-%m-%d %H:%M:%S')                               ║"
log "╚══════════════════════════════════════════════════════════╝"
log "Log: ${AUDIT_LOG}"

# =============================================================================
section "PASSO 1 — Health da API"
# =============================================================================
check "health da API (readiness)"

HEALTH_RESP=$(curl -s "${API_URL}/health/ready" 2>/dev/null || echo "{}")
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${API_URL}/health/ready" 2>/dev/null || echo "000")

if [[ "${HTTP_STATUS}" == "200" ]]; then
  ok "Health API — HTTP ${HTTP_STATUS}"
  for dep in postgres redis kafka clickhouse minio; do
    check "dependência: ${dep}"
    DEP_STATUS=$(echo "${HEALTH_RESP}" | jq -r ".checks.${dep} // .${dep} // \"unknown\"" 2>/dev/null || echo "unknown")
    if [[ "${DEP_STATUS}" == "ok" || "${DEP_STATUS}" == "healthy" ]]; then
      ok "${dep}: ${DEP_STATUS}"
    else
      warn "${dep}: ${DEP_STATUS} (pode ser opcional)"
    fi
  done
else
  fail "Health API retornou HTTP ${HTTP_STATUS} (esperado: 200)"
fi

# =============================================================================
section "PASSO 2 — Frontend"
# =============================================================================
check "frontend em ${FRONTEND_URL}/"

FRONT_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${FRONTEND_URL}/" 2>/dev/null || echo "000")
if [[ "${FRONT_CODE}" =~ ^(200|301|302)$ ]]; then
  ok "Frontend acessível — HTTP ${FRONT_CODE}"
else
  warn "Frontend retornou HTTP ${FRONT_CODE} (pode estar desabilitado neste ambiente)"
fi

# =============================================================================
section "PASSO 3 — Login Tenant A"
# =============================================================================
check "login tenant A (${ADMIN_A_USER} / ${TENANT_A_SLUG})"

LOGIN_A_RESP=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${ADMIN_A_USER}\",\"password\":\"${ADMIN_A_PASS}\",\"tenant_slug\":\"${TENANT_A_SLUG}\"}" \
  "${API_URL}/auth/login" 2>/dev/null || echo "{}")

TOKEN_A=$(echo "${LOGIN_A_RESP}" | jq -r '.access_token // empty' 2>/dev/null || echo "")
if [[ -z "${TOKEN_A}" ]]; then
  # fallback: login sem tenant_slug (compatibilidade)
  LOGIN_A_RESP=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${ADMIN_A_USER}\",\"password\":\"${ADMIN_A_PASS}\"}" \
    "${API_URL}/auth/login" 2>/dev/null || echo "{}")
  TOKEN_A=$(echo "${LOGIN_A_RESP}" | jq -r '.access_token // empty' 2>/dev/null || echo "")
fi

if [[ -n "${TOKEN_A}" ]]; then
  ok "Login tenant A — token obtido (${TOKEN_A:0:16}...)"
else
  fail "Login tenant A falhou — resposta: ${LOGIN_A_RESP:0:200}"
  TOKEN_A=""
fi

# =============================================================================
section "PASSO 4 — Login Tenant B"
# =============================================================================
check "login tenant B (${ADMIN_B_USER} / ${TENANT_B_SLUG})"

LOGIN_B_RESP=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"${ADMIN_B_USER}\",\"password\":\"${ADMIN_B_PASS}\",\"tenant_slug\":\"${TENANT_B_SLUG}\"}" \
  "${API_URL}/auth/login" 2>/dev/null || echo "{}")

TOKEN_B=$(echo "${LOGIN_B_RESP}" | jq -r '.access_token // empty' 2>/dev/null || echo "")
if [[ -z "${TOKEN_B}" ]]; then
  LOGIN_B_RESP=$(curl -s -X POST \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${ADMIN_B_USER}\",\"password\":\"${ADMIN_B_PASS}\"}" \
    "${API_URL}/auth/login" 2>/dev/null || echo "{}")
  TOKEN_B=$(echo "${LOGIN_B_RESP}" | jq -r '.access_token // empty' 2>/dev/null || echo "")
fi

if [[ -n "${TOKEN_B}" ]]; then
  ok "Login tenant B — token obtido (${TOKEN_B:0:16}...)"
else
  fail "Login tenant B falhou — resposta: ${LOGIN_B_RESP:0:200}"
  TOKEN_B=""
fi

# =============================================================================
section "PASSO 5 — Ingestão de CSV"
# =============================================================================
ensure_csv

check "upload CSV via /ingest/file (source_system=BackofficeAlpha)"

if [[ -z "${TOKEN_A}" ]]; then
  fail "Token A ausente — pulando ingestão"
  JOB_ID=""
else
  INGEST_RESP=$(curl -s -X POST \
    -H "Authorization: Bearer ${TOKEN_A}" \
    -F "file=@${INGEST_CSV}" \
    -F "source_system=BackofficeAlpha" \
    "${API_URL}/ingest/file" 2>/dev/null || echo "{}")
  JOB_ID=$(echo "${INGEST_RESP}" | jq -r '.job_id // .id // empty' 2>/dev/null || echo "")
  if [[ -n "${JOB_ID}" ]]; then
    ok "Ingestão iniciada — job_id: ${JOB_ID}"
  else
    fail "Ingestão falhou — resposta: ${INGEST_RESP:0:300}"
    JOB_ID=""
  fi
fi

# =============================================================================
section "PASSO 6 — Polling do Job de Ingestão (12×5s)"
# =============================================================================
JOB_STATUS=""
if [[ -n "${JOB_ID}" ]]; then
  check "status do job ${JOB_ID} (máx 12 tentativas × 5s)"
  tries=0
  max_tries=12
  while [[ $tries -lt $max_tries ]]; do
    sleep 5
    tries=$((tries + 1))
    JOB_RESP=$(curl -s \
      -H "Authorization: Bearer ${TOKEN_A}" \
      "${API_URL}/ingest/jobs/${JOB_ID}" 2>/dev/null || echo "{}")
    JOB_STATUS=$(echo "${JOB_RESP}" | jq -r '.status // empty' 2>/dev/null || echo "")
    log "   tentativa ${tries}/${max_tries} → status: ${JOB_STATUS:-desconhecido}"
    if [[ "${JOB_STATUS}" == "COMPLETED" || "${JOB_STATUS}" == "DONE" ]]; then
      ok "Job concluído com sucesso (${JOB_STATUS})"
      break
    elif [[ "${JOB_STATUS}" == "FAILED" || "${JOB_STATUS}" == "ERROR" ]]; then
      fail "Job falhou (${JOB_STATUS}) — ${JOB_RESP:0:200}"
      break
    fi
  done
  if [[ "${JOB_STATUS}" != "COMPLETED" && "${JOB_STATUS}" != "DONE" && "${JOB_STATUS}" != "FAILED" && "${JOB_STATUS}" != "ERROR" ]]; then
    fail "Job não concluiu em 60s — último status: ${JOB_STATUS}"
  fi
else
  warn "Sem job_id — passo de polling ignorado"
fi

# =============================================================================
section "PASSO 7 — Alertas com composite_score"
# =============================================================================
check "alertas OPEN (Bearer TOKEN_A)"

if [[ -z "${TOKEN_A}" ]]; then
  fail "Token A ausente — pulando verificação de alertas"
else
  ALERTS_RESP=$(curl -s \
    -H "Authorization: Bearer ${TOKEN_A}" \
    "${API_URL}/alerts?status=OPEN&limit=20" 2>/dev/null || echo "[]")
  ALERT_COUNT=$(echo "${ALERTS_RESP}" | jq 'if type=="array" then length elif .items then (.items|length) else 0 end' 2>/dev/null || echo "0")
  if [[ "${ALERT_COUNT}" -gt 0 ]]; then
    ok "Alertas OPEN encontrados: ${ALERT_COUNT}"
    # verifica composite_score
    check "composite_score presente em ≥1 alerta"
    SCORED=$(echo "${ALERTS_RESP}" | jq '[if type=="array" then .[] else .items[] end | select(.composite_score != null)] | length' 2>/dev/null || echo "0")
    if [[ "${SCORED}" -gt 0 ]]; then
      ok "composite_score presente em ${SCORED} alerta(s)"
    else
      warn "Nenhum alerta com composite_score — ML pode estar inicializando"
    fi
  else
    warn "Nenhum alerta OPEN encontrado — ingestão pode estar pendente"
  fi
fi

# =============================================================================
section "PASSO 8 — Auto-case para alerta CRITICAL"
# =============================================================================
check "alertas com severity=CRITICAL"

if [[ -z "${TOKEN_A}" ]]; then
  fail "Token A ausente — pulando verificação de auto-case"
else
  CRIT_RESP=$(curl -s \
    -H "Authorization: Bearer ${TOKEN_A}" \
    "${API_URL}/alerts?severity=CRITICAL&limit=1" 2>/dev/null || echo "[]")
  CRIT_PLAYER=$(echo "${CRIT_RESP}" | jq -r '[if type=="array" then .[] else .items[] end | .player_id] | first // empty' 2>/dev/null || echo "")

  if [[ -n "${CRIT_PLAYER}" ]]; then
    ok "Alerta CRITICAL encontrado (player_id=${CRIT_PLAYER})"
    check "caso OPEN auto-criado para player ${CRIT_PLAYER}"
    CASES_RESP=$(curl -s \
      -H "Authorization: Bearer ${TOKEN_A}" \
      "${API_URL}/cases?player_id=${CRIT_PLAYER}&status=OPEN" 2>/dev/null || echo "[]")
    CASE_COUNT=$(echo "${CASES_RESP}" | jq 'if type=="array" then length elif .items then (.items|length) else 0 end' 2>/dev/null || echo "0")
    if [[ "${CASE_COUNT}" -gt 0 ]]; then
      ok "Caso auto-criado encontrado para o player (${CASE_COUNT} caso(s))"
    else
      fail "Nenhum caso OPEN para player ${CRIT_PLAYER} — auto-case não disparou"
    fi
  else
    warn "Nenhum alerta CRITICAL disponível — regra de auto-case não verificada"
  fi
fi

# =============================================================================
section "PASSO 9 — Compatibilidade Renda/Volume (financial-profile)"
# =============================================================================
check "lista de players (limit=1)"

PLAYER_ID=""
if [[ -z "${TOKEN_A}" ]]; then
  fail "Token A ausente — pulando verificação de perfil financeiro"
else
  PLAYERS_RESP=$(curl -s \
    -H "Authorization: Bearer ${TOKEN_A}" \
    "${API_URL}/players?limit=1" 2>/dev/null || echo "[]")
  PLAYER_ID=$(echo "${PLAYERS_RESP}" | jq -r '[if type=="array" then .[] else .items[] end | .id] | first // empty' 2>/dev/null || echo "")

  if [[ -n "${PLAYER_ID}" ]]; then
    ok "Player encontrado: ${PLAYER_ID}"
    check "perfil financeiro do player ${PLAYER_ID}"
    # Tenta /financial-profile; se 404, usa /econ-compat (alias real na codebase)
    PROFILE_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -H "Authorization: Bearer ${TOKEN_A}" \
      "${API_URL}/players/${PLAYER_ID}/financial-profile" 2>/dev/null || echo "000")
    if [[ "${PROFILE_CODE}" == "200" ]]; then
      PROFILE_RESP=$(curl -s \
        -H "Authorization: Bearer ${TOKEN_A}" \
        "${API_URL}/players/${PLAYER_ID}/financial-profile" 2>/dev/null || echo "{}")
      COMPAT_STATUS=$(echo "${PROFILE_RESP}" | jq -r '.income_compat.status // .status // empty' 2>/dev/null || echo "")
      COMPAT_RATIO=$(echo "${PROFILE_RESP}" | jq -r '.income_compat.ratio // .ratio // empty' 2>/dev/null || echo "")
      if [[ -n "${COMPAT_STATUS}" ]]; then
        ok "income_compat.status=${COMPAT_STATUS}  ratio=${COMPAT_RATIO}"
      else
        warn "income_compat ausente no perfil — verificar implementação"
      fi
    else
      # fallback: endpoint inline no GET /players/{id}
      PLAYER_DETAIL=$(curl -s \
        -H "Authorization: Bearer ${TOKEN_A}" \
        "${API_URL}/players/${PLAYER_ID}" 2>/dev/null || echo "{}")
      COMPAT_STATUS=$(echo "${PLAYER_DETAIL}" | jq -r '.income_compat.status // empty' 2>/dev/null || echo "")
      if [[ -n "${COMPAT_STATUS}" ]]; then
        ok "income_compat inline em /players/{id} — status=${COMPAT_STATUS}"
      else
        warn "HTTP ${PROFILE_CODE} em /financial-profile e income_compat ausente no perfil direto"
      fi
    fi
  else
    warn "Nenhum player disponível — passo de perfil financeiro ignorado"
  fi
fi

# =============================================================================
section "PASSO 10 — ReportPackage JSON + COAF-XML"
# =============================================================================
# ReportPackage JSON precisa de qualquer caso; COAF-XML exige CLOSED ou REPORTED.
# Tentamos CLOSED primeiro; se não existir, usa OPEN apenas para o JSON.
check "lista de casos (CLOSED preferencialmente)"

CASE_ID=""
CASE_ID_OPEN=""
PKG_ID=""
if [[ -z "${TOKEN_A}" ]]; then
  fail "Token A ausente — pulando verificação de ReportPackage"
else
  CASES_CLOSED=$(curl -s \
    -H "Authorization: Bearer ${TOKEN_A}" \
    "${API_URL}/cases?status_filter=CLOSED&limit=1" 2>/dev/null || echo "[]")
  CASE_ID=$(echo "${CASES_CLOSED}" | jq -r '[if type=="array" then .[] else .items[] end | .id] | first // empty' 2>/dev/null || echo "")

  if [[ -z "${CASE_ID}" ]]; then
    # fallback: usa OPEN (JSON OK, COAF-XML vai falhar com aviso)
    CASES_OPEN=$(curl -s \
      -H "Authorization: Bearer ${TOKEN_A}" \
      "${API_URL}/cases?status_filter=OPEN&limit=1" 2>/dev/null || echo "[]")
    CASE_ID=$(echo "${CASES_OPEN}" | jq -r '[if type=="array" then .[] else .items[] end | .id] | first // empty' 2>/dev/null || echo "")
    CASE_ID_OPEN="${CASE_ID}"
    warn "Nenhum caso CLOSED — usando caso OPEN (COAF-XML pode exigir CLOSED)"
  fi

  if [[ -n "${CASE_ID}" ]]; then
    ok "Caso encontrado: ${CASE_ID}"
    check "criação de ReportPackage para caso ${CASE_ID}"
    NARRATIVE="Auditoria automatizada pós-deploy BetAML. Transações com padrão suspeito identificadas pelo sistema ML."
    INFO_ADICIONAL="Padrão identificado pelo motor de regras BetAML. Comunicado Siscoaf 97. Portaria SPA/MF 1.143/2024."
    PKG_BODY="{\"decision\":\"FILE_SAR\",\"analyst_narrative\":\"${NARRATIVE}\",\"informacoes_adicionais\":\"${INFO_ADICIONAL}\",\"occurrence_codes\":[1407],\"involvement_types\":[1]}"

    # Tenta rota plural (/report-packages) primeiro; fallback para singular
    PKG_RESP=$(curl -s -X POST \
      -H "Authorization: Bearer ${TOKEN_A}" \
      -H "Content-Type: application/json" \
      -d "${PKG_BODY}" \
      "${API_URL}/cases/${CASE_ID}/report-packages" 2>/dev/null || echo "{}")
    PKG_ID=$(echo "${PKG_RESP}" | jq -r '.report_package_id // .id // .package_id // empty' 2>/dev/null || echo "")

    if [[ -z "${PKG_ID}" ]]; then
      # fallback: rota singular (conforme implementação atual)
      PKG_RESP=$(curl -s -X POST \
        -H "Authorization: Bearer ${TOKEN_A}" \
        -H "Content-Type: application/json" \
        -d "${PKG_BODY}" \
        "${API_URL}/cases/${CASE_ID}/report-package" 2>/dev/null || echo "{}")
      PKG_ID=$(echo "${PKG_RESP}" | jq -r '.report_package_id // .id // .package_id // empty' 2>/dev/null || echo "")
    fi

    if [[ -n "${PKG_ID}" ]]; then
      ok "ReportPackage criado — pkg_id: ${PKG_ID}"

      # ── JSON ─────────────────────────────────────────────────────────────
      # Rota real: GET /cases/{id}/report-package/json?rp_id=<pkg_id>
      check "GET ReportPackage JSON (pkg_id=${PKG_ID})"
      JSON_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${TOKEN_A}" \
        "${API_URL}/cases/${CASE_ID}/report-package/json?rp_id=${PKG_ID}" 2>/dev/null || echo "000")
      if [[ "${JSON_CODE}" == "200" ]]; then
        ok "ReportPackage JSON — HTTP ${JSON_CODE}"
      else
        fail "ReportPackage JSON retornou HTTP ${JSON_CODE}"
      fi

      # ── COAF-XML ──────────────────────────────────────────────────────────
      # Rota real: GET /cases/{id}/report-package/coaf-xml?rp_id=<pkg_id>
      check "GET ReportPackage COAF-XML (pkg_id=${PKG_ID})"
      XML_RESP=$(curl -s -w "\n%{http_code}\n%{content_type}" \
        -H "Authorization: Bearer ${TOKEN_A}" \
        "${API_URL}/cases/${CASE_ID}/report-package/coaf-xml?rp_id=${PKG_ID}" 2>/dev/null || echo "")
      XML_CODE=$(echo "${XML_RESP}" | tail -2 | head -1)
      XML_CT=$(echo "${XML_RESP}" | tail -1)
      if [[ "${XML_CODE}" == "200" ]]; then
        if echo "${XML_CT}" | grep -qi "xml"; then
          ok "COAF-XML — HTTP ${XML_CODE}, Content-Type: ${XML_CT}"
        else
          warn "COAF-XML HTTP 200 mas Content-Type inesperado: ${XML_CT}"
        fi
      else
        fail "COAF-XML retornou HTTP ${XML_CODE}"
      fi
    else
      fail "Criação de ReportPackage falhou — resposta: ${PKG_RESP:0:200}"
    fi
  else
    warn "Nenhum caso OPEN disponível — passo de ReportPackage ignorado"
  fi
fi

# =============================================================================
section "PASSO 11 — Isolamento de Tenant (RLS)"
# =============================================================================
check "isolamento entre tenant A e tenant B"

if [[ -z "${TOKEN_A}" || -z "${TOKEN_B}" ]]; then
  warn "Tokens insuficientes — passo de isolamento não verificado"
else
  PLAYER_A=$(curl -s \
    -H "Authorization: Bearer ${TOKEN_A}" \
    "${API_URL}/players?limit=1" 2>/dev/null \
    | jq -r '[if type=="array" then .[] else .items[] end | .id|tostring] | first // "NONE"' 2>/dev/null || echo "NONE")
  PLAYER_B=$(curl -s \
    -H "Authorization: Bearer ${TOKEN_B}" \
    "${API_URL}/players?limit=1" 2>/dev/null \
    | jq -r '[if type=="array" then .[] else .items[] end | .id|tostring] | first // "NONE"' 2>/dev/null || echo "NONE")

  if [[ "${PLAYER_A}" == "NONE" || "${PLAYER_B}" == "NONE" ]]; then
    warn "Um dos tenants sem players cadastrados — isolamento não verificável com dados atuais"
  elif [[ "${PLAYER_A}" != "${PLAYER_B}" ]]; then
    ok "Isolamento de tenant validado — player_A=${PLAYER_A}  player_B=${PLAYER_B} (IDs distintos)"
  else
    fail "POSSÍVEL VAZAMENTO DE TENANT — tenant A e B retornaram mesmo player_id: ${PLAYER_A}"
  fi
fi

# =============================================================================
log ""
log "╔══════════════════════════════════════════════════════════╗"
if [[ "${FAIL_COUNT}" -eq 0 ]]; then
  log "║  ✅  ===== Auditoria rápida concluída com SUCESSO =====  ║"
else
  log "║  ❌  ===== Auditoria concluída com ${FAIL_COUNT} FALHA(S) =====        ║"
fi
log "╚══════════════════════════════════════════════════════════╝"
log "Log completo: ${AUDIT_LOG}"
log ""

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  exit 1
fi
