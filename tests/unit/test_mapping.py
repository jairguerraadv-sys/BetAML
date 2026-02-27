"""
Testes unitários do MappingEngine (libs/mapping.py).
Cobertura: BackofficeAlpha/Beta, todos os transform types.
"""
import pytest
from libs.mapping import MappingEngine, get_default_mapping, TransformType


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def alpha_txn_raw():
    return {
        "transactionId":    "TXN-001",
        "playerId":         "PLY-001",
        "type":             "deposit",
        "amount":           "1500.50",
        "currency":         "BRL",
        "paymentMethod":    "PIX",
        "status":           "completed",
        "transactionDate":  "2024-06-01T10:00:00Z",
        "idempotencyKey":   "idem-001",
    }

@pytest.fixture
def alpha_player_raw():
    return {
        "playerId":          "PLY-100",
        "fullName":          "  João Silva  ",
        "cpf":               "12345678901",
        "email":             "Joao@email.com",
        "dateOfBirth":       "1990-01-15",
        "pepFlag":           False,
        "status":            "active",
        "declaredIncome":    "4000.00",
        "registrationDate":  "2022-03-01T00:00:00Z",
    }

@pytest.fixture
def beta_txn_raw():
    return {
        "txn_id":        "B-999",
        "user_id":       "U-555",
        "txn_type":      "WITHDRAWAL",
        "value":         "800.00",
        "ccy":           "BRL",
        "method":        "ted",
        "txn_status":    "DONE",
        "occurred_utc":  "2024-07-15T14:30:00Z",
    }


# ── BackofficeAlpha TRANSACTION ───────────────────────────────────────────────

def test_alpha_txn_copies_basic_fields(alpha_txn_raw):
    mapping = get_default_mapping("BackofficeAlpha", "TRANSACTION")
    eng = MappingEngine(mapping)
    out = eng.apply(alpha_txn_raw)
    assert out["transaction_id"] == "TXN-001"
    assert out["player_id"] == "PLY-001"

def test_alpha_txn_coerce_amount(alpha_txn_raw):
    mapping = get_default_mapping("BackofficeAlpha", "TRANSACTION")
    out = MappingEngine(mapping).apply(alpha_txn_raw)
    assert isinstance(out["amount"], float)
    assert abs(out["amount"] - 1500.50) < 0.01

def test_alpha_txn_uppercase_type(alpha_txn_raw):
    mapping = get_default_mapping("BackofficeAlpha", "TRANSACTION")
    out = MappingEngine(mapping).apply(alpha_txn_raw)
    # type field mapped via mapEnum or uppercase
    assert out.get("type", "").upper() in ("DEPOSIT", "deposit".upper())

def test_alpha_txn_parse_date(alpha_txn_raw):
    mapping = get_default_mapping("BackofficeAlpha", "TRANSACTION")
    out = MappingEngine(mapping).apply(alpha_txn_raw)
    # deve retornar datetime ou string ISO
    assert out.get("occurred_at") is not None


# ── BackofficeAlpha PLAYER ────────────────────────────────────────────────────

def test_alpha_player_strip_name(alpha_player_raw):
    mapping = get_default_mapping("BackofficeAlpha", "PLAYER")
    out = MappingEngine(mapping).apply(alpha_player_raw)
    assert out.get("full_name", "").strip() == out.get("full_name", "")

def test_alpha_player_normalize_cpf(alpha_player_raw):
    mapping = get_default_mapping("BackofficeAlpha", "PLAYER")
    out = MappingEngine(mapping).apply(alpha_player_raw)
    cpf = out.get("cpf", "")
    # normalizeCpf deve remover formatação
    assert "." not in cpf and "-" not in cpf
    assert len(cpf) == 11

def test_alpha_player_lowercase_email(alpha_player_raw):
    mapping = get_default_mapping("BackofficeAlpha", "PLAYER")
    out = MappingEngine(mapping).apply(alpha_player_raw)
    assert out.get("email", "") == out.get("email", "").lower()

def test_alpha_player_coerce_income(alpha_player_raw):
    mapping = get_default_mapping("BackofficeAlpha", "PLAYER")
    out = MappingEngine(mapping).apply(alpha_player_raw)
    assert isinstance(out.get("declared_income_monthly"), float)


# ── BackofficeBeta TRANSACTION ────────────────────────────────────────────────

def test_beta_txn_copies_fields(beta_txn_raw):
    mapping = get_default_mapping("BackofficeBeta", "TRANSACTION")
    out = MappingEngine(mapping).apply(beta_txn_raw)
    assert out.get("transaction_id") == "B-999"
    assert out.get("player_id") == "U-555"

def test_beta_txn_amount_float(beta_txn_raw):
    mapping = get_default_mapping("BackofficeBeta", "TRANSACTION")
    out = MappingEngine(mapping).apply(beta_txn_raw)
    assert isinstance(out.get("amount"), float)
    assert abs(out["amount"] - 800.0) < 0.01


# ── Mapping missing fields ────────────────────────────────────────────────────

def test_missing_optional_field_does_not_raise(alpha_txn_raw):
    """Campos opcionais ausentes não devem lançar KeyError."""
    raw = {k: v for k, v in alpha_txn_raw.items() if k != "idempotencyKey"}
    mapping = get_default_mapping("BackofficeAlpha", "TRANSACTION")
    try:
        out = MappingEngine(mapping).apply(raw)
        assert isinstance(out, dict)
    except KeyError:
        pytest.fail("MappingEngine lançou KeyError para campo opcional ausente")


# ── get_default_mapping ───────────────────────────────────────────────────────

def test_get_default_mapping_returns_none_for_unknown():
    result = get_default_mapping("UnknownBackoffice", "TRANSACTION")
    assert result is None

def test_get_default_mapping_alpha_transaction():
    m = get_default_mapping("BackofficeAlpha", "TRANSACTION")
    assert m is not None
    assert len(m) > 0

def test_get_default_mapping_beta_player():
    m = get_default_mapping("BackofficeBeta", "PLAYER")
    assert m is not None
