"""Lakehouse (MinIO/S3) writer for the stream_processor service."""

from __future__ import annotations

import logging
from typing import Any

from clients.s3_client import LakehouseClient

logger = logging.getLogger(__name__)


class LakehouseWriter:
    """Thin wrapper around :class:`clients.s3_client.LakehouseClient`.

    All write methods log and swallow errors so a single failed S3 put does
    not crash the consumer loop.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        self._client = LakehouseClient(
            bucket=bucket,
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def write_bronze(
        self,
        tenant_id: str,
        entity_type: str,
        event_date: Any,
        source_system: str,
        records: list[dict[str, Any]],
    ) -> str:
        """Write *records* to the Bronze layer.

        Returns the S3 key on success, or an empty string on error.
        """
        try:
            key = self._client.write_bronze(
                tenant_id=tenant_id,
                entity_type=entity_type,
                event_date=event_date,
                source_system=source_system,
                records=records,
            )
            logger.debug("Bronze written: %s", key)
            return key
        except Exception as exc:
            logger.error(
                "Failed to write bronze for %s/%s/%s: %s",
                tenant_id, entity_type, event_date, exc,
            )
            return ""

    def write_silver(
        self,
        tenant_id: str,
        entity_type: str,
        event_date: Any,
        records: list[dict[str, Any]],
    ) -> str:
        """Write *records* to the Silver layer.

        Returns the S3 key on success, or an empty string on error.
        """
        try:
            key = self._client.write_silver(
                tenant_id=tenant_id,
                entity_type=entity_type,
                event_date=event_date,
                records=records,
            )
            logger.debug("Silver written: %s", key)
            return key
        except Exception as exc:
            logger.error(
                "Failed to write silver for %s/%s/%s: %s",
                tenant_id, entity_type, event_date, exc,
            )
            return ""
