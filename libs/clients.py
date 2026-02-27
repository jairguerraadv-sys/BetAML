"""
Shared async clients: Kafka (aiokafka), Redis, ClickHouse.
Cada cliente é iniciado como singleton configurado por env vars.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────
# Kafka Producer / Consumer wrappers
# ──────────────────────────────────────────────────

class KafkaProducerClient:
    """
    Wrapper leve sobre aiokafka.AIOKafkaProducer.
    Serializa mensagens como JSON UTF-8.
    """

    def __init__(self, bootstrap_servers: str | None = None):
        self._servers = bootstrap_servers or os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
        self._producer: Any = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer  # type: ignore

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._servers,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            enable_idempotence=True,
            acks="all",
        )
        await self._producer.start()
        logger.info("KafkaProducer conectado em %s", self._servers)

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def send(self, topic: str, value: dict[str, Any], key: str | None = None) -> None:
        if not self._producer:
            raise RuntimeError("KafkaProducerClient não iniciado")
        await self._producer.send_and_wait(topic, value=value, key=key)


class KafkaConsumerClient:
    """
    Wrapper sobre aiokafka.AIOKafkaConsumer para loop de consumo.
    """

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str | None = None,
        auto_offset_reset: str = "earliest",
    ):
        self._topics = topics
        self._group_id = group_id
        self._servers = bootstrap_servers or os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
        self._auto_offset_reset = auto_offset_reset
        self._consumer: Any = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer  # type: ignore

        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._servers,
            group_id=self._group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=True,
        )
        await self._consumer.start()
        logger.info(
            "KafkaConsumer [%s] inscrito em %s", self._group_id, self._topics
        )

    async def stop(self) -> None:
        if self._consumer:
            await self._consumer.stop()

    def __aiter__(self):
        return self._consumer.__aiter__()


# ──────────────────────────────────────────────────
# Redis client
# ──────────────────────────────────────────────────

class RedisClient:
    """
    Wrapper sobre redis.asyncio.Redis.
    Operações: get_features, set_features, get_json, set_json.
    """

    def __init__(self, url: str | None = None):
        self._url = url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis: Any = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis  # type: ignore

        self._redis = aioredis.from_url(self._url, decode_responses=True)
        await self._redis.ping()
        logger.info("Redis conectado em %s", self._url)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def hset_dict(self, key: str, data: dict[str, str], ttl: int = 3600) -> None:
        await self._redis.hset(key, mapping=data)
        await self._redis.expire(key, ttl)

    async def hgetall(self, key: str) -> dict[str, str]:
        return await self._redis.hgetall(key)

    async def set_json(self, key: str, value: Any, ttl: int = 3600) -> None:
        await self._redis.set(key, json.dumps(value, default=str), ex=ttl)

    async def get_json(self, key: str) -> Any:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    def features_key(self, tenant_id: str, player_id: str) -> str:
        return f"betaml:{tenant_id}:features:{player_id}"

    def dedup_key(self, tenant_id: str, source_system: str, source_event_id: str) -> str:
        return f"betaml:{tenant_id}:dedup:{source_system}:{source_event_id}"


# ──────────────────────────────────────────────────
# ClickHouse client
# ──────────────────────────────────────────────────

class ClickHouseClient:
    """
    Wrapper sobre clickhouse_driver (sync) para consultas OLAP.
    Para operações batch, use execute_many.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int = 9000,
        database: str = "default",
        user: str = "default",
        password: str = "",
    ):
        self._host = host or os.getenv("CLICKHOUSE_HOST", "localhost")
        self._port = int(os.getenv("CLICKHOUSE_PORT", str(port)))
        self._database = os.getenv("CLICKHOUSE_DB", database)
        self._user = os.getenv("CLICKHOUSE_USER", user)
        self._password = os.getenv("CLICKHOUSE_PASSWORD", password)
        self._client: Any = None

    def connect(self) -> None:
        from clickhouse_driver import Client  # type: ignore

        self._client = Client(
            host=self._host,
            port=self._port,
            database=self._database,
            user=self._user,
            password=self._password,
        )
        logger.info("ClickHouse conectado em %s:%s", self._host, self._port)

    def execute(self, query: str, params: Any = None) -> list[Any]:
        if not self._client:
            self.connect()
        return self._client.execute(query, params or [])

    def execute_many(self, query: str, rows: list[Any]) -> None:
        if not self._client:
            self.connect()
        self._client.execute(query, rows)

    def insert_dict(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        col_str = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        query = f"INSERT INTO {table} ({col_str}) VALUES"
        data = [tuple(r[c] for c in cols) for r in rows]
        self.execute_many(query, data)
