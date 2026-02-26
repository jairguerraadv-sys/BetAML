"""
BetAML seed script.

Populates Postgres with tenants, users, rules, players, and synthetic
suspicious-activity scenarios plus the associated alerts and cases.

Requires: psycopg2-binary, passlib[bcrypt], faker
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from faker import Faker
from passlib.hash import bcrypt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL_RAW = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://betaml:devpass@postgres:5432/betaml",
)
# psycopg2 uses a plain DSN — strip the async driver prefix if present
DSN = DATABASE_URL_RAW.replace("postgresql+asyncpg://", "postgresql://")

TENANTS = [
    {
        "name": "OperadorA",
        "slug": "operador-a",
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    },
    {
        "name": "OperadorB",
        "slug": "operador-b",
        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
    },
]

USERS = [
    {
        "email": "admin@operadora.com",
        "password": "Admin123!",
        "role": "ADMIN",
        "full_name": "Admin A",
        "tenant_index": 0,
    },
    {
        "email": "analyst@operadora.com",
        "password": "Analyst123!",
        "role": "AML_ANALYST",
        "full_name": "Analyst A",
        "tenant_index": 0,
    },
    {
        "email": "auditor@operadora.com",
        "password": "Auditor123!",
        "role": "AUDITOR",
        "full_name": "Auditor A",
        "tenant_index": 0,
    },
    {
        "email": "admin@operadorb.com",
        "password": "Admin123!",
        "role": "ADMIN",
        "full_name": "Admin B",
        "tenant_index": 1,
    },
    {
        "email": "analyst@operadorb.com",
        "password": "Analyst123!",
        "role": "AML_ANALYST",
        "full_name": "Analyst B",
        "tenant_index": 1,
    },
    {
        "email": "auditor@operadorb.com",
        "password": "Auditor123!",
        "role": "AUDITOR",
        "full_name": "Auditor B",
        "tenant_index": 1,
    },
]

DEFAULT_RULES = [
    {
        "name": "High Z-Score Deposit",
        "description": "Deposit amount is more than 3 standard deviations above the player baseline.",
        "condition": "features.zscore_current_deposit_vs_baseline > 3.0",
        "severity": "HIGH",
        "alert_type": "STRUCTURING",
    },
    {
        "name": "Excessive Deposits 24h",
        "description": "Player made 10 or more deposits within the last 24 hours.",
        "condition": "features.deposit_count_24h >= 10",
        "severity": "HIGH",
        "alert_type": "STRUCTURING",
    },
    {
        "name": "New Instrument Large Deposit",
        "description": "New payment instrument used and deposit amount exceeds R$5,000.",
        "condition": "features.new_payment_instrument_flag == 1 and transaction.amount > 5000",
        "severity": "MEDIUM",
        "alert_type": "SUSPICIOUS_TRANSACTION",
    },
    {
        "name": "PEP High Z-Score",
        "description": "Politically exposed person with above-baseline deposit activity.",
        "condition": "player.pepFlag == true and features.zscore_current_deposit_vs_baseline > 2.0",
        "severity": "HIGH",
        "alert_type": "PEP_ALERT",
    },
    {
        "name": "Shared Bank Account",
        "description": "Bank account is shared among more than 2 distinct players.",
        "condition": "features.shared_bank_account_count > 2",
        "severity": "HIGH",
        "alert_type": "COLLUSION",
    },
    {
        "name": "Shared Device",
        "description": "Device is shared among more than 3 distinct players.",
        "condition": "features.shared_device_count > 3",
        "severity": "MEDIUM",
        "alert_type": "COLLUSION",
    },
    {
        "name": "High Withdrawal Ratio",
        "description": "Withdrawals exceed 90% of deposits over the past 7 days.",
        "condition": "features.ratio_withdrawal_to_deposit_7d > 0.9",
        "severity": "HIGH",
        "alert_type": "MONEY_LAUNDERING",
    },
    {
        "name": "High Bet Stake Volume",
        "description": "Total bet stake in 7 days exceeds R$10,000.",
        "condition": "features.bet_stake_sum_7d > 10000",
        "severity": "MEDIUM",
        "alert_type": "EXCESSIVE_GAMBLING",
    },
    {
        "name": "Rapid Withdrawal Pattern",
        "description": "Withdrawal-to-deposit ratio is above 85% — possible layering.",
        "condition": "features.ratio_withdrawal_to_deposit_7d > 0.85",
        "severity": "MEDIUM",
        "alert_type": "MONEY_LAUNDERING",
    },
    {
        "name": "Structuring Micro Deposits",
        "description": "More than 5 deposits in 24h with combined sum above baseline.",
        "condition": "features.deposit_count_24h >= 5 and features.zscore_current_deposit_vs_baseline > 2.0",
        "severity": "HIGH",
        "alert_type": "STRUCTURING",
    },
    {
        "name": "New Device High Volume",
        "description": "New device flag set and 7-day deposit sum is elevated.",
        "condition": "features.new_device_flag == 1 and features.deposit_sum_7d > 20000",
        "severity": "MEDIUM",
        "alert_type": "SUSPICIOUS_TRANSACTION",
    },
    {
        "name": "Spike Deposit 7d",
        "description": "7-day deposit sum exceeds R$50,000.",
        "condition": "features.deposit_sum_7d > 50000",
        "severity": "HIGH",
        "alert_type": "STRUCTURING",
    },
]

fake = Faker("pt_BR")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


def _hash(password: str) -> str:
    return bcrypt.hash(password)


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
        (table,),
    )
    return cur.fetchone()[0]


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        )
        """,
        (table, column),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


