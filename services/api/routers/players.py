"""routers/players.py — Listagem, perfil e compatibilidade econômica de players."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decrypt_pii, get_current_user, mask_cpf, require_roles
from database import get_db
from models import FinancialTransaction, Player, ScoringConfig, User

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["players"])


@router.get("/players")
async def list_players(
    limit: int = Query(50, le=200),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Player)
        .where(Player.tenant_id == current_user.tenant_id)
        .limit(limit)
        .offset(offset)
    )
    players = (await db.execute(q)).scalars().all()
    return [
        {
            "id": p.id,
            "external_player_id": p.external_player_id,
            "cpf_masked": mask_cpf(decrypt_pii(p.cpf_encrypted)),
            "pep_flag": p.pep_flag,
            "risk_score": float(p.risk_score),
            "risk_band": p.risk_band,
            "created_at": p.created_at,
        }
        for p in players
    ]


@router.get("/players/{player_id}")
async def get_player(
    player_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    cpf_plain = decrypt_pii(p.cpf_encrypted)
    show_full = current_user.role in ("ADMIN", "AML_ANALYST")
    return {
        "id": p.id,
        "external_player_id": p.external_player_id,
        "cpf": cpf_plain if show_full else mask_cpf(cpf_plain),
        "pep_flag": p.pep_flag,
        "risk_score": float(p.risk_score),
        "risk_band": p.risk_band,
        "declared_income_monthly": float(p.declared_income_monthly) if p.declared_income_monthly else None,
        "last_scored_at": p.last_scored_at,
    }


@router.get("/players/{player_id}/econ-compat")
async def get_player_econ_compat(
    player_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    """
    Compatibilidade econômica do player (art. 2 Res. COAF 40/2021).
    Compara volume de depósitos 30d com renda mensal declarada.
    """
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    sc_row = (
        await db.execute(
            select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id).limit(1)
        )
    ).scalars().first()
    ratio_threshold = float(sc_row.income_volume_ratio_threshold) if sc_row else 1.5

    cutoff_30d = datetime.utcnow() - timedelta(days=30)
    deposit_sum_30d = float(
        (
            await db.execute(
                select(sqlfunc.coalesce(sqlfunc.sum(FinancialTransaction.amount), 0)).where(
                    FinancialTransaction.tenant_id == current_user.tenant_id,
                    FinancialTransaction.player_id == player_id,
                    FinancialTransaction.type == "DEPOSIT",
                    FinancialTransaction.occurred_at >= cutoff_30d,
                )
            )
        ).scalar()
        or 0
    )

    declared_income = float(p.declared_income_monthly) if p.declared_income_monthly else None
    if declared_income and declared_income > 0:
        income_ratio_30d = round(deposit_sum_30d / declared_income, 4)
    else:
        income_ratio_30d = None

    if income_ratio_30d is None:        tier = "UNKNOWN"
    elif income_ratio_30d <= ratio_threshold:  tier = "GREEN"
    elif income_ratio_30d <= ratio_threshold * 2: tier = "YELLOW"
    else:                                tier = "RED"

    return {
        "player_id":              player_id,
        "declared_income_monthly": declared_income,
        "deposit_sum_30d":        deposit_sum_30d,
        "income_ratio_30d":       income_ratio_30d,
        "ratio_threshold":        ratio_threshold,
        "tier":                   tier,
        "interpretation": {
            "GREEN":   f"Volume 30d <= {ratio_threshold}x renda declarada (baixo risco)",
            "YELLOW":  f"Volume 30d entre {ratio_threshold}x e {ratio_threshold * 2:.1f}x renda (atenção)",
            "RED":     f"Volume 30d > {ratio_threshold * 2:.1f}x renda declarada (INCOMPATÍVEL — verificar COAF)",
            "UNKNOWN": "Renda declarada não informada; análise manual requerida",
        }.get(tier, ""),
    }


@router.get("/players/{player_id}/feature-history")
async def get_player_feature_history(
    player_id: str,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    """
    Histórico de features diárias do player (Gold layer — ClickHouse betaml.player_features_daily).
    Retorna até `days` dias de histórico, ordenado do mais recente ao mais antigo.
    Requer role ADMIN ou AML_ANALYST.
    """
    import asyncio

    # Garantir que o player existe e pertence ao tenant antes de ir ao ClickHouse
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    from libs.clients import ClickHouseClient

    _COLUMNS = [
        "feature_date", "deposit_sum_24h", "deposit_sum_7d", "deposit_sum_30d",
        "deposit_count_24h", "deposit_count_7d",
        "withdrawal_sum_24h", "withdrawal_sum_7d", "withdrawal_count_24h",
        "bet_stake_sum_24h", "bet_stake_sum_7d",
        "ratio_w2d_7d", "baseline_avg_deposit", "baseline_stddev_deposit",
        "zscore_deposit", "new_payment_flag", "new_device_flag",
        "shared_device_count", "shared_bank_count", "chargeback_count_30d",
        "computed_at",
    ]

    def _query_ch() -> list:
        ch = ClickHouseClient()
        return ch.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM betaml.player_features_daily
            WHERE tenant_id = %(tid)s
              AND player_id = %(pid)s
              AND feature_date >= today() - %(days)s
            ORDER BY feature_date DESC
            """,
            {"tid": current_user.tenant_id, "pid": player_id, "days": days},
        )

    try:
        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _query_ch)
    except Exception as exc:
        logger.warning("feature_history_clickhouse_error", error=str(exc), player_id=player_id)
        raise HTTPException(503, "Feature store temporariamente indisponível")

    return {
        "player_id": player_id,
        "days_requested": days,
        "count": len(rows),
        "data": [
            {col: (float(val) if hasattr(val, "__float__") and not isinstance(val, (str, bool)) else val)
             for col, val in zip(_COLUMNS, row)}
            for row in rows
        ],
    }
