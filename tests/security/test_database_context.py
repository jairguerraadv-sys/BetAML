from __future__ import annotations

import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


def test_get_db_sets_current_tenant_and_auth_flow_contexts():
    from database import get_db

    source = inspect.getsource(get_db)

    assert "set_config('app.current_tenant'" in source
    assert "set_config('app.auth_flow'" in source
    assert "current_tenant_id.get()" in source
