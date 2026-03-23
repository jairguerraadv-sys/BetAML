"""
Generate a concise CSV summary from Locust --csv outputs.

Usage:
    python tests/load/generate_report.py /tmp/betaml_load_results
    python tests/load/generate_report.py /tmp/betaml_load_results --output /tmp/summary.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _load_stats_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_summary(prefix: Path) -> list[dict[str, str]]:
    stats_path = prefix.with_name(prefix.name + "_stats.csv")
    if not stats_path.exists():
        raise FileNotFoundError(f"Locust stats CSV não encontrado: {stats_path}")

    rows = _load_stats_rows(stats_path)
    summary: list[dict[str, str]] = []
    for row in rows:
        request_name = row.get("Name", "") or row.get("name", "")
        method = row.get("Type", "") or row.get("method", "")
        if request_name.upper() == "AGGREGATED":
            continue
        summary.append(
            {
                "request_name": request_name,
                "method": method,
                "request_count": row.get("Request Count", row.get("request_count", "0")),
                "failure_count": row.get("Failure Count", row.get("failure_count", "0")),
                "median_ms": row.get("Median Response Time", row.get("median_response_time", "0")),
                "avg_ms": row.get("Average Response Time", row.get("avg_response_time", "0")),
                "p95_ms": row.get("95%", row.get("95_percentile", "0")),
                "rps": row.get("Requests/s", row.get("requests_per_sec", "0")),
                "failures_per_sec": row.get("Failures/s", row.get("failures_per_sec", "0")),
            }
        )
    return summary


def write_summary(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "request_name",
                "method",
                "request_count",
                "failure_count",
                "median_ms",
                "avg_ms",
                "p95_ms",
                "rps",
                "failures_per_sec",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate concise CSV summary from Locust CSV results")
    parser.add_argument("prefix", help="Locust CSV prefix, e.g. /tmp/betaml_load_results")
    parser.add_argument("--output", help="Output CSV path", default=None)
    args = parser.parse_args()

    prefix = Path(args.prefix)
    output = Path(args.output) if args.output else prefix.with_name(prefix.name + "_summary.csv")
    try:
        rows = build_summary(prefix)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    write_summary(rows, output)
    print(f"Wrote {len(rows)} summary rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
