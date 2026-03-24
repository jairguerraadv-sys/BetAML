#!/usr/bin/env python3
"""
Script para ingestão de dados de teste do BetAML.

Uso:
    python scripts/ingest_test_data.py --scenario structuring --format all
    python scripts/ingest_test_data.py --scenario spike
    python scripts/ingest_test_data.py --all
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

# Config
BASE_URL = os.getenv("BETAML_API_URL", "http://localhost:8000")
API_KEY = os.getenv("BETAML_API_KEY", "betaml_v2_test_key_dummy")
TENANT_ID = os.getenv("BETAML_TENANT_ID", "operator-a")
TEST_DATA_DIR = Path(__file__).parent.parent / "test_data"

# Cores para output
class Colors:
    OK = "\033[92m"
    WARN = "\033[93m"
    FAIL = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

def log_info(msg: str):
    print(f"{Colors.BOLD}{msg}{Colors.RESET}")

def log_success(msg: str):
    print(f"{Colors.OK}✓ {msg}{Colors.RESET}")

def log_error(msg: str):
    print(f"{Colors.FAIL}✗ {msg}{Colors.RESET}")

def log_warn(msg: str):
    print(f"{Colors.WARN}⚠ {msg}{Colors.RESET}")

class BetAMLIngestor:
    def __init__(self, base_url: str, api_key: str, tenant_id: str):
        self.base_url = base_url
        self.api_key = api_key
        self.tenant_id = tenant_id
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
        })
        self.stats = {"success": 0, "failed": 0, "skipped": 0}

    def health_check(self) -> bool:
        """Verifica se API está respondendo."""
        try:
            resp = self.session.get(f"{self.base_url}/health/ready", timeout=5)
            if resp.status_code == 200:
                log_success(f"API healthy at {self.base_url}")
                return True
            else:
                log_error(f"API returned {resp.status_code}")
                return False
        except Exception as e:
            log_error(f"Cannot reach API: {e}")
            return False

    def ingest_file(self, file_path: Path, source_system: str) -> bool:
        """Ingere arquivo via POST /ingest/file."""
        if not file_path.exists():
            log_error(f"File not found: {file_path}")
            return False

        file_ext = file_path.suffix.lower()
        content_type = {
            ".xml": "application/xml",
            ".json": "application/json",
            ".ndjson": "application/x-ndjson",
            ".csv": "text/csv",
        }.get(file_ext, "application/octet-stream")

        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, content_type)}
                data = {
                    "source_system": source_system,
                }

                log_info(f"Ingestando {file_path.name} como {source_system}...")
                resp = self.session.post(
                    f"{self.base_url}/ingest/file",
                    files=files,
                    data=data,
                    timeout=30,
                )

                if resp.status_code in [200, 202]:
                    result = resp.json()
                    job_id = result.get("id") or result.get("job_id")
                    status = result.get("status", "PROCESSING")
                    log_success(
                        f"Ingestão iniciada: job_id={job_id}, status={status}"
                    )
                    self.stats["success"] += 1
                    return True
                else:
                    log_error(f"Ingest failed: {resp.status_code}")
                    log_error(f"  Response: {resp.text[:200]}")
                    self.stats["failed"] += 1
                    return False

        except Exception as e:
            log_error(f"Error: {e}")
            self.stats["failed"] += 1
            return False

    def ingest_scenario(self, scenario: str) -> None:
        """Ingere todos os arquivos de um cenário."""
        log_info(f"\n{'='*60}")
        log_info(f"SCENARIO: {scenario.upper()}")
        log_info(f"{'='*60}")

        scenario_files = {
            "structuring": [
                ("transactions/structuring_gamma.xml", "ConnectorGamma"),
                ("transactions/structuring_delta.ndjson", "ConnectorDelta"),
            ],
            "spike": [
                ("transactions/spike_backoffice.json", "BackofficeAlpha"),
            ],
            "network": [
                ("transactions/network_combined.ndjson", "ConnectorDelta"),
            ],
            "normal": [
                ("transactions/legit_sample.csv", "BackofficeAlpha"),
                ("bets/sports_bets.json", "SportsBook"),
            ],
            "all": [
                ("transactions/structuring_gamma.xml", "ConnectorGamma"),
                ("transactions/structuring_delta.ndjson", "ConnectorDelta"),
                ("transactions/spike_backoffice.json", "BackofficeAlpha"),
                ("transactions/network_combined.ndjson", "ConnectorDelta"),
                ("transactions/legit_sample.csv", "BackofficeAlpha"),
                ("bets/sports_bets.json", "SportsBook"),
            ],
        }

        files = scenario_files.get(scenario, [])
        if not files:
            log_warn(f"Scenario '{scenario}' not found")
            return

        for file_rel, source_system in files:
            file_path = TEST_DATA_DIR / file_rel
            self.ingest_file(file_path, source_system)

    def wait_for_processing(self, timeout_seconds: int = 30) -> None:
        """Aguarda que eventos sejam processados."""
        log_info(f"\nAguardando processamento (máx {timeout_seconds}s)...")

        start = datetime.now()
        while (datetime.now() - start).total_seconds() < timeout_seconds:
            try:
                resp = self.session.get(f"{self.base_url}/ingest/jobs", timeout=5)
                if resp.status_code == 200:
                    jobs = resp.json().get("items", [])
                    processing = [j for j in jobs if j["status"] in ["QUEUED", "PROCESSING"]]
                    if not processing:
                        log_success("Todos os jobs completaram!")
                        return
                    else:
                        log_info(f"  {len(processing)} jobs ainda processando...")
            except Exception as e:
                log_warn(f"Wait check failed: {e}")

            import time
            time.sleep(2)

        log_warn("Timeout aguardando processamento")

    def print_summary(self) -> None:
        """Imprime sumário de ingestão."""
        total = self.stats["success"] + self.stats["failed"]
        log_info(f"\n{'='*60}")
        log_info("RESUMO DE INGESTÃO")
        log_info(f"{'='*60}")
        log_success(f"Sucesso: {self.stats['success']}/{total}")
        if self.stats["failed"] > 0:
            log_error(f"Falhas: {self.stats['failed']}/{total}")
        if self.stats["skipped"] > 0:
            log_warn(f"Pulado: {self.stats['skipped']}/{total}")

def main():
    parser = argparse.ArgumentParser(description="Ingerir dados de teste no BetAML")
    parser.add_argument(
        "--scenario",
        choices=["structuring", "spike", "network", "normal", "all"],
        default="all",
        help="Cenário de teste",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Ingerir todos os cenários",
    )
    parser.add_argument(
        "--api-url",
        default=BASE_URL,
        help="URL da API BetAML",
    )
    parser.add_argument(
        "--tenant-id",
        default=TENANT_ID,
        help="Tenant ID",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=30,
        help="Segundos para aguardar processamento",
    )

    args = parser.parse_args()

    log_info("="*60)
    log_info("BetAML Test Data Ingestion")
    log_info("="*60)
    log_info(f"API URL: {args.api_url}")
    log_info(f"Tenant ID: {args.tenant_id}")

    ingestor = BetAMLIngestor(args.api_url, API_KEY, args.tenant_id)

    # Health check
    if not ingestor.health_check():
        log_error("Aborting: API not healthy")
        sys.exit(1)

    # Ingest scenarios
    scenarios = ["structuring", "spike", "network", "normal"] if args.all else [args.scenario]

    for scenario in scenarios:
        ingestor.ingest_scenario(scenario)

    # Wait for processing
    if args.wait > 0:
        ingestor.wait_for_processing(args.wait)

    # Summary
    ingestor.print_summary()

    sys.exit(0 if ingestor.stats["failed"] == 0 else 1)

if __name__ == "__main__":
    main()
