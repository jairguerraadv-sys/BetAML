"""MinIO / S3 Lakehouse client using boto3 + pyarrow (Parquet)."""

from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime
from typing import Any, Optional

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# S3 key templates
_BRONZE_KEY = "{tenant_id}/bronze/{entity_type}/{event_date}/{source_system}/{filename}"
_SILVER_KEY = "{tenant_id}/silver/{entity_type}/{event_date}/{filename}"
_DEFAULT_FILENAME = "data.parquet"


class LakehouseClient:
    """Read / write data to a MinIO-compatible S3 Lakehouse in Parquet format.

    Parameters
    ----------
    bucket:
        Target S3 bucket name.
    endpoint_url:
        Override the S3 endpoint (required for MinIO; omit for real AWS S3).
    aws_access_key_id / aws_secret_access_key:
        Credentials.  Defaults to the standard boto3 credential chain.
    region_name:
        AWS region (default ``us-east-1``).
    client:
        Inject a pre-built :class:`boto3.client` for testing.
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: str = "us-east-1",
        client: Optional[Any] = None,
    ) -> None:
        self._bucket = bucket
        if client is not None:
            self._s3 = client
        else:
            session = boto3.session.Session()
            self._s3 = session.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=region_name,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_date(event_date: Any) -> str:
        if isinstance(event_date, (datetime, date)):
            return event_date.strftime("%Y-%m-%d")
        return str(event_date)

    @staticmethod
    def _records_to_parquet(records: list[dict[str, Any]]) -> bytes:
        """Serialise a list of dicts to in-memory Parquet bytes."""
        # Coerce values that pyarrow cannot infer (e.g. Decimal, UUID)
        safe_records = [
            {k: (str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v) for k, v in r.items()}
            for r in records
        ]
        table = pa.Table.from_pylist(safe_records)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return buf.getvalue()

    @staticmethod
    def _parquet_to_records(data: bytes) -> list[dict[str, Any]]:
        """Deserialise Parquet bytes to a list of dicts."""
        buf = io.BytesIO(data)
        table = pq.read_table(buf)
        return table.to_pylist()

    def _put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)
        logger.debug("Wrote %d bytes to s3://%s/%s", len(data), self._bucket, key)

    def _get(self, key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_bronze(
        self,
        tenant_id: str,
        entity_type: str,
        event_date: Any,
        source_system: str,
        records: list[dict[str, Any]],
        filename: str = _DEFAULT_FILENAME,
    ) -> str:
        """Write *records* to the Bronze layer and return the S3 key."""
        key = _BRONZE_KEY.format(
            tenant_id=tenant_id,
            entity_type=entity_type.lower(),
            event_date=self._normalise_date(event_date),
            source_system=source_system,
            filename=filename,
        )
        self._put(key, self._records_to_parquet(records))
        return key

    def write_silver(
        self,
        tenant_id: str,
        entity_type: str,
        event_date: Any,
        records: list[dict[str, Any]],
        filename: str = _DEFAULT_FILENAME,
    ) -> str:
        """Write *records* to the Silver layer and return the S3 key."""
        key = _SILVER_KEY.format(
            tenant_id=tenant_id,
            entity_type=entity_type.lower(),
            event_date=self._normalise_date(event_date),
            filename=filename,
        )
        self._put(key, self._records_to_parquet(records))
        return key

    def read_silver(
        self,
        tenant_id: str,
        entity_type: str,
        event_date: Any,
        filename: str = _DEFAULT_FILENAME,
    ) -> list[dict[str, Any]]:
        """Read and return records from the Silver layer.

        Returns an empty list if the object does not exist.
        """
        key = _SILVER_KEY.format(
            tenant_id=tenant_id,
            entity_type=entity_type.lower(),
            event_date=self._normalise_date(event_date),
            filename=filename,
        )
        try:
            data = self._get(key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return []
            raise
        return self._parquet_to_records(data)

    def list_bronze_partitions(
        self,
        tenant_id: str,
        entity_type: str,
        event_date: Any,
        source_system: Optional[str] = None,
    ) -> list[str]:
        """List all Bronze object keys for the given partition."""
        prefix = f"{tenant_id}/bronze/{entity_type.lower()}/{self._normalise_date(event_date)}/"
        if source_system:
            prefix += f"{source_system}/"
        paginator = self._s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys
