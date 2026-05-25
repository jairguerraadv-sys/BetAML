"""routers/players.py — Listagem, perfil e compatibilidade econômica de players."""
from __future__ import annotations

import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case as sqla_case, func as sqlfunc, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, compute_cpf_hmac, decrypt_pii, encrypt_pii, get_effective_roles, mask_cpf, require_role, require_role_any
from database import get_db
from models import Alert, Bet, Case, DeviceEvent, FinancialTransaction, Player, PlayerKycEvent, ScoringConfig, User
from repositories import PlayerRepository
from repositories.players import get_player_repo
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
    envelope: bool = Query(False, description="Quando true, retorna {items,total,limit,offset}."),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR, AppRole.SUPER_ADMIN])),
    repo: PlayerRepository = Depends(get_player_repo),
    db: AsyncSession = Depends(get_db),
):
    players = await repo.list_active(current_user.tenant_id, limit=limit, offset=offset)
    # LGPD Art. 37 — log de acesso a dados pessoais (CPF mascarado)
    if get_effective_roles(current_user).intersection({AppRole.ANALISTA, AppRole.GESTOR}) and players:
        await write_audit(
            db, current_user.tenant_id, current_user.id,
            "LIST_PLAYERS", "Player", None,
            pii_accessed="cpf_masked"
        )
        await db.flush()
    items = [
        {
            "id": p.id,
            "external_player_id": p.external_player_id,
            "cpf_masked": mask_cpf(decrypt_pii(p.cpf_encrypted)),
            "pep_flag": p.pep_flag,
            "risk_score": float(p.risk_score),
            "risk_band": p.risk_band,
            "status": p.status,
            "self_exclusion_flag": p.self_exclusion_flag,
            "deposit_limit_daily": float(p.deposit_limit_daily) if p.deposit_limit_daily is not None else None,
            "created_at": p.created_at,
        }
        for p in players
    ]
    if not envelope:
        return items

    total = await repo.count_active(current_user.tenant_id)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/players/{player_id}")
async def get_player(
    player_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR, AppRole.SUPER_ADMIN])),
    repo: PlayerRepository = Depends(get_player_repo),
    db: AsyncSession = Depends(get_db),
):
    p = await repo.get_by_id(current_user.tenant_id, player_id)
    if not p:
        raise HTTPException(404, "Player não encontrado")
    if p.status == "ERASED":
        # LGPD Art. 18 — dados anonimizados; não retornar PII
        raise HTTPException(410, "Dados deste player foram anonimizados (LGPD Art. 18)")
    cpf_plain = decrypt_pii(p.cpf_encrypted)
    # AUDITOR is read-only and must not receive full PII.
    _legacy_role = getattr(current_user, "role", None)
    show_full = bool(
        get_effective_roles(current_user).intersection({AppRole.ANALISTA, AppRole.GESTOR})
        and _legacy_role != "AUDITOR"
    )

    # Audit access to PII (LGPD Art. 37) — always log, even for masked CPF
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "GET_PLAYER", "Player", player_id,
        pii_accessed="cpf" if show_full else "cpf_masked"
    )
    await db.flush()

    # ── income_compat inline (evita chamada extra a /econ-compat) ──────────────
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

    if income_ratio_30d is None:
        compat_tier = "UNKNOWN"
    elif income_ratio_30d <= ratio_threshold:
        compat_tier = "GREEN"
    elif income_ratio_30d <= ratio_threshold * 2:
        compat_tier = "YELLOW"
    else:
        compat_tier = "RED"

    return {
        "id": p.id,
        "external_player_id": p.external_player_id,
        "cpf": cpf_plain if show_full else mask_cpf(cpf_plain),
        "pep_flag": p.pep_flag,
        "risk_score": float(p.risk_score),
        "risk_band": p.risk_band,
        "status": p.status,
        "self_exclusion_flag": p.self_exclusion_flag,
        "deposit_limit_daily": float(p.deposit_limit_daily) if p.deposit_limit_daily is not None else None,
        "declared_income_monthly": declared_income,
        "last_scored_at": p.last_scored_at,
        "income_compat": {
            "deposit_sum_30d": deposit_sum_30d,
            "income_ratio_30d": income_ratio_30d,
            "ratio_threshold": ratio_threshold,
            "tier": compat_tier,
        },
    }


