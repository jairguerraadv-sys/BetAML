"""Unit tests for libs/transforms/mapping.py."""

import sys
import os
from datetime import datetime
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from libs.transforms.mapping import (
    MappingError,
    MappingTransformer,
    coerce_decimal,
    map_enum,
    normalize_cpf,
    parse_date,
)


# ---------------------------------------------------------------------------
# normalize_cpf
# ---------------------------------------------------------------------------


class TestNormalizeCpf:
    # Known-valid CPF: 529.982.247-25
    VALID_CPF_RAW = "529.982.247-25"
    VALID_CPF_DIGITS = "52998224725"

    def test_strips_formatting(self):
        assert normalize_cpf(self.VALID_CPF_RAW) == self.VALID_CPF_DIGITS

    def test_plain_digits_accepted(self):
        assert normalize_cpf(self.VALID_CPF_DIGITS) == self.VALID_CPF_DIGITS

    def test_zero_padded(self):
        # CPF starting with 0; verified with the official check-digit algorithm
        assert normalize_cpf("010.000.000-28") == "01000000028"

    def test_non_string_raises(self):
        with pytest.raises(MappingError, match="CPF must be a string"):
            normalize_cpf(52998224725)  # type: ignore[arg-type]

    def test_all_same_digits_raises(self):
        with pytest.raises(MappingError, match="invalid"):
            normalize_cpf("111.111.111-11")

    def test_wrong_check_digit_raises(self):
        with pytest.raises(MappingError, match="check digit"):
            normalize_cpf("529.982.247-00")

    def test_too_many_digits_raises(self):
        with pytest.raises(MappingError):
            normalize_cpf("1234567890123")  # 13 digits → still 13 after strip


# ---------------------------------------------------------------------------
# parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    def test_iso_format(self):
        result = parse_date("2024-01-15T10:30:00")
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_iso_with_timezone(self):
        result = parse_date("2024-06-01T00:00:00Z")
        assert result.year == 2024

    def test_datetime_passthrough(self):
        dt = datetime(2024, 3, 1, 12, 0, 0)
        assert parse_date(dt) is dt

    def test_explicit_format(self):
        result = parse_date("15/01/2024", fmt="%d/%m/%Y")
        assert result.day == 15
        assert result.month == 1
        assert result.year == 2024

    def test_explicit_format_fallback_to_dateutil(self):
        # fmt doesn't match but dateutil can handle it
        result = parse_date("January 15, 2024", fmt="%d/%m/%Y")
        assert result.year == 2024

    def test_non_string_raises(self):
        with pytest.raises(MappingError, match="Cannot parse date"):
            parse_date(20240101)

    def test_invalid_date_string_raises(self):
        with pytest.raises(MappingError, match="Unable to parse date"):
            parse_date("not-a-date")


# ---------------------------------------------------------------------------
# coerce_decimal
# ---------------------------------------------------------------------------


class TestCoerceDecimal:
    def test_from_int(self):
        assert coerce_decimal(100) == Decimal("100")

    def test_from_float(self):
        assert coerce_decimal(1.5) == Decimal("1.5")

    def test_from_string(self):
        assert coerce_decimal("3.14") == Decimal("3.14")

    def test_from_decimal_passthrough(self):
        d = Decimal("99.99")
        assert coerce_decimal(d) is d

    def test_invalid_string_raises(self):
        with pytest.raises(MappingError, match="Cannot coerce"):
            coerce_decimal("not-a-number")

    def test_none_raises(self):
        with pytest.raises(MappingError, match="Cannot coerce"):
            coerce_decimal(None)


# ---------------------------------------------------------------------------
# map_enum
# ---------------------------------------------------------------------------


