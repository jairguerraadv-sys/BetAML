"""
routers/ml.py — Model registry, A/B testing and feedback-loop analytics.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, require_role_any
from config import settings
from database import get_db
from libs.models import Alert, AuditLog, ModelInferenceLog, ModelRegistry, ScoringConfig
from libs.schemas import (
    ModelABMetricsOut,
    ModelPerformanceSummaryOut,
    ModelRegistryOut,
)
from utils import write_audit

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ml"])

MODEL_REGISTRY_READ_ROLES = [
    AppRole.ANALISTA,
    AppRole.GESTOR,
    AppRole.ADMIN_TECNICO,
    AppRole.SUPER_ADMIN,
]
MODEL_REGISTRY_WRITE_ROLES = [AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN]


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


def _is_champion_status(status: Any) -> bool:
    return str(status or "") in {"champion", "active", "PRODUCTION"}


async def _write_audit(db, tenant_id, actor, action, resource_type, resource_id=None, details=None):
    db.add(AuditLog(
        tenant_id=tenant_id, user_id=actor, action=action,
        entity_type=resource_type,
        entity_id=str(resource_id) if resource_id else None,
        after=details or {},
    ))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _alert_model_id(alert: Any) -> str | None:
    evidence = getattr(alert, "evidence", None) or {}
    if not isinstance(evidence, dict):
        return None
    value = evidence.get("model_id")
    return str(value) if value else None


def _alert_rule_name(alert: Any) -> str:
    title = str(getattr(alert, "title", "") or "").strip()
    if "—" in title:
        return title.split("—", 1)[0].strip()
    if title:
        return title
    rule_id = getattr(alert, "rule_id", None)
    if rule_id:
        return f"Rule {str(rule_id)[:8]}"
    return "Sem regra"


def _precision(tp: int, fp: int) -> float:
    den = tp + fp
    return round(tp / den, 4) if den else 0.0


def _false_positive_rate(tp: int, fp: int) -> float:
    den = tp + fp
    return round(fp / den, 4) if den else 0.0


def _status_rank(status: str) -> tuple[int, str]:
    ordering = {
        "champion": 0,
        "active": 0,
        "challenger": 1,
        "PRODUCTION": 0,
        "STAGING": 2,
        "archived": 3,
        "ARCHIVED": 3,
    }
    return ordering.get(status, 9), status


def _build_performance_summary(
    models: list[Any],
    alerts: list[Any],
    *,
    days: int,
    challenger_split_pct: int = 0,
) -> dict[str, Any]:
    total_tp_with_model = 0
    by_day: dict[str, dict[str, Any]] = {}
    by_rule: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}

    for model in models:
        model_id = str(model.id)
        by_model[model_id] = {
            "model_id": model_id,
            "model_name": getattr(model, "model_name", None),
            "algorithm": getattr(model, "algorithm", None),
            "status": str(getattr(model, "status", "STAGING") or "STAGING"),
            "total_alerts": 0,
            "true_positive_count": 0,
            "false_positive_count": 0,
            "unknown_count": 0,
            "precision_estimated": 0.0,
            "recall_estimated": 0.0,
            "false_positive_rate": 0.0,
        }

    totals = {
        "total_alerts": 0,
        "labeled_alerts": 0,
        "true_positive_count": 0,
        "false_positive_count": 0,
        "unknown_count": 0,
        "precision_estimated": 0.0,
        "false_positive_rate": 0.0,
        "recall_estimated": 0.0,
    }

    for alert in alerts:
        created_at = getattr(alert, "created_at", None)
        date_key = created_at.date().isoformat() if created_at else "unknown"
        day_bucket = by_day.setdefault(date_key, {
            "date": date_key,
            "total_alerts": 0,
            "true_positive_count": 0,
            "false_positive_count": 0,
            "unknown_count": 0,
        })
        totals["total_alerts"] += 1
        day_bucket["total_alerts"] += 1

        label = str(getattr(alert, "label", "") or "")
        model_id = _alert_model_id(alert)
        if label == "TRUE_POSITIVE":
            totals["true_positive_count"] += 1
            totals["labeled_alerts"] += 1
            day_bucket["true_positive_count"] += 1
            if model_id:
                total_tp_with_model += 1
        elif label == "FALSE_POSITIVE":
            totals["false_positive_count"] += 1
            totals["labeled_alerts"] += 1
            day_bucket["false_positive_count"] += 1
        else:
            totals["unknown_count"] += 1
            day_bucket["unknown_count"] += 1

        rule_key = str(getattr(alert, "rule_id", None) or f"title::{_alert_rule_name(alert)}")
        rule_bucket = by_rule.setdefault(rule_key, {
            "rule_id": str(getattr(alert, "rule_id", None)) if getattr(alert, "rule_id", None) else None,
            "rule_name": _alert_rule_name(alert),
            "total_alerts": 0,
            "true_positive_count": 0,
            "false_positive_count": 0,
            "unknown_count": 0,
            "precision_estimated": 0.0,
            "false_positive_rate": 0.0,
        })
        rule_bucket["total_alerts"] += 1

        if label == "TRUE_POSITIVE":
            rule_bucket["true_positive_count"] += 1
        elif label == "FALSE_POSITIVE":
            rule_bucket["false_positive_count"] += 1
        else:
            rule_bucket["unknown_count"] += 1

        if model_id:
            model_bucket = by_model.setdefault(model_id, {
                "model_id": model_id,
                "model_name": None,
                "algorithm": None,
                "status": "UNKNOWN",
                "total_alerts": 0,
                "true_positive_count": 0,
                "false_positive_count": 0,
                "unknown_count": 0,
                "precision_estimated": 0.0,
                "recall_estimated": 0.0,
                "false_positive_rate": 0.0,
            })
            model_bucket["total_alerts"] += 1
            if label == "TRUE_POSITIVE":
                model_bucket["true_positive_count"] += 1
            elif label == "FALSE_POSITIVE":
                model_bucket["false_positive_count"] += 1
            else:
                model_bucket["unknown_count"] += 1

    totals["precision_estimated"] = _precision(
        totals["true_positive_count"],
        totals["false_positive_count"],
    )
    totals["false_positive_rate"] = _false_positive_rate(
        totals["true_positive_count"],
        totals["false_positive_count"],
    )
    totals["recall_estimated"] = 1.0 if totals["true_positive_count"] > 0 else 0.0

    for bucket in by_rule.values():
        bucket["precision_estimated"] = _precision(
            bucket["true_positive_count"],
            bucket["false_positive_count"],
        )
        bucket["false_positive_rate"] = _false_positive_rate(
            bucket["true_positive_count"],
            bucket["false_positive_count"],
        )

    for bucket in by_model.values():
        bucket["precision_estimated"] = _precision(
            bucket["true_positive_count"],
            bucket["false_positive_count"],
        )
        bucket["false_positive_rate"] = _false_positive_rate(
            bucket["true_positive_count"],
            bucket["false_positive_count"],
        )
        bucket["recall_estimated"] = round(
            bucket["true_positive_count"] / total_tp_with_model, 4,
        ) if total_tp_with_model else 0.0

    return {
        "days_window": days,
        "challenger_split_pct": challenger_split_pct,
        "totals": totals,
        "by_day": sorted(by_day.values(), key=lambda item: item["date"]),
        "by_rule": sorted(
            by_rule.values(),
            key=lambda item: (-item["total_alerts"], item["rule_name"]),
        ),
        "by_model": sorted(
            by_model.values(),
            key=lambda item: (_status_rank(item["status"]), -item["total_alerts"]),
        ),
    }


def _build_ab_metrics(
    model: Any,
    peer_model: Any | None,
    logs: list[Any],
    alerts: list[Any],
    *,
    days: int,
) -> dict[str, Any]:
    role = "challenger" if getattr(model, "is_challenger", False) else "champion"
    champion_model = peer_model if role == "challenger" else model
    challenger_model = model if role == "challenger" else peer_model

    champion_id = str(getattr(champion_model, "id", "")) if champion_model else None
    challenger_id = str(getattr(challenger_model, "id", "")) if challenger_model else None

    timeline: dict[str, dict[str, Any]] = {}
    score_sums = defaultdict(float)
    score_counts = defaultdict(int)
    summary = {
        "champion_inferences": 0,
        "challenger_inferences": 0,
        "champion_tp": 0,
        "champion_fp": 0,
        "challenger_tp": 0,
        "challenger_fp": 0,
    }

    def _day_bucket(date_key: str) -> dict[str, Any]:
        return timeline.setdefault(date_key, {
            "date": date_key,
            "champion_inferences": 0,
            "challenger_inferences": 0,
            "champion_avg_score": None,
            "challenger_avg_score": None,
            "champion_tp": 0,
            "champion_fp": 0,
            "challenger_tp": 0,
            "challenger_fp": 0,
        })

    for log in logs:
        mid = str(getattr(log, "model_id", "") or "")
        if mid not in {champion_id, challenger_id}:
            continue
        created_at = getattr(log, "created_at", None)
        date_key = created_at.date().isoformat() if created_at else "unknown"
        bucket = _day_bucket(date_key)
        score = _safe_float(getattr(log, "anomaly_score", None))
        side = "champion" if mid == champion_id else "challenger"
        bucket[f"{side}_inferences"] += 1
        summary[f"{side}_inferences"] += 1
        score_sums[side] += score
        score_counts[side] += 1
        score_sums[f"{side}:{date_key}"] += score
        score_counts[f"{side}:{date_key}"] += 1

    for alert in alerts:
        mid = _alert_model_id(alert)
        if mid not in {champion_id, challenger_id}:
            continue
        label = str(getattr(alert, "label", "") or "")
        if label not in {"TRUE_POSITIVE", "FALSE_POSITIVE"}:
            continue
        created_at = getattr(alert, "created_at", None)
        date_key = created_at.date().isoformat() if created_at else "unknown"
        bucket = _day_bucket(date_key)
        side = "champion" if mid == champion_id else "challenger"
        if label == "TRUE_POSITIVE":
            bucket[f"{side}_tp"] += 1
            summary[f"{side}_tp"] += 1
        else:
            bucket[f"{side}_fp"] += 1
            summary[f"{side}_fp"] += 1

    for date_key, bucket in timeline.items():
        for side in ("champion", "challenger"):
            key = f"{side}:{date_key}"
            if score_counts[key]:
                bucket[f"{side}_avg_score"] = round(score_sums[key] / score_counts[key], 4)

    total_tp = summary["champion_tp"] + summary["challenger_tp"]

    return {
        "model_id": str(model.id),
        "model_name": getattr(model, "model_name", None),
        "role": role,
        "status": str(getattr(model, "status", "STAGING") or "STAGING"),
        "days_window": days,
        "champion_model_id": champion_id,
        "challenger_model_id": challenger_id,
        "champion_inferences": summary["champion_inferences"],
        "challenger_inferences": summary["challenger_inferences"],
        "champion_avg_score": round(score_sums["champion"] / score_counts["champion"], 4) if score_counts["champion"] else None,
        "challenger_avg_score": round(score_sums["challenger"] / score_counts["challenger"], 4) if score_counts["challenger"] else None,
        "champion_precision_estimated": _precision(summary["champion_tp"], summary["champion_fp"]),
        "challenger_precision_estimated": _precision(summary["challenger_tp"], summary["challenger_fp"]),
        "champion_recall_estimated": round(summary["champion_tp"] / total_tp, 4) if total_tp else 0.0,
        "challenger_recall_estimated": round(summary["challenger_tp"] / total_tp, 4) if total_tp else 0.0,
        "champion_false_positive_rate": _false_positive_rate(summary["champion_tp"], summary["champion_fp"]),
        "challenger_false_positive_rate": _false_positive_rate(summary["challenger_tp"], summary["challenger_fp"]),
        "timeline": sorted(timeline.values(), key=lambda item: item["date"]),
    }


@router.get("/model-registry", response_model=list[ModelRegistryOut])
async def list_models(
    model_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(MODEL_REGISTRY_READ_ROLES)),
):
    stmt = select(ModelRegistry).where(_tenant_filter(ModelRegistry, current_user.tenant_id))
    if model_type:
        stmt = stmt.where(ModelRegistry.model_type == model_type)
    stmt = stmt.order_by(desc(ModelRegistry.trained_at))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/model-registry/performance/summary", response_model=ModelPerformanceSummaryOut)
async def get_model_performance_summary(
    days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(MODEL_REGISTRY_READ_ROLES)),
):
    since = datetime.now(UTC) - timedelta(days=days)

    models = (await db.execute(
        select(ModelRegistry)
        .where(_tenant_filter(ModelRegistry, current_user.tenant_id))
        .order_by(desc(ModelRegistry.trained_at))
    )).scalars().all()

    alerts = (await db.execute(
        select(Alert)
        .where(
            Alert.tenant_id == current_user.tenant_id,
            Alert.created_at >= since,
        )
        .order_by(Alert.created_at.asc())
    )).scalars().all()

    scoring_cfg = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id).limit(1)
    )).scalar_one_or_none()

    return _build_performance_summary(
        list(models),
        list(alerts),
        days=days,
        challenger_split_pct=_safe_int(getattr(scoring_cfg, "ml_challenger_pct", 0)),
    )


@router.get("/model-registry/{model_id}", response_model=ModelRegistryOut)
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(MODEL_REGISTRY_READ_ROLES)),
):
    model = (await db.execute(
        select(ModelRegistry).where(
            ModelRegistry.id == model_id,
            _tenant_filter(ModelRegistry, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Modelo não encontrado")
    return model


@router.get("/model-registry/{model_id}/ab-metrics", response_model=ModelABMetricsOut)
async def get_model_ab_metrics(
    model_id: str,
    days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(MODEL_REGISTRY_READ_ROLES)),
):
    model = (await db.execute(
        select(ModelRegistry).where(
            ModelRegistry.id == model_id,
            _tenant_filter(ModelRegistry, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Modelo não encontrado")

    peer_stmt = (
        select(ModelRegistry)
        .where(
            ModelRegistry.tenant_id == current_user.tenant_id,
            ModelRegistry.model_type == model.model_type,
            ModelRegistry.id != model.id,
        )
        .order_by(desc(ModelRegistry.trained_at))
    )
    peer_candidates = (await db.execute(peer_stmt)).scalars().all()

    peer_model = None
    if getattr(model, "is_challenger", False):
        peer_model = next(
            (item for item in peer_candidates if str(getattr(item, "status", "")) in {"champion", "active", "PRODUCTION"}),
            None,
        )
    else:
        peer_model = next((item for item in peer_candidates if getattr(item, "is_challenger", False)), None)
        if peer_model is None:
            peer_model = next(
                (item for item in peer_candidates if str(getattr(item, "status", "")) in {"champion", "active", "PRODUCTION"}),
                None,
            )

    relevant_ids = [str(model.id)]
    if peer_model is not None:
        relevant_ids.append(str(peer_model.id))

    since = datetime.now(UTC) - timedelta(days=days)
    logs = (await db.execute(
        select(ModelInferenceLog)
        .where(
            ModelInferenceLog.tenant_id == current_user.tenant_id,
            ModelInferenceLog.created_at >= since,
            ModelInferenceLog.model_id.in_(relevant_ids),
        )
        .order_by(ModelInferenceLog.created_at.asc())
    )).scalars().all()

    alerts = (await db.execute(
        select(Alert)
        .where(
            Alert.tenant_id == current_user.tenant_id,
            Alert.created_at >= since,
        )
        .order_by(Alert.created_at.asc())
    )).scalars().all()

    return _build_ab_metrics(model, peer_model, list(logs), list(alerts), days=days)


@router.post("/model-registry/{model_id}/promote")
async def promote_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(MODEL_REGISTRY_WRITE_ROLES)),
):
    """Promove um modelo challenger para champion, arquivando o champion atual."""
    model = (await db.execute(
        select(ModelRegistry).where(
            ModelRegistry.id == model_id,
            _tenant_filter(ModelRegistry, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Modelo não encontrado")
    if str(getattr(model, "status", "")) != "challenger" or not bool(getattr(model, "is_challenger", False)):
        raise HTTPException(409, "Somente challenger designado pode ser promovido")

    await db.execute(
        update(ModelRegistry).where(
            _tenant_filter(ModelRegistry, current_user.tenant_id),
            ModelRegistry.model_type == model.model_type,
            ModelRegistry.id != model_id,
            ModelRegistry.status.in_(["champion", "active", "PRODUCTION"]),
        ).values(status="archived", is_active=False, is_challenger=False, champion_id=None)
    )
    model.status = "champion"
    model.is_challenger = False
    model.is_active = True
    model.champion_id = None
    model.promoted_by = current_user.id
    model.promoted_at = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "PROMOTE_MODEL", "ModelRegistry", model_id,
                       {"model_type": model.model_type})
    await db.commit()
    return {"status": "promoted", "model_id": model_id}


@router.post("/model-registry/{model_id}/challenger")
async def designate_challenger(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(MODEL_REGISTRY_WRITE_ROLES)),
):
    """Designa um modelo STAGING como challenger para A/B evaluation."""
    model = (await db.execute(
        select(ModelRegistry).where(
            ModelRegistry.id == model_id,
            _tenant_filter(ModelRegistry, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if model is None:
        raise HTTPException(404, "Modelo não encontrado")
    if _is_champion_status(getattr(model, "status", None)):
        raise HTTPException(400, "Champion não pode ser designado como challenger diretamente; use /promote")
    if str(getattr(model, "status", "")) != "STAGING":
        raise HTTPException(409, "Apenas modelos STAGING podem virar challenger")

    champion = (await db.execute(
        select(ModelRegistry).where(
            _tenant_filter(ModelRegistry, current_user.tenant_id),
            ModelRegistry.model_type == model.model_type,
            ModelRegistry.status.in_(["champion", "active", "PRODUCTION"]),
        ).order_by(desc(ModelRegistry.trained_at))
    )).scalar_one_or_none()
    if champion is None:
        raise HTTPException(409, "Nao existe champion ativo para comparar este challenger")

    await db.execute(
        update(ModelRegistry).where(
            _tenant_filter(ModelRegistry, current_user.tenant_id),
            ModelRegistry.model_type == model.model_type,
            ModelRegistry.id != model_id,
            ModelRegistry.is_challenger.is_(True),
        ).values(status="STAGING", is_challenger=False, champion_id=None, is_active=False)
    )

    model.is_challenger = True
    model.status = "challenger"
    model.is_active = False
    model.champion_id = champion.id
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "DESIGNATE_CHALLENGER", "ModelRegistry", model_id,
                       {"model_type": model.model_type, "algorithm": model.algorithm, "champion_id": str(champion.id)})
    await db.commit()
    return {"status": "challenger", "model_id": model_id}


# ── M5 — SHAP Persistence: computa e persiste SHAP values por alerta ─────────

_SHAP_COMPUTE_ROLES = [AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN]
_ML_SERVICE_TIMEOUT = 15.0  # segundos


@router.post("/alerts/{alert_id}/shap")
async def compute_and_persist_shap(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any(_SHAP_COMPUTE_ROLES)),
):
    """Computa SHAP values via ML Service e persiste na evidência do alerta.

    Garante trilha de auditoria: o resultado SHAP fica armazenado em
    alert.evidence["shap_values"] para ser recuperado pelo endpoint
    GET /alerts/{id}/explainability sem nova chamada ao ML Service.

    Erros de comunicação com o ML Service retornam 503 (não 500).
    """
    alert = (
        await db.execute(
            select(Alert).where(
                Alert.id == alert_id,
                Alert.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if alert is None:
        raise HTTPException(404, "Alerta não encontrado")

    # Extrai features do alerta (evidência + snapshot de features)
    evidence = alert.evidence or {}
    if not isinstance(evidence, dict):
        evidence = {}

    features: dict[str, Any] = {}
    features.update({k: v for k, v in evidence.items() if isinstance(v, (int, float))})
    feat_snapshot = evidence.get("features_snapshot") or {}
    if isinstance(feat_snapshot, dict):
        features.update(feat_snapshot)

    if not features:
        raise HTTPException(422, "Alerta não possui features para calcular SHAP")

    # Determina model_type a partir da evidência ou default
    model_type = str(evidence.get("model_type") or "IsolationForest")

    shap_request = {
        "tenant_id": str(current_user.tenant_id),
        "player_id": str(alert.player_id) if alert.player_id else "unknown",
        "features": features,
        "model_type": model_type,
    }

    ml_service_url = settings.ml_service_url.rstrip("/")
    # T10: autenticação interna — propagar API key se configurada
    _ml_headers: dict[str, str] = {}
    if settings.ml_internal_api_key:
        _ml_headers["X-Internal-Api-Key"] = settings.ml_internal_api_key
    try:
        async with httpx.AsyncClient(timeout=_ML_SERVICE_TIMEOUT) as client:
            resp = await client.post(f"{ml_service_url}/score/shap", json=shap_request, headers=_ml_headers)
            resp.raise_for_status()
            shap_result = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(503, "ML Service timeout ao calcular SHAP — tente novamente")
    except httpx.ConnectError:
        raise HTTPException(503, "ML Service indisponível — SHAP não pode ser calculado agora")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(503, f"ML Service retornou erro {exc.response.status_code}")

    shap_values: dict[str, float] = shap_result.get("shap_values") or {}
    baseline: float = float(shap_result.get("baseline") or 0.0)

    # Persiste no alerta (merge preserva campos existentes)
    new_evidence = {**evidence}
    new_evidence["shap_values"] = shap_values
    new_evidence["shap_baseline"] = baseline
    new_evidence["shap_computed_at"] = datetime.now(UTC).isoformat()
    new_evidence["shap_model_type"] = model_type
    alert.evidence = new_evidence

    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "SHAP_COMPUTED",
        "Alert",
        alert_id,
        after={
            "model_type": model_type,
            "feature_count": len(shap_values),
            "baseline": baseline,
        },
    )
    await db.commit()

    logger.info(
        "shap_persisted",
        alert_id=alert_id,
        tenant_id=str(current_user.tenant_id),
        feature_count=len(shap_values),
        model_type=model_type,
    )

    return {
        "alert_id": alert_id,
        "player_id": str(alert.player_id) if alert.player_id else None,
        "model_type": model_type,
        "shap_values": shap_values,
        "baseline": baseline,
        "computed_at": new_evidence["shap_computed_at"],
        "feature_count": len(shap_values),
    }
