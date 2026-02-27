"""
tests/load/locustfile.py
Locust load-test scenarios for BetAML API.

Run:
    locust -f tests/load/locustfile.py --host http://localhost:8000 --users 50 --spawn-rate 5

Scenarios:
  - IngestUser   : ingest CSV file → poll job status
  - ScoringUser  : score player + get features
  - AlertUser    : list & triage alerts
  - CaseUser     : list & view case detail
"""
from __future__ import annotations

import json
import random
import uuid
from io import BytesIO

from locust import HttpUser, SequentialTaskSet, between, task

# ── Constants ─────────────────────────────────────────────────────────────────

_TENANT_EMAIL = "analyst@betaml.io"
_TENANT_PASS  = "analyst123"

# Shared state for inter-task data flow (per-user)
class _SharedCtx:
    tenant_id:   str = ""
    player_ids:  list[str] = []
    alert_ids:   list[str] = []
    case_ids:    list[str] = []
    ingest_jobs: list[str] = []


def _csv_payload(n_rows: int = 50) -> bytes:
    lines = ["player_id,event_type,amount,currency,timestamp"]
    for _ in range(n_rows):
        lines.append(
            f"{uuid.uuid4()},DEPOSIT,{random.uniform(10, 5000):.2f},BRL,"
            f"2024-11-{random.randint(1,28):02d}T{random.randint(0,23):02d}:00:00Z"
        )
    return "\n".join(lines).encode()


# ── Login mixin ───────────────────────────────────────────────────────────────

class AuthMixin:
    token: str = ""
    ctx: _SharedCtx

    def on_start(self):
        self.ctx = _SharedCtx()
        resp = self.client.post(
            "/auth/login",
            data={"username": _TENANT_EMAIL, "password": _TENANT_PASS},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            name="/auth/login",
        )
        if resp.status_code == 200:
            self.token = resp.json().get("access_token", "")
        else:
            self.token = ""

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


# ── Ingest tasks ──────────────────────────────────────────────────────────────

class IngestTaskSet(SequentialTaskSet):
    @task
    def upload_csv(self):
        payload = _csv_payload(50)
        self.client.post(
            "/ingest/file",
            files={"file": ("transactions.csv", BytesIO(payload), "text/csv")},
            data={"source_system": "delta", "mapping_config_id": "default"},
            headers=self.user._h(),
            name="/ingest/file",
        )

    @task
    def list_jobs(self):
        self.client.get("/ingest/jobs", headers=self.user._h(), name="/ingest/jobs")


class IngestUser(AuthMixin, HttpUser):
    tasks = [IngestTaskSet]
    wait_time = between(1, 3)


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
    def score_player(self):
        if not self.player_id:
            return
        self.client.post(
            "/score",
            json={"player_id": self.player_id, "tenant_id": "default"},
            headers=self.user._h(),
            name="POST /score",
        )

    @task
    def get_features(self):
        if not self.player_id:
            return
        self.client.get(
            f"/players/{self.player_id}/features/current",
            headers=self.user._h(),
            name="/players/{id}/features/current",
        )


class ScoringUser(AuthMixin, HttpUser):
    tasks = [ScoringTaskSet]
    wait_time = between(0.5, 2)


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
            alerts = resp.json()
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
    tasks = [AlertTaskSet]
    wait_time = between(1, 4)


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
            f"/cases/{self.case_id}/events",
            headers=self.user._h(),
            name="/cases/{id}/events",
        )


class CaseUser(AuthMixin, HttpUser):
    tasks = [CaseTaskSet]
    wait_time = between(2, 5)