class TestMapEnum:
    MAPPING = {"DEPOSIT": "DEP", "WITHDRAWAL": "WDR", "BET": "BET"}

    def test_exact_match(self):
        assert map_enum("DEPOSIT", self.MAPPING) == "DEP"

    def test_case_insensitive(self):
        assert map_enum("deposit", self.MAPPING) == "DEP"

    def test_mixed_case(self):
        assert map_enum("Withdrawal", self.MAPPING) == "WDR"

    def test_no_match_returns_default_none(self):
        assert map_enum("TRANSFER", self.MAPPING) is None

    def test_no_match_returns_custom_default(self):
        assert map_enum("TRANSFER", self.MAPPING, default="UNKNOWN") == "UNKNOWN"

    def test_whitespace_trimmed(self):
        assert map_enum("  DEPOSIT  ", self.MAPPING) == "DEP"


# ---------------------------------------------------------------------------
# MappingTransformer.apply
# ---------------------------------------------------------------------------


class TestMappingTransformerApply:
    def _transformer(self):
        return MappingTransformer()

    def test_simple_field_rename(self):
        config = {"fields": {"amount": {"source": "valor"}}}
        result = self._transformer().apply({"valor": 100}, config)
        assert result["amount"] == 100

    def test_coerce_decimal_transform(self):
        config = {
            "fields": {
                "amount": {"source": "valor", "transform": "coerce_decimal"}
            }
        }
        result = self._transformer().apply({"valor": "250.50"}, config)
        assert result["amount"] == Decimal("250.50")

    def test_normalize_cpf_transform(self):
        config = {
            "fields": {
                "cpf": {"source": "cpf_raw", "transform": "normalize_cpf"}
            }
        }
        result = self._transformer().apply({"cpf_raw": "529.982.247-25"}, config)
        assert result["cpf"] == "52998224725"

    def test_map_enum_transform(self):
        config = {
            "fields": {
                "type": {
                    "source": "tipo",
                    "transform": "map_enum",
                    "args": {"mapping": {"DEPOSITO": "DEPOSIT", "SAQUE": "WITHDRAWAL"}},
                }
            }
        }
        result = self._transformer().apply({"tipo": "DEPOSITO"}, config)
        assert result["type"] == "DEPOSIT"

    def test_parse_date_transform(self):
        config = {
            "fields": {
                "occurred_at": {"source": "data", "transform": "parse_date"}
            }
        }
        result = self._transformer().apply({"data": "2024-01-15T10:00:00"}, config)
        assert isinstance(result["occurred_at"], datetime)

    def test_upper_transform(self):
        config = {"fields": {"name": {"source": "name", "transform": "upper"}}}
        result = self._transformer().apply({"name": "alice"}, config)
        assert result["name"] == "ALICE"

    def test_lower_transform(self):
        config = {"fields": {"name": {"source": "name", "transform": "lower"}}}
        result = self._transformer().apply({"name": "ALICE"}, config)
        assert result["name"] == "alice"

    def test_strip_transform(self):
        config = {"fields": {"name": {"source": "name", "transform": "strip"}}}
        result = self._transformer().apply({"name": "  alice  "}, config)
        assert result["name"] == "alice"

    def test_default_value_used_when_source_missing(self):
        config = {
            "fields": {"status": {"source": "estado", "default": "PENDING"}}
        }
        result = self._transformer().apply({}, config)
        assert result["status"] == "PENDING"

    def test_passthrough_copies_unmapped_fields(self):
        config = {
            "fields": {"amount": {"source": "valor"}},
            "passthrough": True,
        }
        result = self._transformer().apply({"valor": 50, "extra": "data"}, config)
        assert result["amount"] == 50
        assert result["extra"] == "data"

    def test_passthrough_false_no_extras(self):
        config = {
            "fields": {"amount": {"source": "valor"}},
            "passthrough": False,
        }
        result = self._transformer().apply({"valor": 50, "extra": "data"}, config)
        assert "extra" not in result

    def test_unknown_transform_raises(self):
        config = {
            "fields": {"x": {"source": "x", "transform": "nonexistent"}}
        }
        with pytest.raises(MappingError, match="Unknown transform"):
            self._transformer().apply({"x": 1}, config)
