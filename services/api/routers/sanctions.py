"""routers/sanctions.py — Verificação de sanções e PEP.

Endpoints:
  POST /sanctions/check      — Consulta avulsa de CPF/nome contra as listas em memória
  GET  /sanctions/status     — Estado do checker (total de entradas, último carregamento)
  POST /sanctions/reload     — Força recarga imediata do CSV (ADMIN only)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import require_roles
from models import User
from sanctions import get_sanctions_checker, reload_sanctions

router = APIRouter(tags=["sanctions"])


class SanctionsCheckIn(BaseModel):
    cpf_hmac: str | None = None
    name: str | None = None


@router.post("/sanctions/check")
async def check_sanctions(
    body: SanctionsCheckIn,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Verifica se o CPF HMAC ou nome informado constam em alguma lista de sanções/PEP.

    Pelo menos um campo (cpf_hmac ou name) deve ser informado.
    A consulta é realizada contra o índice em memória — sem I/O ao banco.
    """
    if not body.cpf_hmac and not body.name:
        raise HTTPException(status_code=422, detail="Informe ao menos cpf_hmac ou name.")

    checker = get_sanctions_checker()
    result = checker.check(cpf_hmac=body.cpf_hmac, name=body.name)
    return result.to_dict()


@router.get("/sanctions/status")
async def sanctions_status(
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Retorna o estado atual do checker de sanções: entradas carregadas e última recarga."""
    checker = get_sanctions_checker()
    return {
        "loaded": checker.is_loaded,
        "total_entries": checker.total_entries,
        "last_loaded_at": checker.last_loaded_at,
        "csv_path": checker.csv_path,
    }


@router.post("/sanctions/reload", status_code=200)
async def force_reload_sanctions(
    current_user: User = Depends(require_roles("ADMIN")),
):
    """Força a recarga imediata do CSV de sanções/PEP.

    Normalmente executado automaticamente via job diário às 06:00 UTC.
    Use este endpoint após atualizar o arquivo CSV sem aguardar o próximo ciclo.
    """
    loaded = reload_sanctions()
    return {"reloaded": True, "total_entries": loaded}