def seed_tenants(cur) -> None:
    if not _table_exists(cur, "tenants"):
        print("  [skip] 'tenants' table does not exist yet — skipping.")
        return
    for t in TENANTS:
        cur.execute(
            """
            INSERT INTO tenants (id, name, slug, created_at, updated_at)
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            (t["id"], t["name"], t["slug"]),
        )
    print(f"  Upserted {len(TENANTS)} tenants.")


def seed_users(cur) -> None:
    if not _table_exists(cur, "users"):
        print("  [skip] 'users' table does not exist yet — skipping.")
        return
    for u in USERS:
        tenant = TENANTS[u["tenant_index"]]
        hashed = _hash(u["password"])
        cur.execute(
            """
            INSERT INTO users (id, tenant_id, email, hashed_password, role, full_name,
                               is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW())
            ON CONFLICT (email) DO NOTHING
            """,
            (_uid(), tenant["id"], u["email"], hashed, u["role"], u["full_name"]),
        )
    print(f"  Upserted {len(USERS)} users.")


def seed_mapping_configs(cur) -> None:
    if not _table_exists(cur, "mapping_configs"):
        print("  [skip] 'mapping_configs' table does not exist yet — skipping.")
        return
    backoffices = ["BackofficeAlpha", "BackofficeBeta"]
    count = 0
    for t in TENANTS:
        for bo in backoffices:
            cur.execute(
                """
                INSERT INTO mapping_configs
                    (id, tenant_id, backoffice_name, field_mappings, created_at, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, NOW(), NOW())
                ON CONFLICT DO NOTHING
                """,
                (
                    _uid(),
                    t["id"],
                    bo,
                    '{"player_id": "playerId", "amount": "amount", "cpf": "cpf"}',
                ),
            )
            count += 1
    print(f"  Upserted {count} mapping configs.")


def seed_rules(cur) -> dict[str, list[str]]:
    """Returns mapping of tenant_id -> list of rule IDs inserted."""
    if not _table_exists(cur, "rule_definitions"):
        print("  [skip] 'rule_definitions' table does not exist yet — skipping.")
        return {}
    rule_ids_by_tenant: dict[str, list[str]] = {}
    for t in TENANTS:
        ids = []
        for r in DEFAULT_RULES:
            rid = _uid()
            cur.execute(
                """
                INSERT INTO rule_definitions
                    (id, tenant_id, name, description, condition_dsl,
                     severity, alert_type, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW())
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                (
                    rid,
                    t["id"],
                    r["name"],
                    r["description"],
                    r["condition"],
                    r["severity"],
                    r["alert_type"],
                ),
            )
            row = cur.fetchone()
            ids.append(row[0] if row else rid)
        rule_ids_by_tenant[t["id"]] = ids
        print(f"  Upserted {len(ids)} rules for tenant {t['name']}.")
    return rule_ids_by_tenant


