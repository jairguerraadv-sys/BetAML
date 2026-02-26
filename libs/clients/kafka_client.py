"""Kafka producer and consumer wrappers using confluent-kafka."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from confluent_kafka import Consumer, Producer
from confluent_kafka import Message as KafkaMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


class KafkaProducerClient:
    """Thin wrapper around :class:`confluent_kafka.Producer`.

    Parameters
    ----------
    config:
        Dict passed verbatim to :class:`confluent_kafka.Producer`.
        Must at minimum include ``bootstrap.servers``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._producer = Producer(config)

    def produce(
        self,
        topic: str,
        key: str,
        value_dict: dict[str, Any],
        *,
        headers: Optional[dict[str, str]] = None,
        on_delivery: Optional[Callable[[Exception | None, KafkaMessage], None]] = None,
    ) -> None:
        """Serialise *value_dict* to JSON and produce a message to *topic*.

        Parameters
        ----------
        topic:
            Target Kafka topic name.
        key:
            Message key (encoded as UTF-8).
        value_dict:
            Payload to serialise as JSON.
        headers:
            Optional dict of message headers.
        on_delivery:
            Optional delivery-report callback ``(err, msg) -> None``.
        """
        encoded_key = key.encode("utf-8")
        encoded_value = json.dumps(value_dict, default=str).encode("utf-8")

        produce_kwargs: dict[str, Any] = {
            "topic": topic,
            "key": encoded_key,
            "value": encoded_value,
        }
        if headers:
            produce_kwargs["headers"] = headers
        if on_delivery:
            produce_kwargs["on_delivery"] = on_delivery

        self._producer.produce(**produce_kwargs)

    def flush(self, timeout: float = 30.0) -> int:
        """Flush outstanding messages and wait up to *timeout* seconds.

        Returns the number of messages still in queue (0 on success).
        """
        return self._producer.flush(timeout)

    def poll(self, timeout: float = 0.0) -> int:
        """Trigger delivery-report callbacks for produced messages."""
        return self._producer.poll(timeout)


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------


class KafkaConsumerClient:
    """Thin wrapper around :class:`confluent_kafka.Consumer`.

    Parameters
    ----------
    config:
        Dict passed verbatim to :class:`confluent_kafka.Consumer`.
        Must at minimum include ``bootstrap.servers`` and ``group.id``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        # Ensure we don't auto-commit by default; callers control offsets.
        effective_config = {"enable.auto.commit": False, **config}
        self._consumer = Consumer(effective_config)

    def subscribe(
        self,
        topics: list[str],
        on_assign: Optional[Callable] = None,
        on_revoke: Optional[Callable] = None,
    ) -> None:
        """Subscribe to *topics*.

        Parameters
        ----------
        topics:
            List of topic names to subscribe to.
        on_assign / on_revoke:
            Optional rebalance callbacks.
        """
        kwargs: dict[str, Any] = {}
        if on_assign:
            kwargs["on_assign"] = on_assign
        if on_revoke:
            kwargs["on_revoke"] = on_revoke
        self._consumer.subscribe(topics, **kwargs)

    def poll(self, timeout: float = 1.0) -> Optional[dict[str, Any]]:
        """Poll for a single message and return it deserialised.

        Returns the decoded JSON payload dict, or *None* if no message was
        available within *timeout* seconds.

        Raises :class:`RuntimeError` on Kafka errors.
        """
        msg: Optional[KafkaMessage] = self._consumer.poll(timeout)
        if msg is None:
            return None
        if msg.error():
            raise RuntimeError(f"Kafka consumer error: {msg.error()}")
        raw = msg.value()
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    def commit(self, asynchronous: bool = False) -> None:
        """Commit the current offsets."""
        self._consumer.commit(asynchronous=asynchronous)

    def close(self) -> None:
        """Close the consumer and release resources."""
        self._consumer.close()

    def __enter__(self) -> "KafkaConsumerClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
