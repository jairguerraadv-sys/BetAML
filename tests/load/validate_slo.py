from __future__ import annotations

import argparse
from pathlib import Path

from generate_report import build_summary


def _to_float(value: str | None) -> float:
    raw = (value or "0").strip()
    if not raw:
        return 0.0
    return float(raw)


def _find_request_row(prefix: Path, request_name: str) -> dict[str, str]:
    for row in build_summary(prefix):
        if row.get("request_name") == request_name:
            return row
    raise ValueError(f"Request nao encontrado no report Locust: {request_name}")


def evaluate_thresholds(
    row: dict[str, str],
    *,
    batch_size: int,
    min_rps: float,
    min_event_rps: float,
    max_p95_ms: float,
    max_failure_rate_pct: float,
) -> tuple[bool, list[str]]:
    request_count = _to_float(row.get("request_count"))
    failure_count = _to_float(row.get("failure_count"))
    p95_ms = _to_float(row.get("p95_ms"))
    rps = _to_float(row.get("rps"))
    event_rps = rps * batch_size
    failure_rate_pct = 0.0 if request_count == 0 else (failure_count / request_count) * 100.0

    evidence = [
        f"request_name={row.get('request_name', '')}",
        f"request_count={int(request_count)}",
        f"failure_count={int(failure_count)}",
        f"failure_rate_pct={failure_rate_pct:.4f}",
        f"p95_ms={p95_ms:.2f}",
        f"rps={rps:.2f}",
        f"batch_size={batch_size}",
        f"event_rps={event_rps:.2f}",
        f"threshold_min_rps={min_rps:.2f}",
        f"threshold_min_event_rps={min_event_rps:.2f}",
        f"threshold_max_p95_ms={max_p95_ms:.2f}",
        f"threshold_max_failure_rate_pct={max_failure_rate_pct:.4f}",
    ]

    failures: list[str] = []
    if rps < min_rps:
        failures.append(f"rps_below_threshold actual={rps:.2f} expected>={min_rps:.2f}")
    if event_rps < min_event_rps:
        failures.append(
            f"event_rps_below_threshold actual={event_rps:.2f} expected>={min_event_rps:.2f}"
        )
    if p95_ms > max_p95_ms:
        failures.append(f"p95_above_threshold actual={p95_ms:.2f} expected<={max_p95_ms:.2f}")
    if failure_rate_pct > max_failure_rate_pct:
        failures.append(
            f"failure_rate_above_threshold actual={failure_rate_pct:.4f} expected<={max_failure_rate_pct:.4f}"
        )

    if failures:
        evidence.append("load_slo=FAIL")
        evidence.extend(failures)
        return False, evidence

    evidence.append("load_slo=PASS")
    return True, evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Locust CSV results against load/SLO thresholds")
    parser.add_argument("prefix", help="Locust CSV prefix, e.g. /tmp/betaml_load_results")
    parser.add_argument("--request-name", default="POST /ingest/batch")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--min-rps", type=float, default=0.0)
    parser.add_argument("--min-event-rps", type=float, default=0.0)
    parser.add_argument("--max-p95-ms", type=float, default=500.0)
    parser.add_argument("--max-failure-rate-pct", type=float, default=1.0)
    parser.add_argument("--evidence-out", default="")
    args = parser.parse_args()

    row = _find_request_row(Path(args.prefix), args.request_name)
    ok, evidence = evaluate_thresholds(
        row,
        batch_size=args.batch_size,
        min_rps=args.min_rps,
        min_event_rps=args.min_event_rps,
        max_p95_ms=args.max_p95_ms,
        max_failure_rate_pct=args.max_failure_rate_pct,
    )
    rendered = "\n".join(evidence)
    print(rendered)
    if args.evidence_out:
      Path(args.evidence_out).write_text(rendered + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())