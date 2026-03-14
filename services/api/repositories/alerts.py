"""repositories/alerts.py — acesso a dados de Alert."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Alert


class AlertRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, tenant_id: str, alert_id: str) -> Optional[Alert]:
        """Retorna alert por id, validando tenant."""
        a = await self.db.get(Alert, alert_id)
        if not a or a.tenant_id != tenant_id:
            return None
        return a

    async def list_filtered(
        self,
        tenant_id: str,
        *,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        player_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        created_after: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Alert]:
        """Lista alertas com filtros opcionais, ordenados por created_at desc."""
        q = select(Alert).where(Alert.tenant_id == tenant_id)
        if severity:
            q = q.where(Alert.severity == severity)
        if status:
            q = q.where(Alert.status == status)
        if player_id:
            q = q.where(Alert.player_id == player_id)
        if rule_id:
            q = q.where(Alert.rule_id == rule_id)
        if created_after:
            q = q.where(Alert.created_at > created_after)
        q = q.order_by(Alert.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(q)).scalars().all())

    async def count_filtered(
        self,
        tenant_id: str,
        *,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        player_id: Optional[str] = None,
    ) -> int:
        """Conta alertas com filtros opcionais."""
        q = select(func.count()).select_from(Alert).where(Alert.tenant_id == tenant_id)
        if severity:
            q = q.where(Alert.severity == severity)
        if status:
            q = q.where(Alert.status == status)
        if player_id:
            q = q.where(Alert.player_id == player_id)
        return (await self.db.execute(q)).scalar() or 0

    async def count_by_severity(self, tenant_id: str) -> dict[str, int]:
        """Retorna contagem de alertas OPEN agrupados por severidade."""
        rows = (
            await self.db.execute(
                select(Alert.severity, func.count().label("n"))
                .where(Alert.tenant_id == tenant_id, Alert.status == "OPEN")
                .group_by(Alert.severity)
            )
        ).all()
        return {row.severity: row.n for row in rows}

    async def list_open_recent(
        self, tenant_id: str, *, limit: int = 10, created_after: Optional[datetime] = None
    ) -> list[Alert]:
        """Lista alertas OPEN recentes — usado pelo SSE stream."""
        q = select(Alert).where(Alert.tenant_id == tenant_id, Alert.status == "OPEN")
        if created_after:
            q = q.where(Alert.created_at > created_after)
        q = q.order_by(Alert.created_at.desc()).limit(limit)
        return list((await self.db.execute(q)).scalars().all())

    async def update_status(
        self,
        alert: Alert,
        new_status: str,
        *,
        triaged_by: Optional[str] = None,
        label: Optional[str] = None,
        label_note: Optional[str] = None,
    ) -> None:
        """Atualiza status e campos de triagem do alert."""
        alert.status = new_status
        if triaged_by is not None:
            alert.triaged_by = triaged_by
            alert.triaged_at = datetime.now(__import__("datetime").timezone.utc)
        if label is not None:
            alert.label = label
        if label_note is not None:
            alert.label_note = label_note
        self.db.add(alert)


def get_alert_repo(db: AsyncSession = Depends(get_db)) -> AlertRepository:
    return AlertRepository(db)
