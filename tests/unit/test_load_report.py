from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tests/load"))

from generate_report import build_summary, write_summary


def test_generate_report_builds_summary_from_locust_csv(tmp_path: Path):
    prefix = tmp_path / "betaml_load_results"
    stats_path = tmp_path / "betaml_load_results_stats.csv"
    with stats_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Type",
                "Name",
                "Request Count",
                "Failure Count",
                "Median Response Time",
                "Average Response Time",
                "95%",
                "Requests/s",
                "Failures/s",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Type": "POST",
                "Name": "POST /ingest/batch",
                "Request Count": "1500",
                "Failure Count": "3",
                "Median Response Time": "80",
                "Average Response Time": "96.2",
                "95%": "140",
                "Requests/s": "125.5",
                "Failures/s": "0.1",
            }
        )

    summary = build_summary(prefix)
    assert len(summary) == 1
    assert summary[0]["request_name"] == "POST /ingest/batch"
    assert summary[0]["failure_count"] == "3"

    output = tmp_path / "summary.csv"
    write_summary(summary, output)
    assert output.exists()