def seed_players(cur, n: int = 50) -> dict[str, list[dict]]:
    """Returns mapping of tenant_id -> list of player dicts."""
    if not _table_exists(cur, "players"):
        print("  [skip] 'players' table does not exist yet — skipping.")
        return {}
    players_by_tenant: dict[str, list[dict]] = {}
    has_pep = _column_exists(cur, "players", "pep_flag")
    for t in TENANTS:
        players = []
        for _ in range(n):
            pid = _uid()
            player: dict = {
                "id": pid,
                "tenant_id": t["id"],
                "external_id": fake.bothify(text="PLY-########"),
                "cpf": fake.cpf().replace(".", "").replace("-", ""),
                "full_name": fake.name(),
                "email": fake.email(),
                "phone": fake.phone_number()[:20],
                "date_of_birth": fake.date_of_birth(minimum_age=18, maximum_age=70),
                "country": "BR",
                "device_id": fake.uuid4(),
            }
            if has_pep:
                player["pep_flag"] = False
            players.append(player)

        for p in players:
            cols = [
                "id", "tenant_id", "external_id", "cpf", "full_name",
                "email", "phone", "date_of_birth", "country", "device_id",
            ]
            vals = [
                p["id"], p["tenant_id"], p["external_id"], p["cpf"],
                p["full_name"], p["email"], p["phone"], p["date_of_birth"],
                p["country"], p["device_id"],
            ]
            if has_pep:
                cols.append("pep_flag")
                vals.append(p["pep_flag"])
            placeholders = ", ".join(["%s"] * len(vals))
            col_str = ", ".join(cols)
            cur.execute(
                f"""
                INSERT INTO players ({col_str}, created_at, updated_at)
                VALUES ({placeholders}, NOW(), NOW())
                ON CONFLICT (id) DO NOTHING
                """,
                vals,
            )
        players_by_tenant[t["id"]] = players
        print(f"  Inserted {n} players for tenant {t['name']}.")
    return players_by_tenant


