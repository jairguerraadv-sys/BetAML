"""
routers/search.py — Global search endpoint for the BetAML frontend.

Searches players (by external_id/name/CPF-hash), cases (by reference_number/title),
and alerts (by alert_type/id) for the current tenant.
"""
from __future__ import annotations

from collections import OrderedDict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import compute_cpf_hmac, decrypt_pii, get_current_user, mask_cpf
from database import get_db
from models import Alert, Case, Player, User
from utils import write_audit

router = APIRouter(prefix="/search", tags=["search"])

_MAX = 5  # max results per category


def _safe_masked_cpf(player: Player) -> str | None:
    try:
        return mask_cpf(decrypt_pii(player.cpf_encrypted))
    except Exception:  # noqa: BLE001
        return None


def _query_digits(q: str) -> str:
    return "".join(ch for ch in q if ch.isdigit())


@router.get("", summary="Global search")
async def global_search(
    q: str = Query(..., min_length=2, max_length=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Search players, cases and alerts across the current tenant.
    Matches on:
     - Players: external_player_id (prefix), name (partial), CPF (HMAC index O(1))
     - Cases: reference_number (prefix), title (partial)
     - Alerts: alert_id (prefix), alert_type (partial)
    """
    tid = current_user.tenant_id
    q_like = f"%{q}%"
    q_prefix = f"{q}%"
    digits = _query_digits(q)

    # ── Players — text lookup ─────────────────────────────────────────────────
    stmt_players = (
        select(Player)
        .where(
            Player.tenant_id == tid,
            Player.status != "ERASED",
            or_(
                Player.external_player_id.ilike(q_prefix),
                # full_name is the unencrypted display name populated during ingest
                Player.full_name.ilike(q_like),
            ),
        )
        .limit(_MAX)
    )
    players_rows = list((await db.execute(stmt_players)).scalars().all())

    # ── CPF lookup: O(1) via cpf_hmac index (preferred) ──────────────────────
    if digits and len(digits) == 11:
        # Exact 11-digit CPF → HMAC lookup is deterministic and O(1)
        try:
            cpf_hmac = compute_cpf_hmac(digits)
            hmac_rows = list((await db.execute(
                select(Player)
                .where(
                    Player.tenant_id == tid,
                    Player.status != "ERASED",
                    Player.cpf_hmac == cpf_hmac,
                )
                .limit(_MAX)
            )).scalars().all())
            players_rows.extend(hmac_rows)
        except Exception:  # noqa: BLE001
            pass  # fallback: HMAC not available (e.g. key rotation)

    elif digits and 3 <= len(digits) < 11:
        # Partial CPF digits: bounded O(n) fallback (max 250 rows, sorted by recency)
        # NOTE: this path disappears once cpf_hmac is backfilled (migration_v21)
        # and the query can switch to LIKE on a computed column.
        cpf_candidates = list((
            await db.execute(
                select(Player)
                .where(
                    Player.tenant_id == tid,
                    Player.status != "ERASED",
                )
                .order_by(Player.updated_at.desc())
                .limit(250)
            )
        ).scalars().all())
        for player in cpf_candidates:
            try:
                cpf_plain = decrypt_pii(player.cpf_encrypted)
            except Exception:  # noqa: BLE001
                continue
            cpf_dig = "".join(ch for ch in cpf_plain if ch.isdigit())
            masked_dig = "".join(ch for ch in mask_cpf(cpf_plain) if ch.isdigit())
            if digits in cpf_dig or digits in masked_dig:
                players_rows.append(player)

    players_rows = list(OrderedDict((str(p.id), p) for p in players_rows).values())[:_MAX]

    # LGPD Art. 37 — log PII access when player names are returned in search results
    if players_rows:
        await write_audit(
            db, tid, current_user.id,
            "SEARCH_PLAYERS", "Player", None,
            pii_accessed="full_name",
        )
        await db.flush()

    # ── Cases ─────────────────────────────────────────────────────────────────
    stmt_cases = (
        select(Case)
        .where(
            Case.tenant_id == tid,
            or_(
                Case.reference_number.ilike(q_prefix),
                Case.title.ilike(q_like),
            ),
        )
        .order_by(Case.created_at.desc())
        .limit(_MAX)
    )
    cases_rows = (await db.execute(stmt_cases)).scalars().all()

    # ── Alerts ────────────────────────────────────────────────────────────────
    stmt_alerts = (
        select(Alert)
        .where(
            Alert.tenant_id == tid,
            or_(
                cast(Alert.id, String).ilike(q_prefix),
                Alert.alert_type.ilike(q_like),
            ),
        )
        .order_by(Alert.created_at.desc())
        .limit(_MAX)
    )
    alerts_rows = (await db.execute(stmt_alerts)).scalars().all()

    return {
        "players": [
            {
                "id": str(p.id),
                "external_id": p.external_player_id,
                "name": p.full_name or p.external_player_id,
                "cpf_masked": _safe_masked_cpf(p),
                "risk_band": p.risk_band or "UNKNOWN",
            }
            for p in players_rows
        ],
        "cases": [
            {
                "id": str(c.id),
                "reference_number": c.reference_number or str(c.id)[:8],
                "title": c.title or "",
                "status": c.status or "",
            }
            for c in cases_rows
        ],
        "alerts": [
            {
                "id": str(a.id),
                "alert_type": a.alert_type or "",
                "severity": a.severity or "",
                "player_id": str(a.player_id) if a.player_id else "",
            }
            for a in alerts_rows
        ],
    }
