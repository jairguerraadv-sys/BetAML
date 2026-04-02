#!/usr/bin/env python3
"""
scripts/backfill_cpf_hmac.py — Backfill da coluna cpf_hmac na tabela players.

Computa HMAC-SHA256 determinístico do CPF para todos os players sem cpf_hmac.
Permite lookup indexado O(1) em lugar de scan O(n) com decriptografia.

Uso:
    python scripts/backfill_cpf_hmac.py
    python scripts/backfill_cpf_hmac.py --batch-size=1000 --dry-run

Pré-requisitos:
    - migration_v21.sql deve ter sido aplicada (coluna cpf_hmac existe)
    - DATABASE_URL e PII_ENCRYPTION_KEY devem estar configurados

Referências:
    - LGPD Art. 46 (proteção por design)
    - Portaria SPA/MF 1.143/2024
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys

import psycopg2


def _compute_cpf_hmac(cpf_digits: str, hmac_key: bytes) -> str:
    return hmac.new(hmac_key, cpf_digits.encode("utf-8"), hashlib.sha256).hexdigest()


def _decrypt_cpf(ciphertext: bytes, fernet) -> str | None:
    try:
        return fernet.decrypt(ciphertext).decode("utf-8")
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill cpf_hmac for players")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Imports que dependem de libs instaladas do projeto
    try:
        import base64
        from cryptography.fernet import Fernet
    except ImportError:
        print("ERROR: cryptography não instalado. Execute: pip install cryptography")
        sys.exit(1)

    database_url = os.environ.get("DATABASE_URL", "")
    pii_key = os.environ.get("PII_ENCRYPTION_KEY", "")

    if not database_url:
        print("ERROR: DATABASE_URL não configurada")
        sys.exit(1)
    if not pii_key:
        print("ERROR: PII_ENCRYPTION_KEY não configurada")
        sys.exit(1)

    # Derivar chave Fernet (mesmo processo que auth.py)
    raw_key = pii_key.encode("utf-8")
    key_32 = hashlib.sha256(raw_key).digest()
    fernet_key = base64.urlsafe_b64encode(key_32)
    fernet = Fernet(fernet_key)

    # Chave HMAC (domain separation)
    hmac_key = hashlib.sha256(raw_key + b":cpf_hmac").digest()

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    cur = conn.cursor()

    # Contar total
    cur.execute("SELECT COUNT(*) FROM players WHERE cpf_hmac IS NULL")
    total = cur.fetchone()[0]
    print(f"Players sem cpf_hmac: {total}")
    if total == 0:
        print("Nada a fazer.")
        conn.close()
        return

    offset = 0
    updated = 0
    errors = 0

    while True:
        cur.execute(
            "SELECT id, cpf_encrypted FROM players WHERE cpf_hmac IS NULL LIMIT %s OFFSET %s",
            (args.batch_size, offset),
        )
        rows = cur.fetchall()
        if not rows:
            break

        batch_updates = []
        for player_id, cpf_enc in rows:
            if cpf_enc is None:
                errors += 1
                continue
            cpf_plain = _decrypt_cpf(bytes(cpf_enc), fernet)
            if cpf_plain is None:
                print(f"  WARN: falha ao decriptografar player {player_id}")
                errors += 1
                continue
            digits = "".join(c for c in cpf_plain if c.isdigit())
            cpf_hash = _compute_cpf_hmac(digits, hmac_key)
            batch_updates.append((cpf_hash, str(player_id)))

        if not args.dry_run and batch_updates:
            cur.executemany(
                "UPDATE players SET cpf_hmac = %s WHERE id = %s",
                batch_updates,
            )
            conn.commit()

        updated += len(batch_updates)
        offset += args.batch_size
        print(f"  Processados: {updated}/{total} (erros: {errors})")

    if args.dry_run:
        print(f"\nDRY-RUN: {updated} players seriam atualizados ({errors} erros)")
    else:
        print(f"\nConcluído: {updated} players atualizados ({errors} erros)")

    conn.close()


if __name__ == "__main__":
    main()
