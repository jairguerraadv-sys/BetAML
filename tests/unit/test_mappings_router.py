from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def _make_user(tenant_id: str = "tenant-1", role: str = "ADMIN"):
    return SimpleNamespace(id="user-1", tenant_id=tenant_id, role=role)


@pytest.mark.asyncio
async def test_preview_mapping_accepts_connector_native_sample_text():
    from routers.mappings import MappingPreviewIn, preview_mapping_config

    body = MappingPreviewIn(
        config_json={
            "source_system": "ConnectorGamma",
            "entity_type": "TRANSACTION",
            "fields": [
                {"target": "event_id", "source": "event_id", "transform": "copy"},
                {"target": "external_player_id", "source": "external_player_id", "transform": "copy"},
                {"target": "transaction_type", "source": "transaction_type", "transform": "copy"},
                {"target": "amount", "source": "amount", "transform": "coerceDecimal"},
                {"target": "occurred_at", "source": "occurred_at", "transform": "parseDate"},
            ],
        },
        format="json",
        sample_text=(
            "<Events><Transaction><EventId>G-1</EventId><PlayerId>PLY-1</PlayerId>"
            "<Type>DEPOSIT</Type><Amount currency='BRL'>100.0</Amount>"
            "<Timestamp>2026-03-20T10:00:00Z</Timestamp></Transaction></Events>"
        ),
    )

    response = await preview_mapping_config(body=body, current_user=_make_user(role="AML_ANALYST"))

    assert response["valid"] is True
    assert response["preview"]["event_id"] == "G-1"
    assert response["sample_parse"]["accepted"] == 1
    assert response["sample_parse"]["failed"] == 0


@pytest.mark.asyncio
async def test_rollback_mapping_creates_new_current_version():
    from routers.mappings import rollback_mapping_version

    ref = SimpleNamespace(
        id="map-v2",
        tenant_id="tenant-1",
        source_system="ConnectorDelta",
        entity_type="TRANSACTION",
        version_number=2,
        config_json={"fields": [{"target": "amount"}]},
        name="ConnectorDelta Transaction",
        version="2.0",
        active=True,
        is_current=True,
    )
    target = SimpleNamespace(
        id="map-v1",
        tenant_id="tenant-1",
        source_system="ConnectorDelta",
        entity_type="TRANSACTION",
        version_number=1,
        config_json={"fields": [{"target": "amount"}]},
        name="ConnectorDelta Transaction",
        version="1.0",
        active=True,
        is_current=False,
    )

    target_result = MagicMock()
    target_result.scalar_one_or_none.return_value = target
    max_result = MagicMock()
    max_result.scalar_one_or_none.return_value = 2
    update_result = MagicMock()

    db = AsyncMock()
    db.get = AsyncMock(return_value=ref)
    db.execute = AsyncMock(side_effect=[target_result, max_result, update_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch("routers.mappings.write_audit", new_callable=AsyncMock):
        response = await rollback_mapping_version(
            mapping_id="map-v2",
            version_number=1,
            current_user=_make_user(),
            db=db,
        )

    rollback_row = db.add.call_args.args[0]
    assert rollback_row.version_number == 3
    assert rollback_row.is_current is True
    assert rollback_row.parent_id == "map-v1"
    assert response["version_number"] == 3
    assert response["rollback_source_version_number"] == 1
