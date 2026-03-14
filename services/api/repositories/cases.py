"""repositories/cases.py — acesso a dados de Case."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Case


# Status válidos para persistência — mesmos do transition graph em cases.py
VALID_STATUSES = frozenset({"OPEN", "INVESTIGATING", "PENDING_REVIEW", "CLOSED", "REPORTED"})


class CaseRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, tenant_id: Any, case_id: str) -> Optional[Case]:
        """Retorna case por id, validando tenant."""
        c = await self.db.get(Case, case_id)
        if not c or c.tenant_id != tenant_id:
            return None
        return c

    async def list_filtered(
        self,
        tenant_id: Any,
        *,
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        player_id: Optional[str] = None,
        priority: Optional[str] = None,
        sla_breached: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Case]:
        """Lista cases com filtros opcionais, ordenados por created_at desc."""
        q = select(Case).where(Case.tenant_id == tenant_id)
        if status and status != "active":
            q = q.where(Case.status == status)
        elif status == "active":
            q = q.where(Case.status.in_(["OPEN", "INVESTIGATING", "PENDING_REVIEW"]))
        if assigned_to:
            q = q.where(Case.assigned_to == assigned_to)
        if player_id:
            q = q.where(Case.player_id == player_id)
        if priority:
            q = q.where(Case.priority == priority)
        if sla_breached:
            q = q.where(Case.sla_due_at < datetime.now(timezone.utc))
        q = q.order_by(Case.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(q)).scalars().all())

    async def count_filtered(
        self,
        tenant_id: Any,
        *,
        status: Optional[str] = None,
        sla_breached: bool = False,
    ) -> int:
        """Conta cases com filtros opcionais."""
        q = select(func.count()).select_from(Case).where(Case.tenant_id == tenant_id)
        if status and status != "active":
            q = q.where(Case.status == status)
        elif status == "active":
            q = q.where(Case.status.in_(["OPEN", "INVESTIGATING", "PENDING_REVIEW"]))
        if sla_breached:
            q = q.where(Case.sla_due_at < datetime.now(timezone.utc))
        return (await self.db.execute(q)).scalar() or 0

    async def count_open(self, tenant_id: Any) -> int:
        """Conta cases OPEN — usado no dashboard."""
        return await self.count_filtered(tenant_id, status="active")

    async def count_sla_breached(self, tenant_id: str) -> int:
        """Conta cases ativos com SLA vencido."""
        q = select(func.count()).select_from(Case).where(
            Case.tenant_id == tenant_id,
            Case.status.in_(["OPEN", "INVESTIGATING", "PENDING_REVIEW"]),
            Case.sla_due_at < datetime.now(timezone.utc),
        )
        return (await self.db.execute(q)).scalar() or 0

    async def transition_status(
        self,
        case: Case,
        new_status: str,
        *,
        transition_graph: dict[str, list[str]],
    ) -> None:
        """Valida e aplica transição de status. Levanta ValueError em transição inválida."""
        allowed = transition_graph.get(case.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Transição inválida: {case.status} → {new_status}. "
                f"Permitidas: {allowed}"
            )
        case.status = new_status
        self.db.add(case)

    async def assign(self, case: Case, user_id: str) -> None:
        """Atribui case a um analista."""
        case.assigned_to = user_id
        self.db.add(case)


def get_case_repo(db: AsyncSession = Depends(get_db)) -> CaseRepository:
    return CaseRepository(db)