@router.get("/players/{player_id}/econ-compat")
async def get_player_econ_compat(
    player_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
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

    if income_ratio_30d is None:
        tier = "UNKNOWN"
    elif income_ratio_30d <= ratio_threshold:
        tier = "GREEN"
    elif income_ratio_30d <= ratio_threshold * 2:
        tier = "YELLOW"
    else:
        tier = "RED"

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
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
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
        "avg_dep_to_wdraw_hours", "inconsistent_currency_flag", "chargeback_rate_30d",
        "bonus_to_real_ratio_30d", "cashout_ratio_7d", "shared_instrument_score",
        "feature_version",
        "computed_at",
    ]

    def _query_ch() -> list:
        ch = ClickHouseClient()
        col_clause = ", ".join(_COLUMNS)
        sql = (  # nosec B608
            f"""
            SELECT {col_clause}
                        FROM betaml.player_features_daily FINAL
            WHERE tenant_id = %(tid)s
              AND player_id = %(pid)s
              AND feature_date >= today() - %(days)s
            ORDER BY feature_date DESC
            """
        )
        return ch.execute(
            sql,
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
    current_user: User = Depends(require_role_any([AppRole.GESTOR, AppRole.SUPER_ADMIN])),
    repo: object = Depends(get_player_repo),
):
    """
    LGPD Art. 18 — Direito ao Esquecimento / Erasure Request.

    Anonimiza dados pessoais do player, mantendo registro para auditoria:
            - Substitui CPF por valor anonimizado (cpf_erased_<player_id_hash>) em cpf_encrypted
      - Substitui nome por "ANON_<player_id_suffix>"
      - Status ← "ERASED"
      - Registra em audit_log com motivo

    IMPORTANTE: Esta operação é IRREVERSÍVEL.
    """
    # Compat: unit tests chamam o handler diretamente (sem injeção de Depends).
    if not hasattr(repo, "get_by_id"):
        repo = PlayerRepository(db)

    # Capture primitives early to avoid ORM attribute refresh after commit/rollback.
    tenant_id = str(current_user.tenant_id)
    actor_id = str(current_user.id)

    # RLS: ensure this DB session is in the right tenant context for ALL operations
    # in this handler (Player + audit_logs).
    try:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": tenant_id})
    except Exception:
        pass

    p = await repo.get_by_id(tenant_id, player_id)  # type: ignore[attr-defined]
    # `/ingest/event` é assíncrono (fila); em testes e incidentes operacionais,
    # pode existir solicitação LGPD antes do player ser materializado no Postgres.
    # Neste caso, criamos um placeholder já anonimizado para manter trilha de auditoria.
    if not p:
        import hashlib

        player_hash = hashlib.sha256(player_id.encode()).hexdigest()[:12]
        suffix = player_id[-6:] if len(player_id) >= 6 else player_id
        anon_cpf = f"cpf_erased_{player_hash}"
        anon_name = f"ANON_{suffix}"

        try:
            p = Player(
                tenant_id=tenant_id,
                external_player_id=player_id,
                full_name=anon_name,
                cpf_encrypted=encrypt_pii(anon_cpf),
                cpf_hmac=compute_cpf_hmac(anon_cpf),
                name_encrypted=encrypt_pii(anon_name),
                status="ERASED",
                updated_at=_utcnow(),
            )
            db.add(p)
            await db.flush()

            await write_audit(
                db,
                tenant_id,
                actor_id,
                "ERASE_PLAYER_DATA",
                "Player",
                player_id,
                before={"status": None},
                after={"status": "ERASED", "reason": reason or "Solicitação de titular (LGPD Art. 18)"},
            )
            await db.commit()

            return {
                "status": "erased",
                "player_id": player_id,
                "message": "Dados pessoais anonimizados com sucesso (LGPD Art. 18)",
                "erased_at": _utcnow().isoformat(),
            }
        except IntegrityError:
            # Corrida: player materializado por outro fluxo entre o lookup e o INSERT.
            await db.rollback()
            p = await repo.get_by_external_id(tenant_id, player_id)  # type: ignore[attr-defined]
            if not p:
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
    p.name_encrypted = encrypt_pii(anon_name)  # GAP-7: also clear name_encrypted
    p.full_name = anon_name
    p.status = "ERASED"
    p.birth_date = None
    p.profession = None
    p.declared_income_monthly = None
    p.registered_since = None
    p.updated_at = _utcnow()

    # Audit log (LGPD Art. 37)
    await write_audit(
        db, tenant_id, actor_id,
        "ERASE_PLAYER_DATA", "Player", player_id,
        before={"status": "ACTIVE", "cpf": "***", "name": "***"},
        after={"status": "ERASED", "reason": reason or "Solicitação de titular (LGPD Art. 18)"},
    )

    await db.commit()

    logger.info(
        "player_data_erased",
        player_id=player_id,
        tenant_id=tenant_id,
        actor=actor_id,
        reason=reason,
    )

    # Count related records for LGPD completeness audit
    from sqlalchemy import text as _text
    erased_from: dict[str, int] = {}
    for table, col in [("alerts", "player_id"), ("cases", "player_id"), ("transactions", "player_id")]:
        try:
            row = (await db.execute(
                _text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :tid AND {col} = (SELECT id FROM players WHERE external_player_id = :pid AND tenant_id = :tid LIMIT 1)"),
                {"tid": tenant_id, "pid": player_id},
            )).scalar_one_or_none()
            erased_from[table] = int(row or 0)
        except Exception:
            erased_from[table] = -1

    return {
        "status": "erased",
        "player_id": player_id,
        "message": "Dados pessoais anonimizados com sucesso (LGPD Art. 18)",
        "erased_at": _utcnow().isoformat(),
        "erased_from": erased_from,
    }


