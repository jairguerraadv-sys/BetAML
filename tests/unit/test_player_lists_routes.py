from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def _user():
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = "t1"
    return u


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_list_player_list_entries_returns_rows():
    from routers.player_lists import list_player_list_entries

    db = _db()
    entry = MagicMock()
    entry.id = "e1"
    entry.value = "12345678900"
    entry.value_type = "CPF"
    entry.added_at = None

    async def execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = [entry]
        return res

    db.execute = execute

    with patch("routers.player_lists._get_list_or_404", AsyncMock()):
        result = await list_player_list_entries("l1", db=db, current_user=_user())

    assert len(result) == 1
    assert result[0] is entry


@pytest.mark.asyncio
async def test_delete_player_list_entry_deletes_row():
    from routers.player_lists import delete_player_list_entry

    db = _db()
    row = MagicMock()
    row.id = "e1"
    row.value = "123"
    row.value_type = "CPF"

    async def execute(stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = row
        return res

    db.execute = execute
    db.delete = AsyncMock()

    with patch("routers.player_lists._get_list_or_404", AsyncMock()), patch("routers.player_lists.write_audit", AsyncMock()):
        await delete_player_list_entry("l1", "e1", db=db, current_user=_user())

    db.delete.assert_awaited_once_with(row)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_list_csv_adds_entries_and_audits():
    from routers.player_lists import upload_list_csv

    db = _db()
    uploaded = MagicMock()
    uploaded.read = AsyncMock(return_value=b"12345678901\n\"98765432100\"\n\n")

    class _List:
        id = "l1"

    async def execute(stmt):
        res = MagicMock()
        res.scalar_one_or_none.return_value = _List()
        return res

    db.execute = execute
    db.add = MagicMock()

    with patch("routers.player_lists.UploadFile", return_value=uploaded), patch("routers.player_lists.write_audit", AsyncMock()) as audit_mock:
        result = await upload_list_csv(
            list_id="l1",
            file=uploaded,
            value_type="CPF",
            db=db,
            current_user=_user(),
        )

    assert result["added"] == 2
    assert db.add.call_count == 2
    audit_mock.assert_awaited_once()
