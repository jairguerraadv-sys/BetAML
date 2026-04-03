#!/usr/bin/env python3
"""
Script para ingestão do pacote sintético 'fictibet_pld' no BetAML.

Uso:
    python scripts/ingest_fictibet_pld.py               # ingesta tudo
    python scripts/ingest_fictibet_pld.py --only players # só jogadores
    python scripts/ingest_fictibet_pld.py --api-url http://localhost:8000

Requisitos:
    - API BetAML rodando (docker compose up -d)
    - datasets/fictibet_pld/ com os CSVs gerados por generate.py
"""
import argparse
import os
import sys
import time
from pathlib import Path

import requests

BASE_URL = os.getenv("BETAML_API_URL", "http://localhost:8000")
API_KEY = os.getenv("BETAML_API_KEY", "betaml_v2_test_key_dummy")
DATA_DIR = Path(__file__).parent.parent / "datasets" / "fictibet_pld"

GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Ordem importa: players primeiro para cadastro, depois transações, apostas, dispositivos
FILES = [
    ("players.csv",       "text/csv", "FictiBetPLD"),
    ("transactions.csv",  "text/csv", "FictiBetPLD"),
    ("bets.csv",          "text/csv", "FictiBetPLD"),
    ("device_events.csv", "text/csv", "FictiBetPLD"),
]


def ingest_file(session: requests.Session, base_url: str, path: Path,
                content_type: str, source_system: str) -> bool:
    if not path.exists():
        print(f"{RED}✗ Arquivo não encontrado: {path}{RESET}")
        return False

    with open(path, "rb") as f:
        resp = session.post(
            f"{base_url}/ingest/file",
            files={"file": (path.name, f, content_type)},
            data={"source_system": source_system},
            timeout=60,
        )

    if resp.status_code in (200, 202):
        body = resp.json()
        job_id = body.get("id") or body.get("job_id", "?")
        rows = body.get("records_total") or body.get("rows", "?")
        print(f"{GREEN}✓ {path.name}: job={job_id}, rows={rows}{RESET}")
        return True

    print(f"{RED}✗ {path.name}: HTTP {resp.status_code}{RESET}")
    print(f"  {resp.text[:300]}")
    return False


def wait_jobs(session: requests.Session, base_url: str, timeout: int = 60):
    print(f"\n{BOLD}Aguardando processamento (max {timeout}s)...{RESET}")
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            r = session.get(f"{base_url}/ingest/jobs", timeout=5)
            if r.status_code == 200:
                items = r.json().get("items", [])
                pending = [j for j in items if j.get("status") in ("QUEUED", "PROCESSING")]
                if not pending:
                    print(f"{GREEN}✓ Todos os jobs finalizados{RESET}")
                    return
                print(f"  {len(pending)} jobs em processamento...")
        except Exception:
            pass
        time.sleep(3)
    print(f"{RED}⚠ Timeout atingido{RESET}")


def main():
    parser = argparse.ArgumentParser(description="Ingest fictibet_pld synthetic data")
    parser.add_argument("--api-url", default=BASE_URL)
    parser.add_argument("--only", choices=["players", "transactions", "bets", "devices"],
                        help="Ingestar apenas um tipo de entidade")
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({
        "X-API-Key": API_KEY,
        "Authorization": f"Bearer {API_KEY}",
    })

    # Health check
    try:
        r = session.get(f"{args.api_url}/health/ready", timeout=5)
        assert r.status_code == 200
        print(f"{GREEN}✓ API healthy @ {args.api_url}{RESET}")
    except Exception as e:
        print(f"{RED}✗ API inacessível: {e}{RESET}")
        sys.exit(1)

    filter_map = {
        "players": "players.csv",
        "transactions": "transactions.csv",
        "bets": "bets.csv",
        "devices": "device_events.csv",
    }

    ok = 0
    fail = 0
    for fname, ctype, src in FILES:
        if args.only and fname != filter_map.get(args.only):
            continue
        success = ingest_file(session, args.api_url, DATA_DIR / fname, ctype, src)
        if success:
            ok += 1
        else:
            fail += 1

    print(f"\n{BOLD}Resultado: {ok} ok, {fail} falhas{RESET}")

    if not args.no_wait and ok > 0:
        wait_jobs(session, args.api_url)


if __name__ == "__main__":
    main()
