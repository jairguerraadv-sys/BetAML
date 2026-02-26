"""Scoring router."""

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ml.config import settings
from ml.db import get_db
from ml.schemas import ScoreRequest, ScoreResponse
from ml.scorer import ModelScorer

router = APIRouter()
_scorer = ModelScorer()


def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> None:
    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


@router.post("/score", response_model=ScoreResponse)
async def score_player(
    body: ScoreRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_api_key),
) -> ScoreResponse:
    result = await _scorer.score(
        tenant_id=str(body.tenant_id),
        features=body.features,
        db=db,
    )
    return ScoreResponse(**result)
