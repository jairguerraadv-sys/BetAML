"""Entry point for the rules_engine service.

Starts consumer threads for ``canonical.transactions`` and
``canonical.bets`` and handles graceful shutdown on SIGTERM / SIGINT.
"""

from __future__ import annotations

import logging
import signal
import threading
from urllib.parse import urlparse

from clients.kafka_client import KafkaConsumerClient, KafkaProducerClient
from clients.redis_client import RedisFeatureStore
from engine.alert_creator import AlertCreator
from engine.config import settings
from engine.consumers.bet_consumer import BetConsumer
from engine.consumers.transaction_consumer import TransactionConsumer
from engine.db import create_session_factory
from engine.rule_evaluator import RuleEvaluator

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
        logger.info("Shutdown signal received – stopping rules engine…")
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ------------------------------------------------------------------ #
    # Shared dependencies
    # ------------------------------------------------------------------ #
    session_factory, _engine = create_session_factory(settings.database_url)
    redis_store = RedisFeatureStore(**_parse_redis_url(settings.redis_url))
    producer = _make_producer()
    alert_creator = AlertCreator()

    def _make_evaluator() -> RuleEvaluator:
        """Each consumer thread gets its own DB session + evaluator."""
        db = session_factory()
        return RuleEvaluator(
            db_session=db,
            redis_client=redis_store,
            cache_ttl_seconds=float(settings.rules_cache_ttl_seconds),
        )

    # ------------------------------------------------------------------ #
    # Consumers
    # ------------------------------------------------------------------ #
    tx_consumer = TransactionConsumer(
        consumer=_make_consumer("transactions"),
        producer=producer,
        redis_store=redis_store,
        rule_evaluator=_make_evaluator(),
        alert_creator=alert_creator,
        session_factory=session_factory,
        high_severity_threshold=settings.high_severity_threshold,
    )
    bet_consumer = BetConsumer(
        consumer=_make_consumer("bets"),
        producer=producer,
        redis_store=redis_store,
        rule_evaluator=_make_evaluator(),
        alert_creator=alert_creator,
        session_factory=session_factory,
        high_severity_threshold=settings.high_severity_threshold,
    )

    threads = [
        threading.Thread(
            target=tx_consumer.run, args=(stop_event,), name="tx-consumer", daemon=True
        ),
        threading.Thread(
            target=bet_consumer.run, args=(stop_event,), name="bet-consumer", daemon=True
        ),
    ]

    for t in threads:
        t.start()

    logger.info("Rules engine started – %d consumer threads running", len(threads))

    for t in threads:
        t.join()

    producer.flush(timeout=10.0)
    logger.info("Rules engine shut down cleanly")


if __name__ == "__main__":
    main()
