#!/usr/bin/env python3
"""
Script para validar resultados de teste do BetAML.

Valida que os cenários geraram os alertas e features esperados.

Uso:
    python scripts/validate_test_results.py
    python scripts/validate_test_results.py --scenario structuring
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

BASE_URL = os.getenv("BETAML_API_URL", "http://localhost:8000")
API_KEY = os.getenv("BETAML_API_KEY", "betaml_v2_test_key_dummy")
OPERATOR_ID = os.getenv("BETAML_OPERATOR_ID", "operator-a")

class Colors:
    OK = "\033[92m"
    WARN = "\033[93m"
    FAIL = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"

def log_info(msg):
    print(f"{Colors.BOLD}{msg}{Colors.RESET}")

def log_success(msg):
    print(f"{Colors.OK}✓ {msg}{Colors.RESET}")

def log_error(msg):
    print(f"{Colors.FAIL}✗ {msg}{Colors.RESET}")

def log_warn(msg):
    print(f"{Colors.WARN}⚠ {msg}{Colors.RESET}")

class BetAMLValidator:
    def __init__(self, base_url: str, api_key: str, operator_id: str):
        self.base_url = base_url
        self.api_key = api_key
        self.operator_id = operator_id
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-Key": api_key,
            "Authorization": f"Bearer {api_key}",
        })
        self.results = {
            "total_checks": 0,
            "passed": 0,
            "failed": 0,
            "warnings": 0,
        }

    def check(self, condition: bool, message: str, critical: bool = False):
        """Registra um check."""
        self.results["total_checks"] += 1
        if condition:
            self.results["passed"] += 1
            log_success(message)
        else:
            if critical:
                self.results["failed"] += 1
                log_error(message)
            else:
                self.results["warnings"] += 1
                log_warn(message)

    def get_alerts(self, player_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Busca alertas."""
        try:
            params = {"limit": limit}
            if player_id:
                params["player_id"] = player_id

            resp = self.session.get(f"{self.base_url}/alerts", params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("items", [])
            else:
                log_error(f"Failed to fetch alerts: {resp.status_code}")
                return []
        except Exception as e:
            log_error(f"Error fetching alerts: {e}")
            return []

    def get_player_features(self, player_id: str) -> Dict[str, Any]:
        """Busca features de um player."""
        try:
            resp = self.session.get(
                f"{self.base_url}/feature-store/players/{player_id}/latest",
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("features", {})
            else:
                return {}
        except Exception as e:
            log_warn(f"Could not fetch features for {player_id}: {e}")
            return {}

    def validate_scenario(self, scenario: str):
        """Valida um cenário de teste."""
        log_info(f"\n{'='*60}")
        log_info(f"VALIDANDO: {scenario.upper()}")
        log_info(f"{'='*60}")

        if scenario == "structuring":
            self.validate_structuring()
        elif scenario == "spike":
            self.validate_spike()
        elif scenario == "network":
            self.validate_network()
        elif scenario == "normal":
            self.validate_normal()
        else:
            log_warn(f"Scenario '{scenario}' not recognized")

    def validate_structuring(self):
        """Valida cenário de structuring."""
        player_ids = ["PLY-STRUCT-001", "PLY-STRUCT-002"]

        for player_id in player_ids:
            log_info(f"\nCheckando {player_id}...")

            # Buscar alertas
            alerts = self.get_alerts(player_id=player_id)
            structuring_alerts = [
                a for a in alerts
                if "STRUCTURING" in a.get("title", "") or
                   a.get("alert_type") == "STRUCTURING_DETECTED"
            ]

            self.check(
                len(structuring_alerts) >= 1,
                f"Alerta de structuring detectado para {player_id}",
                critical=True,
            )

            # Buscar features
            features = self.get_player_features(player_id)
            self.check(
                features.get("deposit_velocity", 0) > 2.0,
                f"deposit_velocity > 2.0 para {player_id}",
            )
            self.check(
                features.get("structuring_score", 0) > 0.7,
                f"structuring_score > 0.7 para {player_id}",
            )

    def validate_spike(self):
        """Valida cenário de spike."""
        player_id = "PLY-SPIKE-001"
        log_info(f"\nCheckando {player_id}...")

        # Buscar alerta
        alerts = self.get_alerts(player_id=player_id)
        spike_alerts = [
            a for a in alerts
            if "SPIKE" in a.get("title", "") or "ANOMALOUS" in a.get("title", "")
        ]

        self.check(
            len(spike_alerts) >= 1,
            f"Alerta de spike/anomalia detectado para {player_id}",
            critical=True,
        )

        # Buscar features
        features = self.get_player_features(player_id)
        self.check(
            features.get("deposit_sum_24h", 0) > 10000,
            f"deposit_sum_24h > 10K para {player_id}",
        )

    def validate_network(self):
        """Valida cenário de network clustering."""
        player_ids = ["PLY-NETWORK-001", "PLY-NETWORK-002", "PLY-NETWORK-003"]
        log_info(f"\nCheckando cluster de {len(player_ids)} players...")

        cluster_ids = []
        for player_id in player_ids:
            features = self.get_player_features(player_id)
            cluster_id = features.get("cluster_id")
            if cluster_id:
                cluster_ids.append(cluster_id)
                log_success(f"  {player_id}: cluster_id={cluster_id}")
            else:
                log_warn(f"  {player_id}: sem cluster_id")

        # Verificar se todos estão no mesmo cluster
        self.check(
            len(set(cluster_ids)) == 1 and len(cluster_ids) >= 2,
            f"Players conectados no mesmo cluster (size={len(cluster_ids)})",
            critical=True,
        )

        # Verificar shared_device_score
        for player_id in player_ids:
            features = self.get_player_features(player_id)
            self.check(
                features.get("shared_device_score", 0) > 0.5,
                f"shared_device_score > 0.5 para {player_id}",
            )

    def validate_normal(self):
        """Valida cenário normal (sem alertas críticos)."""
        player_ids = ["PLY-NORMAL-001", "PLY-NORMAL-002"]

        for player_id in player_ids:
            log_info(f"\nCheckando {player_id}...")

            # Buscar alertas
            alerts = self.get_alerts(player_id=player_id)
            critical_alerts = [
                a for a in alerts
                if a.get("severity") == "CRITICAL"
            ]

            self.check(
                len(critical_alerts) == 0,
                f"Nenhum alerta CRITICAL para {player_id}",
            )

            # Buscar features
            features = self.get_player_features(player_id)
            risk_score = features.get("risk_score", 0)
            self.check(
                risk_score < 0.5,
                f"risk_score < 0.5 (atual: {risk_score:.2f}) para {player_id}",
            )

    def print_summary(self):
        """Imprime sumário de validação."""
        total = self.results["passed"] + self.results["failed"] + self.results["warnings"]

        log_info(f"\n{'='*60}")
        log_info("RESUMO DE VALIDAÇÃO")
        log_info(f"{'='*60}")

        if total == 0:
            log_warn("Nenhum check foi executado")
            return

        pass_pct = (self.results["passed"] / total) * 100 if total > 0 else 0
        log_success(f"Passed: {self.results['passed']}/{total} ({pass_pct:.1f}%)")

        if self.results["failed"] > 0:
            log_error(f"Failed: {self.results['failed']}/{total}")

        if self.results["warnings"] > 0:
            log_warn(f"Warnings: {self.results['warnings']}/{total}")

def main():
    parser = argparse.ArgumentParser(description="Validar resultados de teste do BetAML")
    parser.add_argument(
        "--scenario",
        choices=["structuring", "spike", "network", "normal", "all"],
        default="all",
        help="Cenário a validar",
    )
    parser.add_argument(
        "--api-url",
        default=BASE_URL,
        help="URL da API BetAML",
    )

    args = parser.parse_args()

    log_info("="*60)
    log_info("BetAML Test Results Validator")
    log_info("="*60)
    log_info(f"API URL: {args.api_url}")

    validator = BetAMLValidator(args.api_url, API_KEY, OPERATOR_ID)

    # Validate scenarios
    scenarios = ["structuring", "spike", "network", "normal"] if args.scenario == "all" else [args.scenario]

    for scenario in scenarios:
        validator.validate_scenario(scenario)

    # Summary
    validator.print_summary()

    sys.exit(0 if validator.results["failed"] == 0 else 1)

if __name__ == "__main__":
    main()
