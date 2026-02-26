import json
import logging
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_producer = None

try:
    from confluent_kafka import KafkaException, Producer as KafkaProducer
    _KAFKA_AVAILABLE = True
except ImportError:
    _KAFKA_AVAILABLE = False
    logger.warning("confluent-kafka not installed; Kafka publishing disabled")


def _get_producer():
    global _producer
    if not _KAFKA_AVAILABLE:
        return None
    if _producer is None:
        try:
            _producer = KafkaProducer({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS})
        except KafkaException as exc:
            logger.error("Kafka producer initialization failed: %s", exc)
            _producer = None
    return _producer


def publish(topic: str, key: str, value: dict[str, Any], headers: Optional[dict[str, str]] = None) -> None:
    producer = _get_producer()
    if producer is None:
        logger.warning("Kafka not available; dropping message to topic=%s key=%s", topic, key)
        return
    encoded_key = key.encode("utf-8")
    encoded_value = json.dumps(value, default=str).encode("utf-8")
    kwargs: dict[str, Any] = {"topic": topic, "key": encoded_key, "value": encoded_value}
    if headers:
        kwargs["headers"] = headers
    producer.produce(**kwargs)
    producer.poll(0)


def flush(timeout: float = 5.0) -> None:
    if _producer is not None:
        _producer.flush(timeout)
