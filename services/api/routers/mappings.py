"""routers/mappings.py — CRUD + versionamento + validação/preview de MappingConfig."""
from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from libs.connectors import CONNECTOR_TEMPLATE_REGISTRY
from libs.mapping import (
    MappingConfigSchema,
    MappingEngine,
    validate_mapped_payload_against_canonical_schema,
    validate_mapping_targets_against_canonical_schema,
)
from models import MappingConfig, User
from utils import write_audit

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["mappings"])


class MappingCreate(BaseModel):
    name: str
    source_system: str
    entity_type: str
    config_json: dict[str, Any] | None = None
    config_text: str | None = None
    format: str = Field(default="json", pattern="^(json|yaml)$")
    change_notes: str | None = None
    version: str = "1.0"


class MappingPatch(BaseModel):
    name: str | None = None
    config_json: dict[str, Any] | None = None
    config_text: str | None = None
    format: str = Field(default="json", pattern="^(json|yaml)$")
    change_notes: str | None = None


class MappingValidateIn(BaseModel):
    config_json: dict[str, Any] | None = None
    config_text: str | None = None
    format: str = Field(default="json", pattern="^(json|yaml)$")


class MappingPreviewIn(MappingValidateIn):
    sample: dict[str, Any]


class MappingTestIn(BaseModel):
    sample: dict[str, Any]


