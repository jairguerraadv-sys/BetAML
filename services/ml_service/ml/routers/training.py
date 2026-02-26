"""Training and model-registry routers."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ml.config import settings
from ml.db import get_db
from ml.models import ModelRegistry
from ml.schemas import ModelResponse, TrainRequest, TrainResponse
from ml.trainer import ModelTrainer

router = APIRouter()
_trainer = ModelTrainer()


def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


@router.post("/train", response_model=TrainResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_training(
    body: TrainRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_api_key),
) -> TrainResponse:
    result = await _trainer.train(
        tenant_id=str(body.tenant_id),
        dataset_window_days=body.dataset_window_days,
        db=db,
    )
    return TrainResponse(**result)


@router.get("/models", response_model=list[ModelResponse])
async def list_models(
    tenant_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_api_key),
) -> list[ModelResponse]:
    result = await db.execute(
        select(ModelRegistry)
        .where(ModelRegistry.tenant_id == tenant_id)
        .order_by(ModelRegistry.created_at.desc())
    )
    rows = result.scalars().all()
    return [ModelResponse.model_validate(r) for r in rows]


@router.get("/models/{model_id}", response_model=ModelResponse)
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_api_key),
) -> ModelResponse:
    result = await db.execute(
        select(ModelRegistry).where(ModelRegistry.id == model_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return ModelResponse.model_validate(row)
