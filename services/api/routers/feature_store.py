"""
routers/feature_store.py — Feature Store: histórico e online features por player.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from libs.models import FeatureSnapshot, Notification, Player
from libs.schemas import (
    FeaturePopulationStatsOut,
    FeatureQualityStatusOut,
    FeatureSnapshotOut,
    FeatureStoreCurrentOut,
    FeatureStoreHistoryOut,
)

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["features"])


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


def _feature_null_ratio(rows: list[FeatureSnapshot], key: str) -> float:
    if not rows:
        return 0.0
    missing = 0
    for row in rows:
        value = (row.features or {}).get(key)
        if value in (None, "", "null"):
            missing += 1
    return missing / max(len(rows), 1)


def _feature_mean(rows: list[FeatureSnapshot], key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        value = (row.features or {}).get(key)
        if isinstance(value, bool):
            values.append(float(value))
            continue
        try:
            if value not in (None, "", "null"):
                values.append(float(value))
        except Exception:
            continue
    if not values:
        return None
    return sum(values) / len(values)


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
        "source": "redis-online",
        "feature_version": int(normalized.get("feature_version", 2) or 2),
        "snapshot_version": int(normalized.get("snapshot_version", normalized.get("feature_version", 2)) or 2),
        "entity_type": str(normalized.get("entity_type", "PLAYER") or "PLAYER"),
        "snapshot_date": normalized.get("snapshot_date"),
        "gold_object_path": normalized.get("gold_object_path"),
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
                "entity_type": str((row.features or {}).get("entity_type", "PLAYER")),
                "gold_object_path": (row.features or {}).get("gold_object_path"),
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


@router.get("/feature-store/population-stats", response_model=FeaturePopulationStatsOut)
async def get_feature_population_stats(
    current_user=Depends(get_current_user),
):
    """Retorna estatísticas de população de features do tenant (Redis, TTL 25h).

    Populado pelo job compute_feature_population_stats() diariamente às 06:00 UTC.
    Retorna objeto vazio ({computed_at: null, features: {}}) se ainda não calculado.
    """
    try:
        import redis.asyncio as aioredis
        from config import settings as _settings
        redis_client = aioredis.from_url(_settings.redis_url, decode_responses=True)
        key = f"feature_stats:{current_user.tenant_id}"
        raw = await redis_client.get(key)
        await redis_client.aclose()
    except Exception as exc:
        logger.warning("feature_population_stats_lookup_failed", error=str(exc))
        raise HTTPException(503, "Feature store temporariamente indisponível.")

    if raw is None:
        return FeaturePopulationStatsOut(computed_at=None, features={})

    try:
        stats_dict = json.loads(raw)
    except Exception as exc:
        logger.warning("feature_population_stats_parse_failed", error=str(exc))
        return FeaturePopulationStatsOut(computed_at=None, features={})

    if isinstance(stats_dict, dict) and isinstance(stats_dict.get("features"), dict):
        return FeaturePopulationStatsOut(
            computed_at=stats_dict.get("computed_at"),
            features=stats_dict.get("features") or {},
        )

    return FeaturePopulationStatsOut(computed_at=None, features=stats_dict if isinstance(stats_dict, dict) else {})


@router.get("/feature-store/quality/latest", response_model=FeatureQualityStatusOut)
async def get_feature_quality_latest(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = (
        select(FeatureSnapshot.feature_date)
        .where(_tenant_filter(FeatureSnapshot, current_user.tenant_id))
        .distinct()
        .order_by(FeatureSnapshot.feature_date.desc())
        .limit(2)
    )
    dates = list((await db.execute(stmt)).scalars().all())
    if len(dates) < 2:
        return FeatureQualityStatusOut()

    current_date, previous_date = dates[0], dates[1]
    current_rows = list(
        (
            await db.execute(
                select(FeatureSnapshot).where(
                    _tenant_filter(FeatureSnapshot, current_user.tenant_id),
                    FeatureSnapshot.feature_date == current_date,
                )
            )
        ).scalars().all()
    )
    previous_rows = list(
        (
            await db.execute(
                select(FeatureSnapshot).where(
                    _tenant_filter(FeatureSnapshot, current_user.tenant_id),
                    FeatureSnapshot.feature_date == previous_date,
                )
            )
        ).scalars().all()
    )
    if not current_rows or not previous_rows:
        return FeatureQualityStatusOut(
            feature_date=current_date.isoformat(),
            previous_feature_date=previous_date.isoformat(),
        )

    feature_keys = sorted(set().union(*[(row.features or {}).keys() for row in current_rows + previous_rows]))
    findings: list[dict[str, Any]] = []
    max_drift_score = 0.0
    for key in feature_keys:
        null_ratio = _feature_null_ratio(current_rows, key)
        prev_null_ratio = _feature_null_ratio(previous_rows, key)
        if null_ratio >= 0.30 and null_ratio - prev_null_ratio >= 0.20:
            delta = null_ratio - prev_null_ratio
            findings.append(
                {
                    "feature_name": key,
                    "finding_type": "NULL_RATIO",
                    "current_value": round(null_ratio, 4),
                    "previous_value": round(prev_null_ratio, 4),
                    "delta": round(delta, 4),
                    "severity": "CRITICAL" if null_ratio >= 0.5 else "WARN",
                }
            )
            max_drift_score = max(max_drift_score, min(1.0, null_ratio))
            continue

        mean_now = _feature_mean(current_rows, key)
        mean_prev = _feature_mean(previous_rows, key)
        if mean_now is None or mean_prev is None:
            continue
        delta = abs(mean_now - mean_prev) / max(abs(mean_prev), 1.0)
        if delta >= 0.50:
            findings.append(
                {
                    "feature_name": key,
                    "finding_type": "MEAN_DRIFT",
                    "current_value": round(mean_now, 4),
                    "previous_value": round(mean_prev, 4),
                    "delta": round(delta, 4),
                    "severity": "CRITICAL" if delta >= 1.0 else "WARN",
                }
            )
            max_drift_score = max(max_drift_score, min(1.0, delta))

    title = f"Drift de features detectado em {current_date.isoformat()}"
    notification_exists = (
        await db.execute(
            select(Notification.id).where(
                Notification.tenant_id == current_user.tenant_id,
                Notification.type == "FEATURE_DRIFT",
                Notification.title == title,
            )
        )
    ).scalar_one_or_none()

    return FeatureQualityStatusOut(
        feature_date=current_date.isoformat(),
        previous_feature_date=previous_date.isoformat(),
        drift_detected=bool(findings),
        max_drift_score=round(max_drift_score, 4),
        admin_notification_sent=notification_exists is not None,
        findings=findings[:10],
    )
