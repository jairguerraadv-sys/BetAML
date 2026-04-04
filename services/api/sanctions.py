"""sanctions.py — Verificador de sanções e PEP em memória.

Carrega um ou mais arquivos CSV de listas de sanções/PEP e
expõe uma API síncrona para checagem rápida sem roundtrip ao banco.

Fontes suportadas:
  - Arquivo local (SANCTIONS_CSV_PATH env var, padrão /data/sanctions.csv)
  - O CSV deve ter colunas: name,cpf,type,source,notes  (cabeçalho obrigatório)
    - ``name``: nome completo (será normalizado para comparação)
    - ``cpf``: CPF em qualquer formato; opcional (string vazia = ignorar)
    - ``type``: PEP | SANCTION | TERRORIST_FINANCING | OTHER
    - ``source``: OFAC | UN | MJ_BR | COAF | CUSTOM
    - ``notes``: texto livre

Uso típico:
    # Em lifespan / startup
    checker = get_sanctions_checker()   # carrega CSV uma vez
    checker.reload()                    # força recarga (job periódico)

    # Em request handlers
    result = checker.check(cpf_hmac="abc123...", name="João da Silva")
    if result.matched:
        # tomar ação...
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from auth import compute_cpf_hmac

logger = logging.getLogger(__name__)
UTC = timezone.utc

# ── Configurações via env ─────────────────────────────────────────────────────
_DEFAULT_CSV = "/data/sanctions.csv"


# ── Tipos ─────────────────────────────────────────────────────────────────────

@dataclass
class SanctionEntry:
    name: str
    name_normalized: str
    cpf_hmac: str | None   # HMAC-SHA256 do CPF; None se CPF ausente
    entry_type: str        # PEP / SANCTION / TERRORIST_FINANCING / OTHER
    source: str            # OFAC / UN / MJ_BR / COAF / CUSTOM
    notes: str


@dataclass
class SanctionCheckResult:
    matched: bool
    match_type: str           # "CPF_HMAC" | "NAME_EXACT" | "NAME_PARTIAL" | "NONE"
    matched_entries: list[SanctionEntry] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "match_type": self.match_type,
            "matched_count": len(self.matched_entries),
            "entries": [
                {
                    "name": e.name,
                    "type": e.entry_type,
                    "source": e.source,
                    "notes": e.notes,
                }
                for e in self.matched_entries
            ],
            "checked_at": self.checked_at,
        }


# ── Normalização de nomes ─────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Remove acentos, lowercase, colapsa espaços."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def _cpf_hmac(cpf_plain: str) -> str:
    """Calcula HMAC-SHA256 do CPF usando a mesma derivação do auth."""
    return compute_cpf_hmac(cpf_plain)


# ── SanctionsChecker ──────────────────────────────────────────────────────────

class SanctionsChecker:
    """Verificador em memória de listas de sanções e PEP.

    Thread-safe para leitura (reload() deve ser chamado em contexto single-thread
    ou protegido por lock externo).
    """

    def __init__(self, csv_path: str | None = None) -> None:
        self._csv_path = csv_path or os.getenv("SANCTIONS_CSV_PATH", _DEFAULT_CSV)
        # Índice principal: cpf_hmac → entry
        self._by_cpf: dict[str, SanctionEntry] = {}
        # Índice secundário: normalized_name → [entry, ...]
        self._by_name: dict[str, list[SanctionEntry]] = {}
        self._total = 0
        self._loaded_at: datetime | None = None

    def reload(self) -> int:
        """Carrega (ou recarrega) o CSV. Retorna número de entradas carregadas."""
        path = Path(self._csv_path)
        if not path.exists():
            logger.warning("sanctions_csv_not_found path=%s", path)
            self._loaded_at = datetime.now(UTC)
            return 0

        by_cpf: dict[str, SanctionEntry] = {}
        by_name: dict[str, list[SanctionEntry]] = {}
        count = 0

        try:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    name = (row.get("name") or "").strip()
                    if not name:
                        continue
                    entry = SanctionEntry(
                        name=name,
                        name_normalized=_normalize_name(name),
                        cpf_hmac=_cpf_hmac(row["cpf"]) if (row.get("cpf") or "").strip() else None,
                        entry_type=(row.get("type") or "SANCTION").strip().upper(),
                        source=(row.get("source") or "CUSTOM").strip().upper(),
                        notes=(row.get("notes") or "").strip(),
                    )
                    if entry.cpf_hmac:
                        by_cpf[entry.cpf_hmac] = entry
                    norm = entry.name_normalized
                    by_name.setdefault(norm, []).append(entry)
                    count += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("sanctions_csv_load_error path=%s error=%s", path, exc)
            return 0

        self._by_cpf = by_cpf
        self._by_name = by_name
        self._total = count
        self._loaded_at = datetime.now(UTC)
        logger.info("sanctions_loaded count=%d path=%s", count, path)
        return count

    @property
    def is_loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def total_entries(self) -> int:
        return self._total

    def check(
        self,
        *,
        cpf_hmac: str | None = None,
        name: str | None = None,
        partial_name_threshold: int = 3,
    ) -> SanctionCheckResult:
        """Verifica CPF (por HMAC) e/ou nome na lista de sanções.

        Args:
            cpf_hmac: HMAC-SHA256 do CPF (como calculado por ``compute_cpf_hmac``).
            name: Nome completo do sujeito (será normalizado internamente).
            partial_name_threshold: Mínimo de palavras do nome que precisam
                coincidir para acionar match parcial (0 = desabilitado).

        Returns:
            ``SanctionCheckResult`` com ``matched=True`` se qualquer critério bater.
        """
        matched_entries: list[SanctionEntry] = []
        match_type = "NONE"

        # 1. Verificação por CPF HMAC (O(1), mais confiável)
        if cpf_hmac and cpf_hmac in self._by_cpf:
            entry = self._by_cpf[cpf_hmac]
            matched_entries.append(entry)
            match_type = "CPF_HMAC"

        # 2. Verificação por nome (exato ou parcial)
        if name:
            norm = _normalize_name(name)
            # Exato
            if norm in self._by_name:
                for e in self._by_name[norm]:
                    if e not in matched_entries:
                        matched_entries.append(e)
                if match_type == "NONE":
                    match_type = "NAME_EXACT"
            # Parcial: todas as palavras significativas (>= 3 chars) do sujeito
            # presentes no nome da lista
            elif partial_name_threshold > 0:
                subject_words = {w for w in norm.split() if len(w) >= partial_name_threshold}
                for list_norm, entries in self._by_name.items():
                    list_words = set(list_norm.split())
                    if subject_words and subject_words.issubset(list_words):
                        for e in entries:
                            if e not in matched_entries:
                                matched_entries.append(e)
                        if match_type == "NONE":
                            match_type = "NAME_PARTIAL"

        return SanctionCheckResult(
            matched=bool(matched_entries),
            match_type=match_type,
            matched_entries=matched_entries,
        )


# ── Singleton global ──────────────────────────────────────────────────────────

_checker: SanctionsChecker | None = None


def get_sanctions_checker() -> SanctionsChecker:
    """Retorna (ou inicializa) o checker singleton. Seguro para chamadas repetidas."""
    global _checker
    if _checker is None:
        _checker = SanctionsChecker()
        _checker.reload()
    return _checker


def reload_sanctions() -> int:
    """Força recarga do CSV de sanções (chamado por job periódico do scheduler)."""
    checker = get_sanctions_checker()
    return checker.reload()
