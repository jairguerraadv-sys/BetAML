from __future__ import annotations

from datetime import UTC, datetime


def build_case_reference_number(case_obj: object) -> str:
    raw_id = str(getattr(case_obj, "id", "") or "")
    suffix = (raw_id.replace("-", "")[:8] or "UNKNOWN").upper()
    created_at = getattr(case_obj, "created_at", None)
    if not isinstance(created_at, datetime):
        created_at = datetime.now(UTC)
    return f"CASE-{created_at.strftime('%Y%m%d')}-{suffix}"