"""DSL-based rule evaluator for the rules_engine service."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from clients.redis_client import RedisFeatureStore
from dsl.parser import DSLEvalError, DSLParseError, DSLEvaluator, DSLParser

logger = logging.getLogger(__name__)

_parser = DSLParser()
_evaluator = DSLEvaluator()


@dataclass
class RuleData:
    """Lightweight representation of a ``rule_definitions`` row."""

    id: uuid.UUID
    name: str
    severity: str
    scope: str
    condition_dsl: str
    params: dict[str, Any]
    version: int


class RuleEvaluator:
    """Loads active rules from Postgres and evaluates them via the DSL engine.

    Parameters
    ----------
    db_session:
        A synchronous SQLAlchemy ``Session`` used to query rule definitions.
    redis_client:
        ``RedisFeatureStore`` instance (kept for future feature look-ups).
    cache_ttl_seconds:
        How long to cache per-tenant rules before re-fetching (default 60 s).
    """

    def __init__(
        self,
        db_session: Session,
        redis_client: RedisFeatureStore,
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self._db = db_session
        self._redis = redis_client
        self._cache_ttl = cache_ttl_seconds
        # tenant_id -> (rules, monotonic_time_loaded)
        self._cache: dict[str, tuple[list[RuleData], float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_rules(
        self,
        tenant_id: str,
        event_envelope: dict[str, Any],
        features: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Evaluate all active rules for *tenant_id* against the event.

        Returns a list of matched-rule dicts, each with:
        ``rule_id``, ``rule_name``, ``severity``, ``evidence``,
        ``execution_time_ms``.
        """
        rules = self._load_rules(tenant_id)
        matches: list[dict[str, Any]] = []

        for rule in rules:
            try:
                matched, exec_ms = self._eval_rule(rule, event_envelope, features)
            except Exception as exc:
                logger.warning("Skipping rule %s due to error: %s", rule.id, exc)
                continue

            if matched:
                matches.append(
                    {
                        "rule_id": str(rule.id),
                        "rule_name": rule.name,
                        "severity": rule.severity,
                        "evidence": {
                            "features": features,
                            "thresholds": rule.params,
                            "ruleVersion": rule.version,
                        },
                        "execution_time_ms": exec_ms,
                    }
                )

        return matches

    def invalidate_cache(self, tenant_id: str) -> None:
        """Remove cached rules for *tenant_id* (force reload on next call)."""
        self._cache.pop(tenant_id, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_rules(self, tenant_id: str) -> list[RuleData]:
        """Return active rules for *tenant_id*, refreshing the cache if stale."""
        entry = self._cache.get(tenant_id)
        now = time.monotonic()
        if entry is not None and (now - entry[1]) < self._cache_ttl:
            return entry[0]

        try:
            rows = self._db.execute(
                text(
                    "SELECT id, name, severity, scope, condition_dsl, params, version "
                    "FROM rule_definitions "
                    "WHERE tenant_id = :tenant_id AND status = 'ACTIVE'"
                ),
                {"tenant_id": tenant_id},
            ).fetchall()
        except Exception as exc:
            logger.error("Failed to load rules for tenant %s: %s", tenant_id, exc)
            return self._cache.get(tenant_id, ([], 0.0))[0]  # serve stale if available

        rules = [
            RuleData(
                id=row[0],
                name=row[1],
                severity=row[2],
                scope=row[3],
                condition_dsl=row[4],
                params=row[5] or {},
                version=row[6],
            )
            for row in rows
        ]
        self._cache[tenant_id] = (rules, now)
        logger.debug("Loaded %d active rules for tenant %s", len(rules), tenant_id)
        return rules

    def _eval_rule(
        self,
        rule: RuleData,
        event_envelope: dict[str, Any],
        features: dict[str, Any],
    ) -> tuple[bool, int]:
        """Evaluate *rule* against the event context.

        Returns ``(matched, execution_time_ms)``.
        """
        start = time.monotonic()

        entity_type = str(
            event_envelope.get("entityType") or event_envelope.get("entity_type") or ""
        ).upper()

        payload = event_envelope.get("payload") or {}

        context: dict[str, Any] = {
            "transaction": payload if entity_type == "TRANSACTION" else {},
            "bet": payload if entity_type == "BET" else {},
            # player fields present in transaction/bet payloads (pepFlag etc.)
            "player": {k: v for k, v in payload.items()},
            "features": features,
            "event": event_envelope,
        }

        try:
            ast = _parser.parse(rule.condition_dsl)
            matched = _evaluator.evaluate(ast, context)
        except (DSLParseError, DSLEvalError) as exc:
            logger.debug("DSL error for rule %s (%s): %s", rule.name, rule.id, exc)
            matched = False

        exec_ms = int((time.monotonic() - start) * 1000)
        return matched, exec_ms