@router.post("/players/{player_id}/right-to-erasure", status_code=200)
async def right_to_erasure_alias(
    player_id: str,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role_any([AppRole.GESTOR, AppRole.SUPER_ADMIN])),
):
    """
    Backward-compatible alias for POST /players/{player_id}/erase.

    LGPD Art. 18 — Direito ao Esquecimento.
    Delegates to the canonical erasure implementation.
    """
    return await erase_player_data(
        player_id=player_id,
        reason=reason,
        db=db,
        current_user=current_user,
    )


# ── LGPD Data Export (Portabilidade) ─────────────────────────────────────────

class PersonalDataOut(BaseModel):
    name: Optional[str] = None
    cpf: Optional[str] = None
    birth_date: Optional[str] = None
    email: Optional[str] = None
    pep_flag: bool = False
    registered_since: Optional[str] = None


class FinancialSummaryOut(BaseModel):
    total_transactions: int = 0
    total_deposits: float = 0.0
    total_withdrawals: float = 0.0
    first_transaction: Optional[datetime] = None
    last_transaction: Optional[datetime] = None


class PlayerDataExportOut(BaseModel):
    export_id: str
    generated_at: datetime
    player_id: str
    personal_data: PersonalDataOut
    financial_summary: FinancialSummaryOut
    cases_count: int = 0
    alerts_count: int = 0


@router.get("/players/{player_id}/data-export", response_model=PlayerDataExportOut)
async def export_player_data(
    player_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """
    LGPD Art. 18 — Portabilidade de dados pessoais (Data Export Request).

    Retorna todos os dados pessoais retidos para um player em formato estruturado.
    Para players anonimizados (status=ERASED), retorna apenas os dados anonimizados.
    Registra acesso em audit_log com ação LGPD_DATA_EXPORT (LGPD Art. 37).
    """
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    is_erased = p.status == "ERASED"

    # Resolve personal data fields — respect erasure
    if is_erased:
        cpf_display = "[ANONIMIZADO]"
        name_display = p.full_name or "[ANONIMIZADO]"
    else:
        cpf_display = decrypt_pii(p.cpf_encrypted)
        name_display = p.full_name

    # Financial summary — aggregate from FinancialTransaction table
    total_txs = (await db.execute(
        select(sqlfunc.count(FinancialTransaction.id)).where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
        )
    )).scalar_one()

    total_deposits = float((await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(FinancialTransaction.amount), 0)).where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
            FinancialTransaction.type == "DEPOSIT",
        )
    )).scalar() or 0)

    total_withdrawals = float((await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(FinancialTransaction.amount), 0)).where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
            FinancialTransaction.type == "WITHDRAWAL",
        )
    )).scalar() or 0)

    first_tx = (await db.execute(
        select(sqlfunc.min(FinancialTransaction.occurred_at)).where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
        )
    )).scalar()

    last_tx = (await db.execute(
        select(sqlfunc.max(FinancialTransaction.occurred_at)).where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
        )
    )).scalar()

    # Cases count
    cases_count = (await db.execute(
        select(sqlfunc.count(Case.id)).where(
            Case.tenant_id == current_user.tenant_id,
            Case.player_id == player_id,
        )
    )).scalar_one()

    # Alerts count
    alerts_count = (await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == current_user.tenant_id,
            Alert.player_id == player_id,
        )
    )).scalar_one()

    # Audit log — LGPD Art. 37 (PII accessed during export)
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "LGPD_DATA_EXPORT", "Player", player_id,
        after={"pii_accessed": True, "is_erased": is_erased},
    )
    await db.commit()

    logger.info(
        "lgpd_data_export",
        player_id=player_id,
        tenant_id=current_user.tenant_id,
        actor=current_user.id,
        is_erased=is_erased,
    )

    return PlayerDataExportOut(
        export_id=str(_uuid_mod.uuid4()),
        generated_at=_utcnow(),
        player_id=player_id,
        personal_data=PersonalDataOut(
            name=name_display,
            cpf=cpf_display,
            birth_date=p.birth_date.isoformat() if p.birth_date else None,
            email=None,  # not stored in Player model
            pep_flag=p.pep_flag,
            registered_since=p.registered_since.isoformat() if p.registered_since else None,
        ),
        financial_summary=FinancialSummaryOut(
            total_transactions=total_txs or 0,
            total_deposits=total_deposits,
            total_withdrawals=total_withdrawals,
            first_transaction=first_tx,
            last_transaction=last_tx,
        ),
        cases_count=cases_count or 0,
        alerts_count=alerts_count or 0,
    )


# ── Module 5: Player Enrichment Endpoints ────────────────────────────────────

