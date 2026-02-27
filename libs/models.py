"""
libs/models.py — Re-export dos modelos ORM (SQLAlchemy).

Este módulo existe para que routes_enterprise.py (e quaisquer outros módulos
fora de services/api/) possam importar modelos sem depender do caminho físico
de services/api/models.py.

Quando executando dentro do container da API:
  - /app          → services/api/
  - /app/libs     → libs/
  - 'from models import ...' resolve para /app/models.py (services/api/models.py)

Quando executando localmente (testes unitários):
  - sys.path inclui o diretório pai para resolver 'models' de services/api/models.py
"""
from __future__ import annotations

import os
import sys

# Garante que services/api esteja no path ao importar fora do contexto Docker
_api_dir = os.path.join(os.path.dirname(__file__), "..", "services", "api")
if _api_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(_api_dir))

from models import (  # noqa: E402, F401
    Alert,
    ApiKey,
    AuditLog,
    Base,
    Bet,
    Case,
    CaseEvent,
    CompoundRule,
    DeviceEvent,
    FeatureSnapshot,
    FinancialTransaction,
    IngestError,
    IngestJob,
    MappingConfig,
    ModelRegistry,
    Notification,
    Player,
    PlayerList,
    PlayerListEntry,
    ReportPackage,
    RuleDefinition,
    RuleExecutionLog,
    RuleMacro,
    ScoringConfig,
    SystemFlag,
    Tenant,
    User,
)

__all__ = [
    "Alert",
    "ApiKey",
    "AuditLog",
    "Base",
    "Bet",
    "Case",
    "CaseEvent",
    "CompoundRule",
    "DeviceEvent",
    "FeatureSnapshot",
    "FinancialTransaction",
    "IngestError",
    "IngestJob",
    "MappingConfig",
    "ModelRegistry",
    "Notification",
    "Player",
    "PlayerList",
    "PlayerListEntry",
    "ReportPackage",
    "RuleDefinition",
    "RuleExecutionLog",
    "RuleMacro",
    "ScoringConfig",
    "SystemFlag",
    "Tenant",
    "User",
]
