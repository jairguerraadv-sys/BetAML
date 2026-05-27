"""Helpers for ML model governance and synthetic-promotion hardening."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ALLOWED_SYNTHETIC_ENVIRONMENTS = {"development", "dev", "test", "local"}
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_DEFAULT_MIN_PRECISION = 0.80
_DEFAULT_MAX_FALSE_POSITIVE_RATE = 0.20

_PRECISION_ALIASES = ("precision", "val_precision", "validation_precision")
_FALSE_POSITIVE_RATE_ALIASES = (
    "false_positive_rate",
    "fpr",
    "fp_rate",
    "validation_false_positive_rate",
)
_RECALL_ALIASES = ("recall", "val_recall", "validation_recall")


@dataclass(frozen=True)
class ModelPromotionDecision:
    allowed: bool
    reasons: list[str]
    metrics: dict[str, float | None]
    thresholds: dict[str, float | bool | None]


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


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_model_metric(metrics: dict[str, Any] | None, aliases: tuple[str, ...]) -> float | None:
    if not isinstance(metrics, dict):
        return None
    for key in aliases:
        if key not in metrics:
            continue
        value = _to_float(metrics.get(key))
        if value is not None:
            return value
    return None


def get_scoring_governance_thresholds(scoring_config: Any | None) -> dict[str, float | bool | None]:
    min_precision = _to_float(getattr(scoring_config, "min_precision", None))
    max_fpr = _to_float(getattr(scoring_config, "max_false_positive_rate", None))
    min_recall = _to_float(getattr(scoring_config, "min_recall", None))
    require_manual_approval = bool(getattr(scoring_config, "require_manual_approval", True))
    return {
        "min_precision": min_precision if min_precision is not None else _DEFAULT_MIN_PRECISION,
        "max_false_positive_rate": (
            max_fpr if max_fpr is not None else _DEFAULT_MAX_FALSE_POSITIVE_RATE
        ),
        "min_recall": min_recall,
        "require_manual_approval": require_manual_approval,
    }


def evaluate_model_promotion_candidate(
    *,
    metrics: dict[str, Any] | None,
    trained_on_synthetic: bool | str | int | None,
    scoring_config: Any | None,
    environment: str | None,
) -> ModelPromotionDecision:
    thresholds = get_scoring_governance_thresholds(scoring_config)
    strict_environment = blocks_synthetic_model_promotion(environment)

    precision = extract_model_metric(metrics, _PRECISION_ALIASES)
    false_positive_rate = extract_model_metric(metrics, _FALSE_POSITIVE_RATE_ALIASES)
    recall = extract_model_metric(metrics, _RECALL_ALIASES)
    metric_snapshot = {
        "precision": precision,
        "false_positive_rate": false_positive_rate,
        "recall": recall,
    }

    reasons: list[str] = []
    if is_synthetic_model(metrics, trained_on_synthetic) and strict_environment:
        reasons.append("synthetic_model_promotion_blocked")

    min_precision = float(thresholds["min_precision"])
    max_fpr = float(thresholds["max_false_positive_rate"])
    min_recall = thresholds["min_recall"]

    if precision is None and strict_environment:
        reasons.append("missing_precision_metric")
    elif precision is not None and precision < min_precision:
        reasons.append("precision_below_threshold")

    if false_positive_rate is None and strict_environment:
        reasons.append("missing_false_positive_rate_metric")
    elif false_positive_rate is not None and false_positive_rate > max_fpr:
        reasons.append("false_positive_rate_above_threshold")

    if min_recall is not None:
        min_recall_value = float(min_recall)
        if recall is None and strict_environment:
            reasons.append("missing_recall_metric")
        elif recall is not None and recall < min_recall_value:
            reasons.append("recall_below_threshold")

    return ModelPromotionDecision(
        allowed=len(reasons) == 0,
        reasons=reasons,
        metrics=metric_snapshot,
        thresholds=thresholds,
    )
