"""
network_clustering — Detecção de clusters suspeitos usando DBSCAN em grafo de players.

COMPLIANCE: COAF Res. 36/2021 Art. 6º — Utilização de múltiplas contas/identidades
para operações coordenadas (mulas, redes de fraude, layering).

Features de rede:
- shared_device_score: quantos players compartilham dispositivos
- shared_instrument_score: quantos players compartilham conta bancária
- cluster_size: tamanho do cluster detectado (players interconectados)

Algoritmo: DBSCAN (Density-Based Spatial Clustering)
- eps=0.3 (raio máximo de vizinhança)
- min_samples=3 (mínimo de 3 players para formar cluster core)

Output: cluster_id (MD5 hash) persistido em Player.cluster_id
        cluster_size armazenado em Player.cluster_size

Uso: Job semanal que recalcula clusters e atualiza DB
"""
from __future__ import annotations

import hashlib
import io
import pickle
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from minio import Minio
from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# 3 features de rede (todas normalizadas 0-1 pelo stream processor)
NETWORK_FEATURES = [
    "shared_device_score",
    "shared_instrument_score",
    "cluster_size",  # tamanho atual (recursivo: será atualizado)
]


def _extract_network_vector(player) -> list[float] | None:
    """Extrai vetor de features de rede do Player."""
    # Features devem estar em player.features JSONB (atualizadas pelo stream processor)
    features = player.features or {}

    shared_device = features.get("shared_device_score") or 0.0
    shared_instrument = features.get("shared_instrument_score") or 0.0
    current_cluster_size = features.get("cluster_size") or 1.0

    # Se não há compartilhamento (scores = 0), player está isolado
    if shared_device == 0 and shared_instrument == 0:
        return None

    return [float(shared_device), float(shared_instrument), float(current_cluster_size)]


async def train_network_clustering(
    db: AsyncSession,
    minio_client: Minio,
    bucket: str = "betaml-models",
    tenant_id: str | None = None,
) -> dict | None:
    """
    Executa DBSCAN clustering para detectar redes suspeitas.

    Args:
        db: AsyncSession do SQLAlchemy
        minio_client: Cliente MinIO
        bucket: Nome do bucket MinIO
        tenant_id: Se None, processa TODOS tenants (multi-tenant clustering)

    Returns:
        dict com metrics + artifact_uri OU None se insuficientes dados
    """
    from models import Player  # noqa: PLC0415

    version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    logger.info("network_clustering_start", tenant_id=tenant_id or "ALL")

    # Busca players ativos com features de rede (últimos 90 dias de atividade)
    cutoff = datetime.now(UTC) - timedelta(days=90)
    stmt = select(Player).where(Player.status.in_(["ACTIVE", "PEP", "HIGH_RISK"]))

    if tenant_id:
        from sqlalchemy import UUID  # noqa: PLC0415

        stmt = stmt.where(Player.tenant_id == UUID(tenant_id))

    players = (await db.execute(stmt)).scalars().all()

    # Extrai vetores de features
    X: list[list[float]] = []
    player_ids: list[str] = []

    for player in players:
        vec = _extract_network_vector(player)
        if vec is None:
            continue

        X.append(vec)
        player_ids.append(str(player.id))

    sample_count = len(X)

    if sample_count < 10:
        logger.warning(
            "network_clustering_skipped_insufficient_samples",
            samples=sample_count,
            minimum=10,
        )
        return None

    logger.info("network_clustering_samples", total=sample_count)

    # ── Normalização (StandardScaler para DBSCAN) ──
    X_arr = np.array(X, dtype=float)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_arr)

    # ── Clustering DBSCAN ──
    clustering = DBSCAN(eps=0.3, min_samples=3, n_jobs=-1)
    labels = clustering.fit_predict(X_scaled)

    # ── Análise de clusters ──
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = list(labels).count(-1)

    cluster_sizes = defaultdict(int)
    for label in labels:
        if label != -1:
            cluster_sizes[label] += 1

    # Identifica clusters suspeitos (tamanho >= 5)
    suspicious_clusters = {k: v for k, v in cluster_sizes.items() if v >= 5}

    logger.info(
        "network_clustering_results",
        n_clusters=n_clusters,
        n_noise=n_noise,
        suspicious_clusters=len(suspicious_clusters),
        largest_cluster=max(cluster_sizes.values()) if cluster_sizes else 0,
    )

    # ── Atualização do DB: cluster_id e cluster_size para cada player ──
    cluster_updates = []
    for idx, player_id in enumerate(player_ids):
        cluster_label = int(labels[idx])

        if cluster_label == -1:
            # Noise: player isolado
            cluster_id = None
            cluster_size = 1
        else:
            # Hash determinístico: cluster_{label}_{tenant_id}
            cluster_id_str = f"cluster_{cluster_label}_{tenant_id or 'global'}"
            cluster_id = hashlib.md5(cluster_id_str.encode()).hexdigest()[:16]
            cluster_size = cluster_sizes[cluster_label]

        cluster_updates.append(
            {
                "player_id": player_id,
                "cluster_id": cluster_id,
                "cluster_size": cluster_size,
            }
        )

    # Batch update em Players
    for upd in cluster_updates:
        from sqlalchemy import UUID  # noqa: PLC0415

        stmt_upd = (
            update(Player)
            .where(Player.id == UUID(upd["player_id"]))
            .values(
                cluster_id=upd["cluster_id"],
                # Atualiza features JSONB
                features=Player.features.op("||")(
                    {
                        "cluster_id": upd["cluster_id"],
                        "cluster_size": upd["cluster_size"],
                    }
                ),
            )
        )
        await db.execute(stmt_upd)

    await db.commit()

    logger.info("network_clustering_db_updated", players_updated=len(cluster_updates))

    # ── Persistência do scaler + modelo DBSCAN (para scoring futuro) ──
    model_data = {
        "scaler": scaler,
        "dbscan": clustering,
        "feature_columns": NETWORK_FEATURES,
        "eps": 0.3,
        "min_samples": 3,
    }

    model_filename = f"network_clustering_v{version}.pkl"
    model_bytes = pickle.dumps(model_data)

    minio_client.put_object(
        bucket,
        model_filename,
        io.BytesIO(model_bytes),
        len(model_bytes),
        content_type="application/octet-stream",
    )

    artifact_uri = f"s3://{bucket}/{model_filename}"
    logger.info("network_clustering_saved", artifact_uri=artifact_uri)

    return {
        "model_name": "network_clustering",
        "model_type": "network_detection",
        "algorithm": "DBSCAN",
        "model_version": version,
        "artifact_uri": artifact_uri,
        "training_rows": sample_count,
        "metrics": {
            "n_clusters": n_clusters,
            "n_noise": n_noise,
            "suspicious_clusters_count": len(suspicious_clusters),
            "largest_cluster_size": max(cluster_sizes.values()) if cluster_sizes else 0,
            "silhouette_score": 0.0,  # TODO: compute if needed
        },
        "feature_columns": NETWORK_FEATURES,
        "training_metadata": {
            "eps": 0.3,
            "min_samples": 3,
            "tenant_id": tenant_id or "ALL",
            "training_window_days": 90,
            "suspicious_clusters": dict(suspicious_clusters),
        },
    }
