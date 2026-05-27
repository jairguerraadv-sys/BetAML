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


def _try_get_request_id_from_context() -> str | None:
    try:
        import structlog  # type: ignore

        ctx = structlog.contextvars.get_contextvars()
        request_id = ctx.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    except Exception:
        return None
    return None


def _try_bind_request_id(request_id: str | None) -> None:
    try:
        import structlog  # type: ignore

        structlog.contextvars.clear_contextvars()
        if request_id:
            structlog.contextvars.bind_contextvars(request_id=request_id)
    except Exception:
        return


def _try_bind_event_id(event_id: str | None) -> None:
    try:
        import structlog  # type: ignore

        if event_id:
            structlog.contextvars.bind_contextvars(event_id=event_id)
    except Exception:
        return


def _request_id_from_kafka_headers(headers: list[tuple[str, bytes]] | None) -> str | None:
    if not headers:
        return None
    for key, value in headers:
        if key == "X-Request-ID":
            try:
                if isinstance(value, (bytes, bytearray)):
                    decoded = value.decode("utf-8", errors="replace")
                    return decoded or None
            except Exception:
                return None
    return None


def _event_id_from_kafka_headers(headers: list[tuple[str, bytes]] | None) -> str | None:
    if not headers:
        return None
    for key, value in headers:
        if key == "X-Event-ID":
            try:
                if isinstance(value, (bytes, bytearray)):
                    decoded = value.decode("utf-8", errors="replace")
                    return decoded or None
            except Exception:
                return None
    return None


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

    async def send(
        self,
        topic: str,
        value: dict[str, Any],
        key: str | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        if not self._producer:
            raise RuntimeError("KafkaProducerClient não iniciado")
        effective_headers = headers
        if effective_headers is None:
            effective_headers = []
            request_id = _try_get_request_id_from_context()
            if request_id:
                effective_headers.append(("X-Request-ID", request_id.encode("utf-8")))
            event_id = value.get("event_id") if isinstance(value, dict) else None
            if isinstance(event_id, str) and event_id:
                effective_headers.append(("X-Event-ID", event_id.encode("utf-8")))
            if not effective_headers:
                effective_headers = None
        await self._producer.send_and_wait(topic, value=value, key=key, headers=effective_headers)


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
        enable_auto_commit: bool = True,
    ):
        self._topics = topics
        self._group_id = group_id
        self._servers = bootstrap_servers or os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
        )
        self._auto_offset_reset = auto_offset_reset
        self._enable_auto_commit = enable_auto_commit
        self._consumer: Any = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer  # type: ignore

        self._consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._servers,
            group_id=self._group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=self._enable_auto_commit,
        )
        await self._consumer.start()
        logger.info(
            "KafkaConsumer [%s] inscrito em %s", self._group_id, self._topics
        )

    async def stop(self) -> None:
        if self._consumer:
            await self._consumer.stop()

    async def _iter_messages(self):
        if not self._consumer:
            raise RuntimeError("KafkaConsumerClient não iniciado")
        async for msg in self._consumer:
            try:
                request_id = _request_id_from_kafka_headers(getattr(msg, "headers", None))
                event_id = _event_id_from_kafka_headers(getattr(msg, "headers", None))
                _try_bind_request_id(request_id)
                _try_bind_event_id(event_id)
            except Exception:
                # best-effort correlation only
                pass
            yield msg

    async def commit(self) -> None:
        if not self._consumer:
            raise RuntimeError("KafkaConsumerClient não iniciado")
        await self._consumer.commit()

    def __aiter__(self):
        return self._iter_messages()


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
        self._mode = os.getenv("REDIS_MODE", "standalone").strip().lower()
        self._cluster_nodes = os.getenv("REDIS_CLUSTER_NODES", "")
        self._redis: Any = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis  # type: ignore

        if self._mode == "cluster":
            from redis.asyncio.cluster import RedisCluster  # type: ignore

            nodes = [n.strip() for n in self._cluster_nodes.split(",") if n.strip()]
            if not nodes:
                raise RuntimeError(
                    "REDIS_MODE=cluster exige REDIS_CLUSTER_NODES com host:port (separado por vírgula)"
                )

            startup_nodes: list[dict[str, str | int]] = []
            for node in nodes:
                host, sep, port = node.partition(":")
                if not host or not sep or not port.isdigit():
                    raise RuntimeError(f"REDIS_CLUSTER_NODES inválido: '{node}' (esperado host:port)")
                startup_nodes.append({"host": host, "port": int(port)})

            self._redis = RedisCluster(startup_nodes=startup_nodes, decode_responses=True)
            await self._redis.initialize()
            logger.info("Redis Cluster conectado em %s", nodes)
            return

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

    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool:
        result = await self._redis.set(key, value, ex=ttl, nx=True)
        return bool(result)

    # ── Sorted Set helpers (janelas de tempo por player) ──────────────────
    async def zadd_event(self, key: str, score: float, value: str, window_ttl: int = 2592000) -> None:
        """Adiciona entrada ao sorted set e mantém TTL de janela."""
        await self._redis.zadd(key, {value: score})
        await self._redis.expire(key, window_ttl)

    async def zrange_by_score(self, key: str, min_score: float, max_score: float = float("inf")) -> list[str]:
        """Retorna membros do sorted set dentro do intervalo de score."""
        return await self._redis.zrangebyscore(key, min_score, max_score)

    async def zremrange_by_score(self, key: str, min_score: float, max_score: float) -> None:
        """Remove membros do sorted set abaixo do cutoff (cleanup de janela)."""
        await self._redis.zremrangebyscore(key, min_score, max_score)

    # ── Set helpers (device/bank sharing) ────────────────────────────────
    async def sadd_member(self, key: str, member: str, ttl: int = 2592000) -> None:
        await self._redis.sadd(key, member)
        await self._redis.expire(key, ttl)

    async def smembers_set(self, key: str) -> set[str]:
        return await self._redis.smembers(key)

    async def scard_set(self, key: str) -> int:
        return await self._redis.scard(key)

    async def sismember(self, key: str, member: str) -> bool:
        """Returns True if `member` is already in the Redis Set at `key`."""
        result = await self._redis.sismember(key, member)
        return bool(result)

    def features_key(self, tenant_id: str, player_id: str) -> str:
        return f"betaml:{tenant_id}:features:{player_id}"

    def txn_window_key(self, tenant_id: str, player_id: str) -> str:
        return f"betaml:{tenant_id}:txn:{player_id}"

    def bet_window_key(self, tenant_id: str, player_id: str) -> str:
        return f"betaml:{tenant_id}:bet:{player_id}"

    def device_members_key(self, tenant_id: str, device_id: str) -> str:
        return f"betaml:{tenant_id}:dev:{device_id}"

    def player_devices_key(self, tenant_id: str, player_id: str) -> str:
        return f"betaml:{tenant_id}:pdev:{player_id}"

    def bank_members_key(self, tenant_id: str, holder_doc: str) -> str:
        import hashlib
        h = hashlib.sha256(holder_doc.encode()).hexdigest()[:16]
        return f"betaml:{tenant_id}:bank:{h}"

    def player_banks_key(self, tenant_id: str, player_id: str) -> str:
        return f"betaml:{tenant_id}:pbank:{player_id}"

    def player_instruments_key(self, tenant_id: str, player_id: str) -> str:
        """Redis Set key tracking all payment-instrument fingerprints seen for a player."""
        return f"betaml:{tenant_id}:pinstr:{player_id}"

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
        _placeholders = ", ".join(["%s"] * len(cols))
        query = f"INSERT INTO {table} ({col_str}) VALUES"
        data = [tuple(r[c] for c in cols) for r in rows]
        self.execute_many(query, data)
