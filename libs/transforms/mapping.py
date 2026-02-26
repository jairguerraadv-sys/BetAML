"""Mapping and transformation utilities for the BetAML ingestion pipeline."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from dateutil import parser as dateutil_parser


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MappingError(Exception):
    """Raised when a mapping/transform operation fails."""


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def parse_date(value: Any, fmt: Optional[str] = None) -> datetime:
    """Parse *value* into a :class:`datetime`.

    If *fmt* is provided it is tried first using :func:`datetime.strptime`;
    on failure (or when *fmt* is omitted) the value is handed to
    ``python-dateutil`` for best-effort parsing.

    Raises :class:`MappingError` if the value cannot be parsed.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise MappingError(f"Cannot parse date from type {type(value).__name__!r}: {value!r}")
    if fmt:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass  # fall through to dateutil
    try:
        return dateutil_parser.parse(value)
    except (ValueError, OverflowError) as exc:
        raise MappingError(f"Unable to parse date {value!r}: {exc}") from exc


def normalize_cpf(value: str) -> str:
    """Strip non-digits from *value*, zero-pad to 11 digits, and validate.

    Raises :class:`MappingError` if the result is not a valid 11-digit CPF.
    """
    if not isinstance(value, str):
        raise MappingError(f"CPF must be a string, got {type(value).__name__!r}")
    digits = re.sub(r"\D", "", value)
    digits = digits.zfill(11)
    if len(digits) != 11:
        raise MappingError(f"CPF {value!r} has {len(digits)} digits after normalisation (expected 11)")
    if digits == digits[0] * 11:
        raise MappingError(f"CPF {value!r} is invalid (all digits identical)")

    # Official CPF check-digit algorithm
    def _check(d: str, weights: list[int]) -> bool:
        total = sum(int(c) * w for c, w in zip(d, weights))
        remainder = (total * 10) % 11
        remainder = 0 if remainder == 10 else remainder
        return remainder == int(d[len(weights)])

    if not _check(digits, list(range(10, 1, -1))):
        raise MappingError(f"CPF {value!r} has invalid first check digit")
    if not _check(digits, list(range(11, 1, -1))):
        raise MappingError(f"CPF {value!r} has invalid second check digit")
    return digits


def map_enum(value: str, mapping: dict[str, Any], default: Any = None) -> Any:
    """Map *value* to a canonical enum value using *mapping*.

    Lookup is case-insensitive.  Returns *default* when no match is found.
    """
    normalised = str(value).strip().upper()
    # Try exact key first, then upper-cased key
    if value in mapping:
        return mapping[value]
    if normalised in mapping:
        return mapping[normalised]
    for k, v in mapping.items():
        if str(k).strip().upper() == normalised:
            return v
    return default


def coerce_decimal(value: Any) -> Decimal:
    """Coerce *value* (str / int / float / Decimal) to :class:`Decimal`.

    Raises :class:`MappingError` on failure.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise MappingError(f"Cannot coerce {value!r} to Decimal: {exc}") from exc


# ---------------------------------------------------------------------------
# MappingTransformer
# ---------------------------------------------------------------------------


class MappingTransformer:
    """Apply a declarative *config* to transform a source *record* dict.

    Config structure::

        {
            "fields": {
                "<target_field>": {
                    "source": "<source_field>",      # required
                    "transform": "<transform_name>", # optional
                    "args": {...},                    # optional extra args
                    "default": <value>,              # optional fallback
                }
            }
        }

    Supported transform names
    -------------------------
    ``parse_date``
        Convert the value to a :class:`datetime`.  Pass ``fmt`` in *args* to
        specify a :func:`datetime.strptime` format string.

    ``normalize_cpf``
        Normalise a CPF string (see :func:`normalize_cpf`).

    ``map_enum``
        Map source values to canonical enum values.  Pass ``mapping`` in
        *args*.

    ``coerce_decimal``
        Coerce the value to :class:`Decimal`.

    ``upper`` / ``lower`` / ``strip``
        String casing / whitespace transforms.
    """

    _TRANSFORMS: dict[str, Any] = {
        "parse_date": parse_date,
        "normalize_cpf": normalize_cpf,
        "coerce_decimal": coerce_decimal,
    }

    def apply(self, record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        """Transform *record* according to *config* and return a new dict."""
        field_configs: dict[str, Any] = config.get("fields", {})
        result: dict[str, Any] = {}

        for target_field, field_cfg in field_configs.items():
            source_key: str = field_cfg.get("source", target_field)
            default_value: Any = field_cfg.get("default", None)
            raw_value: Any = record.get(source_key, default_value)

            transform_name: Optional[str] = field_cfg.get("transform")
            if transform_name is not None:
                extra_args: dict = field_cfg.get("args", {})
                raw_value = self._apply_transform(transform_name, raw_value, extra_args)

            result[target_field] = raw_value

        # Pass through any fields not explicitly mapped when passthrough=True
        if config.get("passthrough", False):
            mapped_sources = {cfg.get("source", k) for k, cfg in field_configs.items()}
            for key, val in record.items():
                if key not in mapped_sources and key not in result:
                    result[key] = val

        return result

    def _apply_transform(self, name: str, value: Any, args: dict) -> Any:
        if name == "map_enum":
            mapping: dict = args.get("mapping", {})
            default: Any = args.get("default", None)
            return map_enum(value, mapping, default)

        if name in ("upper", "lower", "strip"):
            if not isinstance(value, str):
                return value
            return getattr(value, name)()

        if name == "parse_date":
            fmt: Optional[str] = args.get("fmt")
            return parse_date(value, fmt)

        if name == "normalize_cpf":
            return normalize_cpf(value)

        if name == "coerce_decimal":
            return coerce_decimal(value)

        if name in self._TRANSFORMS:
            return self._TRANSFORMS[name](value, **args)

        raise MappingError(f"Unknown transform {name!r}")
