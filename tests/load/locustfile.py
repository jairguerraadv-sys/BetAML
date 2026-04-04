"""
tests/load/locustfile.py
Locust load-test scenarios for BetAML API.

Run (batch ingest throughput target — 1 000 events/s por tenant, 5min):
    locust -f tests/load/locustfile.py \
        --host http://localhost:8000 \
        --users 100 --spawn-rate 25 \
        --headless --run-time 300s \
        --csv /tmp/betaml_load_results \
        --only-summary

Run (full mixed load):
    locust -f tests/load/locustfile.py --host http://localhost:8000 --users 50 --spawn-rate 5

Throughput target: POST /ingest/batch @ ≥1 000 events/second por tenant
    With 100 BatchIngestUser workers, each posting 10 events with wait_time=0,
    events/s ≈ users × BATCH_SIZE / avg_latency_s.
  Tune --users and BATCH_SIZE to hit target for your hardware.

After the run, generate a compact CSV report:
    python tests/load/generate_report.py /tmp/betaml_load_results --output /tmp/betaml_load_summary.csv

Scenarios:
  - BatchIngestUser : POST /ingest/batch (JSON, high-throughput target)
  - IngestUser      : POST /ingest/file (CSV upload) → poll jobs
  - ScoringUser     : score player + get features
  - AlertUser       : list & triage alerts
  - CaseUser        : list & view case detail
"""
from __future__ import annotations

import os
import random
import string
import uuid
from io import BytesIO

from locust import HttpUser, SequentialTaskSet, between, constant, task

# ── Constants ─────────────────────────────────────────────────────────────────

_TENANT_USER = os.getenv("LOADTEST_USER", "analyst_a")
_TENANT_PASS = os.getenv("LOADTEST_PASS", "analyst123")
_TENANT_SLUG = os.getenv("LOADTEST_TENANT_SLUG", "")
BATCH_SIZE    = 10  # events per POST /ingest/batch request
_BATCH_USER_WEIGHT = int(os.getenv("LOADTEST_WEIGHT_BATCH", "4"))
_INGEST_USER_WEIGHT = int(os.getenv("LOADTEST_WEIGHT_INGEST", "1"))
_SCORING_USER_WEIGHT = int(os.getenv("LOADTEST_WEIGHT_SCORING", "1"))
_ALERT_USER_WEIGHT = int(os.getenv("LOADTEST_WEIGHT_ALERT", "1"))
_CASE_USER_WEIGHT = int(os.getenv("LOADTEST_WEIGHT_CASE", "1"))

# Shared state for inter-task data flow (per-user)
class _SharedCtx:
    tenant_id:   str = ""
    player_ids:  list[str] = []
    alert_ids:   list[str] = []
    case_ids:    list[str] = []
    ingest_jobs: list[str] = []


def _extract_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _csv_payload(n_rows: int = 50) -> bytes:
    lines = ["player_id,event_type,amount,currency,timestamp"]
    for _ in range(n_rows):
        lines.append(
            f"{uuid.uuid4()},DEPOSIT,{random.uniform(10, 5000):.2f},BRL,"
            f"2024-11-{random.randint(1,28):02d}T{random.randint(0,23):02d}:00:00Z"
        )
    return "\n".join(lines).encode()


def _batch_events(n: int = BATCH_SIZE) -> list[dict]:
    """Generate n synthetic transaction events for /ingest/batch."""
    tx_types = ["DEPOSIT", "WITHDRAWAL", "BET_STAKE", "BET_WIN"]
    return [
        {
            "source_system": "BackofficeAlpha",
            "entity_type": "transaction",
            "external_player_id": f"load-{uuid.uuid4().hex[:8]}",
            "payload": {
                "transaction_id": uuid.uuid4().hex,
                "transaction_type": random.choice(tx_types),
                "amount": round(random.uniform(10, 10000), 2),
                "currency": "BRL",
                "payment_method": random.choice(["PIX", "CARD", "BOLETO"]),
                "instrument_token": "".join(random.choices(string.hexdigits, k=16)),
                "occurred_at": f"2024-11-{random.randint(1,28):02d}T{random.randint(0,23):02d}:{random.randint(0,59):02d}:00Z",
            },
        }
        for _ in range(n)
    ]


# ── Login mixin ───────────────────────────────────────────────────────────────

class AuthMixin:
    token: str = ""
    ctx: _SharedCtx

    def on_start(self):
        self.ctx = _SharedCtx()
        resp = self.client.post(
            "/auth/login",
            json={
                "username": _TENANT_USER,
                "password": _TENANT_PASS,
                **({"tenant_slug": _TENANT_SLUG} if _TENANT_SLUG else {}),
            },
            headers={"Content-Type": "application/json"},
            name="/auth/login",
        )
        if resp.status_code == 200:
            self.token = resp.json().get("access_token", "")
        else:
            self.token = ""

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


# ── Batch ingest tasks (high-throughput target: ≥1 000 events/s) ──────────────

