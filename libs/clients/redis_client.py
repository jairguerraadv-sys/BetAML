"""Redis feature-store client for per-player feature vectors."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)

_KEY_TEMPLATE = "betaml:{tenant_id}:player:{player_id}:features"


class RedisFeatureStore:
    """Per-player feature store backed by Redis.

    Parameters
    ----------
    host:
        Redis host (default ``localhost``).
    port:
        Redis port (default ``6379``).
    db:
        Redis database index (default ``0``).
    password:
        Optional Redis password.
    ssl:
        Enable TLS (default ``False``).
    client:
        Supply a pre-constructed :class:`redis.Redis` instance to skip
        automatic connection creation (useful for testing with fakeredis).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        ssl: bool = False,
        client: Optional[redis.Redis] = None,
    ) -> None:
        if client is not None:
            self._redis = client
        else:
            self._redis = redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                ssl=ssl,
                decode_responses=True,
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_player_features(
        self,
        tenant_id: str,
        player_id: str,
        features: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> None:
        """Serialise *features* to JSON and store under the player key.

        Parameters
        ----------
        tenant_id:
            Tenant identifier used to namespace the Redis key.
        player_id:
            Player identifier.
        features:
            Arbitrary feature dict (must be JSON-serialisable).
        ttl_seconds:
            Key TTL in seconds (default 1 hour).
        """
        key = _KEY_TEMPLATE.format(tenant_id=tenant_id, player_id=player_id)
        serialised = json.dumps(features, default=str)
        self._redis.setex(key, ttl_seconds, serialised)
        logger.debug("Set features for player %s (tenant %s), TTL=%ds", player_id, tenant_id, ttl_seconds)

    def get_player_features(
        self,
        tenant_id: str,
        player_id: str,
    ) -> Optional[dict[str, Any]]:
        """Retrieve and deserialise the stored feature dict.

        Returns *None* if the key does not exist or has expired.
        """
        key = _KEY_TEMPLATE.format(tenant_id=tenant_id, player_id=player_id)
        raw = self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def delete_player_features(self, tenant_id: str, player_id: str) -> bool:
        """Delete the feature key.  Returns ``True`` if the key existed."""
        key = _KEY_TEMPLATE.format(tenant_id=tenant_id, player_id=player_id)
        return bool(self._redis.delete(key))

    def get_ttl(self, tenant_id: str, player_id: str) -> int:
        """Return the remaining TTL in seconds, or ``-2`` if key is absent."""
        key = _KEY_TEMPLATE.format(tenant_id=tenant_id, player_id=player_id)
        return self._redis.ttl(key)

    def ping(self) -> bool:
        """Return ``True`` if Redis is reachable."""
        try:
            return self._redis.ping()
        except redis.RedisError:
            return False
