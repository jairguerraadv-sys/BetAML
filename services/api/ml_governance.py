"""Compatibility shim for ML governance helpers.

services/ml_trainer inserts services/api into sys.path and imports this module
as `ml_governance`; the implementation lives in libs.ml_governance.
"""
from libs.ml_governance import *  # noqa: F403
