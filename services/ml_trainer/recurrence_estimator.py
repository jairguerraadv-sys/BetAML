"""
recurrence_estimator — k-NN para detectar padrões recorrentes e reincidência de players.

COMPLIANCE: COAF Res. 36/2021 Art. 9º — Pessoas já comunicadas anteriormente
que retornam com nova identidade/conta (mulas, evasão de blacklist).

Features de comportamento temporal + device:
- device_fingerprint_hash: hash SHA256 do device_id (8 primeiros chars)
- ip_hash: hash SHA256 do IP (8 primeiros chars)
- hour_of_day_mode: hora do dia mais frequente (0-23)
- day_of_week_mode: dia da semana mais frequente (0-6)
- avg_transaction_amount: ticket médio de transações
- transaction_frequency_per_hour: taxa de transações/hora
- avg_bet_stake: stake médio de apostas
- deposit_to_withdrawal_ratio: razão depósitos/saques

Algoritmo: k-NN (k=5) com métrica euclidiana
Target: Players com status ERASED ou REPORTED (histórico de risco)
Predição: Similaridade > 0.85 → flag potencial recorrência

Output: recurrence_score (0-1) em Player.features
        recurrence_suspect flag em Player.flags
"""
from __future__ import annotations

import hashlib
import io
import pickle
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from minio import Minio
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# 8 features de padrão comportamental + device/IP
RECURRENCE_FEATURES = [
    "device_fingerprint_hash_int",  # hash convertido para int (primeiros 8 chars hex → int)
    "ip_hash_int",
    "hour_of_day_mode",
    "day_of_week_mode",
    "avg_transaction_amount",
    "transaction_frequency_per_hour",
    "avg_bet_stake",
    "deposit_to_withdrawal_ratio",
]


def _hash_to_int(value: str | None) -> int:
    """Converte string para hash SHA256 e retorna primeiros 8 chars como int."""
    if not value:
        return 0
    hash_hex = hashlib.sha256(value.encode()).hexdigest()[:8]
    return int(hash_hex, 16) % (10**9)  # limita a 1 bilhão para evitar overflow


def _extract_recurrence_vector(player) -> list[float] | None:
    """Extrai vetor de features de recorrência do Player."""
    features = player.features or {}

    # Device e IP hashes (convertidos para int)
    device_hash_int = _hash_to_int(features.get("device_fingerprint"))
    ip_hash_int = _hash_to_int(features.get("primary_ip"))

    # Padrões temporais (mode = valor mais frequente)
    hour_mode = features.get("hour_of_day_mode") or 12.0  # default: meio-dia
    day_mode = features.get("day_of_week_mode") or 3.0  # default: quarta-feira

    # Financeiros
    avg_txn = features.get("avg_transaction_amount") or 0.0
    txn_freq = features.get("transaction_frequency_per_hour") or 0.0
    avg_bet = features.get("avg_bet_stake") or 0.0
    dep_wdraw_ratio = features.get("deposit_to_withdrawal_ratio") or 1.0

    # Se todos zeros (player sem atividade), skip
    if all(
        x == 0
        for x in [
            device_hash_int,
            ip_hash_int,
            avg_txn,
            txn_freq,
            avg_bet,
        ]
    ):
        return None

    return [
        float(device_hash_int),
        float(ip_hash_int),
        float(hour_mode),
        float(day_mode),
        float(avg_txn),
        float(txn_freq),
        float(avg_bet),
        float(dep_wdraw_ratio),
    ]


