import os
import sys
import importlib.util
from types import SimpleNamespace

# Force services/api main.py instead of stream_processor/main.py
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
API_DIR = os.path.join(ROOT, 'services', 'api')
LIBS_DIR = os.path.join(ROOT, 'libs')
for path in (API_DIR, LIBS_DIR):
    while path in sys.path:
        sys.path.remove(path)
sys.path.insert(0, LIBS_DIR)
sys.path.insert(0, API_DIR)

for key in ('models', 'libs.models', 'main', 'database'):
    sys.modules.pop(key, None)

spec = importlib.util.spec_from_file_location(
    'api_main_feature_maintenance',
    os.path.join(API_DIR, 'main.py'),
)
assert spec and spec.loader
api_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_main)


def _row(features: dict):
    return SimpleNamespace(features=features)


def test_feature_null_ratio_counts_null_like_values():
    rows = [
        _row({'deposit_velocity': None}),
        _row({'deposit_velocity': ''}),
        _row({'deposit_velocity': 'null'}),
        _row({'deposit_velocity': 1.2}),
    ]
    ratio = api_main._feature_null_ratio(rows, 'deposit_velocity')
    assert ratio == 0.75


def test_feature_mean_ignores_invalid_and_casts_bool():
    rows = [
        _row({'shared_device_score': True}),
        _row({'shared_device_score': '2.5'}),
        _row({'shared_device_score': 'bad'}),
        _row({'shared_device_score': None}),
    ]
    mean = api_main._feature_mean(rows, 'shared_device_score')
    assert mean == 1.75


def test_feature_mean_returns_none_without_numeric_values():
    rows = [
        _row({'cluster_size': None}),
        _row({'cluster_size': ''}),
        _row({'cluster_size': 'bad'}),
    ]
    assert api_main._feature_mean(rows, 'cluster_size') is None
