from __future__ import annotations

import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


def test_mapping_router_keeps_versioned_current_flag_protocol():
    import routers.mappings as mappings

    source = inspect.getsource(mappings)

    assert "version_number" in source
    assert "is_current" in source
    assert "ROLLBACK_MAPPING" in source
    assert "UPDATE_MAPPING" in source


def test_ingest_resolves_current_mapping_or_explicit_mapping_id():
    import routers.ingest as ingest

    source = inspect.getsource(ingest._resolve_effective_mapping_config)

    assert "mapping_config_id" in source
    assert "MappingConfig.is_current.is_(True)" in source
    assert "MappingConfig.active.is_(True)" in source
