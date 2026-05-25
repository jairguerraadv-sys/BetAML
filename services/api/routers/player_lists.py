"""routers/player_lists.py — Player watchlists (M3): CRUD + CSV bulk upload."""
from __future__ import annotations


from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from models import PlayerList, PlayerListEntry, User
from utils import write_audit

router = APIRouter(prefix="/player-lists", tags=["player-lists"])


# ── Pydantic in/out ───────────────────────────────────────────────────────────

class PlayerListOut(BaseModel):
    id: str
    tenant_id: str | None = None
    name: str
    description: str | None = None
    list_type: str
    source: str | None = None
    active: bool = True
    entry_count: int = 0
    created_at: object | None = None
    updated_at: object | None = None


class PlayerListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    list_type: str = "BLACKLIST"
    source: str = "MANUAL"


class PlayerListUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    list_type: str | None = None
    source: str | None = None
    active: bool | None = None


class PlayerListEntryBulk(BaseModel):
    values: list[str]
    value_type: str = "CPF"


class PlayerListEntryOut(BaseModel):
    id: str
    value: str | None = None
    value_type: str | None = None
    external_player_id: str | None = None
    cpf_hash: str | None = None
    notes: str | None = None
    added_at: object | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_list_or_404(list_id: str, tenant_id: str, db: AsyncSession) -> PlayerList:
    pl = (await db.execute(
        select(PlayerList).where(
            PlayerList.id == list_id,
            PlayerList.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if pl is None:
        raise HTTPException(404, "Player list not found")
    return pl


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_player_lists(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    envelope: bool = Query(False, description="Quando true, retorna {items,total,limit,offset}."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all player watchlists for the current tenant."""
    envelope_enabled = envelope if isinstance(envelope, bool) else False
    base_q = select(PlayerList).where(PlayerList.tenant_id == current_user.tenant_id)
    if envelope_enabled:
        rows = (await db.execute(base_q.limit(limit).offset(offset))).scalars().all()
    else:
        rows = (await db.execute(base_q)).scalars().all()

    out = []
    for row in rows:
        cnt = (await db.execute(
            select(func.count()).where(PlayerListEntry.player_list_id == row.id)
        )).scalar_one()
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        d["entry_count"] = cnt
        out.append(d)
    if not envelope_enabled:
        return out

    total = (await db.execute(
        select(func.count()).select_from(PlayerList).where(PlayerList.tenant_id == current_user.tenant_id)
    )).scalar_one()
    return {
        "items": out,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{list_id}", response_model=PlayerListOut)
async def get_player_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = await _get_list_or_404(list_id, current_user.tenant_id, db)
    cnt = (await db.execute(
        select(func.count()).where(PlayerListEntry.player_list_id == row.id)
    )).scalar_one()
    d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    d["entry_count"] = cnt
    return d


@router.post("", status_code=201)
async def create_player_list(
    body: PlayerListCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Create a new player watchlist."""
    pl = PlayerList(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        list_type=body.list_type,
        source=body.source,
        created_by=current_user.id,
    )
    db.add(pl)
    await db.flush()
    await write_audit(
        db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.id,
        action="CREATE_PLAYER_LIST",
        entity_type="PlayerList",
        entity_id=str(pl.id),
        after={"name": body.name},
    )
    await db.commit()
    return {"id": str(pl.id), "name": pl.name}


@router.patch("/{list_id}", response_model=PlayerListOut)
async def update_player_list(
    list_id: str,
    body: PlayerListUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    pl = await _get_list_or_404(list_id, current_user.tenant_id, db)
    before = {"name": pl.name, "list_type": pl.list_type, "source": pl.source, "active": pl.active}
    for field in ("name", "description", "list_type", "source", "active"):
        value = getattr(body, field)
        if value is not None:
            setattr(pl, field, value)
    pl.updated_by = current_user.id
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "UPDATE_PLAYER_LIST", "PlayerList", list_id,
        before=before, after=body.model_dump(exclude_none=True),
    )
    await db.commit()
    await db.refresh(pl)
    cnt = (await db.execute(
        select(func.count()).where(PlayerListEntry.player_list_id == pl.id)
    )).scalar_one()
    d = {c.name: getattr(pl, c.name) for c in pl.__table__.columns}
    d["entry_count"] = cnt
    return d


@router.get("/{list_id}/entries", response_model=list[PlayerListEntryOut])
async def list_player_list_entries(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _get_list_or_404(list_id, current_user.tenant_id, db)
    rows = (
        await db.execute(
            select(PlayerListEntry).where(
                PlayerListEntry.player_list_id == list_id,
                PlayerListEntry.tenant_id == current_user.tenant_id,
            ).order_by(PlayerListEntry.added_at.desc())
        )
    ).scalars().all()
    return rows


@router.post("/{list_id}/entries", status_code=201)
async def bulk_add_list_entries(
    list_id: str,
    body: PlayerListEntryBulk,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Bulk-add entries (CPF hashes, IPs, etc.) to a player watchlist."""
    pl = await _get_list_or_404(list_id, current_user.tenant_id, db)
    added = 0
    for val in body.values:
        db.add(PlayerListEntry(
            list_id=pl.id,
            tenant_id=current_user.tenant_id,
            player_list_id=list_id,
            value=val,
            value_type=body.value_type,
            added_by=current_user.id,
        ))
        added += 1
    await db.commit()
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "BULK_ADD_LIST_ENTRIES", "PlayerList", list_id,
        after={"count": len(body.values), "value_type": body.value_type},
    )
    return {"added": added}


@router.delete("/{list_id}", status_code=204)
async def delete_player_list(
    list_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Delete a player watchlist and all its entries."""
    pl = await _get_list_or_404(list_id, current_user.tenant_id, db)
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "DELETE_PLAYER_LIST", "PlayerList", list_id,
        before={"name": pl.name},
    )
    await db.delete(pl)
    await db.commit()


@router.delete("/{list_id}/entries/{entry_id}", status_code=204)
async def delete_player_list_entry(
    list_id: str,
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    await _get_list_or_404(list_id, current_user.tenant_id, db)
    row = (
        await db.execute(
            select(PlayerListEntry).where(
                PlayerListEntry.id == entry_id,
                PlayerListEntry.player_list_id == list_id,
                PlayerListEntry.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Player list entry not found")
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "DELETE_PLAYER_LIST_ENTRY", "PlayerListEntry", entry_id,
        before={"value": row.value, "value_type": row.value_type},
    )
    await db.delete(row)
    await db.commit()


@router.post("/{list_id}/upload-csv", status_code=201)
async def upload_list_csv(
    list_id: str,
    file: UploadFile = File(...),
    value_type: str = Query("CPF"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Upload a CSV file to bulk-populate a player watchlist (one value per line)."""
    pl = await _get_list_or_404(list_id, current_user.tenant_id, db)

    content = await file.read()
    lines = content.decode("utf-8", errors="replace").splitlines()
    added = 0
    for line in lines:
        val = line.strip().strip('"').strip("'")
        if val:
            db.add(PlayerListEntry(
                list_id=pl.id,
                tenant_id=current_user.tenant_id,
                player_list_id=list_id,
                value=val,
                value_type=value_type,
                added_by=current_user.id,
            ))
            added += 1
    await db.commit()
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "UPLOAD_PLAYER_LIST_CSV", "PlayerList", list_id,
        after={"count": added, "value_type": value_type},
    )
    return {"added": added}
