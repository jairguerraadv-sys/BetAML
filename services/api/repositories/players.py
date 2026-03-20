"""repositories/players.py — acesso a dados de Player."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Player


class PlayerRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, tenant_id: str, player_id: str) -> Optional[Player]:
        """Retorna player por id (UUID interno) OU external_player_id, validando tenant."""
        try:
            UUID(str(player_id))
            is_uuid = True
        except Exception:
            is_uuid = False

        # Se parece UUID, trate como PK interna (não faz fallback), evitando
        # leaks cross-tenant e ambiguidades quando external IDs coincidem.
        if is_uuid:
            p = await self.db.get(Player, player_id)
            if not p or p.tenant_id != tenant_id:
                return None
            return p

        # Caso contrário, trate como external_player_id.
        return await self.get_by_external_id(tenant_id, player_id)

    async def list_active(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        risk_band: Optional[str] = None,
        pep_only: bool = False,
    ) -> list[Player]:
        """Lista players ativos (excluindo ERASED) com filtros opcionais."""
        q = (
            select(Player)
            .where(
                Player.tenant_id == tenant_id,
                Player.status != "ERASED",
            )
        )
        if risk_band:
            q = q.where(Player.risk_band == risk_band)
        if pep_only:
            q = q.where(Player.pep_flag == True)  # noqa: E712
        q = q.order_by(Player.created_at.desc()).limit(limit).offset(offset)
        return list((await self.db.execute(q)).scalars().all())

    async def count_active(self, tenant_id: str) -> int:
        """Conta players ativos (excluindo ERASED)."""
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.count()).select_from(Player).where(
                Player.tenant_id == tenant_id,
                Player.status != "ERASED",
            )
        )
        return result.scalar() or 0

    async def get_by_external_id(
        self, tenant_id: str, external_player_id: str
    ) -> Optional[Player]:
        """Busca player por external_player_id do sistema de origem."""
        result = await self.db.execute(
            select(Player).where(
                Player.tenant_id == tenant_id,
                Player.external_player_id == external_player_id,
            )
        )
        return result.scalar_one_or_none()

    async def mark_erased(self, player: Player) -> None:
        """Apaga dados PII e marca player como ERASED (LGPD Art. 18)."""
        player.status = "ERASED"
        player.cpf_encrypted = b""
        player.name_encrypted = b""
        player.birth_date = None
        player.declared_income_monthly = None
        player.profession = None
        self.db.add(player)


def get_player_repo(db: AsyncSession = Depends(get_db)) -> PlayerRepository:
    return PlayerRepository(db)
