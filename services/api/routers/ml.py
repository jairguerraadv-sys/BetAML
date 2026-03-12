"""
routers/ml.py — Model Registry: listagem, promoção de champion, métricas A/B.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from libs.models import AuditLog, ModelRegistry
from libs.schemas import ModelRegistryOut

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ml"])


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


async def _write_audit(db, tenant_id, actor, action, resource_type, resource_id=None, details=None):
    db.add(AuditLog(
        tenant_id=tenant_id, user_id=actor, action=action,
        entity_type=resource_type,
        entity_id=str(resource_id) if resource_id else None,
        after=details or {},
    ))


@router.get("/model-registry", response_model=list[ModelRegistryOut])
async def list_models(
    model_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = select(ModelRegistry).where(_tenant_filter(ModelRegistry, current_user.tenant_id))
    if model_type:
        stmt = stmt.where(ModelRegistry.model_type == model_type)
    stmt = stmt.order_by(desc(ModelRegistry.trained_at))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/model-registry/{model_id}/promote")
async def promote_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("ADMIN")),
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

    # Arquivar champion atual do mesmo tipo
    await db.execute(
        update(ModelRegistry).where(
            _tenant_filter(ModelRegistry, current_user.tenant_id),
            ModelRegistry.model_type == model.model_type,
            ModelRegistry.status == "champion",
        ).values(status="archived")
    )
    model.status = "champion"
    model.is_challenger = False
    model.promoted_by = current_user.id
    model.promoted_at = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "PROMOTE_MODEL", "ModelRegistry", model_id,
                       {"model_type": model.model_type})
    await db.commit()
    return {"status": "promoted", "model_id": model_id}