def _insert_alert(cur, tenant_id: str, player: dict, rule_id: str | None,
                  alert_type: str, severity: str, risk_score: float) -> str:
    if not _table_exists(cur, "alerts"):
        return ""
    aid = _uid()
    cur.execute(
        """
        INSERT INTO alerts
            (id, tenant_id, player_id, player_cpf, rule_id, alert_type,
             severity, status, risk_score, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, NOW(), NOW())
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (
            aid, tenant_id, player["id"], player.get("cpf", ""),
            rule_id, alert_type, severity, risk_score,
        ),
    )
    row = cur.fetchone()
    return row[0] if row else aid


def _insert_case(cur, tenant_id: str, alert_id: str) -> None:
    if not _table_exists(cur, "cases"):
        return
    cur.execute(
        """
        INSERT INTO cases
            (id, tenant_id, alert_id, status, priority, created_at, updated_at)
        VALUES (%s, %s, %s, 'OPEN', 'HIGH', NOW(), NOW())
        ON CONFLICT DO NOTHING
        """,
        (_uid(), tenant_id, alert_id),
    )


def seed_suspicious_scenarios(cur, players_by_tenant: dict, rule_ids_by_tenant: dict) -> None:
    if not players_by_tenant:
        print("  [skip] No players seeded — skipping suspicious scenarios.")
        return

    alert_count = 0
    case_count = 0

    for t in TENANTS:
        tid = t["id"]
        players = players_by_tenant.get(tid, [])
        rule_ids = rule_ids_by_tenant.get(tid, [None])
        if len(players) < 10:
            print(f"  [skip] Not enough players for tenant {t['name']}.")
            continue

        structuring_rule = rule_ids[0] if rule_ids else None
        withdrawal_rule = rule_ids[6] if len(rule_ids) > 6 else None
        shared_device_rule = rule_ids[5] if len(rule_ids) > 5 else None
        shared_bank_rule = rule_ids[4] if len(rule_ids) > 4 else None
        spike_rule = rule_ids[11] if len(rule_ids) > 11 else None

        # --- Scenario 1: Structuring — 15 small deposits in 24h ---
        structuring_player = players[0]
        if _table_exists(cur, "transactions"):
            for i in range(15):
                cur.execute(
                    """
                    INSERT INTO transactions
                        (id, tenant_id, player_id, player_cpf, type, amount,
                         currency, method, status, occurred_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'DEPOSIT', %s, 'BRL', 'PIX',
                            'COMPLETED', %s, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        _uid(), tid,
                        structuring_player["id"], structuring_player.get("cpf", ""),
                        round(900 + i * 10, 2),
                        NOW - timedelta(hours=i),
                    ),
                )
        aid = _insert_alert(cur, tid, structuring_player, structuring_rule,
                            "STRUCTURING", "HIGH", 0.92)
        if aid:
            alert_count += 1
            _insert_case(cur, tid, aid)
            case_count += 1

        # --- Scenario 2: Spike — sudden 10x deposit ---
        spike_player = players[1]
        if _table_exists(cur, "transactions"):
            # baseline deposits
            for i in range(5):
                cur.execute(
                    """
                    INSERT INTO transactions
                        (id, tenant_id, player_id, player_cpf, type, amount,
                         currency, method, status, occurred_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'DEPOSIT', 500, 'BRL', 'PIX',
                            'COMPLETED', %s, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        _uid(), tid,
                        spike_player["id"], spike_player.get("cpf", ""),
                        NOW - timedelta(days=7 + i),
                    ),
                )
            # spike deposit
            cur.execute(
                """
                INSERT INTO transactions
                    (id, tenant_id, player_id, player_cpf, type, amount,
                     currency, method, status, occurred_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'DEPOSIT', 50000, 'BRL', 'TED',
                        'COMPLETED', %s, NOW(), NOW())
                ON CONFLICT DO NOTHING
                """,
                (
                    _uid(), tid,
                    spike_player["id"], spike_player.get("cpf", ""),
                    NOW - timedelta(hours=2),
                ),
            )
        aid = _insert_alert(cur, tid, spike_player, spike_rule,
                            "STRUCTURING", "HIGH", 0.95)
        if aid:
            alert_count += 1
            _insert_case(cur, tid, aid)
            case_count += 1

        # --- Scenario 3: Shared device — 5 players on same deviceId ---
        shared_device_id = str(uuid.uuid4())
        shared_device_players = players[2:7]
        if _table_exists(cur, "players") and _column_exists(cur, "players", "device_id"):
            for p in shared_device_players:
                cur.execute(
                    "UPDATE players SET device_id = %s WHERE id = %s",
                    (shared_device_id, p["id"]),
                )
        for p in shared_device_players:
            aid = _insert_alert(cur, tid, p, shared_device_rule,
                                "COLLUSION", "MEDIUM", 0.70)
            if aid:
                alert_count += 1

        # --- Scenario 4: Rapid withdrawal — deposit then withdrawal same day ---
        rapid_player = players[7]
        if _table_exists(cur, "transactions"):
            for tx_type, amount, delta_h in [("DEPOSIT", 10000, 3), ("WITHDRAWAL", 9500, 1)]:
                cur.execute(
                    """
                    INSERT INTO transactions
                        (id, tenant_id, player_id, player_cpf, type, amount,
                         currency, method, status, occurred_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'BRL', 'PIX',
                            'COMPLETED', %s, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        _uid(), tid,
                        rapid_player["id"], rapid_player.get("cpf", ""),
                        tx_type, amount,
                        NOW - timedelta(hours=delta_h),
                    ),
                )
        aid = _insert_alert(cur, tid, rapid_player, withdrawal_rule,
                            "MONEY_LAUNDERING", "HIGH", 0.88)
        if aid:
            alert_count += 1
            _insert_case(cur, tid, aid)
            case_count += 1

        # --- Scenario 5: Shared bank account — 3 players same holderDocument ---
        shared_doc = fake.cpf().replace(".", "").replace("-", "")
        shared_bank_players = players[8:11]
        if _table_exists(cur, "transactions"):
            for p in shared_bank_players:
                cur.execute(
                    """
                    INSERT INTO transactions
                        (id, tenant_id, player_id, player_cpf, type, amount,
                         currency, method, status, holder_document,
                         occurred_at, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'DEPOSIT', 3000, 'BRL', 'TED',
                            'COMPLETED', %s, %s, NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        _uid(), tid,
                        p["id"], p.get("cpf", ""),
                        shared_doc,
                        NOW - timedelta(days=1),
                    ),
                )
        for p in shared_bank_players:
            aid = _insert_alert(cur, tid, p, shared_bank_rule,
                                "COLLUSION", "HIGH", 0.85)
            if aid:
                alert_count += 1
                _insert_case(cur, tid, aid)
                case_count += 1

        print(
            f"  Tenant {t['name']}: created {alert_count} alerts, {case_count} cases "
            "(cumulative across tenants)."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def wait_for_postgres(dsn: str, retries: int = 15, delay: int = 5) -> psycopg2.extensions.connection:
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(dsn)
            print("  Connected to Postgres.")
            return conn
        except psycopg2.OperationalError as exc:
            print(f"  Postgres not ready ({attempt}/{retries}): {exc}")
            time.sleep(delay)
    print("ERROR: Could not connect to Postgres after multiple retries.")
    sys.exit(1)


def main() -> None:
    print("=== BetAML Seed Script ===")
    conn = wait_for_postgres(DSN)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("\n[1/6] Seeding tenants...")
        seed_tenants(cur)

        print("\n[2/6] Seeding users...")
        seed_users(cur)

        print("\n[3/6] Seeding mapping configs...")
        seed_mapping_configs(cur)

        print("\n[4/6] Seeding rule definitions...")
        rule_ids_by_tenant = seed_rules(cur)

        print("\n[5/6] Seeding players...")
        players_by_tenant = seed_players(cur, n=50)

        print("\n[6/6] Seeding suspicious scenarios, alerts, and cases...")
        seed_suspicious_scenarios(cur, players_by_tenant, rule_ids_by_tenant)

        conn.commit()
        print("\n=== Seed completed successfully. ===")
    except Exception as exc:
        conn.rollback()
        print(f"\nERROR during seeding — rolled back: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
