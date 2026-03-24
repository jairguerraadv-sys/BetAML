#!/usr/bin/env python3
"""
Script para gerar relatório consolidado de testes do BetAML.

Gera HTML com visualização completa dos resultados.

Uso:
    python scripts/generate_test_report.py
    python scripts/generate_test_report.py --output results.html
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import requests

BASE_URL = os.getenv("BETAML_API_URL", "http://localhost:8000")
API_KEY = os.getenv("BETAML_API_KEY", "betaml_v2_test_key_dummy")
TEST_DATA_DIR = Path(__file__).parent.parent / "test_data"

class TestReportGenerator:
    def __init__(self, base_url: str, api_key: str, output_file: str):
        self.base_url = base_url
        self.api_key = api_key
        self.output_file = output_file
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
        self.data = {}

    def fetch_data(self):
        """Busca dados da API para relatório."""
        print("Buscando dados da API...")

        try:
            # Ingest jobs
            resp = self.session.get(f"{self.base_url}/ingest/jobs", timeout=10)
            self.data["ingest_jobs"] = resp.json().get("items", []) if resp.status_code == 200 else []

            # Alerts
            resp = self.session.get(f"{self.base_url}/alerts?limit=100", timeout=10)
            self.data["alerts"] = resp.json().get("items", []) if resp.status_code == 200 else []

            # Summary stats
            self.data["stats"] = {
                "total_jobs": len(self.data["ingest_jobs"]),
                "total_alerts": len(self.data["alerts"]),
                "alerts_by_severity": {},
            }

            for alert in self.data["alerts"]:
                sev = alert.get("severity", "UNKNOWN")
                self.data["stats"]["alerts_by_severity"][sev] = (
                    self.data["stats"]["alerts_by_severity"].get(sev, 0) + 1
                )

            print(f"✓ Fetched: {self.data['stats']['total_jobs']} ingest jobs, {self.data['stats']['total_alerts']} alerts")

        except Exception as e:
            print(f"✗ Error fetching data: {e}")

    def generate_html(self):
        """Gera relatório HTML."""
        html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BetAML Test Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }}
        h2 {{
            color: #34495e;
            margin-top: 30px;
            border-left: 4px solid #3498db;
            padding-left: 15px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: #ecf0f1;
            padding: 20px;
            border-radius: 4px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 32px;
            font-weight: bold;
            color: #3498db;
        }}
        .stat-label {{
            font-size: 14px;
            color: #7f8c8d;
            margin-top: 10px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 30px;
        }}
        th {{
            background: #34495e;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }}
        td {{
            padding: 12px;
            border-bottom: 1px solid #ecf0f1;
        }}
        tr:hover {{
            background: #f8f9fa;
        }}
        .severity-critical {{
            background: #fadbd8;
            color: #c0392b;
            font-weight: bold;
        }}
        .severity-high {{
            background: #fdebd0;
            color: #d68910;
            font-weight: bold;
        }}
        .severity-medium {{
            background: #fef5e7;
            color: #f39c12;
        }}
        .severity-low {{
            background: #eafaf1;
            color: #27ae60;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #ecf0f1;
            color: #95a5a6;
            font-size: 12px;
        }}
        .scenario {{
            background: #f8f9fa;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 15px;
            border-left: 4px solid #3498db;
        }}
        .scenario-title {{
            font-weight: bold;
            font-size: 16px;
            margin-bottom: 10px;
        }}
        .alert-item {{
            background: white;
            padding: 15px;
            margin-bottom: 10px;
            border-radius: 4px;
            border-left: 4px solid #3498db;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🧪 BetAML Test Report</h1>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{self.data['stats']['total_jobs']}</div>
                <div class="stat-label">Ingest Jobs</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{self.data['stats']['total_alerts']}</div>
                <div class="stat-label">Alerts Generated</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{self.data['stats']['alerts_by_severity'].get('CRITICAL', 0)}</div>
                <div class="stat-label">Critical Alerts</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{self.data['stats']['alerts_by_severity'].get('HIGH', 0)}</div>
                <div class="stat-label">High Priority</div>
            </div>
        </div>

        <h2>Alerts by Severity</h2>
        <table>
            <thead>
                <tr>
                    <th>Severity</th>
                    <th>Count</th>
                    <th>Percentage</th>
                </tr>
            </thead>
            <tbody>
                {self._render_severity_table()}
            </tbody>
        </table>

        <h2>Ingest Jobs</h2>
        <table>
            <thead>
                <tr>
                    <th>Job ID</th>
                    <th>Source System</th>
                    <th>Status</th>
                    <th>Records Processed</th>
                    <th>Failed</th>
                </tr>
            </thead>
            <tbody>
                {self._render_jobs_table()}
            </tbody>
        </table>

        <h2>Top Alerts</h2>
        {self._render_top_alerts()}

        <div class="footer">
            <strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}<br>
            <strong>BetAML Test Data Suite</strong> — 5 scenarios, diverse compliance patterns
        </div>
    </div>
</body>
</html>
"""
        return html

    def _render_severity_table(self) -> str:
        rows = ""
        total = sum(self.data['stats']['alerts_by_severity'].values())
        for sev, count in sorted(self.data['stats']['alerts_by_severity'].items(), reverse=True):
            pct = (count / total * 100) if total > 0 else 0
            rows += f"""
            <tr>
                <td><span class="severity-{sev.lower()}">{sev}</span></td>
                <td>{count}</td>
                <td>{pct:.1f}%</td>
            </tr>
            """
        return rows

    def _render_jobs_table(self) -> str:
        rows = ""
        for job in self.data['ingest_jobs'][:10]:  # Top 10
            rows += f"""
            <tr>
                <td>{job.get('id', 'N/A')[:8]}...</td>
                <td>{job.get('source_system', 'N/A')}</td>
                <td><strong>{job.get('status', 'UNKNOWN')}</strong></td>
                <td>{job.get('processed_records', 0)}</td>
                <td>{job.get('failed_records', 0)}</td>
            </tr>
            """
        return rows if rows else "<tr><td colspan='5' style='text-align:center;color:#95a5a6;'>No jobs found</td></tr>"

    def _render_top_alerts(self) -> str:
        html = ""
        scenarios = {
            "structuring": [a for a in self.data['alerts'] if 'STRUCT' in a.get('title', '')],
            "spike": [a for a in self.data['alerts'] if 'SPIKE' in a.get('title', '') or 'ANOM' in a.get('title', '')],
            "network": [a for a in self.data['alerts'] if 'CLUST' in a.get('title', '') or 'NETWORK' in a.get('title', '')],
        }

        for scenario, alerts in scenarios.items():
            html += f"""
            <div class="scenario">
                <div class="scenario-title">📌 {scenario.upper()}</div>
                <div>{len(alerts)} alert(s) detected</div>
            </div>
            """

        return html

    def save_report(self):
        """Salva relatório HTML."""
        html = self.generate_html()
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✓ Report saved to {self.output_file}")

def main():
    parser = argparse.ArgumentParser(description="Gerar relatório de testes do BetAML")
    parser.add_argument(
        "--output",
        default="test_data/results/test_report.html",
        help="Arquivo de saída",
    )
    parser.add_argument(
        "--api-url",
        default=BASE_URL,
        help="URL da API BetAML",
    )

    args = parser.parse_args()

    # Ensure output dir exists
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generator = TestReportGenerator(args.api_url, API_KEY, args.output)
    generator.fetch_data()
    generator.save_report()

    print(f"\n✓ Test report available at: {output_path.resolve()}")

if __name__ == "__main__":
    main()
