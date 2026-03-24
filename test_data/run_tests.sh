#!/bin/bash
# BetAML E2E Test Runner — Executa suite completa de testes realistas

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}ℹ ${1}${NC}"
}

log_success() {
    echo -e "${GREEN}✓ ${1}${NC}"
}

log_warn() {
    echo -e "${YELLOW}⚠ ${1}${NC}"
}

log_error() {
    echo -e "${RED}✗ ${1}${NC}"
}

log_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}${1}${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# Parse arguments
SCENARIO=${1:-all}
WAIT_TIME=${2:-45}

log_header "BetAML End-to-End Test Suite"
log_info "Scenario: $SCENARIO"
log_info "Processing timeout: ${WAIT_TIME}s"
log_info "Test data: test_data/"

# Step 1: Health Check
log_header "STEP 1: Health Check"

if ! curl -s -f http://localhost:8000/health/ready > /dev/null 2>&1; then
    log_error "API not responding at http://localhost:8000"
    log_warn "Starting Docker Compose..."
    docker-compose up -d
    log_info "Waiting for API to be ready..."
    sleep 15

    if ! curl -s -f http://localhost:8000/health/ready > /dev/null 2>&1; then
        log_error "API still not ready. Check logs: docker-compose logs api"
        exit 1
    fi
fi

log_success "API is healthy"

# Step 2: Ingest Test Data
log_header "STEP 2: Ingest Test Data"

python scripts/ingest_test_data.py \
    --scenario "$SCENARIO" \
    --wait "$WAIT_TIME" \
    --api-url "http://localhost:8000"

# Step 3: Validate Results
log_header "STEP 3: Validate Results"

python scripts/validate_test_results.py \
    --scenario "$SCENARIO" \
    --api-url "http://localhost:8000"

# Step 4: Generate Report
log_header "STEP 4: Generate Report"

python scripts/generate_test_report.py \
    --output "test_data/results/test_report_${SCENARIO}_$(date +%s).html" \
    --api-url "http://localhost:8000"

# Final Summary
log_header "✓ TEST SUITE COMPLETED"

log_success "Scenario: $SCENARIO"
log_success "Data: test_data/"
log_success "Report: test_data/results/"

log_info ""
log_info "Next steps:"
log_info "  1. View live dashboard: http://localhost:3000 (credentials: admin_a/admin123)"
log_info "  2. Use analyst panel: http://localhost:3000/cases (analyst_a/analyst123)"
log_info "  3. Check audit logs: http://localhost:3000/audit-logs (auditor_a/auditor123)"
log_info "  4. API docs: http://localhost:8000/docs"
log_info ""

exit 0
