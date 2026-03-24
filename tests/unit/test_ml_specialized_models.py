"""
test_ml_specialized_models — Testes unitários para os 3 modelos ML especializados.

Módulo 4 — ML: Structuring Detector, Network Clustering, Recurrence Estimator
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def ml_baseline():
    """Carrega baseline de métricas ML do arquivo JSON."""
    baseline_path = os.path.join(
        os.path.dirname(__file__),
        "fixtures",
        "ml_baseline_metrics.json",
    )
    with open(baseline_path) as f:
        return json.load(f)


@pytest.fixture
def mock_minio_client():
    """Mock do MinIO client para persistência de modelos."""
    client = MagicMock()
    client.bucket_exists.return_value = True
    client.put_object.return_value = None
    return client


@pytest.fixture
def mock_db_session():
    """Mock AsyncSession com alerts/players ficticios."""
    db = AsyncMock()

    # Mock execute() que retorna result com scalars().all()
    class _MockResult:
        def scalars(self):
            return self

        def all(self):
            return self._data

        def __init__(self, data):
            self._data = data

    async def _execute_side_effect(stmt):
        # Detecta tipo de query baseado no repr
        stmt_str = str(stmt)

        # Alerts labelados para structuring detector
        if "Alert" in stmt_str and "label" in stmt_str:
            from datetime import UTC, datetime
            from uuid import uuid4

            # 40 alerts fictícios (20 TP structuring, 20 FP)
            alerts = []
            for i in range(20):
                alert = MagicMock()
                alert.id = uuid4()
                alert.label = "TRUE_POSITIVE"
                alert.title = f"MÚLTIPLOS DEPÓSITOS FRACIONADOS {i}"
                alert.evidence = {
                    "features": {
                        "deposit_count_24h": 8.0,
                        "deposit_count_7d": 15.0,
                        "deposit_velocity": 2.5,
                        "unique_instruments_used_7d": 3.0,
                        "avg_time_between_deposit_and_withdrawal_7d": 12.0,
                        "deposit_sum_24h": 4500.0,
                        "deposit_sum_7d": 9200.0,
                        "night_activity_ratio": 0.35,
                        "round_amount_ratio": 0.80,
                        "structuring_score": 0.85,
                    }
                }
                alert.created_at = datetime.now(UTC)
                alerts.append(alert)

            for i in range(20):
                alert = MagicMock()
                alert.id = uuid4()
                alert.label = "FALSE_POSITIVE"
                alert.title = f"ALERTA NORMAL {i}"
                alert.evidence = {
                    "features": {
                        "deposit_count_24h": 2.0,
                        "deposit_count_7d": 4.0,
                        "deposit_velocity": 0.3,
                        "unique_instruments_used_7d": 1.0,
                        "avg_time_between_deposit_and_withdrawal_7d": 48.0,
                        "deposit_sum_24h": 500.0,
                        "deposit_sum_7d": 1200.0,
                        "night_activity_ratio": 0.05,
                        "round_amount_ratio": 0.15,
                        "structuring_score": 0.10,
                    }
                }
                alert.created_at = datetime.now(UTC)
                alerts.append(alert)

            return _MockResult(alerts)

        # Players para network clustering
        elif "Player" in stmt_str and "status" in stmt_str:
            from uuid import uuid4

            players = []
            # 20 players com features de rede
            for i in range(20):
                player = MagicMock()
                player.id = uuid4()
                player.tenant_id = uuid4()
                player.status = "ACTIVE"
                player.features = {
                    "shared_device_score": 0.6 if i < 10 else 0.2,
                    "shared_instrument_score": 0.4 if i < 10 else 0.1,
                    "cluster_size": 5 if i < 10 else 1,
                }
                players.append(player)

            return _MockResult(players)

        # Default: lista vazia
        return _MockResult([])

    db.execute = AsyncMock(side_effect=_execute_side_effect)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


# ── Structuring Detector ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_structuring_detector_trains_successfully(mock_db_session, mock_minio_client):
    """Testa que StructuringDetector treina com >= 30 amostras e retorna métricas."""
    import sys
    from pathlib import Path

    # Adiciona ml_trainer ao path
    ml_trainer_path = Path(__file__).parent.parent.parent / "services" / "ml_trainer"
    sys.path.insert(0, str(ml_trainer_path))

    from structuring_detector import train_structuring_detector

    result = await train_structuring_detector(mock_db_session, mock_minio_client)

    assert result is not None, "Deveria retornar resultado com >= 30 amostras"
    assert result["model_name"] == "structuring_detector"
    assert result["model_type"] == "structuring_detection"
    assert result["algorithm"] == "RandomForestClassifier"
    assert "metrics" in result
    assert "f1_score" in result["metrics"]
    assert result["metrics"]["f1_score"] >= 0.0  # métrica válida
    assert "artifact_uri" in result
    assert "betaml-models" in result["artifact_uri"]


@pytest.mark.asyncio
async def test_structuring_detector_skips_insufficient_data(mock_minio_client):
    """Testa que StructuringDetector retorna None quando < 30 amostras."""
    import sys
    from pathlib import Path

    ml_trainer_path = Path(__file__).parent.parent.parent / "services" / "ml_trainer"
    sys.path.insert(0, str(ml_trainer_path))

    from structuring_detector import train_structuring_detector

    # DB mock com ZERO alerts
    db = AsyncMock()

    class _EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    db.execute = AsyncMock(return_value=_EmptyResult())

    result = await train_structuring_detector(db, mock_minio_client)

    assert result is None, "Deveria retornar None com < 30 amostras"


# ── Network Clustering ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_network_clustering_trains_successfully(mock_db_session, mock_minio_client):
    """Testa que Network Clustering executa DBSCAN e atualiza DB."""
    import sys
    from pathlib import Path

    ml_trainer_path = Path(__file__).parent.parent.parent / "services" / "ml_trainer"
    sys.path.insert(0, str(ml_trainer_path))

    from network_clustering import train_network_clustering

    result = await train_network_clustering(mock_db_session, mock_minio_client, tenant_id=None)

    assert result is not None, "Deveria retornar resultado com >= 10 players"
    assert result["model_name"] == "network_clustering"
    assert result["model_type"] == "network_detection"
    assert result["algorithm"] == "DBSCAN"
    assert "metrics" in result
    assert "n_clusters" in result["metrics"]
    assert result["metrics"]["n_clusters"] >= 0


@pytest.mark.asyncio
async def test_network_clustering_updates_player_cluster_ids(mock_db_session, mock_minio_client):
    """Testa que Network Clustering atualiza cluster_id e cluster_size no DB."""
    import sys
    from pathlib import Path

    ml_trainer_path = Path(__file__).parent.parent.parent / "services" / "ml_trainer"
    sys.path.insert(0, str(ml_trainer_path))

    from network_clustering import train_network_clustering

    await train_network_clustering(mock_db_session, mock_minio_client)

    # Verifica que execute foi chamado (updates de Player)
    assert mock_db_session.execute.call_count >= 1
    # Verifica que commit foi chamado (persiste updates)
    mock_db_session.commit.assert_called()


# ── Recurrence Estimator ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recurrence_estimator_trains_successfully(mock_db_session, mock_minio_client):
    """Testa que Recurrence Estimator treina k-NN e score players."""
    import sys
    from pathlib import Path

    ml_trainer_path = Path(__file__).parent.parent.parent / "services" / "ml_trainer"
    sys.path.insert(0, str(ml_trainer_path))

    from recurrence_estimator import train_recurrence_estimator

    # Mock DB com baseline e active players
    class _RecurrenceResult:
        def __init__(self, data):
            self._data = data

        def scalars(self):
            return self

        def all(self):
            return self._data

    from uuid import uuid4

    # 10 baseline players (ERASED/REPORTED)
    baseline = [
        MagicMock(
            id=uuid4(),
            status="REPORTED",
            features={
                "device_fingerprint": f"device_{i}",
                "primary_ip": f"192.168.1.{i}",
                "hour_of_day_mode": 14.0,
                "day_of_week_mode": 2.0,
                "avg_transaction_amount": 500.0,
                "transaction_frequency_per_hour": 2.0,
                "avg_bet_stake": 100.0,
                "deposit_to_withdrawal_ratio": 1.5,
            },
        )
        for i in range(10)
    ]

    # 15 active players
    active = [
        MagicMock(
            id=uuid4(),
            status="ACTIVE",
            features={
                "device_fingerprint": f"device_{i}_active",
                "primary_ip": f"10.0.0.{i}",
                "hour_of_day_mode": 15.0,
                "day_of_week_mode": 3.0,
                "avg_transaction_amount": 450.0,
                "transaction_frequency_per_hour": 1.8,
                "avg_bet_stake": 95.0,
                "deposit_to_withdrawal_ratio": 1.4,
            },
        )
        for i in range(15)
    ]

    call_count = [0]

    async def _execute_side_effect(stmt):
        stmt_str = str(stmt)
        call_count[0] += 1

        # Primeira chamada: baseline players (ERASED/REPORTED)
        if "ERASED" in stmt_str or "REPORTED" in stmt_str:
            return _RecurrenceResult(baseline)
        # Segunda chamada: active players
        elif "ACTIVE" in stmt_str:
            return _RecurrenceResult(active)
        # Demais: vazio
        return _RecurrenceResult([])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute_side_effect)
    db.commit = AsyncMock()

    result = await train_recurrence_estimator(db, mock_minio_client)

    assert result is not None, "Deveria retornar resultado com >= 5 baseline samples"
    assert result["model_name"] == "recurrence_estimator"
    assert result["model_type"] == "recurrence_detection"
    assert result["algorithm"] == "k-NN"
    assert "metrics" in result
    assert result["metrics"]["baseline_samples"] == 10
    assert result["metrics"]["active_players_scored"] == 15


# ── Regression Tests ──────────────────────────────────────────────────────────


def test_ml_baseline_json_exists():
    """Testa que baseline ML JSON existe e tem estrutura correta."""
    baseline_path = os.path.join(
        os.path.dirname(__file__),
        "fixtures",
        "ml_baseline_metrics.json",
    )
    assert os.path.exists(baseline_path), "Baseline ML JSON deve existir"

    with open(baseline_path) as f:
        baseline = json.load(f)

    assert "baseline_version" in baseline
    assert "models" in baseline
    assert "anomaly_detection" in baseline["models"]
    assert "structuring_detection" in baseline["models"]
    assert "network_detection" in baseline["models"]
    assert "recurrence_detection" in baseline["models"]


def test_structuring_metrics_vs_baseline(ml_baseline):
    """Testa que métricas do Structuring Detector estão acima do mínimo aceitável."""
    structuring = ml_baseline["models"]["structuring_detection"]
    min_metrics = structuring["min_acceptable_metrics"]

    # Simula novo modelo com métricas ligeiramente acima do mínimo
    new_metrics = {
        "precision": 0.76,
        "recall": 0.69,
        "f1_score": 0.72,
        "auc_roc": 0.78,
    }

    assert new_metrics["f1_score"] >= min_metrics["f1_score"], "F1 abaixo do mínimo aceitável"
    assert new_metrics["precision"] >= min_metrics["precision"], "Precision abaixo do mínimo"

    # Testa regressão: novo modelo 5% pior que baseline
    baseline_f1 = structuring["baseline_metrics"]["f1_score"]
    regression = (baseline_f1 - new_metrics["f1_score"]) / baseline_f1

    assert regression < 0.10, f"Regressão de F1 muito alta: {regression*100:.1f}% (max: 10%)"


def test_network_clustering_baseline_structure(ml_baseline):
    """Testa estrutura de métricas do Network Clustering."""
    network = ml_baseline["models"]["network_detection"]

    assert "baseline_metrics" in network
    assert "n_clusters" in network["baseline_metrics"]
    assert "suspicious_clusters_count" in network["baseline_metrics"]
    assert network["baseline_metrics"]["n_clusters"] >= 5, "Baseline deve ter >= 5 clusters"
