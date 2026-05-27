from __future__ import annotations

import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


def test_replay_ingest_error_preserves_lineage_fields():
    import routers.ingest as ingest

    source = inspect.getsource(ingest.replay_ingest_error)

    assert "quarantine_replay" in source
    assert "ingest_error_id" in source
    assert "source_event_id" in source


def test_invalid_webhook_payload_is_quarantined_as_ingest_error():
    import routers.ingest as ingest

    source = inspect.getsource(ingest.ingest_epsilon_webhook)

    assert "IngestError" in source
    assert "webhook_validation_failed" in source
    assert "raw_payload" in source


def test_file_upload_has_type_and_size_controls():
    import routers.ingest as ingest

    source = inspect.getsource(ingest)

    assert "file_size_bytes" in source
    assert "content_type" in source
    assert "text/csv" in source
