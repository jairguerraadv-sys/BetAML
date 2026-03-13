"""routers/players.py — Listagem, perfil e compatibilidade econômica de players."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decrypt_pii, encrypt_pii, get_current_user, mask_cpf, require_roles
from database import get_db
from models import FinancialTransaction, Player, ScoringConfig, User
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["players"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_scalar(value):
    if hasattr(value, "__float__") and not isinstance(value, (str, bool)):
        return float(value)
    return value


def _normalize_feature_history_row(columns: list[str], row: tuple) -> dict:
    record = {col: _coerce_scalar(val) for col, val in zip(columns, row)}
    if "unique_instruments_7d" in record and "unique_instruments_used_7d" not in record:
        record["unique_instruments_used_7d"] = record["unique_instruments_7d"]
    if "bonus_to_real_ratio_30d" in record and "bonus_to_real_money_ratio_30d" not in record:
        record["bonus_to_real_money_ratio_30d"] = record["bonus_to_real_ratio_30d"]
    return record


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
    # LGPD Art. 37 — log de acesso a dados pessoais (CPF mascarado)
    if current_user.role in ("ADMIN", "AML_ANALYST") and players:
        await write_audit(
            db, current_user.tenant_id, current_user.id,
            "LIST_PLAYERS", "Player", None,
            pii_accessed="cpf_masked"
        )
        await db.flush()
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
    if p.status == "ERASED":
        # LGPD Art. 18 — dados anonimizados; não retornar PII
        raise HTTPException(410, "Dados deste player foram anonimizados (LGPD Art. 18)")
    cpf_plain = decrypt_pii(p.cpf_encrypted)
    show_full = current_user.role in ("ADMIN", "AML_ANALYST")

    # Audit access to PII (LGPD Art. 37)
    if show_full:
        await write_audit(
            db, current_user.tenant_id, current_user.id,
            "GET_PLAYER", "Player", player_id,
            pii_accessed="cpf"
        )
        await db.flush()

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

    cutoff_30d = _utcnow() - timedelta(days=30)
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
        "deposit_velocity", "unique_instruments_7d", "night_activity_ratio",
        "weekend_activity_ratio", "avg_odds_bet_7d", "win_loss_ratio_30d",
        "avg_dep_to_wdraw_hours", "multi_currency_flag", "chargeback_rate_30d",
        "bonus_to_real_ratio_30d", "cashout_ratio_7d", "shared_instrument_score",
        "feature_version",
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
        "data": [_normalize_feature_history_row(_COLUMNS, row) for row in rows],
    }


@router.post("/players/{player_id}/erase", status_code=200)
async def erase_player_data(
    player_id: str,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "SUPER_ADMIN")),
):
    """
    LGPD Art. 18 — Direito ao Esquecimento / Erasure Request.

    Anonimiza dados pessoais do player, mantendo registro para auditoria:
      - Substitui CPF por valor anonimizado (cpf_erased_<player_id_hash>)
      - Substitui nome por "ANON_<player_id_suffix>"
      - Status ← "ERASED"
      - Registra em audit_log com motivo

    IMPORTANTE: Esta operação é IRREVERSÍVEL.
    """
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    if p.status == "ERASED":
        return {"status": "already_erased", "player_id": player_id, "message": "Player já foi anonimizado anteriormente"}

    # Gerar valores anonimizados determinísticos (mesmo player gera mesmo anon)
    import hashlib
    player_hash = hashlib.sha256(player_id.encode()).hexdigest()[:12]
    suffix = player_id[-6:] if len(player_id) >= 6 else player_id

    # Anonimizar PII
    anon_cpf = f"cpf_erased_{player_hash}"
    anon_name = f"ANON_{suffix}"

    # Atualizar player
    p.cpf_encrypted = encrypt_pii(anon_cpf)
    p.full_name = anon_name
    p.status = "ERASED"
    p.updated_at = _utcnow()

    # Audit log (LGPD Art. 37)
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "ERASE_PLAYER_DATA", "Player", player_id,
        before={"status": "ACTIVE", "cpf": "***", "name": "***"},
        after={"status": "ERASED", "reason": reason or "Solicitação de titular (LGPD Art. 18)"},
    )

    await db.commit()

    logger.info(
        "player_data_erased",
        player_id=player_id,
        tenant_id=current_user.tenant_id,
        actor=current_user.id,
        reason=reason,
    )

    return {
        "status": "erased",
        "player_id": player_id,
        "message": "Dados pessoais anonimizados com sucesso (LGPD Art. 18)",
        "erased_at": _utcnow().isoformat(),
    }
