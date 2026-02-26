"""Integration test: ingest → canonical → rules → alert pipeline (fully mocked)."""

import json
import sys
import os
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _make_canonical_event(transaction: dict, tenant_id: str) -> dict:
    """Build a minimal canonical event envelope around a transaction payload."""
    return {
        "eventId": str(uuid.uuid4()),
        "tenantId": tenant_id,
        "sourceSystem": "BackofficeAlpha",
        "sourceEventId": transaction.get("transactionId", "ext-1"),
        "schemaVersion": 1,
        "entityType": "TRANSACTION",
        "occurredAt": transaction.get("occurredAt", "2024-01-01T10:00:00Z"),
        "payload": transaction,
        "rawPayload": transaction,
        "ingestMetadata": {"receivedAt": "2024-01-01T10:00:01Z", "mapperVersion": "1.0"},
    }


def _make_alert(rule_id: str, tenant_id: str, player_id: str, score: float) -> dict:
    return {
        "alertId": str(uuid.uuid4()),
        "tenantId": tenant_id,
        "ruleId": rule_id,
        "playerId": player_id,
        "score": score,
        "status": "OPEN",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id():
    return "tenant-001"


@pytest.fixture
def sample_transaction(tenant_id):
    return {
        "transactionId": "txn-001",
        "tenantId": tenant_id,
        "playerId": "player-001",
        "amount": "6000.00",
        "currency": "BRL",
        "type": "DEPOSIT",
        "occurredAt": "2024-06-01T15:00:00Z",
        "cpf": "52998224725",
    }


@pytest.fixture
def sample_features():
    return {
        "playerId": "player-001",
        "deposit_sum_24h": Decimal("6000.00"),
        "deposit_count_24h": 1,
        "zscore_current_vs_baseline": Decimal("3.5"),
        "baseline_avg_daily_deposit": Decimal("500.00"),
        "baseline_stddev_deposit": Decimal("200.00"),
        "withdrawal_sum_7d": Decimal("0"),
        "deposit_sum_7d": Decimal("6000.00"),
    }


@pytest.fixture
def mock_kafka_producer():
    producer = MagicMock()
    producer.produce = MagicMock()
    producer.flush = MagicMock()
    return producer


@pytest.fixture
def mock_kafka_consumer():
    consumer = MagicMock()
    consumer.subscribe = MagicMock()
    consumer.poll = MagicMock()
    consumer.commit = MagicMock()
    consumer.close = MagicMock()
    return consumer


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    redis.get = MagicMock(return_value=None)
    redis.set = MagicMock(return_value=True)
    redis.hgetall = MagicMock(return_value={})
    redis.hset = MagicMock(return_value=True)
    return redis


@pytest.fixture
def mock_postgres_conn():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall = MagicMock(return_value=[])
    cursor.fetchone = MagicMock(return_value=None)
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor = MagicMock(return_value=cursor)
    conn.commit = MagicMock()
    conn.rollback = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Pipeline simulation
# ---------------------------------------------------------------------------


class FakeIngestService:
    """Simulates the ingest endpoint: validates, maps and publishes to Kafka."""

    def __init__(self, producer, redis, postgres):
        self.producer = producer
        self.redis = redis
        self.postgres = postgres
        self.published: list[dict] = []

    def ingest(self, raw_event: dict, tenant_id: str) -> dict:
        canonical = _make_canonical_event(raw_event, tenant_id)
        payload = json.dumps(canonical)
        self.producer.produce("canonical.transactions", payload)
        self.producer.flush()
        self.published.append(canonical)
        return canonical


class FakeRulesEngine:
    """Simulates the rules engine consumer: polls Kafka, evaluates DSL rules."""

    def __init__(self, consumer, producer, redis, postgres):
        self.consumer = consumer
        self.producer = producer
        self.redis = redis
        self.postgres = postgres
        self.alerts: list[dict] = []

    def _load_rules(self, tenant_id: str) -> list[dict]:
        # Simulated rules from Postgres
        return [
            {
                "ruleId": "rule-high-deposit",
                "tenantId": tenant_id,
                "dsl": "features.deposit_sum_24h > 5000",
                "score": 0.75,
            }
        ]

    def _load_features(self, player_id: str, tenant_id: str) -> dict:
        # Simulated feature lookup from Redis
        raw = self.redis.hgetall(f"features:{tenant_id}:{player_id}")
        if raw:
            return raw
        return {}

    def process_event(self, canonical_event: dict, features: dict) -> list[dict]:
        from libs.dsl.parser import DSLParser, DSLEvaluator

        tenant_id = canonical_event["tenantId"]
        player_id = canonical_event["payload"].get("playerId")
        rules = self._load_rules(tenant_id)
        parser = DSLParser()
        evaluator = DSLEvaluator()
        created_alerts: list[dict] = []

        for rule in rules:
            ast = parser.parse(rule["dsl"])
            context = {"features": features, "transaction": canonical_event["payload"]}
            try:
                triggered = evaluator.evaluate(ast, context)
            except Exception:
                triggered = False

            if triggered:
                alert = _make_alert(rule["ruleId"], tenant_id, player_id, rule["score"])
                self.alerts.append(alert)
                self.producer.produce("scoring.alerts", json.dumps(alert))
                created_alerts.append(alert)

        return created_alerts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIngestPipeline:
    def test_ingest_publishes_to_kafka(
        self, sample_transaction, tenant_id, mock_kafka_producer, mock_redis, mock_postgres_conn
    ):
        service = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical = service.ingest(sample_transaction, tenant_id)

        mock_kafka_producer.produce.assert_called_once()
        call_args = mock_kafka_producer.produce.call_args
        assert call_args[0][0] == "canonical.transactions"
        published = json.loads(call_args[0][1])
        assert published["tenantId"] == tenant_id
        assert published["entityType"] == "TRANSACTION"

    def test_ingest_produces_valid_envelope(
        self, sample_transaction, tenant_id, mock_kafka_producer, mock_redis, mock_postgres_conn
    ):
        service = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical = service.ingest(sample_transaction, tenant_id)

        assert "eventId" in canonical
        assert canonical["schemaVersion"] == 1
        assert canonical["sourceSystem"] == "BackofficeAlpha"
        assert canonical["payload"]["playerId"] == "player-001"

    def test_kafka_flush_called_after_produce(
        self, sample_transaction, tenant_id, mock_kafka_producer, mock_redis, mock_postgres_conn
    ):
        service = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        service.ingest(sample_transaction, tenant_id)
        mock_kafka_producer.flush.assert_called_once()


class TestRulesEnginePipeline:
    def test_high_deposit_triggers_alert(
        self,
        sample_transaction,
        sample_features,
        tenant_id,
        mock_kafka_consumer,
        mock_kafka_producer,
        mock_redis,
        mock_postgres_conn,
    ):
        ingest = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical = ingest.ingest(sample_transaction, tenant_id)

        engine = FakeRulesEngine(
            mock_kafka_consumer, mock_kafka_producer, mock_redis, mock_postgres_conn
        )
        alerts = engine.process_event(canonical, sample_features)

        assert len(alerts) == 1
        assert alerts[0]["ruleId"] == "rule-high-deposit"
        assert alerts[0]["tenantId"] == tenant_id
        assert alerts[0]["playerId"] == "player-001"
        assert alerts[0]["score"] == 0.75
        assert alerts[0]["status"] == "OPEN"

    def test_alert_published_to_kafka(
        self,
        sample_transaction,
        sample_features,
        tenant_id,
        mock_kafka_consumer,
        mock_kafka_producer,
        mock_redis,
        mock_postgres_conn,
    ):
        ingest = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical = ingest.ingest(sample_transaction, tenant_id)

        engine = FakeRulesEngine(
            mock_kafka_consumer, mock_kafka_producer, mock_redis, mock_postgres_conn
        )
        engine.process_event(canonical, sample_features)

        # produce should have been called twice: once for ingest, once for the alert
        assert mock_kafka_producer.produce.call_count == 2
        alert_call = mock_kafka_producer.produce.call_args_list[1]
        assert alert_call[0][0] == "scoring.alerts"
        alert_payload = json.loads(alert_call[0][1])
        assert alert_payload["status"] == "OPEN"

    def test_low_deposit_does_not_trigger_alert(
        self,
        sample_transaction,
        tenant_id,
        mock_kafka_consumer,
        mock_kafka_producer,
        mock_redis,
        mock_postgres_conn,
    ):
        low_features = {"deposit_sum_24h": Decimal("100.00")}
        ingest = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical = ingest.ingest(sample_transaction, tenant_id)

        engine = FakeRulesEngine(
            mock_kafka_consumer, mock_kafka_producer, mock_redis, mock_postgres_conn
        )
        alerts = engine.process_event(canonical, low_features)

        assert alerts == []
        # Only the ingest produce call, no alert
        assert mock_kafka_producer.produce.call_count == 1

    def test_redis_feature_lookup_called(
        self,
        sample_transaction,
        sample_features,
        tenant_id,
        mock_kafka_consumer,
        mock_kafka_producer,
        mock_redis,
        mock_postgres_conn,
    ):
        ingest = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical = ingest.ingest(sample_transaction, tenant_id)

        engine = FakeRulesEngine(
            mock_kafka_consumer, mock_kafka_producer, mock_redis, mock_postgres_conn
        )
        engine._load_features("player-001", tenant_id)

        mock_redis.hgetall.assert_called_once_with(f"features:{tenant_id}:player-001")

    def test_tenant_isolation(
        self,
        sample_transaction,
        sample_features,
        mock_kafka_consumer,
        mock_kafka_producer,
        mock_redis,
        mock_postgres_conn,
    ):
        tenant_a = "tenant-001"
        tenant_b = "tenant-002"

        ingest = FakeIngestService(mock_kafka_producer, mock_redis, mock_postgres_conn)
        canonical_a = ingest.ingest(sample_transaction, tenant_a)

        engine = FakeRulesEngine(
            mock_kafka_consumer, mock_kafka_producer, mock_redis, mock_postgres_conn
        )
        alerts = engine.process_event(canonical_a, sample_features)

        # All alerts should belong to tenant_a only
        for alert in alerts:
            assert alert["tenantId"] == tenant_a
            assert alert["tenantId"] != tenant_b
