"""
routers/feature_store.py — Feature Store: histórico e online features por player.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from libs.models import FeatureSnapshot, Player
from libs.schemas import FeatureSnapshotOut, FeatureStoreCurrentOut, FeatureStoreHistoryOut

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["feature-store"])


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


def _coerce_feature_value(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            if any(ch in value for ch in (".", "e", "E")):
                return float(value)
            return int(value)
        except ValueError:
            return value
    return value


async def _get_feature_store_current_payload(player_id: str, tenant_id: str) -> dict[str, Any]:
    try:
        import redis.asyncio as aioredis
        from config import settings as _settings

        redis_client = aioredis.from_url(_settings.redis_url, decode_responses=True)
        key = f"betaml:{tenant_id}:features:{player_id}"
        data = await redis_client.hgetall(key)
        await redis_client.aclose()
    except Exception as exc:
        logger.warning("feature_store_current_lookup_failed", error=str(exc), player_id=player_id)
        raise HTTPException(503, "Feature store temporariamente indisponível.")

    if not data:
        raise HTTPException(404, "Nenhuma feature encontrada para este player. Pode ainda não ter transacionado.")

    normalized = {k: _coerce_feature_value(v) for k, v in data.items()}
    return {
        "player_id": player_id,
        "source": "redis",
        "feature_version": int(normalized.get("feature_version", 2) or 2),
        "computed_at": normalized.get("computed_at"),
        "features": normalized,
    }


@router.get("/players/{player_id}/features", response_model=list[FeatureSnapshotOut])
async def get_player_features_history(
    player_id: str,
    days: int = Query(30, le=365),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retorna snapshots diários de features do player (Gold layer — Postgres)."""
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    from_dt = datetime.now(UTC) - timedelta(days=days)
    try:
        result = await db.execute(
            select(FeatureSnapshot).where(
                FeatureSnapshot.player_id == player_id,
                _tenant_filter(FeatureSnapshot, current_user.tenant_id),
                FeatureSnapshot.created_at >= from_dt,
            ).order_by(FeatureSnapshot.snapshot_date)
        )
        return result.scalars().all()
    except Exception as exc:
        logger.warning("feature_snapshot_query_error", error=str(exc), player_id=player_id)
        return []


@router.get("/feature-store/players/{player_id}/history", response_model=FeatureStoreHistoryOut)
async def get_feature_store_player_history(
    player_id: str,
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Histórico canônico (feature-store) por janela de datas, baseado nos snapshots Gold."""
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    if from_date and to_date and from_date > to_date:
        raise HTTPException(400, "Parâmetro 'from' não pode ser maior que 'to'")

    stmt = select(FeatureSnapshot).where(
        FeatureSnapshot.player_id == player_id,
        _tenant_filter(FeatureSnapshot, current_user.tenant_id),
    )
    if from_date:
        stmt = stmt.where(FeatureSnapshot.snapshot_date >= from_date)
    if to_date:
        stmt = stmt.where(FeatureSnapshot.snapshot_date <= to_date)
    stmt = stmt.order_by(FeatureSnapshot.snapshot_date.desc())

    rows = (await db.execute(stmt)).scalars().all()
    return {
        "player_id": player_id,
        "from": from_date.isoformat() if from_date else None,
        "to": to_date.isoformat() if to_date else None,
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "snapshot_date": str(row.snapshot_date_value),
                "created_at": row.created_at,
                "features": row.features,
                "drift_score": row.drift_score,
                "feature_version": row.feature_version,
            }
            for row in rows
        ],
    }


@router.get("/feature-store/players/{player_id}/current", response_model=FeatureStoreCurrentOut)
async def get_feature_store_player_current(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Features online atuais do player, servidas direto do Redis."""
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    return await _get_feature_store_current_payload(player_id, current_user.tenant_id)


@router.get("/players/{player_id}/features/current", response_model=FeatureStoreCurrentOut)
async def get_player_features_current(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Alias canônico: features online atuais do player (Redis)."""
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    return await _get_feature_store_current_payload(player_id, current_user.tenant_id)