def _normalize_raw_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Normaliza formato legado (transforms) para formato canônico (fields)."""
    if "fields" in raw:
        cfg = {
            "version": str(raw.get("version", "1.0")),
            "source_system": raw.get("source_system"),
            "entity_type": str(raw.get("entity_type", "TRANSACTION")).upper(),
            "fields": raw.get("fields", []),
        }
        MappingConfigSchema(**cfg)
        return cfg

    transforms = raw.get("transforms", [])
    if not isinstance(transforms, list):
        raise ValueError("config.transforms deve ser uma lista")

    fields: list[dict[str, Any]] = []
    for tf in transforms:
        if not isinstance(tf, dict):
            raise ValueError("Cada transform deve ser um objeto")
        target = tf.get("field") or tf.get("target")
        transform = tf.get("type") or tf.get("transform") or "copy"
        params = dict(tf.get("params") or {})
        if "mapping" in tf:
            params.update(tf.get("mapping") or {})
        if "value" in tf:
            params["value"] = tf["value"]
        if "format" in tf:
            params["format"] = tf["format"]
        if "attr" in tf:
            params["attr"] = tf["attr"]
        fields.append(
            {
                "target": target,
                "source": tf.get("source"),
                "transform": transform,
                "params": params,
                "required": bool(tf.get("required", False)),
            }
        )

    cfg = {
        "version": str(raw.get("version", "1.0")),
        "source_system": raw.get("source_system"),
        "entity_type": str(raw.get("entity_type", "TRANSACTION")).upper(),
        "fields": fields,
    }
    MappingConfigSchema(**cfg)
    return cfg


def _parse_config_payload(
    *,
    config_json: dict[str, Any] | None,
    config_text: str | None,
    fmt: str,
) -> dict[str, Any]:
    if config_json is not None:
        return _normalize_raw_config(config_json)

    if not config_text:
        raise ValueError("Informe config_json ou config_text")

    if fmt == "yaml":
        if yaml is None:
            raise ValueError("Suporte YAML indisponível no servidor")
        parsed = yaml.safe_load(config_text) or {}
    else:
        parsed = json.loads(config_text)

    if not isinstance(parsed, dict):
        raise ValueError("Config deve ser um objeto JSON/YAML")
    return _normalize_raw_config(parsed)


async def _next_version_number(
    db: AsyncSession,
    tenant_id: str,
    source_system: str,
    entity_type: str,
) -> int:
    stmt = select(func.max(MappingConfig.version_number)).where(
        MappingConfig.tenant_id == tenant_id,
        MappingConfig.source_system == source_system,
        MappingConfig.entity_type == entity_type,
    )
    current = (await db.execute(stmt)).scalar_one_or_none() or 0
    return int(current) + 1


@router.get("/mappings/templates")
async def list_mapping_templates(
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    return list(CONNECTOR_TEMPLATE_REGISTRY.values())


@router.post("/mappings/validate")
async def validate_mapping_config(
    body: MappingValidateIn,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    _ = current_user
    try:
        cfg = _parse_config_payload(
            config_json=body.config_json,
            config_text=body.config_text,
            fmt=body.format,
        )
        canonical_validation = validate_mapping_targets_against_canonical_schema(cfg)
        return {
            "valid": canonical_validation["valid"],
            "normalized_config": cfg,
            "canonical_validation": canonical_validation,
            "error": None if canonical_validation["valid"] else "Config incompatível com schema canônico de ingestão",
        }
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "error": str(exc)}


@router.post("/mappings/preview")
async def preview_mapping_config(
    body: MappingPreviewIn,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    _ = current_user
    try:
        cfg = _parse_config_payload(
            config_json=body.config_json,
            config_text=body.config_text,
            fmt=body.format,
        )
        engine = MappingEngine(cfg)
        preview = engine.apply(body.sample)
        canonical_validation = validate_mapped_payload_against_canonical_schema(cfg["entity_type"], preview)
        return {
            "valid": canonical_validation["valid"],
            "preview": preview,
            "normalized_config": cfg,
            "canonical_validation": canonical_validation,
            "error": None if canonical_validation["valid"] else "Preview incompatível com schema canônico de ingestão",
        }
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "error": str(exc), "preview": {}}


@router.get("/mappings")
async def list_mappings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(MappingConfig)
        .where(MappingConfig.tenant_id == current_user.tenant_id)
        .order_by(MappingConfig.source_system, MappingConfig.entity_type, MappingConfig.version_number.desc())
    )
    mc = (await db.execute(q)).scalars().all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "source_system": m.source_system,
            "entity_type": m.entity_type,
            "version": m.version,
            "version_number": m.version_number,
            "is_current": m.is_current,
            "active": m.active,
            "change_notes": m.change_notes,
            "updated_at": m.updated_at,
        }
        for m in mc
    ]


@router.post("/mappings", status_code=201)
async def create_mapping(
    body: MappingCreate,
    current_user: User = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    cfg = _parse_config_payload(
        config_json=body.config_json,
        config_text=body.config_text,
        fmt=body.format,
    )
    canonical_validation = validate_mapping_targets_against_canonical_schema(cfg)
    if not canonical_validation["valid"]:
        raise HTTPException(
            422,
            {
                "message": "Config incompatível com schema canônico de ingestão",
                "canonical_validation": canonical_validation,
            },
        )

    version_number = await _next_version_number(
        db,
        current_user.tenant_id,
        body.source_system,
        body.entity_type,
    )

    await db.execute(
        update(MappingConfig)
        .where(
            MappingConfig.tenant_id == current_user.tenant_id,
            MappingConfig.source_system == body.source_system,
            MappingConfig.entity_type == body.entity_type,
        )
        .values(is_current=False)
    )

    mc = MappingConfig(
        tenant_id=current_user.tenant_id,
        name=body.name,
        source_system=body.source_system,
        entity_type=body.entity_type,
        config_json=cfg,
        version=body.version,
        version_number=version_number,
        is_current=True,
        change_notes=body.change_notes,
        created_by=current_user.id,
    )
    db.add(mc)
    await db.commit()
    await db.refresh(mc)
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "CREATE_MAPPING", "MappingConfig", str(mc.id),
        after={"name": mc.name, "source_system": mc.source_system, "entity_type": mc.entity_type, "version_number": mc.version_number},
    )
    return {"id": mc.id, "name": mc.name, "version_number": mc.version_number, "is_current": mc.is_current}


@router.get("/mappings/{mapping_id}")
async def get_mapping(
    mapping_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mc = await db.get(MappingConfig, mapping_id)
    if not mc or mc.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Mapping não encontrado")
    return {
        "id": mc.id,
        "name": mc.name,
        "source_system": mc.source_system,
        "entity_type": mc.entity_type,
        "version": mc.version,
        "version_number": mc.version_number,
        "is_current": mc.is_current,
        "change_notes": mc.change_notes,
        "config_json": mc.config_json,
        "canonical_validation": validate_mapping_targets_against_canonical_schema(mc.config_json or {}),
        "active": mc.active,
    }


@router.get("/mappings/{mapping_id}/versions")
async def list_mapping_versions(
    mapping_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ref = await db.get(MappingConfig, mapping_id)
    if not ref or ref.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Mapping não encontrado")

    q = (
        select(MappingConfig)
        .where(
            MappingConfig.tenant_id == current_user.tenant_id,
            MappingConfig.source_system == ref.source_system,
            MappingConfig.entity_type == ref.entity_type,
        )
        .order_by(MappingConfig.version_number.desc())
    )
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "version_number": m.version_number,
            "is_current": m.is_current,
            "change_notes": m.change_notes,
            "created_at": m.created_at,
        }
        for m in rows
    ]


@router.post("/mappings/{mapping_id}/rollback")
async def rollback_mapping_version(
    mapping_id: str,
    version_number: int = Query(..., ge=1),
    current_user: User = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    ref = await db.get(MappingConfig, mapping_id)
    if not ref or ref.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Mapping não encontrado")

    q = select(MappingConfig).where(
        MappingConfig.tenant_id == current_user.tenant_id,
        MappingConfig.source_system == ref.source_system,
        MappingConfig.entity_type == ref.entity_type,
        MappingConfig.version_number == version_number,
    )
    target = (await db.execute(q)).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Versão não encontrada")

    await db.execute(
        update(MappingConfig)
        .where(
            MappingConfig.tenant_id == current_user.tenant_id,
            MappingConfig.source_system == ref.source_system,
            MappingConfig.entity_type == ref.entity_type,
        )
        .values(is_current=False)
    )
    target.is_current = True
    target.active = True
    await db.commit()
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "ROLLBACK_MAPPING", "MappingConfig", str(mapping_id),
        before={"version_number": ref.version_number},
        after={"version_number": target.version_number},
    )
    return {"status": "activated", "id": target.id, "version_number": target.version_number}


@router.put("/mappings/{mapping_id}")
async def create_new_mapping_version(
    mapping_id: str,
    body: MappingPatch,
    current_user: User = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    current = await db.get(MappingConfig, mapping_id)
    if not current or current.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Mapping não encontrado")

    cfg = _parse_config_payload(
        config_json=body.config_json or current.config_json,
        config_text=body.config_text,
        fmt=body.format,
    )
    canonical_validation = validate_mapping_targets_against_canonical_schema(cfg)
    if not canonical_validation["valid"]:
        raise HTTPException(
            422,
            {
                "message": "Config incompatível com schema canônico de ingestão",
                "canonical_validation": canonical_validation,
            },
        )
    version_number = await _next_version_number(
        db,
        current_user.tenant_id,
        current.source_system,
        current.entity_type,
    )

    await db.execute(
        update(MappingConfig)
        .where(
            MappingConfig.tenant_id == current_user.tenant_id,
            MappingConfig.source_system == current.source_system,
            MappingConfig.entity_type == current.entity_type,
        )
        .values(is_current=False)
    )

    new_row = MappingConfig(
        tenant_id=current.tenant_id,
        name=body.name or current.name,
        source_system=current.source_system,
        entity_type=current.entity_type,
        version=f"{version_number}.0",
        version_number=version_number,
        parent_id=current.parent_id or current.id,
        is_current=True,
        active=True,
        change_notes=body.change_notes,
        config_json=cfg,
        created_by=current_user.id,
    )
    db.add(new_row)
    await db.commit()
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "UPDATE_MAPPING", "MappingConfig", str(mapping_id),
        before={"version_number": current.version_number, "name": current.name},
        after={"version_number": new_row.version_number, "name": new_row.name},
    )
    return {
        "id": new_row.id,
        "name": new_row.name,
        "version_number": new_row.version_number,
        "is_current": new_row.is_current,
    }


@router.post("/mappings/{mapping_id}/test")
async def test_mapping(
    mapping_id: str,
    body: MappingTestIn,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    mc = await db.get(MappingConfig, mapping_id)
    if not mc or mc.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Mapping não encontrado")
    try:
        engine = MappingEngine(mc.config_json)
        result = engine.apply(body.sample)
        return {"status": "ok", "canonical": result}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}
