#!/usr/bin/env python3
"""Utilitario operacional para replay de erros de ingestao com opcao dry-run."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import requests


DEFAULT_API_URL = "http://localhost:8000"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _fetch_errors(api_url: str, token: str, limit: int, resolved: bool) -> list[dict[str, Any]]:
    params = {"limit": limit, "resolved": str(resolved).lower()}
    resp = requests.get(
        f"{api_url.rstrip('/')}/ingest/errors",
        params=params,
        headers=_headers(token),
        timeout=20,
    )
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, dict):
        items = body.get("items")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    return []


def _replay_error(api_url: str, token: str, error_id: str, reason: str) -> dict[str, Any]:
    resp = requests.post(
        f"{api_url.rstrip('/')}/ingest/errors/{error_id}/replay",
        json={"reason": reason},
        headers=_headers(token),
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {"raw": data}


def _pick_error_ids(errors: list[dict[str, Any]], explicit_error_id: str | None) -> list[str]:
    if explicit_error_id:
        return [explicit_error_id]
    ids: list[str] = []
    for item in errors:
        candidate = item.get("id") or item.get("error_id")
        if isinstance(candidate, str) and candidate:
            ids.append(candidate)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay de erros de ingestao com dry-run")
    parser.add_argument("--api-url", default=os.getenv("BETAML_API_URL", DEFAULT_API_URL))
    parser.add_argument("--token", default=os.getenv("BETAML_API_TOKEN", ""))
    parser.add_argument("--error-id", default="", help="Reprocessa apenas um erro especifico")
    parser.add_argument("--limit", type=int, default=20, help="Quantidade maxima de erros consultados")
    parser.add_argument("--reason", default="manual dlq replay")
    parser.add_argument("--dry-run", action="store_true", help="Nao executa replay, apenas lista")
    args = parser.parse_args()

    if not args.token:
        print("BETAML_API_TOKEN nao informado", file=sys.stderr)
        return 2

    try:
        errors = _fetch_errors(args.api_url, args.token, args.limit, resolved=False)
    except requests.RequestException as exc:
        print(f"Falha ao consultar ingest/errors: {exc}", file=sys.stderr)
        return 1

    error_ids = _pick_error_ids(errors, args.error_id or None)
    if not error_ids:
        print("Nenhum erro elegivel para replay")
        return 0

    print(f"Erros elegiveis: {len(error_ids)}")
    for error_id in error_ids:
        print(f"- {error_id}")

    if args.dry_run:
        print("Dry-run ativo: nenhum replay executado")
        return 0

    exit_code = 0
    for error_id in error_ids:
        try:
            result = _replay_error(args.api_url, args.token, error_id, args.reason)
            status = result.get("status", "unknown")
            print(f"Replay {error_id}: status={status}")
        except requests.RequestException as exc:
            exit_code = 1
            print(f"Replay {error_id}: falhou ({exc})", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
