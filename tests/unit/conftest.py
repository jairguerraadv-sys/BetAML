"""
conftest.py específico de tests/unit/

Garante que services/api esteja ANTES de libs no sys.path,
evitando conflito de nomes entre services/api/models.py e libs/models.py.
"""
import sys
import os
import pytest

_root          = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_services_api  = os.path.join(_root, "services", "api")
_libs          = os.path.join(_root, "libs")


def _fix_path() -> None:
    """
    Garante services/api na frente de libs no sys.path e limpa
    qualquer módulo 'models' importado com o mapeamento errado.
    """
    for p in (_services_api, _libs):
        while p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, _libs)
    sys.path.insert(0, _services_api)
    # Remove qualquer cache de "models" que possa apontar para libs/models.py
    for key in ("models", "libs.models"):
        sys.modules.pop(key, None)


# Executa imediatamente ao importar o conftest (cobertura de coleta)
_fix_path()


@pytest.fixture(autouse=True, scope="session")
def _ensure_api_path_first():
    """
    Fixture autouse de sessão: re-aplica a correção de path antes dos testes
    rodarem (após a fase de coleta, que pode ter poluído sys.modules).
    """
    _fix_path()
    yield
