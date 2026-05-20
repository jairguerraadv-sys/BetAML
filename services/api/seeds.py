"""
BetAML Seeds — 2 tenants, 3 users/tenant, 50 players/tenant,
cenários suspeitos (structuring, spike, device compartilhado, etc.)
12 regras DSL default por tenant.

Execute: python seeds.py
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from auth import hash_password, encrypt_pii, compute_cpf_hmac
from models import (
    Alert, Base, Case, CaseEvent, CompoundRule,
    MappingConfig, Player, PlayerList, PlayerListEntry, RuleDefinition,
    ScoringConfig, Tenant, User,
)

# ──────────────────────────────────────────────────
# DB setup
# ──────────────────────────────────────────────────
_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(_url, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)

DEFAULT_SUPER_ADMIN_USERNAME = os.getenv("SUPER_ADMIN_USER", "superadmin")
DEFAULT_SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", f"{DEFAULT_SUPER_ADMIN_USERNAME}@betaml.dev")
DEFAULT_SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASS", "superadmin123")


def _seed_allowed() -> bool:
    env = str(settings.environment or "development").strip().lower()
    if env in {"development", "test"}:
        return True
    explicit = os.getenv("ALLOW_SYNTHETIC_SEED", os.getenv("API_AUTO_SEED", "")).strip().lower()
    if explicit not in {"1", "true", "yes", "on"}:
        return False
    # Em staging/produção com ALLOW_SYNTHETIC_SEED=true: exige senha forte
    if DEFAULT_SUPER_ADMIN_PASSWORD in ("superadmin123", "", "changeme"):
        raise RuntimeError(
            "SUPER_ADMIN_PASS não pode ser o valor padrão ('superadmin123') "
            "quando ALLOW_SYNTHETIC_SEED=true fora de development/test. "
            "Defina SUPER_ADMIN_PASS com uma senha segura. "
            "Gere com: python -c \"import secrets; print(secrets.token_urlsafe(20))\""
        )
    return True

# ──────────────────────────────────────────────────
# 12 DSL Rules (default)
# ──────────────────────────────────────────────────
DEFAULT_RULES = [
    {
        "name": "Spike vs Baseline (Z-Score)",
        "description": "Depósito atual com z-score alto versus baseline do jogador",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "zscore(features.deposit_sum_24h, features.baseline_avg_daily_deposit, features.baseline_stddev_deposit) >= params.zscore_threshold and transaction.type == \"DEPOSIT\"",
        "params": {"zscore_threshold": 3},
    },
    {
        "name": "Structuring (Muitos depósitos pequenos 24h)",
        "description": "Múltiplos depósitos pequenos em 24h (fracionamento)",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "features.deposit_count_24h >= params.count_threshold and features.deposit_sum_24h >= params.sum_threshold and transaction.type == \"DEPOSIT\"",
        "params": {"count_threshold": 5, "sum_threshold": 5000},
    },
    {
        "name": "Instrumento novo + valor alto",
        "description": "Uso de instrumento de pagamento nunca visto + valor elevado",
        "severity": "MEDIUM",
        "scope": "TRANSACTION",
        "condition_dsl": "features.new_payment_instrument_flag == true and transaction.amount >= params.amount_threshold",
        "params": {"amount_threshold": 2000},
    },
    {
        "name": "PEP com desvio alto",
        "description": "Jogador PEP com depósito acima do income declarado",
        "severity": "CRITICAL",
        "scope": "TRANSACTION",
        "condition_dsl": "player.pep_flag == true and transaction.amount >= params.pep_threshold",
        "params": {"pep_threshold": 5000},
    },
    {
        "name": "Conta bancária compartilhada",
        "description": "Mesma conta bancária usada por múltiplos players",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "features.shared_bank_account_count >= params.shared_threshold",
        "params": {"shared_threshold": 2},
    },
    {
        "name": "Mesmo device em múltiplos CPFs",
        "description": "Mesmo dispositivo usado por múltiplos joueurs distintos",
        "severity": "HIGH",
        "scope": "DEVICE_EVENT",
        "condition_dsl": "features.shared_device_count >= params.device_threshold",
        "params": {"device_threshold": 3},
    },
    {
        "name": "Alta razão saque/depósito 7d",
        "description": "Razão entre saques e depósitos em 7d acima do threshold",
        "severity": "MEDIUM",
        "scope": "TRANSACTION",
        "condition_dsl": "ratio(features.withdrawal_sum_7d, features.deposit_sum_7d) >= params.ratio_threshold and features.deposit_sum_7d >= params.min_volume",
        "params": {"ratio_threshold": "0.9", "min_volume": 1000},
    },
    {
        "name": "Spike de stake em apostas 7d",
        "description": "Stake de aposta muito acima da média histórica",
        "severity": "MEDIUM",
        "scope": "BET",
        "condition_dsl": "bet.stakeAmount >= params.stake_min and features.bet_stake_sum_7d >= params.volume_threshold",
        "params": {"stake_min": 1000, "volume_threshold": 10000},
    },
    {
        "name": "Chargebacks acima do normal",
        "description": "Múltiplos chargebacks em 30 dias",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "features.chargeback_count_30d >= params.cb_threshold and transaction.type == \"CHARGEBACK\"",
        "params": {"cb_threshold": 2},
    },
    {
        "name": "Depósitos falhos + sucesso grande",
        "description": "Várias tentativas falhas seguidas de depósito bem-sucedido alto",
        "severity": "MEDIUM",
        "scope": "TRANSACTION",
        "condition_dsl": "features.failed_deposit_count_24h >= params.failed_threshold and transaction.amount >= params.amount_threshold and transaction.status == \"SETTLED\"",
        "params": {"failed_threshold": 3, "amount_threshold": 3000},
    },
    {
        "name": "Round-tripping (depósito → aposta mínima → saque)",
        "description": "Padrão de lavagem: depósito, aposta simbólica, saque rápido",
        "severity": "CRITICAL",
        "scope": "TRANSACTION",
        "condition_dsl": "transaction.type == \"WITHDRAWAL\" and ratio(features.withdrawal_sum_24h, features.deposit_sum_24h) >= params.round_trip_ratio and features.bet_stake_sum_24h <= params.max_stake",
        "params": {"round_trip_ratio": "0.8", "max_stake": 50},
    },
    {
        "name": "Saque rápido após depósito",
        "description": "Saque alto realizado logo após depósito relevante no mesmo dia",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "transaction.type == \"WITHDRAWAL\" and features.deposit_sum_24h >= params.min_deposit and ratio(features.withdrawal_sum_24h, features.deposit_sum_24h) >= params.ratio_threshold",
        "params": {"min_deposit": 1000, "ratio_threshold": "0.7"},
    },
    {
        "name": "Incompatibilidade renda/volume 30d",
        "description": "Volume de depósitos nos últimos 30d incompatível com a renda mensal declarada (Res. COAF 40/2021 Art. 2 — indício de lavagem por volume atípico)",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "transaction.type == \"DEPOSIT\" and player.declared_income_monthly != null and features.income_ratio_30d >= params.ratio_threshold",
        "params": {"ratio_threshold": 3.0},
    },
    # ── Regras multi-modalidade (Lei 14.790/2023 art. 3º, II) ──
    {
        "name": "Alta frequência em slots (24h)",
        "description": "Volume anômalo de rodadas de slots em 24h — possível automação ou lavagem via jogos de baixo RTP",
        "severity": "MEDIUM",
        "scope": "BET",
        "condition_dsl": "bet.productType == \"SLOT\" and features.slot_session_count_24h >= params.threshold",
        "params": {"threshold": 200},
    },
    {
        "name": "Diversificação de produto suspeita (7d)",
        "description": "Jogador usando muitas modalidades distintas em 7d — possível dispersão para dificultar rastreamento",
        "severity": "MEDIUM",
        "scope": "BET",
        "condition_dsl": "features.bet_product_diversity_7d >= params.max_types",
        "params": {"max_types": 4},
    },
    {
        "name": "Casino chip washing",
        "description": "Padrão de lavagem em casino ao vivo: apostas mínimas + saque alto (chips → cash)",
        "severity": "HIGH",
        "scope": "BET",
        "condition_dsl": "bet.productType == \"CASINO_LIVE\" and features.casino_session_count_24h >= params.min_sessions and bet.stakeAmount <= params.max_stake",
        "params": {"min_sessions": 50, "max_stake": 25},
    },
    {
        "name": "Alta frequência em casino ao vivo (24h)",
        "description": "Volume anômalo de sessões de casino ao vivo em 24h",
        "severity": "MEDIUM",
        "scope": "BET",
        "condition_dsl": "bet.productType == \"CASINO_LIVE\" and features.casino_session_count_24h >= params.threshold",
        "params": {"threshold": 150},
    },
]

TENANTS = [
    {"name": "OperadorA", "slug": "operador_a"},
    {"name": "OperadorB", "slug": "operador_b"},
]

USERS_TEMPLATE = [
    {"username_tmpl": "admin_{}", "email_tmpl": "admin_{}@betaml.dev", "password": "admin123", "role": "ADMIN"},
    {"username_tmpl": "analyst_{}", "email_tmpl": "analyst_{}@betaml.dev", "password": "analyst123", "role": "AML_ANALYST"},
    {"username_tmpl": "auditor_{}", "email_tmpl": "auditor_{}@betaml.dev", "password": "auditor123", "role": "AUDITOR"},
]


async def ensure_default_super_admin(db: AsyncSession) -> str | None:
    tenant = (
        await db.execute(
            select(Tenant)
            .order_by(Tenant.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if tenant is None:
        return None

    await db.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": str(tenant.id)})

    super_admin = (
        await db.execute(
            select(User)
            .where(
                or_(
                    User.username == DEFAULT_SUPER_ADMIN_USERNAME,
                    User.email == DEFAULT_SUPER_ADMIN_EMAIL,
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    password_hash = hash_password(DEFAULT_SUPER_ADMIN_PASSWORD)

    if super_admin is None:
        db.add(
            User(
                tenant_id=tenant.id,
                username=DEFAULT_SUPER_ADMIN_USERNAME,
                email=DEFAULT_SUPER_ADMIN_EMAIL,
                password_hash=password_hash,
                role="ADMIN",
                roles=["BetAML_SuperAdmin"],
                active=True,
            )
        )
        await db.flush()
        print(
            f"  SuperAdmin seed criado: {DEFAULT_SUPER_ADMIN_USERNAME} / {DEFAULT_SUPER_ADMIN_PASSWORD}"
        )
        return "created"

    super_admin.tenant_id = tenant.id
    super_admin.email = DEFAULT_SUPER_ADMIN_EMAIL
    super_admin.password_hash = password_hash
    super_admin.role = "ADMIN"
    super_admin.roles = ["BetAML_SuperAdmin"]
    super_admin.active = True
    await db.flush()
    print(
        f"  SuperAdmin seed atualizado: {DEFAULT_SUPER_ADMIN_USERNAME} / {DEFAULT_SUPER_ADMIN_PASSWORD}"
    )
    return "updated"


def random_cpf() -> str:
    return "".join([str(random.randint(0, 9)) for _ in range(11)])


async def seed(db: AsyncSession):
    # Idempotência
    result = await db.execute(text("SELECT COUNT(*) FROM tenants"))
    count = result.scalar()
    if count and count > 0:
        action = await ensure_default_super_admin(db)
        await db.commit()
        if action:
            print("Seeds já aplicados. SuperAdmin garantido.")
        else:
            print("Seeds já aplicados. Pulando.")
        return

    print("Aplicando seeds...")

    for tenant_data in TENANTS:
        slug = tenant_data["slug"]
        suffix = slug.split("_")[1]  # 'a' ou 'b'

        # Tenant
        tenant = Tenant(
            id=str(uuid.uuid4()),
            name=tenant_data["name"],
            slug=tenant_data["slug"],
            active=True,
            settings={},
            risk_score_threshold=0.75,
        )
        db.add(tenant)
        await db.flush()
        print(f"  Tenant: {tenant.name} ({tenant.id})")

        # RLS: from here on, all tenant-scoped tables require app.current_tenant.
        # This makes seeds work even when the app connects as betaml_app under FORCE RLS.
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": str(tenant.id)})

        # Users
        users = []
        for ut in USERS_TEMPLATE:
            u = User(
                tenant_id=tenant.id,
                username=ut["username_tmpl"].format(suffix),
                email=ut["email_tmpl"].format(suffix),
                password_hash=hash_password(ut["password"]),
                role=ut["role"],
                active=True,
            )
            db.add(u)
            users.append(u)
        await db.flush()
        admin_user = users[0]
        print(f"    Users: {[u.username for u in users]}")

        # MappingConfigs (BackofficeAlpha + BackofficeBeta)
        from libs.mapping import BACKOFFICE_ALPHA_TRANSACTION, BACKOFFICE_BETA_TRANSACTION
        for mc_conf in [BACKOFFICE_ALPHA_TRANSACTION, BACKOFFICE_BETA_TRANSACTION]:
            mc = MappingConfig(
                tenant_id=tenant.id,
                name=f"{mc_conf['source_system']} Transaction Mapping",
                source_system=mc_conf["source_system"],
                entity_type=mc_conf["entity_type"],
                config_json=mc_conf,
                created_by=admin_user.id,
            )
            db.add(mc)

        # 12 DSL Rules
        for rd in DEFAULT_RULES:
            rule = RuleDefinition(
                tenant_id=tenant.id,
                name=rd["name"],
                description=rd["description"],
                status="ACTIVE",
                severity=rd["severity"],
                scope=rd["scope"],
                condition_dsl=rd["condition_dsl"],
                params=rd["params"],
                created_by=admin_user.id,
            )
            db.add(rule)
        await db.flush()
        print(f"    {len(DEFAULT_RULES)} regras DSL criadas")

        # 50 Players + cenários suspeitos
        device_shared = "dev-shared-001"  # device compartilhado
        _bank_shared   = "12345678901"     # conta bancária compartilhada

        players_list = []
        for i in range(50):
            cpf = random_cpf()
            player = Player(
                tenant_id=tenant.id,
                external_player_id=f"EXT-{slug.upper()}-{i+1:03d}",
                cpf_encrypted=encrypt_pii(cpf),
                cpf_hmac=compute_cpf_hmac(cpf),
                name_encrypted=encrypt_pii(f"Player {i+1} {tenant_data['name']}"),
                pep_flag=(i < 3),   # primeiros 3 são PEP
                declared_income_monthly=random.choice([2000, 5000, 10000, 20000, None]),
                profession=random.choice(["Engineer", "Trader", "Teacher", None]),
                risk_score=round(random.uniform(0.01, 0.3), 4),
                risk_band="LOW",
            )
            db.add(player)
            players_list.append(player)
        await db.flush()
        print("    50 players criados (3 PEP)")

        # Cenários suspeitos — gerar Alerts
        suspicious_players = players_list[:5]

        rule_result = await db.execute(
            text("SELECT id, name FROM rule_definitions WHERE tenant_id = :tid LIMIT 12"),
            {"tid": tenant.id}
        )
        rule_rows = rule_result.fetchall()
        _rules_map = {r.name: r.id for r in rule_rows}

        # Cenário 1: Structuring
        p = suspicious_players[0]
        alert = Alert(
            tenant_id=tenant.id,
            player_id=p.id,
            alert_type="RULE",
            severity="HIGH",
            status="OPEN",
            title=f"Structuring detectado — {p.external_player_id}",
            description="8 depósitos de R$900 em 24h (total R$7.200)",
            evidence={
                "deposit_count_24h": 8,
                "deposit_sum_24h": 7200,
                "threshold_count": 5,
                "threshold_sum": 5000,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
            source_event_id=str(uuid.uuid4()),
        )
        db.add(alert)

        # Cenário 2: Spike (PEP)
        p2 = suspicious_players[1]
        alert2 = Alert(
            tenant_id=tenant.id,
            player_id=p2.id,
            alert_type="RULE",
            severity="CRITICAL",
            status="OPEN",
            title=f"PEP com depósito acima do threshold — {p2.external_player_id}",
            description="Jogador PEP depositou R$15.000 (acima do threshold de R$5.000)",
            evidence={
                "pep_flag": True,
                "amount": 15000,
                "threshold": 5000,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
            source_event_id=str(uuid.uuid4()),
        )
        db.add(alert2)

        # Cenário 3: Device compartilhado
        p3 = suspicious_players[2]
        alert3 = Alert(
            tenant_id=tenant.id,
            player_id=p3.id,
            alert_type="RULE",
            severity="HIGH",
            status="OPEN",
            title=f"Múltiplos CPFs no mesmo device — {p3.external_player_id}",
            description=f"Device {device_shared} associado a 5 CPFs distintos",
            evidence={
                "device_id": device_shared,
                "shared_device_count": 5,
                "threshold": 3,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
            source_event_id=str(uuid.uuid4()),
        )
        db.add(alert3)

        # Cenário 4: Round-tripping
        p4 = suspicious_players[3]
        alert4 = Alert(
            tenant_id=tenant.id,
            player_id=p4.id,
            alert_type="COMPOSITE",
            severity="CRITICAL",
            status="OPEN",
            title=f"Round-tripping detectado — {p4.external_player_id}",
            description="Depósito R$20.000 → aposta R$20 → saque R$19.500 em 2h",
            evidence={
                "deposit_sum_24h": 20000,
                "bet_stake_sum_24h": 20,
                "withdrawal_sum_24h": 19500,
                "ratio": 0.975,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            },
            anomaly_score=0.91,
            source_event_id=str(uuid.uuid4()),
        )
        db.add(alert4)
        await db.flush()

        # Case de alta prioridade para os cenários 1 e 4
        case = Case(
            tenant_id=tenant.id,
            player_id=p4.id,
            title=f"Investigação PLD — Round-tripping {p4.external_player_id}",
            description="Caso auto-criado por detecção de padrão de round-tripping",
            severity="CRITICAL",
            status="OPEN",
            auto_created=True,
            auto_created_reason="scoring.alerts: anomaly_score=0.91, severity=CRITICAL",
            source_alert_id=alert4.id,
            created_by=admin_user.id,
        )
        db.add(case)
        await db.flush()

        alert4.case_id = case.id
        evt = CaseEvent(
            case_id=case.id,
            tenant_id=tenant.id,
            event_type="NOTE",
            content={"note": "Caso criado automaticamente por alta severidade e anomaly_score > 0.9"},
            created_by=admin_user.id,
        )
        db.add(evt)

        # ScoringConfig por tenant (thresholds configuráveis)
        scoring_cfg = ScoringConfig(
            tenant_id=tenant.id,
            rule_weight=0.4,
            ml_weight=0.4,
            network_weight=0.2,
            auto_case_threshold=0.75,
            risk_band_low_threshold=0.35,
            risk_band_high_threshold=0.70,
            income_volume_ratio_threshold=1.5,
            sla_critical_hours=4,
            sla_high_hours=24,
            sla_medium_hours=72,
            sla_low_hours=168,
            updated_by=admin_user.id,
        )
        db.add(scoring_cfg)

        # PlayerLists: watchlist PEP e lista interna de suspeitos
        wl_pep = PlayerList(
            tenant_id=tenant.id,
            name="pep_watchlist",
            list_type="WATCH_LIST",
            description="Jogadores identificados como PEP ou conexão com PEP",
            active=True,
            source="MANUAL",
            created_by=admin_user.id,
        )
        wl_susp = PlayerList(
            tenant_id=tenant.id,
            name="internal_suspects",
            list_type="CUSTOM",
            description="Lista interna de suspeitos identificados em investigações anteriores",
            active=True,
            source="MANUAL",
            created_by=admin_user.id,
        )
        db.add(wl_pep)
        db.add(wl_susp)
        await db.flush()

        # Adicionar jogadores PEP à watchlist
        for p_pep in players_list[:3]:
            entry = PlayerListEntry(
                list_id=wl_pep.id,
                player_list_id=wl_pep.id,
                tenant_id=tenant.id,
                player_id=p_pep.id,
                external_player_id=p_pep.external_player_id,
                value=p_pep.external_player_id,
                value_type="EXTERNAL_ID",
                added_by=admin_user.id,
            )
            db.add(entry)

        # CompoundRule: combina structuring + spike (detecta padrão combinado)
        await db.flush()
        rule_ids_result = await db.execute(
            text("SELECT id, name FROM rule_definitions WHERE tenant_id = :tid"),
            {"tid": tenant.id}
        )
        rule_id_map = {r.name: str(r.id) for r in rule_ids_result.fetchall()}

        structuring_id = rule_id_map.get("Structuring (Muitos depósitos pequenos 24h)")
        spike_id = rule_id_map.get("Spike vs Baseline (Z-Score)")
        roundtrip_id = rule_id_map.get("Round-tripping (depósito → aposta mínima → saque)")

        if structuring_id and spike_id:
            compound = CompoundRule(
                tenant_id=tenant.id,
                name="Structuring + Spike Combinado",
                description="Dispara quando structuring E spike de depósito ocorrem simultaneamente",
                logic="AND",
                component_rule_ids=[structuring_id, spike_id],
                score_weights={structuring_id: 0.6, spike_id: 0.4},
                min_score_threshold=0.70,
                severity_mode="MAX",
                is_active=True,
                created_by=admin_user.id,
            )
            db.add(compound)

        if roundtrip_id:
            compound2 = CompoundRule(
                tenant_id=tenant.id,
                name="Round-trip HIGH Confidence",
                description="Round-tripping com alto score composto (regra + ML)",
                logic="OR",
                component_rule_ids=[roundtrip_id],
                score_weights={roundtrip_id: 1.0},
                min_score_threshold=0.80,
                severity_mode="FIXED",
                fixed_severity="CRITICAL",
                is_active=True,
                created_by=admin_user.id,
            )
            db.add(compound2)

        print("    4 alertas suspeitos + 1 case auto-criado")
        print("    ScoringConfig, 2 PlayerLists, CompoundRules criadas")

    await ensure_default_super_admin(db)

    await db.commit()
    print("\nSeeds aplicados com sucesso!")
    print("\nCredenciais de acesso:")
    print(
        f"\n  Plataforma:\n"
        f"    {DEFAULT_SUPER_ADMIN_USERNAME} / {DEFAULT_SUPER_ADMIN_PASSWORD} (SUPER_ADMIN)"
    )
    for t in TENANTS:
        suffix = t["slug"].split("_")[1]
        print(f"\n  Tenant: {t['name']}")
        print(f"    admin_{suffix}    / admin123   (ADMIN)")
        print(f"    analyst_{suffix}  / analyst123 (AML_ANALYST)")
        print(f"    auditor_{suffix}  / auditor123 (AUDITOR)")


async def main():
    if not _seed_allowed():
        raise RuntimeError(
            "Seed sintético bloqueado fora de development/test. "
            "Defina ALLOW_SYNTHETIC_SEED=true explicitamente para bootstrap controlado."
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Check if database is already seeded (avoid duplicate data on restart)
    async with Session() as db:
        result = await db.execute(text("SELECT COUNT(*) FROM tenants"))
        count = result.scalar_one()
        if count > 0:
            print(f"⚠️  Database already contains {count} tenant(s). Ensuring SuperAdmin seed.")
            await ensure_default_super_admin(db)
            await db.commit()
            print(
                f"   Credencial garantida: {DEFAULT_SUPER_ADMIN_USERNAME} / {DEFAULT_SUPER_ADMIN_PASSWORD} (SUPER_ADMIN)"
            )
            return

        print("✓ Database empty. Running seed...")
        await seed(db)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