class BatchIngestTaskSet(SequentialTaskSet):
    """
    High-throughput batch ingest. Each iteration sends BATCH_SIZE events.

    Throughput formula:
        events/s ≈ concurrency × BATCH_SIZE / avg_latency_s

    With 100 users, BATCH_SIZE=10, avg_latency≈1s:
        100 × 10 / 1.0 = 1 000 events/s
    Use --csv plus tests/load/generate_report.py to capture the report.
    """

    @task
    def post_batch(self):
        events = _batch_events(BATCH_SIZE)
        self.client.post(
            "/ingest/batch",
            json=events,
            headers={**self.user._h(), "Content-Type": "application/json"},
            name="POST /ingest/batch",
        )


class BatchIngestUser(AuthMixin, HttpUser):
    """
    Dedicated high-throughput batch ingest user.
    Use --users 100 as the baseline to target ≥1 000 events/s throughput.
    """
    tasks         = [BatchIngestTaskSet]
    wait_time     = constant(0)          # no wait — maximize throughput
    weight        = _BATCH_USER_WEIGHT   # configurable for batch-only smoke in CI


# ── Ingest tasks (CSV file upload) ────────────────────────────────────────────

class IngestTaskSet(SequentialTaskSet):
    @task
    def upload_csv(self):
        payload = _csv_payload(50)
        self.client.post(
            "/ingest/file",
            files={"file": ("transactions.csv", BytesIO(payload), "text/csv")},
            data={"source_system": "BackofficeAlpha"},
            headers=self.user._h(),
            name="/ingest/file",
        )

    @task
    def list_jobs(self):
        self.client.get("/ingest/jobs", headers=self.user._h(), name="/ingest/jobs")


class IngestUser(AuthMixin, HttpUser):
    tasks     = [IngestTaskSet]
    wait_time = between(1, 3)
    weight    = _INGEST_USER_WEIGHT


# ── Scoring tasks ─────────────────────────────────────────────────────────────

class ScoringTaskSet(SequentialTaskSet):
    player_id: str = ""

    @task
    def get_players(self):
        resp = self.client.get(
            "/players?page=1&page_size=20",
            headers=self.user._h(),
            name="/players",
        )
        if resp.status_code == 200:
            players = resp.json()
            if players:
                self.player_id = players[0]["id"]

    @task
    def get_player_detail(self):
        if not self.player_id:
            return
        self.client.get(
            f"/players/{self.player_id}",
            headers=self.user._h(),
            name="/players/{id}",
        )

    @task
    def get_features(self):
        if not self.player_id:
            return
        with self.client.get(
            f"/players/{self.player_id}/features/current",
            headers=self.user._h(),
            name="/players/{id}/features/current",
            catch_response=True,
        ) as resp:
            if resp.status_code in {200, 404}:
                resp.success()


class ScoringUser(AuthMixin, HttpUser):
    tasks     = [ScoringTaskSet]
    wait_time = between(0.5, 2)
    weight    = _SCORING_USER_WEIGHT


# ── Alert tasks ───────────────────────────────────────────────────────────────

class AlertTaskSet(SequentialTaskSet):
    alert_id: str = ""

    @task
    def list_alerts(self):
        resp = self.client.get(
            "/alerts?status=OPEN&page=1&page_size=20",
            headers=self.user._h(),
            name="/alerts",
        )
        if resp.status_code == 200:
            alerts = _extract_items(resp.json())
            if alerts:
                self.alert_id = alerts[0]["id"]

    @task
    def get_alert_detail(self):
        if not self.alert_id:
            return
        self.client.get(
            f"/alerts/{self.alert_id}",
            headers=self.user._h(),
            name="/alerts/{id}",
        )

    @task
    def label_alert(self):
        if not self.alert_id:
            return
        self.client.post(
            f"/alerts/{self.alert_id}/label",
            json={"label": random.choice(["TRUE_POSITIVE", "FALSE_POSITIVE", "NEED_REVIEW"]), "notes": "load-test"},
            headers=self.user._h(),
            name="/alerts/{id}/label",
        )


class AlertUser(AuthMixin, HttpUser):
    tasks     = [AlertTaskSet]
    wait_time = between(1, 4)
    weight    = _ALERT_USER_WEIGHT


# ── Case tasks ────────────────────────────────────────────────────────────────

class CaseTaskSet(SequentialTaskSet):
    case_id: str = ""

    @task
    def list_cases(self):
        resp = self.client.get(
            "/cases?status=OPEN&page=1&page_size=10",
            headers=self.user._h(),
            name="/cases",
        )
        if resp.status_code == 200:
            cases = resp.json()
            if cases:
                self.case_id = cases[0]["id"]

    @task
    def get_case_detail(self):
        if not self.case_id:
            return
        self.client.get(
            f"/cases/{self.case_id}",
            headers=self.user._h(),
            name="/cases/{id}",
        )

    @task
    def get_case_events(self):
        if not self.case_id:
            return
        self.client.get(
            f"/cases/{self.case_id}/timeline",
            headers=self.user._h(),
            name="/cases/{id}/timeline",
        )


class CaseUser(AuthMixin, HttpUser):
    tasks     = [CaseTaskSet]
    wait_time = between(2, 5)
    weight    = _CASE_USER_WEIGHT
