"""Helpers for ML model governance and synthetic-promotion hardening."""
from __future__ import annotations

from typing import Any


_ALLOWED_SYNTHETIC_ENVIRONMENTS = {"development", "dev", "test", "local"}
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


def is_synthetic_model(
    metrics: dict[str, Any] | None,
    trained_on_synthetic: bool | str | int | None = None,
) -> bool:
    """Resolve synthetic provenance from explicit column and legacy metrics.

    Precedence:
      1) explicit trained_on_synthetic=True always blocks
      2) legacy metrics flags (synthetic_bootstrap/synthetic)
    """
    explicit = _to_bool(trained_on_synthetic)
    if explicit:
        return True

    if not isinstance(metrics, dict):
        return False

    if _to_bool(metrics.get("synthetic_bootstrap")):
        return True

    # Legacy bootstrap marker used by older jobs.
    if _to_bool(metrics.get("synthetic")):
        return True

    return False


def blocks_synthetic_model_promotion(environment: str | None) -> bool:
    env = str(environment or "development").strip().lower()
    return env not in _ALLOWED_SYNTHETIC_ENVIRONMENTS


def sync_synthetic_metrics(metrics: dict[str, Any] | None, synthetic: bool) -> dict[str, Any]:
    payload = dict(metrics or {})
    if synthetic:
        payload["synthetic_bootstrap"] = True
    return payload