async def train_recurrence_estimator(
    db: AsyncSession,
    minio_client: Minio,
    bucket: str = "betaml-models",
    tenant_id: str | None = None,
) -> dict | None:
    """
    Treina k-NN para detectar padrões recorrentes de players já reportados.

    Args:
        db: AsyncSession do SQLAlchemy
        minio_client: Cliente MinIO
        bucket: Nome do bucket MinIO
        tenant_id: Se None, processa TODOS tenants

    Returns:
        dict com metrics + artifact_uri OU None se insuficientes dados
    """
    from models import Player  # noqa: PLC0415

    version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    logger.info("recurrence_estimator_start", tenant_id=tenant_id or "ALL")

    # ── 1. Busca players "conhecidos" (baseline de risco) ──
    stmt_baseline = select(Player).where(Player.status.in_(["ERASED", "REPORTED", "CLOSED"]))

    if tenant_id:
        from sqlalchemy import UUID  # noqa: PLC0415

        stmt_baseline = stmt_baseline.where(Player.tenant_id == UUID(tenant_id))

    baseline_players = (await db.execute(stmt_baseline)).scalars().all()

    X_baseline: list[list[float]] = []
    baseline_ids: list[str] = []

    for player in baseline_players:
        vec = _extract_recurrence_vector(player)
        if vec is None:
            continue
        X_baseline.append(vec)
        baseline_ids.append(str(player.id))

    baseline_count = len(X_baseline)

    if baseline_count < 5:
        logger.warning(
            "recurrence_estimator_skipped_insufficient_baseline",
            baseline_count=baseline_count,
            minimum=5,
        )
        return None

    # ── 2. Busca players ativos (para scoring) ──
    stmt_active = select(Player).where(Player.status.in_(["ACTIVE", "PEP", "HIGH_RISK"]))

    if tenant_id:
        from sqlalchemy import UUID  # noqa: PLC0415

        stmt_active = stmt_active.where(Player.tenant_id == UUID(tenant_id))

    active_players = (await db.execute(stmt_active)).scalars().all()

    X_active: list[list[float]] = []
    active_ids: list[str] = []

    for player in active_players:
        vec = _extract_recurrence_vector(player)
        if vec is None:
            continue
        X_active.append(vec)
        active_ids.append(str(player.id))

    active_count = len(X_active)

    logger.info(
        "recurrence_estimator_samples",
        baseline=baseline_count,
        active=active_count,
    )

    # ── 3. Treina k-NN com baseline (players conhecidos de risco) ──
    X_baseline_arr = np.array(X_baseline, dtype=float)
    scaler = StandardScaler()
    X_baseline_scaled = scaler.fit_transform(X_baseline_arr)

    k = min(5, baseline_count)  # k=5 ou menos se baseline pequeno
    knn = NearestNeighbors(n_neighbors=k, metric="euclidean", n_jobs=-1)
    knn.fit(X_baseline_scaled)

    logger.info("recurrence_estimator_knn_fitted", k=k, baseline_samples=baseline_count)

    # ── 4. Score active players (similaridade vs baseline de risco) ──
    scores = []
    if active_count > 0:
        X_active_arr = np.array(X_active, dtype=float)
        X_active_scaled = scaler.transform(X_active_arr)

        distances, indices = knn.kneighbors(X_active_scaled)

        # Score: média inversa das distâncias aos k vizinhos (normalizado 0-1)
        # Distância pequena → similaridade alta → score alto
        for dist_row in distances:
            avg_dist = float(np.mean(dist_row))
            # Normaliza: dist=0 → score=1; dist=grande → score→0
            # Usando sigmoid-like: score = 1 / (1 + avg_dist)
            score = 1.0 / (1.0 + avg_dist)
            scores.append(score)

        # ── 5. Atualiza DB com recurrence_score ──
        suspicious_count = 0
        for idx, player_id in enumerate(active_ids):
            rec_score = scores[idx]

            # Flag players com score > 0.85 (similaridade muito alta)
            is_suspect = rec_score > 0.85

            from sqlalchemy import UUID  # noqa: PLC0415

            update_values: dict = {}
            if hasattr(Player, "features"):
                update_values["features"] = Player.features.op("||")(
                    {
                        "recurrence_score": float(rec_score),
                        "recurrence_suspect": is_suspect,
                    }
                )
            if update_values:
                stmt_upd = (
                    update(Player)
                    .where(Player.id == UUID(player_id))
                    .values(**update_values)
                )
                await db.execute(stmt_upd)

            if is_suspect:
                suspicious_count += 1

        await db.commit()

        logger.info(
            "recurrence_estimator_scoring_complete",
            active_players_scored=active_count,
            suspicious_count=suspicious_count,
            threshold=0.85,
        )
    else:
        logger.info("recurrence_estimator_no_active_players_to_score")

    # ── 6. Persistência MinIO ──
    model_data = {
        "scaler": scaler,
        "knn": knn,
        "feature_columns": RECURRENCE_FEATURES,
        "k": k,
        "baseline_ids": baseline_ids,  # para auditoria
    }

    model_filename = f"recurrence_estimator_v{version}.pkl"
    model_bytes = pickle.dumps(model_data)

    minio_client.put_object(
        bucket,
        model_filename,
        io.BytesIO(model_bytes),
        len(model_bytes),
        content_type="application/octet-stream",
    )

    artifact_uri = f"s3://{bucket}/{model_filename}"
    logger.info("recurrence_estimator_saved", artifact_uri=artifact_uri)

    return {
        "model_name": "recurrence_estimator",
        "model_type": "recurrence_detection",
        "algorithm": "k-NN",
        "model_version": version,
        "artifact_uri": artifact_uri,
        "training_rows": baseline_count,
        "metrics": {
            "baseline_samples": baseline_count,
            "active_players_scored": active_count,
            "suspicious_count": suspicious_count if active_count > 0 else 0,
            "threshold": 0.85,
            "k": k,
        },
        "feature_columns": RECURRENCE_FEATURES,
        "training_metadata": {
            "k": k,
            "metric": "euclidean",
            "tenant_id": tenant_id or "ALL",
            "training_window_days": 365,  # baseline histórico (1 ano)
        },
    }
