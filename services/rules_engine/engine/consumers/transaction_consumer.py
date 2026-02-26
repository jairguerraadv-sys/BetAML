"""Consumer for ``canonical.transactions`` in the rules_engine service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from clients.kafka_client import KafkaConsumerClient, KafkaProducerClient
from clients.redis_client import RedisFeatureStore
from engine.alert_creator import AlertCreator
from engine.rule_evaluator import RuleEvaluator
from schemas.canonical import CanonicalEventEnvelope, CanonicalTransactionPayload

logger = logging.getLogger(__name__)

_TOPIC = "canonical.transactions"
_ALERTS_TOPIC = "scoring.alerts"

_SEVERITY_SCORES: dict[str, float] = {
    "LOW": 0.3,
    "MEDIUM": 0.5,
    "HIGH": 0.8,
    "CRITICAL": 1.0,
}


class TransactionConsumer:
    """Evaluates AML rules against each incoming transaction event."""

    def __init__(
        self,
        consumer: KafkaConsumerClient,
        producer: KafkaProducerClient,
        redis_store: RedisFeatureStore,
        rule_evaluator: RuleEvaluator,
        alert_creator: AlertCreator,
        session_factory: Any,
        high_severity_threshold: float = 0.7,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._redis = redis_store
        self._evaluator = rule_evaluator
        self._alert_creator = alert_creator
        self._session_factory = session_factory
        self._high_severity_threshold = high_severity_threshold

    def run(self, stop_event: Any) -> None:
        self._consumer.subscribe([_TOPIC])
        logger.info("RulesEngine TransactionConsumer subscribed to %s", _TOPIC)
        while not stop_event.is_set():
            try:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                self._process(msg)
                self._consumer.commit()
            except Exception:
                logger.exception("Unhandled error in RulesEngine TransactionConsumer")
        self._consumer.close()
        logger.info("RulesEngine TransactionConsumer stopped")

    # ------------------------------------------------------------------

    def _process(self, msg: dict[str, Any]) -> None:
        # Parse envelope
        try:
            envelope = CanonicalEventEnvelope(**msg)
        except Exception as exc:
            logger.error("Cannot parse envelope: %s", exc)
            return

        tenant_id = str(envelope.tenantId)
        payload_data = envelope.payload

        try:
            tx = CanonicalTransactionPayload(**payload_data)
        except Exception as exc:
            logger.error("Cannot parse CanonicalTransactionPayload: %s", exc)
            return

        player_id = tx.playerId
        player_cpf = tx.playerCpf
        event_id = str(envelope.eventId)

        # 1. Get features from Redis (empty dict if not present)
        features = self._redis.get_player_features(tenant_id, player_id) or {}

        # 2. Evaluate rules
        matches = self._evaluator.evaluate_rules(tenant_id, msg, features)
        if not matches:
            return

        db: Session = self._session_factory()
        try:
            for match in matches:
                # 3. Create Alert + RuleExecutionLog
                alert_id = self._alert_creator.create_alert(
                    db=db,
                    tenant_id=tenant_id,
                    player_id=player_id,
                    player_cpf=player_cpf,
                    rule_match=match,
                )

                db.execute(
                    text(
                        "INSERT INTO rule_execution_logs "
                        "(id, tenant_id, rule_id, event_id, player_id, matched, "
                        " execution_time_ms, context_snapshot, executed_at) "
                        "VALUES "
                        "(:id, :tenant_id, :rule_id, :event_id, :player_id, true, "
                        " :exec_ms, :snapshot::jsonb, now())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": uuid.UUID(tenant_id),
                        "rule_id": uuid.UUID(match["rule_id"]),
                        "event_id": event_id,
                        "player_id": player_id,
                        "exec_ms": match.get("execution_time_ms", 0),
                        "snapshot": _json_dumps(
                            {"features": features, "event_id": event_id}
                        ),
                    },
                )
                db.commit()

                # 4. Publish to scoring.alerts
                risk_score = _SEVERITY_SCORES.get(match["severity"], 0.5)
                self._producer.produce(
                    topic=_ALERTS_TOPIC,
                    key=f"{tenant_id}:{player_id}",
                    value_dict={
                        "alert_id": str(alert_id),
                        "tenant_id": tenant_id,
                        "player_id": player_id,
                        "player_cpf": player_cpf,
                        "rule_id": match["rule_id"],
                        "rule_name": match["rule_name"],
                        "severity": match["severity"],
                        "risk_score": risk_score,
                        "evidence": match["evidence"],
                        "trigger_event_id": event_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self._producer.poll(0)

                # 5. Create/link Case for HIGH / CRITICAL or above threshold
                if (
                    match["severity"] in ("HIGH", "CRITICAL")
                    or risk_score >= self._high_severity_threshold
                ):
                    self._alert_creator.create_or_get_case(
                        db=db,
                        tenant_id=tenant_id,
                        player_id=player_id,
                        alert_id=alert_id,
                    )

                logger.info(
                    "Alert %s created – player %s rule '%s' severity %s",
                    alert_id,
                    player_id,
                    match["rule_name"],
                    match["severity"],
                )

        except Exception:
            logger.exception("Error while creating alerts for event %s", event_id)
            db.rollback()
        finally:
            db.close()


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)
