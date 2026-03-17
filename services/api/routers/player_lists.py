"""routers/player_lists.py — Player watchlists (M3): CRUD + CSV bulk upload."""
from __future__ import annotations


from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models import PlayerList, PlayerListEntry, User
from utils import write_audit

router = APIRouter(prefix="/player-lists", tags=["player-lists"])


# ── Pydantic in/out ───────────────────────────────────────────────────────────

class PlayerListOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    list_type: str
    entry_count: int = 0


class PlayerListCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    list_type: str = "BLACKLIST"


class PlayerListEntryBulk(BaseModel):
    values: list[str]
    value_type: str = "CPF"


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

@router.get("", response_model=list[PlayerListOut])
async def list_player_lists(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all player watchlists for the current tenant."""
    rows = (await db.execute(
        select(PlayerList).where(PlayerList.tenant_id == current_user.tenant_id)
    )).scalars().all()

    out = []
    for row in rows:
        cnt = (await db.execute(
            select(func.count()).where(PlayerListEntry.player_list_id == row.id)
        )).scalar_one()
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        d["entry_count"] = cnt
        out.append(d)
    return out


@router.post("", status_code=201)
async def create_player_list(
    body: PlayerListCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new player watchlist."""
    pl = PlayerList(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        list_type=body.list_type,
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


@router.post("/{list_id}/entries", status_code=201)
async def bulk_add_list_entries(
    list_id: str,
    body: PlayerListEntryBulk,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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


@router.post("/{list_id}/upload-csv", status_code=201)
async def upload_list_csv(
    list_id: str,
    file: UploadFile = File(...),
    value_type: str = Query("CPF"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
            ))
            added += 1
    await db.commit()
    return {"added": added}
