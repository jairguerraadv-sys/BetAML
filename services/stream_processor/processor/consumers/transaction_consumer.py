"""Consumer for ``canonical.transactions`` topic."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from clients.kafka_client import KafkaConsumerClient, KafkaProducerClient
from clients.redis_client import RedisFeatureStore
from processor.clickhouse_writer import ClickHouseWriter
from processor.config import settings
from processor.feature_engine import FeatureEngine
from processor.lakehouse_writer import LakehouseWriter
from schemas.canonical import CanonicalEventEnvelope, CanonicalTransactionPayload

logger = logging.getLogger(__name__)

_TOPIC = "canonical.transactions"
_FEATURE_TOPIC = "features.player_daily"


class TransactionConsumer:
    """Consumes canonical transaction events and drives feature computation."""

    def __init__(
        self,
        consumer: KafkaConsumerClient,
        producer: KafkaProducerClient,
        redis_store: RedisFeatureStore,
        lakehouse: LakehouseWriter,
        ch_writer: ClickHouseWriter,
        feature_engine: FeatureEngine,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._redis = redis_store
        self._lakehouse = lakehouse
        self._ch = ch_writer
        self._engine = feature_engine

    def run(self, stop_event: Any) -> None:
        """Poll *_TOPIC* until *stop_event* is set."""
        self._consumer.subscribe([_TOPIC])
        logger.info("TransactionConsumer subscribed to %s", _TOPIC)
        while not stop_event.is_set():
            try:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                self._process(msg)
                self._consumer.commit()
            except Exception:
                logger.exception("Unhandled error in TransactionConsumer")
        self._consumer.close()
        logger.info("TransactionConsumer stopped")

    def _process(self, msg: dict[str, Any]) -> None:
        # 1. Parse envelope
        try:
            envelope = CanonicalEventEnvelope(**msg)
        except Exception as exc:
            logger.error("Cannot parse CanonicalEventEnvelope: %s", exc)
            return

        tenant_id = str(envelope.tenantId)
        payload_data = envelope.payload

        try:
            tx = CanonicalTransactionPayload(**payload_data)
        except Exception as exc:
            logger.error("Cannot parse CanonicalTransactionPayload: %s", exc)
            return

        player_id = tx.playerId
        event_date = envelope.occurredAt.date()
        raw_record: dict[str, Any] = {
            **{k: str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v
               for k, v in payload_data.items()},
            "event_id": str(envelope.eventId),
            "tenant_id": tenant_id,
        }

        # 2. Write Bronze
        self._lakehouse.write_bronze(
            tenant_id=tenant_id,
            entity_type="transaction",
            event_date=event_date,
            source_system=envelope.sourceSystem,
            records=[raw_record],
        )

        # 3. Fetch recent history from ClickHouse
        recent_tx = self._ch.fetch_player_transactions(tenant_id, player_id, days=30)
        recent_bets = self._ch.fetch_player_bets(tenant_id, player_id, days=30)

        # Include the current transaction in the window
        current_tx: dict[str, Any] = {
            "amount": float(tx.amount),
            "transaction_type": tx.type.value,
            "occurred_at": envelope.occurredAt,
            "payment_instrument": tx.paymentInstrument,
        }
        all_transactions = recent_tx + [current_tx]

        # 4. Compute features
        features = self._engine.compute_player_features(
            tenant_id=tenant_id,
            player_id=player_id,
            transactions=all_transactions,
            bets=recent_bets,
        )

        # 5. Update Redis (TTL 24h)
        self._redis.set_player_features(
            tenant_id=tenant_id,
            player_id=player_id,
            features=features,
            ttl_seconds=settings.redis_feature_ttl,
        )

        # 6. Write Silver (enriched with feature snapshot)
        silver_record = {**raw_record, "_features_snapshot": json.dumps(features)}
        self._lakehouse.write_silver(
            tenant_id=tenant_id,
            entity_type="transaction",
            event_date=event_date,
            records=[silver_record],
        )

        # 7. Write to ClickHouse transactions table
        ch_record: dict[str, Any] = {
            "id": str(envelope.eventId),
            "tenant_id": tenant_id,
            "player_id": player_id,
            "player_cpf": tx.playerCpf,
            "transaction_type": tx.type.value,
            "amount": float(tx.amount),
            "currency": tx.currency,
            "method": tx.method.value,
            "status": tx.status.value,
            "payment_instrument": json.dumps(tx.paymentInstrument),
            "occurred_at": envelope.occurredAt,
            "source_system": envelope.sourceSystem,
            "ingested_at": datetime.now(timezone.utc),
        }
        self._ch.write_transactions([ch_record])

        # 8. Publish feature update event
        feature_event: dict[str, Any] = {
            "tenant_id": tenant_id,
            "player_id": player_id,
            "player_cpf": tx.playerCpf,
            "features": features,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "trigger_event_id": str(envelope.eventId),
            "trigger_topic": _TOPIC,
        }
        self._producer.produce(
            topic=_FEATURE_TOPIC,
            key=f"{tenant_id}:{player_id}",
            value_dict=feature_event,
        )
        self._producer.poll(0)

        logger.info(
            "Processed transaction %s for player %s (tenant %s)",
            envelope.sourceEventId,
            player_id,
            tenant_id,
        )