@router.get("/players/{player_id}/transactions-chart")
async def get_player_transactions_chart(
    player_id: str,
    days: int = Query(90, ge=7, le=365),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Volume diário de depósitos e saques dos últimos N dias (painel de investigação)."""
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    cutoff = _utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(
            sqlfunc.date_trunc("day", FinancialTransaction.occurred_at).label("day"),
            sqlfunc.coalesce(
                sqlfunc.sum(sqla_case(
                    (FinancialTransaction.type == "DEPOSIT", FinancialTransaction.amount),
                    else_=0
                )), 0).label("deposit_sum"),
            sqlfunc.coalesce(
                sqlfunc.sum(sqla_case(
                    (FinancialTransaction.type == "WITHDRAWAL", FinancialTransaction.amount),
                    else_=0
                )), 0).label("withdrawal_sum"),
        )
        .where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
            FinancialTransaction.occurred_at >= cutoff,
        )
        .group_by(sqlfunc.date_trunc("day", FinancialTransaction.occurred_at))
        .order_by(sqlfunc.date_trunc("day", FinancialTransaction.occurred_at))
    )).all()
    return {"player_id": player_id, "days": days, "data": [
        {"day": str(r.day)[:10], "deposit_sum": float(r.deposit_sum),
         "withdrawal_sum": float(r.withdrawal_sum)}
        for r in rows
    ]}


@router.get("/players/{player_id}/bets-chart")
async def get_player_bets_chart(
    player_id: str,
    days: int = Query(90, ge=7, le=365),
    product_type: str | None = Query(None, description="Filtrar por modalidade (SPORTSBOOK, CASINO_LIVE, SLOT, ...)"),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Volume diário de stake de apostas dos últimos N dias."""
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    cutoff = _utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(
            sqlfunc.date_trunc("day", Bet.occurred_at).label("day"),
            sqlfunc.coalesce(sqlfunc.sum(Bet.stake_amount), 0).label("stake_sum"),
        )
        .where(
            Bet.tenant_id == current_user.tenant_id,
            Bet.player_id == player_id,
            Bet.occurred_at >= cutoff,
            *([Bet.product_type == product_type] if product_type else []),
        )
        .group_by(sqlfunc.date_trunc("day", Bet.occurred_at))
        .order_by(sqlfunc.date_trunc("day", Bet.occurred_at))
    )).all()
    return {"player_id": player_id, "days": days, "data": [
        {"day": str(r.day)[:10], "stake_sum": float(r.stake_sum)}
        for r in rows
    ]}


