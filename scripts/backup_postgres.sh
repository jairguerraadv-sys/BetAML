#!/usr/bin/env bash
# BetAML — Automated PostgreSQL backup
# Runs every 6h via docker-compose betaml-backup service.
# Uploads compressed dump to MinIO/S3, keeps 30-day retention.
set -euo pipefail

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
BACKUP_NAME="postgres_${TIMESTAMP}.sql.gz"
CHECKSUM_NAME="${BACKUP_NAME}.sha256"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

# ── Connection params (injected by docker-compose) ───────────────────────────
PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-betaml}"
PGPASSWORD="${PGPASSWORD:?PGPASSWORD must be set}"
PGDATABASE="${PGDATABASE:-betaml_dev}"
export PGPASSWORD

# ── Storage (MinIO via mc or AWS S3 via awscli) ──────────────────────────────
MINIO_ALIAS="${MINIO_ALIAS:-betaml-minio}"
BUCKET="${BACKUP_BUCKET:-betaml-backups}"
PREFIX="${BACKUP_PREFIX:-postgres}"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backup_start db=${PGDATABASE} file=${BACKUP_NAME}"

# ── Dump ─────────────────────────────────────────────────────────────────────
pg_dump \
  -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE" \
  --no-password \
  --format=plain \
  --no-owner \
  --no-privileges \
  | gzip -9 > "${TMP_DIR}/${BACKUP_NAME}"

# ── Checksum ─────────────────────────────────────────────────────────────────
sha256sum "${TMP_DIR}/${BACKUP_NAME}" | awk '{print $1}' > "${TMP_DIR}/${CHECKSUM_NAME}"
CHECKSUM=$(cat "${TMP_DIR}/${CHECKSUM_NAME}")

# ── Upload ───────────────────────────────────────────────────────────────────
DEST="${BUCKET}/${PREFIX}/${BACKUP_NAME}"

if command -v mc &>/dev/null; then
  mc cp "${TMP_DIR}/${BACKUP_NAME}"     "${MINIO_ALIAS}/${DEST}"
  mc cp "${TMP_DIR}/${CHECKSUM_NAME}"   "${MINIO_ALIAS}/${BUCKET}/${PREFIX}/${CHECKSUM_NAME}"
elif command -v aws &>/dev/null; then
  aws s3 cp "${TMP_DIR}/${BACKUP_NAME}"   "s3://${DEST}"   --no-progress
  aws s3 cp "${TMP_DIR}/${CHECKSUM_NAME}" "s3://${BUCKET}/${PREFIX}/${CHECKSUM_NAME}" --no-progress
else
  echo "[WARN] No upload tool (mc/aws) found. Backup written to ${TMP_DIR}/${BACKUP_NAME}" >&2
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backup_completed file=${BACKUP_NAME} sha256=${CHECKSUM}"

# ── Retention pruning (files older than RETENTION_DAYS) ──────────────────────
CUTOFF=$(date -u -d "${RETENTION_DAYS} days ago" +"%Y%m%dT%H%M%SZ" 2>/dev/null \
         || date -u -v-"${RETENTION_DAYS}"d +"%Y%m%dT%H%M%SZ" 2>/dev/null || true)

if [[ -n "${CUTOFF:-}" ]]; then
  if command -v mc &>/dev/null; then
    mc ls "${MINIO_ALIAS}/${BUCKET}/${PREFIX}/" 2>/dev/null \
      | awk '{print $NF}' \
      | grep -E '^postgres_[0-9T]+Z\.sql\.gz$' \
      | while read -r f; do
          FILE_TS="${f#postgres_}"; FILE_TS="${FILE_TS%.sql.gz}"
          [[ "$FILE_TS" < "$CUTOFF" ]] && mc rm "${MINIO_ALIAS}/${BUCKET}/${PREFIX}/${f}" 2>/dev/null && \
            echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backup_pruned file=${f}"
        done
  fi
fi
