"""Consumer for ``canonical.bets`` topic."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from clients.kafka_client import KafkaConsumerClient, KafkaProducerClient
from processor.clickhouse_writer import ClickHouseWriter
from processor.lakehouse_writer import LakehouseWriter
from schemas.canonical import CanonicalBetPayload, CanonicalEventEnvelope

logger = logging.getLogger(__name__)

_TOPIC = "canonical.bets"


class BetConsumer:
    """Consumes canonical bet events: writes Bronze/Silver/ClickHouse."""

    def __init__(
        self,
        consumer: KafkaConsumerClient,
        producer: KafkaProducerClient,
        lakehouse: LakehouseWriter,
        ch_writer: ClickHouseWriter,
    ) -> None:
        self._consumer = consumer
        self._producer = producer
        self._lakehouse = lakehouse
        self._ch = ch_writer

    def run(self, stop_event: Any) -> None:
        self._consumer.subscribe([_TOPIC])
        logger.info("BetConsumer subscribed to %s", _TOPIC)
        while not stop_event.is_set():
            try:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                self._process(msg)
                self._consumer.commit()
            except Exception:
                logger.exception("Unhandled error in BetConsumer")
        self._consumer.close()
        logger.info("BetConsumer stopped")

    def _process(self, msg: dict[str, Any]) -> None:
        try:
            envelope = CanonicalEventEnvelope(**msg)
        except Exception as exc:
            logger.error("Cannot parse bet envelope: %s", exc)
            return

        tenant_id = str(envelope.tenantId)
        payload_data = envelope.payload

        try:
            bet = CanonicalBetPayload(**payload_data)
        except Exception as exc:
            logger.error("Cannot parse CanonicalBetPayload: %s", exc)
            return

        event_date = envelope.occurredAt.date()
        raw_record: dict[str, Any] = {
            **{k: str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v
               for k, v in payload_data.items()},
            "event_id": str(envelope.eventId),
            "tenant_id": tenant_id,
        }

        # Bronze
        self._lakehouse.write_bronze(
            tenant_id=tenant_id,
            entity_type="bet",
            event_date=event_date,
            source_system=envelope.sourceSystem,
            records=[raw_record],
        )

        # Silver
        self._lakehouse.write_silver(
            tenant_id=tenant_id,
            entity_type="bet",
            event_date=event_date,
            records=[raw_record],
        )

        # ClickHouse
        ch_record: dict[str, Any] = {
            "id": str(envelope.eventId),
            "tenant_id": tenant_id,
            "player_id": bet.playerId,
            "player_cpf": bet.playerCpf,
            "external_bet_id": bet.externalBetId,
            "stake_amount": float(bet.stakeAmount),
            "odds": float(bet.odds),
            "potential_payout": float(bet.potentialPayout),
            "settled_payout": float(bet.settledPayout) if bet.settledPayout is not None else None,
            "market_type": bet.marketType,
            "sport": bet.sport,
            "event_id": bet.eventId,
            "selection": bet.selection,
            "channel": bet.channel.value,
            "placed_at": bet.placedAt,
            "settled_at": bet.settledAt,
            "source_system": envelope.sourceSystem,
            "ingested_at": datetime.now(timezone.utc),
        }
        self._ch.write_bets([ch_record])

        logger.info(
            "Processed bet %s for player %s (tenant %s)",
            envelope.sourceEventId,
            bet.playerId,
            tenant_id,
        )