@router.get("/players/{player_id}/payment-instruments")
async def get_player_payment_instruments(
    player_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Instrumentos de pagamento distintos usados pelo player com datas de primeira/última ocorrência."""
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    rows = (await db.execute(
        select(
            FinancialTransaction.payment_instrument,
            FinancialTransaction.payment_method,
            sqlfunc.min(FinancialTransaction.occurred_at).label("first_seen"),
            sqlfunc.max(FinancialTransaction.occurred_at).label("last_seen"),
            sqlfunc.count(FinancialTransaction.id).label("tx_count"),
        )
        .where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == player_id,
            FinancialTransaction.payment_instrument.isnot(None),
        )
        .group_by(FinancialTransaction.payment_instrument, FinancialTransaction.payment_method)
        .order_by(sqlfunc.max(FinancialTransaction.occurred_at).desc())
    )).all()
    return {"player_id": player_id, "instruments": [
        {
            "payment_instrument": r.payment_instrument,
            "payment_method": r.payment_method,
            "first_seen": r.first_seen,
            "last_seen": r.last_seen,
            "tx_count": r.tx_count,
        }
        for r in rows
    ]}


@router.get("/players/{player_id}/network")
async def get_player_network(
    player_id: str,
    depth: int = Query(1, ge=1, le=2, description="Profundidade do grafo (1=vizinhos diretos, 2=vizinhos de vizinhos)"),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Grafo de rede do player para visualização de clusters e lavagem via intermediários.

    Retorna nodes + edges compatível com D3-force / Cytoscape.js.
    Arestas representam elementos compartilhados: dispositivo, conta bancária ou IP.

    Campos de cada nó:
      id, external_player_id, risk_score, risk_band, pep_flag, cluster_risk

    Campos de cada aresta:
      source, target, edge_type (device|bank_account|ip), shared_hash_prefix,
      event_count (quantos eventos compartilhados), weight (0..1)

    Indicadores de cluster:
      total_nodes, total_edges, max_cluster_risk, network_risk_score,
      has_pep_connection, shared_device_count, shared_bank_count, shared_ip_count
    """
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    tid = current_user.tenant_id

    # ── Coletar seeds para expansão ───────────────────────────────────────────
    seeds = {player_id}
    all_edges: list[dict] = []

    async def _expand(pid_set: set[str]) -> set[str]:
        """Retorna todos os player_ids encontrados como vizinhos de pid_set via qualquer hash."""
        found: set[str] = set()

        # ── Device hash ───────────────────────────────────────────────────────
        dev_hashes = (await db.execute(
            select(DeviceEvent.device_hash, sqlfunc.count(DeviceEvent.id).label("cnt"))
            .where(
                DeviceEvent.tenant_id == tid,
                DeviceEvent.player_id.in_(pid_set),
                DeviceEvent.device_hash.isnot(None),
            )
            .group_by(DeviceEvent.device_hash)
        )).all()

        for dh, cnt in dev_hashes:
            peers_q = (await db.execute(
                select(DeviceEvent.player_id, sqlfunc.count(DeviceEvent.id).label("ev_cnt"))
                .where(
                    DeviceEvent.tenant_id == tid,
                    DeviceEvent.device_hash == dh,
                    DeviceEvent.player_id.notin_(pid_set | found),
                    DeviceEvent.player_id.isnot(None),
                )
                .group_by(DeviceEvent.player_id)
                .limit(50)
            )).all()
            for peer_pid, ev_cnt in peers_q:
                p2 = str(peer_pid)
                found.add(p2)
                # Aresta por player de origem
                for src in pid_set:
                    all_edges.append({
                        "source": src, "target": p2,
                        "edge_type": "device",
                        "shared_hash_prefix": dh[:12] + "…",
                        "event_count": int(ev_cnt),
                        "weight": min(1.0, round(ev_cnt / 10, 2)),
                    })

        # ── Banco ─────────────────────────────────────────────────────────────
        bank_hashes = (await db.execute(
            select(FinancialTransaction.bank_account_hash)
            .distinct()
            .where(
                FinancialTransaction.tenant_id == tid,
                FinancialTransaction.player_id.in_(pid_set),
                FinancialTransaction.bank_account_hash.isnot(None),
            )
        )).scalars().all()

        for bh in bank_hashes:
            peers_q = (await db.execute(
                select(FinancialTransaction.player_id, sqlfunc.count(FinancialTransaction.id).label("ev_cnt"))
                .where(
                    FinancialTransaction.tenant_id == tid,
                    FinancialTransaction.bank_account_hash == bh,
                    FinancialTransaction.player_id.notin_(pid_set | found),
                    FinancialTransaction.player_id.isnot(None),
                )
                .group_by(FinancialTransaction.player_id)
                .limit(50)
            )).all()
            for peer_pid, ev_cnt in peers_q:
                p2 = str(peer_pid)
                found.add(p2)
                for src in pid_set:
                    all_edges.append({
                        "source": src, "target": p2,
                        "edge_type": "bank_account",
                        "shared_hash_prefix": bh[:12] + "…",
                        "event_count": int(ev_cnt),
                        "weight": min(1.0, round(ev_cnt / 5, 2)),
                    })

        # ── IP hash ───────────────────────────────────────────────────────────
        ip_hashes = (await db.execute(
            select(DeviceEvent.ip_hash)
            .distinct()
            .where(
                DeviceEvent.tenant_id == tid,
                DeviceEvent.player_id.in_(pid_set),
                DeviceEvent.ip_hash.isnot(None),
            )
        )).scalars().all()

        for ih in ip_hashes:
            peers_q = (await db.execute(
                select(DeviceEvent.player_id, sqlfunc.count(DeviceEvent.id).label("ev_cnt"))
                .where(
                    DeviceEvent.tenant_id == tid,
                    DeviceEvent.ip_hash == ih,
                    DeviceEvent.player_id.notin_(pid_set | found),
                    DeviceEvent.player_id.isnot(None),
                )
                .group_by(DeviceEvent.player_id)
                .limit(50)
            )).all()
            for peer_pid, ev_cnt in peers_q:
                p2 = str(peer_pid)
                found.add(p2)
                for src in pid_set:
                    all_edges.append({
                        "source": src, "target": p2,
                        "edge_type": "ip",
                        "shared_hash_prefix": ih[:12] + "…",
                        "event_count": int(ev_cnt),
                        "weight": min(1.0, round(ev_cnt / 20, 2)),  # IP compartilhado tem peso menor
                    })

        return found

    # Expansão depth=1
    neighbors_1 = await _expand(seeds)
    all_pids = seeds | neighbors_1

    # Expansão depth=2 (vizinhos de vizinhos)
    if depth >= 2 and neighbors_1:
        neighbors_2 = await _expand(neighbors_1)
        all_pids |= neighbors_2

    # Cap total de nós para evitar resposta gigante
    all_pids = set(list(all_pids)[:100])

    # ── Buscar dados dos nós ──────────────────────────────────────────────────
    player_rows = (await db.execute(
        select(Player.id, Player.external_player_id, Player.risk_score, Player.risk_band, Player.pep_flag)
        .where(Player.id.in_(all_pids), Player.tenant_id == tid)
    )).all()

    _SEV_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    nodes: list[dict] = []
    for pid_row, ext_id, risk_score, risk_band, pep_flag in player_rows:
        nodes.append({
            "id": str(pid_row),
            "external_player_id": ext_id,
            "risk_score": float(risk_score or 0),
            "risk_band": risk_band or "LOW",
            "pep_flag": bool(pep_flag),
            "is_focal": str(pid_row) == player_id,
            "cluster_risk": _SEV_ORDER.get(str(risk_band or "LOW").upper(), 0),
        })

    # Deduplicar arestas: manter a de maior event_count por (source, target, edge_type)
    edge_key: dict[tuple, dict] = {}
    for e in all_edges:
        src, tgt = sorted([e["source"], e["target"]])  # normaliza direção
        k = (src, tgt, e["edge_type"])
        if k not in edge_key or e["event_count"] > edge_key[k]["event_count"]:
            edge_key[k] = {**e, "source": src, "target": tgt}
    deduped_edges = list(edge_key.values())

    # ── Indicadores de cluster ────────────────────────────────────────────────
    risk_bands_all = [n["risk_band"] for n in nodes if not n["is_focal"]]
    max_cluster_risk = max(risk_bands_all, key=lambda b: _SEV_ORDER.get(b, 0), default="LOW") if risk_bands_all else "LOW"
    has_pep = any(n["pep_flag"] for n in nodes if not n["is_focal"])
    device_edges = sum(1 for e in deduped_edges if e["edge_type"] == "device")
    bank_edges   = sum(1 for e in deduped_edges if e["edge_type"] == "bank_account")
    ip_edges     = sum(1 for e in deduped_edges if e["edge_type"] == "ip")

    # network_risk_score: pondera tamanho do cluster, max_risk e PEP
    cluster_size = len(nodes) - 1  # excluindo focal
    _risk_mult = {"LOW": 0.1, "MEDIUM": 0.3, "HIGH": 0.7, "CRITICAL": 1.0}
    peer_risk = _risk_mult.get(max_cluster_risk, 0.1)
    network_risk_score = round(
        min(1.0, peer_risk + (0.1 if has_pep else 0) + min(0.2, cluster_size * 0.02)),
        3,
    )

    return {
        "focal_player_id": player_id,
        "depth": depth,
        "nodes": sorted(nodes, key=lambda n: (-n["risk_score"], n["id"])),
        "edges": deduped_edges,
        "cluster_summary": {
            "total_nodes": len(nodes),
            "total_edges": len(deduped_edges),
            "peer_count": cluster_size,
            "max_cluster_risk": max_cluster_risk,
            "network_risk_score": network_risk_score,
            "has_pep_connection": has_pep,
            "shared_device_edges": device_edges,
            "shared_bank_edges":   bank_edges,
            "shared_ip_edges":     ip_edges,
        },
    }


@router.get("/players/{player_id}/case-alert-history")
async def get_player_case_alert_history(
    player_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Histórico de casos e alertas anteriores do player para o painel de investigação."""
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    cases = (await db.execute(
        select(Case)
        .where(Case.tenant_id == current_user.tenant_id, Case.player_id == player_id)
        .order_by(Case.created_at.desc())
        .limit(20)
    )).scalars().all()
    alerts = (await db.execute(
        select(Alert)
        .where(Alert.tenant_id == current_user.tenant_id, Alert.player_id == player_id)
        .order_by(Alert.created_at.desc())
        .limit(50)
    )).scalars().all()
    return {
        "player_id": player_id,
        "cases": [{"id": str(c.id), "title": c.title, "status": c.status,
                   "severity": c.severity, "created_at": c.created_at} for c in cases],
        "alerts": [{"id": str(a.id), "title": a.title, "severity": a.severity,
                    "status": a.status, "created_at": a.created_at} for a in alerts],
    }


# ─────────────────────────────────────────────────────────────────────────────
# GAP-T1: Timeline narrativa do apostador
# Junta transações + apostas + device events + alertas em blocos cronológicos.
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/players/{player_id}/timeline")
async def get_player_timeline(
    player_id: str,
    window_days: int = Query(90, ge=1, le=365),
    include: str = Query("transactions,bets,devices,alerts", description="Tipos de eventos separados por vírgula"),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Linha do tempo cronológica do apostador com blocos por dia.

    Parâmetros:
    - window_days: janela retroativa em dias (padrão 90)
    - include: tipos de evento desejados (transactions, bets, devices, alerts)
    """
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    since = _utcnow() - timedelta(days=window_days)
    include_set = {s.strip().lower() for s in include.split(",")}

    events: list[dict] = []

    # ── Transações ──────────────────────────────────────────────────────────
    if "transactions" in include_set:
        txns = (await db.execute(
            select(FinancialTransaction)
            .where(
                FinancialTransaction.tenant_id == current_user.tenant_id,
                FinancialTransaction.player_id == player_id,
                FinancialTransaction.occurred_at >= since,
            )
            .order_by(FinancialTransaction.occurred_at)
        )).scalars().all()
        for t in txns:
            events.append({
                "ts": t.occurred_at,
                "type": "TRANSACTION",
                "subtype": t.type,
                "amount": float(t.amount) if t.amount is not None else None,
                "currency": t.currency,
                "method": t.payment_method,
                "status": t.status,
                "id": str(t.id),
                "source_event_id": t.source_event_id,
            })

    # ── Apostas ─────────────────────────────────────────────────────────────
    if "bets" in include_set:
        bets = (await db.execute(
            select(Bet)
            .where(
                Bet.tenant_id == current_user.tenant_id,
                Bet.player_id == player_id,
                Bet.occurred_at >= since,
            )
            .order_by(Bet.occurred_at)
        )).scalars().all()
        for b in bets:
            events.append({
                "ts": b.occurred_at,
                "type": "BET",
                "subtype": b.bet_type,
                "amount": float(b.stake_amount) if b.stake_amount is not None else None,
                "currency": b.currency,
                "odds": float(b.odds) if b.odds is not None else None,
                "potential_payout": float(b.potential_payout) if b.potential_payout is not None else None,
                "settled_payout": float(b.actual_payout) if b.actual_payout is not None else None,
                "sport": b.event_name,
                "market": b.market_name,
                "status": b.status,
                "id": str(b.id),
                "source_event_id": b.source_event_id,
            })

    # ── Device events ───────────────────────────────────────────────────────
    if "devices" in include_set:
        devs = (await db.execute(
            select(DeviceEvent)
            .where(
                DeviceEvent.tenant_id == current_user.tenant_id,
                DeviceEvent.player_id == player_id,
                DeviceEvent.occurred_at >= since,
            )
            .order_by(DeviceEvent.occurred_at)
        )).scalars().all()
        for d in devs:
            events.append({
                "ts": d.occurred_at,
                "type": "DEVICE_EVENT",
                "subtype": d.action,
                "device_id": d.device_id,
                "country": d.country_code,
                "id": str(d.id),
                "source_event_id": d.source_event_id,
            })

    # ── Alertas ─────────────────────────────────────────────────────────────
    if "alerts" in include_set:
        alts = (await db.execute(
            select(Alert)
            .where(
                Alert.tenant_id == current_user.tenant_id,
                Alert.player_id == player_id,
                Alert.created_at >= since,
            )
            .order_by(Alert.created_at)
        )).scalars().all()
        for a in alts:
            events.append({
                "ts": a.created_at,
                "type": "ALERT",
                "subtype": a.alert_type,
                "severity": a.severity,
                "title": a.title,
                "composite_score": float(a.composite_score) if a.composite_score is not None else None,
                "ingest_mode": a.ingest_mode,
                "id": str(a.id),
                "case_id": str(a.case_id) if a.case_id else None,
            })

    # Ordenar todos os eventos por timestamp e agrupar por dia
    events.sort(key=lambda e: e["ts"] or _utcnow())
    days: dict[str, list[dict]] = {}
    for ev in events:
        ts = ev["ts"]
        day_key = ts.strftime("%Y-%m-%d") if ts else "unknown"
        days.setdefault(day_key, []).append({
            **ev,
            "ts": ts.isoformat() if ts else None,
        })

    timeline = [
        {"date": day, "event_count": len(blk), "events": blk}
        for day, blk in sorted(days.items())
    ]

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "GET_PLAYER_TIMELINE", "Player", player_id,
    )
    await db.flush()

    return {
        "player_id": player_id,
        "window_days": window_days,
        "since": since.isoformat(),
        "total_events": len(events),
        "timeline": timeline,
    }


# ── Self-Exclusão (Lei 14.790/2023 Art. 33) ──────────────────────────────────

class _SelfExclusionIn(BaseModel):
    reason: str | None = None


class _DepositLimitIn(BaseModel):
    deposit_limit_daily: float


@router.post("/players/{player_id}/self-exclusion", status_code=200)
async def set_self_exclusion(
    player_id: str,
    body: _SelfExclusionIn = _SelfExclusionIn(),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Ativa auto-exclusão do player (Lei 14.790/2023 Art. 33 e Portaria SPA/MF 1.143/2024).

    Define self_exclusion_flag=True e muda status para SELF_EXCLUDED.
    Requer roles ANALISTA ou GESTOR.
    """
    p = await db.get(Player, player_id)
    if not p or str(p.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Player não encontrado")

    p.self_exclusion_flag = True      # type: ignore[assignment]
    p.status = "SELF_EXCLUDED"        # type: ignore[assignment]

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "PLAYER_SELF_EXCLUSION_SET", "Player", player_id,
        after={"self_exclusion_flag": True, "status": "SELF_EXCLUDED", "reason": body.reason},
    )
    await db.commit()

    logger.info("player_self_exclusion_set", player_id=player_id, user_id=current_user.id)
    return {"player_id": player_id, "self_exclusion_flag": True, "status": "SELF_EXCLUDED"}


@router.delete("/players/{player_id}/self-exclusion", status_code=200)
async def clear_self_exclusion(
    player_id: str,
    current_user: User = Depends(require_role(AppRole.GESTOR)),
    db: AsyncSession = Depends(get_db),
):
    """Remove a auto-exclusão do player. Requer role GESTOR.

    Retorna status para ACTIVE após revisão manual.
    """
    p = await db.get(Player, player_id)
    if not p or str(p.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Player não encontrado")

    if not p.self_exclusion_flag:
        raise HTTPException(400, "Player não está em auto-exclusão.")

    p.self_exclusion_flag = False     # type: ignore[assignment]
    p.status = "ACTIVE"               # type: ignore[assignment]

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "PLAYER_SELF_EXCLUSION_CLEARED", "Player", player_id,
        after={"self_exclusion_flag": False, "status": "ACTIVE"},
    )
    await db.commit()

    logger.info("player_self_exclusion_cleared", player_id=player_id, user_id=current_user.id)
    return {"player_id": player_id, "self_exclusion_flag": False, "status": "ACTIVE"}


@router.patch("/players/{player_id}/deposit-limit", status_code=200)
async def update_deposit_limit(
    player_id: str,
    body: _DepositLimitIn,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Define ou atualiza o limite diário de depósito do player (Lei 14.790/2023 Art. 33)."""
    if body.deposit_limit_daily < 0:
        raise HTTPException(400, "deposit_limit_daily não pode ser negativo.")

    p = await db.get(Player, player_id)
    if not p or str(p.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Player não encontrado")

    previous = float(p.deposit_limit_daily) if p.deposit_limit_daily is not None else None
    p.deposit_limit_daily = body.deposit_limit_daily  # type: ignore[assignment]

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "PLAYER_DEPOSIT_LIMIT_UPDATED", "Player", player_id,
        before={"deposit_limit_daily": previous},
        after={"deposit_limit_daily": body.deposit_limit_daily},
    )
    await db.commit()

    return {"player_id": player_id, "deposit_limit_daily": body.deposit_limit_daily}


# ── KYC Events (PlayerKycEvent) ───────────────────────────────────────────────

class _KycEventIn(BaseModel):
    event_type: str
    provider: str = "manual"
    status: str = "PENDING"
    payload: dict = Field(default_factory=dict)


@router.post("/players/{player_id}/kyc-events", status_code=201)
async def create_kyc_event(
    player_id: str,
    body: _KycEventIn,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Registra um evento KYC para o player.

    Tipos comuns: DOCUMENT_CHECK, FACIAL_BIOMETRY, PEP_CHECK, SANCTIONS_CHECK,
    ADDRESS_VERIFICATION, PHONE_VERIFICATION, MANUAL_APPROVAL, MANUAL_REJECTION.

    Quando status=APPROVED: atualiza player.status para ACTIVE (se estava PENDING_KYC).
    Quando status=REJECTED: atualiza player.status para PENDING_KYC.
    """
    p = await db.get(Player, player_id)
    if not p or str(p.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Player não encontrado")

    event = PlayerKycEvent(
        tenant_id=current_user.tenant_id,
        player_id=player_id,
        entity_type="KYC_EVENT",
        subtype=body.event_type.upper(),
        event_type=body.event_type.upper(),
        provider=body.provider,
        status=body.status.upper(),
        payload=body.payload,
        occurred_at=_utcnow(),
        processed_at=_utcnow() if body.status.upper() in ("APPROVED", "REJECTED") else None,
    )
    db.add(event)

    # Transição de status do player baseada no resultado KYC
    if body.status.upper() == "APPROVED" and str(p.status) == "PENDING_KYC":
        p.status = "ACTIVE"  # type: ignore[assignment]
    elif body.status.upper() == "REJECTED":
        p.status = "PENDING_KYC"  # type: ignore[assignment]

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "PLAYER_KYC_EVENT", "Player", player_id,
        after={"event_type": event.event_type or event.subtype, "status": event.status, "provider": event.provider, "player_status": p.status},
    )
    await db.commit()
    await db.refresh(event)

    return {
        "id": event.id,
        "player_id": player_id,
        "event_type": event.event_type or event.subtype,
        "provider": event.provider,
        "status": event.status,
        "player_status": p.status,
        "created_at": event.created_at,
    }


@router.get("/players/{player_id}/kyc-events")
async def list_kyc_events(
    player_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Lista todos os eventos KYC do player em ordem cronológica decrescente."""
    p = await db.get(Player, player_id)
    if not p or str(p.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Player não encontrado")

    events = (await db.execute(
        select(PlayerKycEvent)
        .where(
            PlayerKycEvent.player_id == player_id,
            PlayerKycEvent.tenant_id == current_user.tenant_id,
        )
        .order_by(PlayerKycEvent.created_at.desc())
    )).scalars().all()

    return [
        {
            "id": ev.id,
            "event_type": ev.event_type or ev.subtype,
            "provider": ev.provider,
            "status": ev.status or "COMPLETED",
            "error_message": ev.error_message,
            "processed_at": ev.processed_at,
            "created_at": ev.created_at,
        }
        for ev in events
    ]
