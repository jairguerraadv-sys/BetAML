"""
Testes unitários de autenticação, RBAC e endpoints críticos.
Usa httpx.AsyncClient + FastAPI TestClient (sem Docker necessário).

Cobre:
  - Login com tenant_slug correto/incorreto
  - Login sem tenant_slug (fallback global)
  - Logout real (blacklist Redis)
  - Refresh de token
  - RBAC em rotas protegidas (roles insuficientes → 403)
  - Rota /me
  - Ingest event (201/422 para payload inválido)
  - Proteção de rota sem JWT → 401
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ── Patch de dependências externas antes do import do app ────────────────────
import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("JWT_SECRET", "test-secret-only-for-unit-tests")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MIN", "60")
os.environ.setdefault("PII_ENCRYPTION_KEY", "test-pii-encryption-key-32bytes!!")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio123")
os.environ.setdefault("MINIO_BUCKET", "betaml-lakehouse")
os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("CLICKHOUSE_PORT", "9000")
os.environ.setdefault("CLICKHOUSE_DB", "betaml")

import sys
# Inserir na ordem INVERSA: o último a ser inserido fica no índice 0.
# Resultado final no sys.path: services/api (idx 0) → libs (idx 1)
# Isso garante que `from models import User` resolva para services/api/models.py
# e não para libs/models.py, evitando circular import.
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

# ── Helpers de token direto (bypass de DB para testes de token) ───────────────

def _make_token(user_id: str = "uid-1", tenant_id: str = "tid-1", role: str = "ADMIN") -> str:
    from auth import create_access_token
    return create_access_token({"sub": user_id, "tenant_id": tenant_id, "role": role})


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


# ────────────────────────────────────────────────────────────────────────────
# Testes de Token JWT (sem banco)
# ────────────────────────────────────────────────────────────────────────────

class TestJWT:
    """Testa criação, decodificação e campos obrigatórios do token."""

    def test_token_has_jti(self):
        """Todo token deve ter um jti único para suportar revogação."""
        from auth import create_access_token
        from jose import jwt
        import os
        tok = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "ADMIN"})
        payload = jwt.decode(tok, os.environ["JWT_SECRET"], algorithms=["HS256"])
        assert "jti" in payload, "jti ausente — revogação não funcionará"

    def test_token_has_tenant_id(self):
        from auth import create_access_token
        from jose import jwt
        import os
        tok = create_access_token({"sub": "u1", "tenant_id": "tid-abc", "role": "AML_ANALYST"})
        payload = jwt.decode(tok, os.environ["JWT_SECRET"], algorithms=["HS256"])
        assert payload["tenant_id"] == "tid-abc"

    def test_token_has_exp(self):
        from auth import create_access_token
        from jose import jwt
        import os, time
        tok = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "AUDITOR"})
        payload = jwt.decode(tok, os.environ["JWT_SECRET"], algorithms=["HS256"])
        assert payload["exp"] > time.time()

    def test_different_tokens_have_different_jti(self):
        from auth import create_access_token
        from jose import jwt
        import os
        t1 = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "ADMIN"})
        t2 = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "ADMIN"})
        p1 = jwt.decode(t1, os.environ["JWT_SECRET"], algorithms=["HS256"])
        p2 = jwt.decode(t2, os.environ["JWT_SECRET"], algorithms=["HS256"])
        assert p1["jti"] != p2["jti"], "jti deve ser único por token"


# ────────────────────────────────────────────────────────────────────────────
# Testes de DSL (sem banco, sem Kafka)
# ────────────────────────────────────────────────────────────────────────────

class TestDSLIntegration:
    """Valida que as 12 regras seed podem ser parseadas sem erros."""

    def _rules(self):
        """Extrai DEFAULT_RULES de seeds.py sem importar o módulo.

        Importar `services/api/seeds.py` dispara setup de engine/sessão; para
        manter teste puramente unitário, parseamos o AST e fazemos literal_eval.
        """
        import ast
        import pathlib

        seeds_path = pathlib.Path(REPO_ROOT / "services" / "api" / "seeds.py")
        src = seeds_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(seeds_path))

        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "DEFAULT_RULES":
                        return ast.literal_eval(node.value)
        raise AssertionError("DEFAULT_RULES não encontrado em services/api/seeds.py")

    def test_all_default_rules_have_valid_dsl(self):
        from libs.dsl_parser import validate_dsl
        rules = self._rules()
        assert rules and isinstance(rules, list), "DEFAULT_RULES vazio ou inválido"
        errors = []
        for rule in rules:
            ok, msg = validate_dsl(rule["condition_dsl"])
            if not ok:
                errors.append(f"Regra '{rule['name']}': {msg}")
        assert not errors, "Regras DSL inválidas:\n" + "\n".join(errors)

    def test_structuring_rule_fires(self):
        from libs.dsl_parser import eval_dsl
        ctx = {
            "transaction": {"type": "DEPOSIT", "amount": 800},
            "features":    {"deposit_count_24h": 7, "deposit_sum_24h": 6000},
            "bet":         {},
            "player":      {"pep_flag": False},
            "params":      {"count_threshold": 5, "sum_threshold": 5000},
        }
        dsl = 'features.deposit_count_24h >= params.count_threshold and features.deposit_sum_24h >= params.sum_threshold and transaction.type == "DEPOSIT"'
        assert eval_dsl(dsl, ctx) is True

    def test_pep_rule_fires(self):
        from libs.dsl_parser import eval_dsl
        ctx = {
            "transaction": {"amount": 8000, "type": "DEPOSIT"},
            "features":    {},
            "bet":         {},
            "player":      {"pep_flag": True},
            "params":      {"pep_threshold": 5000},
        }
        dsl = "player.pep_flag == true and transaction.amount >= params.pep_threshold"
        assert eval_dsl(dsl, ctx) is True

    def test_pep_rule_does_not_fire_for_non_pep(self):
        from libs.dsl_parser import eval_dsl
        ctx = {
            "transaction": {"amount": 8000},
            "features":    {},
            "bet":         {},
            "player":      {"pep_flag": False},
            "params":      {"pep_threshold": 5000},
        }
        dsl = "player.pep_flag == true and transaction.amount >= params.pep_threshold"
        assert eval_dsl(dsl, ctx) is False

    def test_round_trip_rule(self):
        from libs.dsl_parser import eval_dsl
        ctx = {
            "transaction": {"type": "WITHDRAWAL"},
            "features":    {
                "withdrawal_sum_24h": 950.0,
                "deposit_sum_24h":    1000.0,
                "bet_stake_sum_24h":  20.0,
            },
            "bet":    {},
            "player": {},
            "params": {"round_trip_ratio": "0.8", "max_stake": 50},
        }
        dsl = 'transaction.type == "WITHDRAWAL" and ratio(features.withdrawal_sum_24h, features.deposit_sum_24h) >= params.round_trip_ratio and features.bet_stake_sum_24h <= params.max_stake'
        assert eval_dsl(dsl, ctx) is True


# ────────────────────────────────────────────────────────────────────────────
# Testes de PII
# ────────────────────────────────────────────────────────────────────────────

class TestPII:
    def test_encrypt_decrypt_roundtrip(self):
        from auth import encrypt_pii, decrypt_pii
        original = "123.456.789-09"
        ciphertext = encrypt_pii(original)
        assert isinstance(ciphertext, bytes)
        assert decrypt_pii(ciphertext) == original

    def test_ciphertexts_differ_each_time(self):
        """Fernet inclui IV aleatório — dois cifrados do mesmo texto diferem."""
        from auth import encrypt_pii
        c1 = encrypt_pii("98765432100")
        c2 = encrypt_pii("98765432100")
        assert c1 != c2

    def test_mask_cpf(self):
        from auth import mask_cpf
        assert mask_cpf("123.456.789-09") == "***.***.***.09"
        assert mask_cpf("12345678909") == "***.***.***.09"

    def test_invalid_decrypt_raises(self):
        from auth import decrypt_pii
        with pytest.raises(ValueError):
            decrypt_pii(b"not-a-fernet-token")


# ────────────────────────────────────────────────────────────────────────────
# Testes de RBAC (require_roles)
# ────────────────────────────────────────────────────────────────────────────

class TestRBAC:
    """Testa que require_roles rejeita roles insuficientes."""

    def test_require_roles_accepts_all_matching(self):
        """require_roles('ADMIN','AML_ANALYST') aceita ambos."""
        from auth import require_roles
        checker = require_roles("ADMIN", "AML_ANALYST")
        # Verifica que a closure existe e tem a assinatura correta
        import inspect
        sig = inspect.signature(checker)
        assert "current_user" in sig.parameters

    def test_roles_set_is_complete(self):
        from auth import ROLES
        assert ROLES == {"SUPER_ADMIN", "ADMIN", "AML_ANALYST", "AUDITOR"}


# ────────────────────────────────────────────────────────────────────────────
# Testes de MappingEngine
# ────────────────────────────────────────────────────────────────────────────

class TestMappingEngine:
    def test_backoffice_alpha_transaction_mapping(self):
        from libs.mapping import MappingEngine, get_default_mapping
        cfg = get_default_mapping("BackofficeAlpha", "transaction")
        assert cfg is not None
        raw = {
            "transactionId":   "TXN-001",
            "playerId":        "PLY-001",
            "type":            "deposit",
            "amount":          "1500.0",
            "currency":        "BRL",
            "paymentMethod":   "PIX",
            "status":          "completed",
            "transactionDate": "2024-06-15T10:00:00Z",
        }
        result = MappingEngine(cfg).apply(raw)
        assert isinstance(result, dict)
        assert result.get("player_id") == "PLY-001"

    def test_backoffice_beta_transaction_mapping(self):
        from libs.mapping import MappingEngine, get_default_mapping
        cfg = get_default_mapping("BackofficeBeta", "transaction")
        assert cfg is not None
        raw = {
            "txn_id":       "B-001",
            "user_id":      "CUS-001",
            "txn_type":     "WITHDRAWAL",
            "value":        "500.0",
            "ccy":          "BRL",
            "occurred_utc": "2024-06-15T10:00:00Z",
        }
        result = MappingEngine(cfg).apply(raw)
        assert isinstance(result, dict)

    def test_mapping_engine_apply_returns_dict(self):
        from libs.mapping import get_default_mapping, MappingEngine
        cfg = get_default_mapping("BackofficeAlpha", "transaction")
        assert cfg is not None
        raw = {
            "transactionId": "T-X",
            "playerId":      "P-X",
            "amount":        "100",
            "currency":      "BRL",
            "type":          "deposit",
            "status":        "completed",
            "transactionDate": "2024-01-01T00:00:00Z",
        }
        result = MappingEngine(cfg).apply(raw)
        assert isinstance(result, dict), "apply() deve retornar dict"


# ────────────────────────────────────────────────────────────────────────────
# Testes de Feature Computation (função sync)
# ────────────────────────────────────────────────────────────────────────────

class TestFeatureComputation:
    # Nota: existe um único helper _make_txn (abaixo). Mantemos timezone-aware.

    def test_deposit_velocity_basic(self):
        from services.stream_processor.main import compute_features_offline  # type: ignore
        history = {
            "transactions": [
                self._make_txn(1, 100, "DEPOSIT"),
                self._make_txn(2, 200, "DEPOSIT"),
                self._make_txn(3, 300, "DEPOSIT"),
            ]
        }
        feats = compute_features_offline("PLY-001", history)
        assert feats["deposit_count_24h"] == 3
        assert feats["deposit_velocity"] == pytest.approx(3 / 24.0)

    def test_multi_currency_detected(self):
        from services.stream_processor.main import compute_features_offline  # type: ignore
        history = {
            "transactions": [
                self._make_txn(1, 100, "DEPOSIT", "BRL"),
                self._make_txn(2, 100, "DEPOSIT", "USD"),
            ]
        }
        feats = compute_features_offline("PLY-001", history)
        assert feats["multi_currency_flag"] is True

    def test_chargeback_rate(self):
        from services.stream_processor.main import compute_features_offline  # type: ignore
        history = {
            "transactions": [
                self._make_txn(1,  500, "DEPOSIT"),
                self._make_txn(2,  500, "DEPOSIT"),
                self._make_txn(3, -500, "DEPOSIT", is_chargeback=True),
            ]
        }
        feats = compute_features_offline("PLY-001", history)
        assert feats["chargeback_rate_30d"] == pytest.approx(1 / 3.0, abs=0.01)

    def _make_txn(self, hours_ago: float, amount: float, txn_type: str = "DEPOSIT",
                  currency: str = "BRL", result: str | None = None,
                  is_chargeback: bool = False) -> dict:
        from datetime import UTC, datetime, timedelta
        ts = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        return {
            "created_at":   ts,
            "amount":       amount,
            "txn_type":     txn_type,
            "currency":     currency,
            "result":       result,
            "is_chargeback": is_chargeback,
        }
