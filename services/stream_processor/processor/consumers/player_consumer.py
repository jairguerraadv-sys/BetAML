"""Consumer for ``canonical.players`` topic."""

from __future__ import annotations

import logging
from typing import Any

from clients.kafka_client import KafkaConsumerClient
from processor.lakehouse_writer import LakehouseWriter
from schemas.canonical import CanonicalEventEnvelope, CanonicalPlayerPayload

logger = logging.getLogger(__name__)

_TOPIC = "canonical.players"


class PlayerConsumer:
    """Consumes canonical player events: writes Bronze and Silver layers."""

    def __init__(
        self,
        consumer: KafkaConsumerClient,
        lakehouse: LakehouseWriter,
    ) -> None:
        self._consumer = consumer
        self._lakehouse = lakehouse

    def run(self, stop_event: Any) -> None:
        self._consumer.subscribe([_TOPIC])
        logger.info("PlayerConsumer subscribed to %s", _TOPIC)
        while not stop_event.is_set():
            try:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                self._process(msg)
                self._consumer.commit()
            except Exception:
                logger.exception("Unhandled error in PlayerConsumer")
        self._consumer.close()
        logger.info("PlayerConsumer stopped")

    def _process(self, msg: dict[str, Any]) -> None:
        try:
            envelope = CanonicalEventEnvelope(**msg)
        except Exception as exc:
            logger.error("Cannot parse player envelope: %s", exc)
            return

        tenant_id = str(envelope.tenantId)
        payload_data = envelope.payload

        try:
            CanonicalPlayerPayload(**payload_data)
        except Exception as exc:
            logger.warning("Player payload validation warning: %s", exc)

        event_date = envelope.occurredAt.date()
        raw_record: dict[str, Any] = {
            **{k: str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v
               for k, v in payload_data.items()},
            "event_id": str(envelope.eventId),
            "tenant_id": tenant_id,
        }

        self._lakehouse.write_bronze(
            tenant_id=tenant_id,
            entity_type="player",
            event_date=event_date,
            source_system=envelope.sourceSystem,
            records=[raw_record],
        )
        self._lakehouse.write_silver(
            tenant_id=tenant_id,
            entity_type="player",
            event_date=event_date,
            records=[raw_record],
        )

        logger.info(
            "Processed player event %s (tenant %s)",
            envelope.sourceEventId,
            tenant_id,
        )
