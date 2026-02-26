"""Alert and Case creator for the rules_engine service.

Uses raw SQL via SQLAlchemy ``text()`` to avoid duplicating the ORM model
definitions that live in the API service.  The target tables are expected to
exist (managed by the API's Alembic migrations).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SEVERITY_SCORES: dict[str, float] = {
    "LOW": 0.3,
    "MEDIUM": 0.5,
    "HIGH": 0.8,
    "CRITICAL": 1.0,
}


class AlertCreator:
    """Creates Alert and AuditLog rows and optionally links/creates Cases."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_alert(
        self,
        db: Session,
        tenant_id: str,
        player_id: str,
        player_cpf: str | None,
        rule_match: dict[str, Any],
    ) -> uuid.UUID:
        """Insert an Alert + AuditLog and return the new alert UUID.

        Uses INSERT … RETURNING so no extra SELECT is needed.
        """
        alert_id = uuid.uuid4()
        severity = rule_match.get("severity", "MEDIUM")
        rule_id_str = rule_match.get("rule_id")
        rule_id = uuid.UUID(rule_id_str) if rule_id_str else None

        db.execute(
            text(
                "INSERT INTO alerts "
                "(id, tenant_id, player_id, player_cpf, rule_id, alert_type, severity, "
                " status, evidence, risk_score, created_at, updated_at) "
                "VALUES "
                "(:id, :tenant_id, :player_id, :player_cpf, :rule_id, 'RULE', :severity, "
                " 'OPEN', :evidence::jsonb, :risk_score, now(), now())"
            ),
            {
                "id": alert_id,
                "tenant_id": uuid.UUID(tenant_id),
                "player_id": player_id,
                "player_cpf": player_cpf,
                "rule_id": rule_id,
                "severity": severity,
                "evidence": _json_dumps(rule_match.get("evidence", {})),
                "risk_score": _SEVERITY_SCORES.get(severity),
            },
        )

        db.execute(
            text(
                "INSERT INTO audit_logs "
                "(id, tenant_id, user_id, action, entity_type, entity_id, "
                " old_values, new_values, created_at) "
                "VALUES "
                "(:id, :tenant_id, NULL, 'CREATE', 'Alert', :entity_id, "
                " NULL, :new_values::jsonb, now())"
            ),
            {
                "id": uuid.uuid4(),
                "tenant_id": uuid.UUID(tenant_id),
                "entity_id": str(alert_id),
                "new_values": _json_dumps(
                    {
                        "player_id": player_id,
                        "rule_id": rule_id_str,
                        "severity": severity,
                        "status": "OPEN",
                    }
                ),
            },
        )

        db.commit()
        logger.info(
            "Created alert %s for player %s (rule %s, severity %s)",
            alert_id,
            player_id,
            rule_id_str,
            severity,
        )
        return alert_id

    def create_or_get_case(
        self,
        db: Session,
        tenant_id: str,
        player_id: str,
        alert_id: uuid.UUID,
    ) -> uuid.UUID:
        """Link *alert_id* to an existing open case or create a new one."""
        row = db.execute(
            text(
                "SELECT id FROM cases "
                "WHERE tenant_id = :tenant_id AND player_id = :player_id AND status = 'OPEN' "
                "LIMIT 1"
            ),
            {"tenant_id": uuid.UUID(tenant_id), "player_id": player_id},
        ).fetchone()

        if row:
            case_id = row[0]
        else:
            case_id = uuid.uuid4()
            db.execute(
                text(
                    "INSERT INTO cases "
                    "(id, tenant_id, title, description, status, player_id, created_at, updated_at) "
                    "VALUES "
                    "(:id, :tenant_id, :title, :description, 'OPEN', :player_id, now(), now())"
                ),
                {
                    "id": case_id,
                    "tenant_id": uuid.UUID(tenant_id),
                    "title": f"Auto-case for player {player_id}",
                    "description": f"Automatically created by rules engine (alert {alert_id})",
                    "player_id": player_id,
                },
            )
            logger.info("Created case %s for player %s", case_id, player_id)

        # Link alert to case
        db.execute(
            text("UPDATE alerts SET case_id = :case_id WHERE id = :alert_id"),
            {"case_id": case_id, "alert_id": alert_id},
        )
        db.commit()
        return case_id


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)
