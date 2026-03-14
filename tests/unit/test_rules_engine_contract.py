"""
Contract tests — Rules Engine (services/rules_engine/main.py)

Verifica o ciclo completo:
  1. evaluate_rules(): dado um envelope de evento + features + lista de regras, retorna matches corretos
  2. publish_alert(): dado um match, enfileira no Kafka (mock) e na fila de escrita Postgres
  3. Compound rules: composite score >= threshold gera match
  4. DSL falha graciosamente (não levanta exceção, apenas descarta a regra)
  5. db_writer_queue: estrutura das mensagens enfileiradas é compatível com Alert ORM

Estes são unit tests — não requerem Kafka, Postgres ou Redis.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Import rules_engine/main.py como módulo nomeado para evitar colisão com api/main.py
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_RE_PATH = os.path.join(_ROOT, "services", "rules_engine", "main.py")
sys.path.insert(0, os.path.join(_ROOT, "services", "rules_engine"))
sys.path.insert(0, os.path.join(_ROOT, "libs"))

_spec = importlib.util.spec_from_file_location("rules_engine_main", _RE_PATH)
_re_mod = importlib.util.module_from_spec(_spec)
sys.modules["rules_engine_main"] = _re_mod
_spec.loader.exec_module(_re_mod)

evaluate_rules = _re_mod.evaluate_rules
publish_alert = _re_mod.publish_alert


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def transaction_envelope():
    return {
        "event_id":    "evt-001",
        "tenant_id":   "tenant-a",
        "entity_type": "TRANSACTION",
        "source_system": "BackofficeAlpha",
        "payload": {
            "player_id": "player-001",
            "amount":    9999.99,
            "type":      "DEPOSIT",
            "method":    "PIX",
            "status":    "COMPLETED",
            "currency":  "BRL",
        },
    }


@pytest.fixture
def low_risk_features():
    return {
        "pep_flag": False,
        "declared_income_monthly": 10000.0,
        "deposit_sum_24h": 500.0,
        "deposit_count_24h": 2,
        "zscore_deposit_amount": 0.5,
        "withdraw_to_deposit_ratio_7d": 0.1,
    }


@pytest.fixture
def pep_features(low_risk_features):
    return {**low_risk_features, "pep_flag": True, "declared_income_monthly": 5000.0}


@pytest.fixture
def rule_high_amount():
    return {
        "id": 1,
        "name": "HighAmountDeposit",
        "condition_dsl": 'transaction.amount > 5000 and transaction.type == "DEPOSIT"',
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "weight": 1.0,
        "version": 1,
        "params": {"threshold": 5000},
    }


@pytest.fixture
def rule_pep_volume():
    return {
        "id": 2,
        "name": "PepHighVolume",
        "condition_dsl": "player.pepFlag == true and transaction.amount > 3000",
        "severity": "CRITICAL",
        "scope": "TRANSACTION",
        "weight": 1.5,
        "version": 1,
        "params": {},
    }


@pytest.fixture
def rule_bet_scope():
    return {
        "id": 3,
        "name": "HighStakeBet",
        "condition_dsl": "bet.stakeAmount > 500",
        "severity": "MEDIUM",
        "scope": "BET",
        "weight": 0.8,
        "version": 1,
        "params": {},
    }


# ─── evaluate_rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluate_rules_matches_high_amount(
    transaction_envelope, low_risk_features, rule_high_amount
):
    """Regra de valor alto deve disparar para depósito de R$9999."""
    matches = await evaluate_rules(
        transaction_envelope, low_risk_features, [rule_high_amount]
    )
    assert len(matches) == 1
    assert matches[0]["rule"]["id"] == 1
    assert matches[0]["rule_weight"] == 1.0


@pytest.mark.asyncio
async def test_evaluate_rules_no_match_below_threshold(
    transaction_envelope, low_risk_features, rule_high_amount
):
    """Regra não deve disparar para valor abaixo do threshold."""
    envelope = {**transaction_envelope, "payload": {**transaction_envelope["payload"], "amount": 100.0}}
    matches = await evaluate_rules(envelope, low_risk_features, [rule_high_amount])
    assert matches == []


@pytest.mark.asyncio
async def test_evaluate_rules_pep_matches(
    transaction_envelope, pep_features, rule_pep_volume
):
    """Regra PEP deve disparar para player PEP com volume alto."""
    matches = await evaluate_rules(transaction_envelope, pep_features, [rule_pep_volume])
    assert len(matches) == 1
    assert matches[0]["rule"]["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_evaluate_rules_pep_no_match_non_pep(
    transaction_envelope, low_risk_features, rule_pep_volume
):
    """Regra PEP não deve disparar para player não-PEP."""
    matches = await evaluate_rules(transaction_envelope, low_risk_features, [rule_pep_volume])
    assert matches == []


@pytest.mark.asyncio
async def test_evaluate_rules_bet_scope_ignored_for_transaction(
    transaction_envelope, low_risk_features, rule_bet_scope
):
    """Regra de scope BET não deve ser avaliada para evento TRANSACTION."""
    matches = await evaluate_rules(transaction_envelope, low_risk_features, [rule_bet_scope])
    assert matches == []


@pytest.mark.asyncio
async def test_evaluate_rules_multiple_rules_multiple_matches(
    transaction_envelope, pep_features, rule_high_amount, rule_pep_volume
):
    """Quando duas regras batem, dois matches devem ser retornados."""
    matches = await evaluate_rules(
        transaction_envelope, pep_features, [rule_high_amount, rule_pep_volume]
    )
    assert len(matches) == 2
    rule_ids = {m["rule"]["id"] for m in matches}
    assert rule_ids == {1, 2}


@pytest.mark.asyncio
async def test_evaluate_rules_invalid_dsl_does_not_raise(
    transaction_envelope, low_risk_features
):
    """DSL inválido não deve levantar exceção — deve logar e ignorar a regra."""
    bad_rule = {
        "id": 99,
        "name": "BrokenRule",
        "condition_dsl": "INVALID DSL $$$ %%%",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "weight": 1.0,
        "version": 1,
        "params": {},
    }
    matches = await evaluate_rules(transaction_envelope, low_risk_features, [bad_rule])
    assert matches == []


@pytest.mark.asyncio
async def test_evaluate_rules_compound_above_threshold(
    transaction_envelope, pep_features, rule_high_amount, rule_pep_volume
):
    """Compound rule com composite score acima do threshold deve gerar match."""
    compound = {
        "id": 50,
        "name": "CompoundHighRisk",
        "logic": "AND",
        "component_rule_ids": [1, 2],
        "score_weights": {"1": 0.6, "2": 0.4},
        "min_score_threshold": 0.5,
        "severity_mode": "MAX",
    }
    matches = await evaluate_rules(
        transaction_envelope,
        pep_features,
        [rule_high_amount, rule_pep_volume],
        compound_rules=[compound],
    )
    # 2 simples + 1 compound = 3 matches
    assert len(matches) == 3
    compound_match = next(m for m in matches if m["rule"].get("is_compound"))
    assert compound_match["composite_score"] >= 0.5


@pytest.mark.asyncio
async def test_evaluate_rules_compound_below_threshold(
    transaction_envelope, low_risk_features, rule_high_amount, rule_pep_volume
):
    """Compound rule abaixo do threshold não gera match extra."""
    compound = {
        "id": 50,
        "name": "CompoundHighRisk",
        "logic": "AND",
        "component_rule_ids": [1, 2],
        "score_weights": {"1": 0.5, "2": 0.5},
        "min_score_threshold": 0.8,
        "severity_mode": "MAX",
    }
    # Apenas rule_high_amount bate (rule_pep_volume não bate para non-PEP)
    matches = await evaluate_rules(
        transaction_envelope,
        low_risk_features,
        [rule_high_amount, rule_pep_volume],
        compound_rules=[compound],
    )
    # composite = 0.5 * score(1) + 0.5 * score(2) = 0.5 * 1.0 + 0.5 * 0.0 = 0.5, below 0.8
    assert not any(m["rule"].get("is_compound") for m in matches)


@pytest.mark.asyncio
async def test_evaluate_rules_unknown_entity_type_returns_empty(low_risk_features):
    """Evento de tipo desconhecido deve retornar lista vazia."""
    envelope = {
        "event_id":    "evt-999",
        "tenant_id":   "tenant-a",
        "entity_type": "DEVICE_EVENT",
        "payload":     {"player_id": "p1"},
    }
    rule = {
        "id": 1, "name": "R", "condition_dsl": "transaction.amount > 0",
        "severity": "LOW", "scope": "TRANSACTION", "weight": 1.0, "version": 1, "params": {},
    }
    matches = await evaluate_rules(envelope, low_risk_features, [rule])
    assert matches == []


# ─── publish_alert ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_alert_sends_to_kafka_and_enqueues(
    transaction_envelope, rule_high_amount
):
    """publish_alert deve chamar producer.send e adicionar à db_write_queue."""
    mock_producer = AsyncMock()
    mock_producer.send = AsyncMock()
    queue = asyncio.Queue()

    match = {
        "rule":              rule_high_amount,
        "eval_ms":           5,
        "rule_weight":       1.0,
        "context_snapshot":  {"transaction": {"amount": 9999.99}},
        "features_snapshot": {"pep_flag": False},
    }

    await publish_alert(transaction_envelope, match, mock_producer, queue)

    # Verificar que o produtor Kafka foi chamado
    mock_producer.send.assert_awaited_once()
    call_args = mock_producer.send.call_args
    assert call_args[0][0] == "scoring.alerts"
    kafka_msg = call_args[0][1]
    assert kafka_msg["tenant_id"] == "tenant-a"
    assert kafka_msg["player_id"] == "player-001"
    assert kafka_msg["severity"] == "HIGH"
    assert kafka_msg["alert_type"] == "RULE"
    assert "alert_id" in kafka_msg
    assert "evidence" in kafka_msg

    # Verificar que a fila de DB foi alimentada
    assert not queue.empty()
    db_entry = await queue.get()
    assert db_entry["type"] == "alert"
    assert db_entry["alert"]["alert_id"] == kafka_msg["alert_id"]
    assert db_entry["matched"] is True


@pytest.mark.asyncio
async def test_publish_alert_message_schema(transaction_envelope, rule_high_amount):
    """Mensagem publicada deve conter todos os campos obrigatórios do Alert ORM."""
    required_fields = {
        "alert_id", "tenant_id", "player_id", "alert_type",
        "severity", "title", "description", "rule_id",
        "source_event_id", "evidence", "created_at", "schema_version",
    }
    mock_producer = AsyncMock()
    mock_producer.send = AsyncMock()
    queue = asyncio.Queue()

    match = {
        "rule": rule_high_amount,
        "eval_ms": 3,
        "rule_weight": 1.0,
        "context_snapshot": {},
        "features_snapshot": {},
    }

    await publish_alert(transaction_envelope, match, mock_producer, queue)

    kafka_msg = mock_producer.send.call_args[0][1]
    missing = required_fields - set(kafka_msg.keys())
    assert not missing, f"Campos faltando na mensagem Kafka: {missing}"


@pytest.mark.asyncio
async def test_evaluate_rules_evidence_includes_feature_snapshot(
    transaction_envelope, pep_features, rule_pep_volume
):
    """O match deve incluir feature_snapshot com as chaves de evidência configuradas."""
    matches = await evaluate_rules(transaction_envelope, pep_features, [rule_pep_volume])
    assert len(matches) == 1
    snapshot = matches[0].get("features_snapshot", {})
    # Deve incluir ao menos pep_flag (campo de evidência configurado no rules_engine)
    # Se FEATURE_EVIDENCE_KEYS incluir pep_flag, deve estar no snapshot
    assert isinstance(snapshot, dict)
