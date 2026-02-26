"""Entry point for the stream_processor service.

Starts one consumer thread per canonical topic and handles graceful
shutdown on SIGTERM / SIGINT.
"""

from __future__ import annotations

import logging
import signal
import threading
from urllib.parse import urlparse

from clients.kafka_client import KafkaConsumerClient, KafkaProducerClient
from clients.redis_client import RedisFeatureStore
from processor.clickhouse_writer import ClickHouseWriter
from processor.config import settings
from processor.consumers.bet_consumer import BetConsumer
from processor.consumers.device_consumer import DeviceConsumer
from processor.consumers.player_consumer import PlayerConsumer
from processor.consumers.transaction_consumer import TransactionConsumer
from processor.feature_engine import FeatureEngine
from processor.lakehouse_writer import LakehouseWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_redis_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "db": int(parsed.path.lstrip("/") or "0"),
        "password": parsed.password,
    }


def _make_consumer(group_suffix: str) -> KafkaConsumerClient:
    return KafkaConsumerClient(
        {
            "bootstrap.servers": settings.kafka_bootstrap_servers,
            "group.id": f"{settings.kafka_consumer_group}-{group_suffix}",
            "auto.offset.reset": "earliest",
        }
    )


def _make_producer() -> KafkaProducerClient:
    return KafkaProducerClient(
        {"bootstrap.servers": settings.kafka_bootstrap_servers}
    )


def main() -> None:
    stop_event = threading.Event()

    def _handle_signal(*_: object) -> None:
        logger.info("Shutdown signal received – stopping consumers…")
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ------------------------------------------------------------------ #
    # Shared dependencies
    # ------------------------------------------------------------------ #
    redis_store = RedisFeatureStore(**_parse_redis_url(settings.redis_url))

    lakehouse = LakehouseWriter(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
    )

    ch_writer = ClickHouseWriter(
        host=settings.clickhouse_host,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )

    feature_engine = FeatureEngine()
    producer = _make_producer()

    # ------------------------------------------------------------------ #
    # Consumers
    # ------------------------------------------------------------------ #
    tx_consumer = TransactionConsumer(
        consumer=_make_consumer("transactions"),
        producer=producer,
        redis_store=redis_store,
        lakehouse=lakehouse,
        ch_writer=ch_writer,
        feature_engine=feature_engine,
    )
    bet_consumer = BetConsumer(
        consumer=_make_consumer("bets"),
        producer=producer,
        lakehouse=lakehouse,
        ch_writer=ch_writer,
    )
    player_consumer = PlayerConsumer(
        consumer=_make_consumer("players"),
        lakehouse=lakehouse,
    )
    device_consumer = DeviceConsumer(
        consumer=_make_consumer("devices"),
        lakehouse=lakehouse,
    )

    threads = [
        threading.Thread(
            target=tx_consumer.run, args=(stop_event,), name="tx-consumer", daemon=True
        ),
        threading.Thread(
            target=bet_consumer.run, args=(stop_event,), name="bet-consumer", daemon=True
        ),
        threading.Thread(
            target=player_consumer.run, args=(stop_event,), name="player-consumer", daemon=True
        ),
        threading.Thread(
            target=device_consumer.run, args=(stop_event,), name="device-consumer", daemon=True
        ),
    ]

    for t in threads:
        t.start()

    logger.info("Stream processor started – %d consumer threads running", len(threads))

    for t in threads:
        t.join()

    producer.flush(timeout=10.0)
    ch_writer.close()
    logger.info("Stream processor shut down cleanly")


if __name__ == "__main__":
    main()
