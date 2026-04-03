#!/usr/bin/env python3
"""
Gerador de dados sintéticos E2E para validação do BetAML.

Gera 4 CSVs:
  - players.csv          ~120 jogadores (normais + suspeitos)
  - transactions.csv     ~3.500 transações (depósitos/saques/bônus)
  - bets.csv             ~2.500 apostas (sports, cassino, slots)
  - device_events.csv    ~1.200 eventos de login/dispositivo

Cenários cobertos:
  C1 – Normais (~80 jogadores, comportamento padrão)
  C2 – Renda incompatível / high roller (10 jogadores)
  C3 – Structuring via PIX/TED (8 jogadores)
  C4 – Rede suspeita (12 jogadores, 3 clusters de shared device/bank)
  C5 – Bônus abuse / cashout rápido (5 jogadores)
  C6 – PEPs / alto risco (5 jogadores)

Todos os dados são 100% FICTÍCIOS — CPFs inválidos, nomes gerados.
"""
from __future__ import annotations

import csv
import hashlib
import os
import random
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

random.seed(42)  # reproducível

OUT_DIR = Path(__file__).parent

# ── Date range (90 dias, ~Jan 3 → Apr 3 2026) ────────────────────────────
NOW = datetime(2026, 4, 3, 12, 0, 0)
T_START = NOW - timedelta(days=90)

# ── Helpers ────────────────────────────────────────────────────────────────

def uid() -> str:
    return str(uuid.uuid4())[:12]

_tx_seq = 0
def tx_id(prefix: str = "TX") -> str:
    global _tx_seq
    _tx_seq += 1
    return f"{prefix}-{_tx_seq:06d}"

_bet_seq = 0
def bet_id() -> str:
    global _bet_seq
    _bet_seq += 1
    return f"BET-{_bet_seq:06d}"

_dev_seq = 0
def dev_id() -> str:
    global _dev_seq
    _dev_seq += 1
    return f"DEV-{_dev_seq:06d}"

def fake_cpf() -> str:
    """CPF fictício (11 dígitos, inválido por design)."""
    return f"{random.randint(10000000000, 99999999999)}"

def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def rand_dt(start: datetime, end: datetime) -> datetime:
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.uniform(0, delta))

def rand_amount(lo: float, hi: float) -> str:
    return f"{random.uniform(lo, hi):.2f}"

UFS = ["SP", "RJ", "MG", "BA", "PR", "RS", "SC", "CE", "PE", "GO", "DF", "PA", "MA", "AM"]

FIRST_NAMES = [
    "Lucas", "Gabriel", "Arthur", "Pedro", "Rafael", "Matheus", "Bruno",
    "Felipe", "Gustavo", "Thiago", "Ana", "Juliana", "Camila", "Fernanda",
    "Larissa", "Beatriz", "Mariana", "Carolina", "Amanda", "Vanessa",
    "Carlos", "José", "Marcos", "Diego", "Paulo", "Roberto", "Ricardo",
    "Leonardo", "André", "Rodrigo", "Eduardo", "Daniel", "Fábio", "João",
]
LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Pereira", "Costa", "Rodrigues",
    "Almeida", "Nascimento", "Lima", "Araújo", "Fernandes", "Carvalho",
    "Gomes", "Martins", "Rocha", "Ribeiro", "Alves", "Monteiro", "Barbosa",
]

PROFESSIONS_NORMAL = [
    "Analista de Sistemas", "Professor", "Vendedor", "Motorista", "Enfermeiro",
    "Técnico", "Engenheiro", "Designer", "Administrador", "Contador",
    "Advogado", "Médico", "Dentista", "Farmacêutico", "Jornalista",
]
PROFESSIONS_PEP = [
    "Deputado Federal", "Assessor Parlamentar", "Secretário de Estado",
    "Vereador", "Diretor de Autarquia",
]
CANAIS_CADASTRO = ["WEB", "APP", "AGENTE"]
PAYMENT_METHODS = ["PIX", "TED", "DEBIT", "WALLET"]
CHANNELS = ["WEB", "APP", "TERMINAL"]
SPORTS = ["SOCCER", "BASKETBALL", "TENNIS", "MMA", "VOLLEYBALL", "ESPORTS"]
MARKET_TYPES = ["1X2", "OVER_UNDER", "HANDICAP", "BOTH_TEAMS_SCORE", "CORRECT_SCORE"]
BET_TYPES = ["SPORTSBOOK", "CASINO_LIVE", "SLOT", "VIRTUAL"]
DEVICE_TYPES = ["MOBILE_IOS", "MOBILE_ANDROID", "DESKTOP", "TABLET"]

# Multi-modalidade (Lei 14.790/2023 art. 3º)
CASINO_GAMES = [
    {"id": "GAME-ROULETTE-01", "name": "Lightning Roulette", "provider": "Evolution", "category": "LIVE", "rtp": 0.9730},
    {"id": "GAME-BLACKJACK-01", "name": "Infinite Blackjack", "provider": "Evolution", "category": "LIVE", "rtp": 0.9956},
    {"id": "GAME-BACCARAT-01", "name": "Speed Baccarat", "provider": "Evolution", "category": "TABLE", "rtp": 0.9862},
    {"id": "GAME-POKER-01", "name": "Casino Hold'em", "provider": "Evolution", "category": "TABLE", "rtp": 0.9747},
]
SLOT_GAMES = [
    {"id": "GAME-SLOT-01", "name": "Gates of Olympus", "provider": "Pragmatic Play", "category": "SLOT", "rtp": 0.9649},
    {"id": "GAME-SLOT-02", "name": "Sweet Bonanza", "provider": "Pragmatic Play", "category": "SLOT", "rtp": 0.9651},
    {"id": "GAME-SLOT-03", "name": "Big Bass Bonanza", "provider": "Pragmatic Play", "category": "SLOT", "rtp": 0.9670},
    {"id": "GAME-SLOT-04", "name": "Fortune Tiger", "provider": "PG Soft", "category": "SLOT", "rtp": 0.9668},
    {"id": "GAME-SLOT-05", "name": "Aviator", "provider": "Spribe", "category": "INSTANT", "rtp": 0.9700},
]

EVENTS = [
    "FLA_VS_COR_20260320", "PAL_VS_SAO_20260321", "GRE_VS_INT_20260322",
    "BOT_VS_FLU_20260323", "ATM_VS_CAP_20260318", "SAN_VS_VAS_20260317",
    "BAH_VS_FOR_20260319", "CRU_VS_AME_20260316", "BRA_VS_ARG_20260401",
    "NBA_LAL_VS_GSW_20260325", "UFC_300_20260329", "WIMBLEDON_R1_20260315",
]

# ── Player generation ─────────────────────────────────────────────────────

players: list[dict] = []
# Indexed by external_player_id
player_map: dict[str, dict] = {}

def make_player(
    scenario: str,
    income: float,
    pep: bool = False,
    uf: str | None = None,
    profession: str | None = None,
    registration_age_days: int | None = None,
) -> dict:
    pid = f"PLY-{scenario[:4].upper()}-{len(players)+1:03d}"
    cpf = fake_cpf()
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    birth = datetime(
        random.randint(1970, 2003), random.randint(1, 12), random.randint(1, 28)
    )
    reg_days = registration_age_days or random.randint(30, 365)
    reg_date = NOW - timedelta(days=reg_days)
    p = {
        "external_player_id": pid,
        "cpf": cpf,
        "name": name,
        "birth_date": birth.strftime("%Y-%m-%d"),
        "declared_income_monthly": f"{income:.2f}",
        "pep_flag": "true" if pep else "false",
        "status": "ACTIVE",
        "profession": profession or random.choice(PROFESSIONS_NORMAL),
        "nationality": "BR",
        "registration_date": iso(reg_date),
        "self_exclusion_flag": "false",
        "email": f"{pid.lower().replace('-','.')}@fictibet.test",
        "phone": f"5511{random.randint(900000000, 999999999)}",
        "deposit_limit_daily": "",
        "uf": uf or random.choice(UFS),
        "canal_cadastro": random.choice(CANAIS_CADASTRO),
        # internal tracking
        "_scenario": scenario,
        "_income": income,
    }
    players.append(p)
    player_map[pid] = p
    return p


# ── C1: Normais (80 jogadores) ───────────────────────────────────────────

for _ in range(80):
    income = random.choice([2500, 3500, 4500, 5500, 7000, 8500, 10000, 12000])
    make_player("normal", income)

# ── C2: Renda incompatível (10 jogadores) ────────────────────────────────

for _ in range(10):
    income = random.choice([1800, 2200, 2800, 3200])  # renda baixa/média
    make_player("income_mismatch", income)

# ── C3: Structuring (8 jogadores) ────────────────────────────────────────

for _ in range(8):
    income = random.choice([3000, 4000, 5000])
    make_player("structuring", income)

# ── C4: Rede suspeita (12 jogadores, 3 clusters de 4) ───────────────────

SHARED_DEVICES = ["SHARED-NET-ALPHA-001", "SHARED-NET-BETA-001", "SHARED-NET-GAMMA-001"]
SHARED_IPS = ["189.45.100.10", "200.18.77.33", "177.92.55.42"]
SHARED_BANK_ACCOUNTS = [
    "PIX:shared.alpha@fictibet.test",
    "PIX:shared.beta@fictibet.test",
    "PIX:shared.gamma@fictibet.test",
]

for cluster_idx in range(3):
    for _ in range(4):
        income = random.choice([3000, 4000, 5000, 6000])
        p = make_player("network", income)
        p["_cluster"] = cluster_idx  # for device/bank linking

# ── C5: Bônus abuse (5 jogadores) ────────────────────────────────────────

for _ in range(5):
    income = random.choice([2500, 3500, 4500])
    make_player("bonus_abuse", income)

# ── C6: PEP / alto risco (5 jogadores) ──────────────────────────────────

for i in range(5):
    income = random.choice([8000, 15000, 25000, 50000])
    pep = i < 3  # 3 são PEP, 2 são profissão de risco mas não PEP
    prof = random.choice(PROFESSIONS_PEP) if pep else "Empresário"
    make_player("pep_risk", income, pep=pep, profession=prof)


# ── Transaction generation ────────────────────────────────────────────────

transactions: list[dict] = []

def make_tx(
    pid: str,
    tx_type: str,
    amount: float,
    method: str,
    occurred_at: datetime,
    status: str = "SETTLED",
    bank_account: str = "",
    description: str = "",
) -> dict:
    t = {
        "external_transaction_id": tx_id(),
        "external_player_id": pid,
        "type": tx_type,
        "amount": f"{amount:.2f}",
        "currency": "BRL",
        "method": method,
        "status": status,
        "occurred_at": iso(occurred_at),
        "description": description,
        "bank_account_origin": bank_account,
    }
    transactions.append(t)
    return t


# ── C1 transactions: normais ─────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "normal":
        continue
    pid = p["external_player_id"]
    income = p["_income"]

    # 2–5 depósitos/mês × 3 meses  (~6–15 total)
    n_deposits = random.randint(6, 15)
    for _ in range(n_deposits):
        amt = random.uniform(100, min(income * 0.3, 3000))
        dt = rand_dt(T_START, NOW)
        method = random.choice(["PIX", "PIX", "PIX", "TED", "WALLET"])
        make_tx(pid, "DEPOSIT", amt, method, dt)

    # 0–3 saques
    n_withdrawals = random.randint(0, 3)
    for _ in range(n_withdrawals):
        amt = random.uniform(200, 2000)
        dt = rand_dt(T_START + timedelta(days=15), NOW)
        make_tx(pid, "WITHDRAWAL", amt, "PIX", dt)


# ── C2 transactions: renda incompatível (high roller) ────────────────────

for p in players:
    if p["_scenario"] != "income_mismatch":
        continue
    pid = p["external_player_id"]
    income = p["_income"]

    # Depósitos MUITO acima da renda: 3x–5x mensal, em 3 meses → total 9x–15x
    monthly_deposit = income * random.uniform(3, 5)
    n_deposits = random.randint(15, 30)
    per_deposit = monthly_deposit * 3 / n_deposits
    for _ in range(n_deposits):
        amt = per_deposit * random.uniform(0.6, 1.4)
        dt = rand_dt(T_START, NOW)
        method = random.choice(["PIX", "TED", "TED", "WALLET"])
        make_tx(pid, "DEPOSIT", amt, method, dt)

    # Saques frequentes
    n_withdrawals = random.randint(8, 15)
    for _ in range(n_withdrawals):
        amt = random.uniform(1000, 8000)
        dt = rand_dt(T_START + timedelta(days=7), NOW)
        make_tx(pid, "WITHDRAWAL", amt, "PIX", dt)


# ── C3 transactions: structuring ──────────────────────────────────────────

for p in players:
    if p["_scenario"] != "structuring":
        continue
    pid = p["external_player_id"]

    # Rafagas de depósitos pequenos (próximo de R$1.000) em janelas curtas
    n_bursts = random.randint(3, 6)
    for burst in range(n_bursts):
        burst_start = rand_dt(T_START, NOW - timedelta(days=5))
        n_in_burst = random.randint(8, 20)
        for i in range(n_in_burst):
            # Valores entre R$400 e R$990 (sempre abaixo de R$1000)
            amt = random.uniform(400, 990)
            dt = burst_start + timedelta(minutes=random.randint(5, 120) * (i + 1))
            method = random.choice(["PIX", "PIX", "TED"])
            make_tx(pid, "DEPOSIT", amt, method, dt)

    # Alguns saques grandes consolidando
    n_withdrawals = random.randint(3, 6)
    for _ in range(n_withdrawals):
        amt = random.uniform(5000, 15000)
        dt = rand_dt(T_START + timedelta(days=10), NOW)
        make_tx(pid, "WITHDRAWAL", amt, "PIX", dt)


# ── C4 transactions: rede suspeita ────────────────────────────────────────

for p in players:
    if p["_scenario"] != "network":
        continue
    pid = p["external_player_id"]
    cluster = p.get("_cluster", 0)
    shared_bank = SHARED_BANK_ACCOUNTS[cluster]

    n_deposits = random.randint(5, 12)
    for _ in range(n_deposits):
        amt = random.uniform(500, 5000)
        dt = rand_dt(T_START, NOW)
        method = "PIX"
        make_tx(pid, "DEPOSIT", amt, method, dt, bank_account=shared_bank)

    n_withdrawals = random.randint(2, 5)
    for _ in range(n_withdrawals):
        amt = random.uniform(1000, 8000)
        dt = rand_dt(T_START + timedelta(days=7), NOW)
        make_tx(pid, "WITHDRAWAL", amt, "PIX", dt, bank_account=shared_bank)


# ── C5 transactions: bônus abuse ──────────────────────────────────────────

for p in players:
    if p["_scenario"] != "bonus_abuse":
        continue
    pid = p["external_player_id"]

    # Depósito mínimo para ativar bônus
    for _ in range(random.randint(8, 15)):
        amt = random.uniform(50, 200)
        dt = rand_dt(T_START, NOW)
        make_tx(pid, "DEPOSIT", amt, "PIX", dt)

    # Muitos bônus/free_bets recebidos
    for _ in range(random.randint(10, 25)):
        amt = random.uniform(50, 500)
        dt = rand_dt(T_START, NOW)
        make_tx(pid, "BONUS", amt, "WALLET", dt, description="Bonus deposito")

    for _ in range(random.randint(5, 10)):
        amt = random.uniform(20, 100)
        dt = rand_dt(T_START, NOW)
        make_tx(pid, "FREE_BET", amt, "WALLET", dt, description="Free bet promo")

    # Cashouts rápidos (sacam logo após ganho)
    for _ in range(random.randint(8, 15)):
        amt = random.uniform(200, 2000)
        dt = rand_dt(T_START + timedelta(days=5), NOW)
        make_tx(pid, "CASHOUT", amt, "PIX", dt)

    # Saques frequentes
    for _ in range(random.randint(5, 10)):
        amt = random.uniform(300, 3000)
        dt = rand_dt(T_START + timedelta(days=10), NOW)
        make_tx(pid, "WITHDRAWAL", amt, "PIX", dt)


# ── C6 transactions: PEP ──────────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "pep_risk":
        continue
    pid = p["external_player_id"]
    income = p["_income"]

    # Dois PEPs com padrão normal (alto risco inerente)
    # Três com volume alto + structuring combinado
    is_combo = random.random() < 0.6

    if is_combo:
        # Volume alto + bursts
        for _ in range(random.randint(15, 25)):
            amt = random.uniform(2000, 20000)
            dt = rand_dt(T_START, NOW)
            method = random.choice(["PIX", "TED"])
            make_tx(pid, "DEPOSIT", amt, method, dt)
        for _ in range(random.randint(5, 10)):
            amt = random.uniform(5000, 30000)
            dt = rand_dt(T_START + timedelta(days=10), NOW)
            make_tx(pid, "WITHDRAWAL", amt, "TED", dt)
    else:
        # Normais
        for _ in range(random.randint(4, 8)):
            amt = random.uniform(500, 3000)
            dt = rand_dt(T_START, NOW)
            make_tx(pid, "DEPOSIT", amt, "PIX", dt)
        for _ in range(random.randint(1, 3)):
            amt = random.uniform(1000, 5000)
            dt = rand_dt(T_START + timedelta(days=15), NOW)
            make_tx(pid, "WITHDRAWAL", amt, "PIX", dt)


# ── Bet generation ────────────────────────────────────────────────────────

bets: list[dict] = []

def make_bet(
    pid: str,
    stake: float,
    odds: float,
    placed_at: datetime,
    status: str = "LOST",
    bet_type: str = "SPORTSBOOK",
    sport: str | None = None,
    market_type: str | None = None,
) -> dict:
    potential = round(stake * odds, 2)
    actual = potential if status == "WON" else (round(stake * random.uniform(0.3, 0.8), 2) if status == "CASHOUT" else 0)
    settled = placed_at + timedelta(hours=random.randint(1, 72)) if status != "PENDING" else None

    # Multi-modalidade: campos condicionais por product_type
    game_info: dict = {}
    if bet_type == "CASINO_LIVE":
        game = random.choice(CASINO_GAMES)
        game_info = {"game_id": game["id"], "game_name": game["name"],
                     "game_provider": game["provider"], "game_category": game["category"],
                     "rtp_teorico": f"{game['rtp']:.4f}"}
    elif bet_type in ("SLOT", "INSTANT_GAME"):
        game = random.choice(SLOT_GAMES)
        game_info = {"game_id": game["id"], "game_name": game["name"],
                     "game_provider": game["provider"], "game_category": game["category"],
                     "rtp_teorico": f"{game['rtp']:.4f}"}

    b = {
        "external_bet_id": bet_id(),
        "external_player_id": pid,
        "stake_amount": f"{stake:.2f}",
        "odds": f"{odds:.2f}" if bet_type == "SPORTSBOOK" else "",
        "potential_payout": f"{potential:.2f}",
        "settled_payout": f"{actual:.2f}",
        "bet_type": bet_type,
        "product_type": bet_type,
        "sport": sport or (random.choice(SPORTS) if bet_type == "SPORTSBOOK" else ""),
        "market_type": market_type or (random.choice(MARKET_TYPES) if bet_type == "SPORTSBOOK" else ""),
        "event_id": random.choice(EVENTS) if bet_type == "SPORTSBOOK" else "",
        "selection": random.choice(["Home Win", "Away Win", "Draw", "Over 2.5", "Under 2.5", "BTTS Yes"]) if bet_type == "SPORTSBOOK" else "",
        "channel": random.choice(CHANNELS),
        "placed_at": iso(placed_at),
        "settled_at": iso(settled) if settled else "",
        "status": status,
        **game_info,
    }
    bets.append(b)
    return b


# ── C1 bets: normais ─────────────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "normal":
        continue
    pid = p["external_player_id"]
    income = p["_income"]

    n_bets = random.randint(5, 25)
    for _ in range(n_bets):
        stake = random.uniform(10, min(income * 0.05, 500))
        odds = round(random.uniform(1.3, 8.0), 2)
        dt = rand_dt(T_START, NOW)
        status = random.choices(["WON", "LOST", "LOST", "LOST", "CASHOUT"], weights=[20, 55, 55, 55, 15])[0]
        make_bet(pid, stake, odds, dt, status)


# ── C2 bets: high roller ─────────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "income_mismatch":
        continue
    pid = p["external_player_id"]
    income = p["_income"]

    # Apostas muito altas em relação à renda
    n_bets = random.randint(20, 40)
    for _ in range(n_bets):
        stake = random.uniform(500, income * 2)  # stake > renda mensal inteira
        odds = round(random.uniform(1.5, 5.0), 2)
        dt = rand_dt(T_START, NOW)
        status = random.choices(["WON", "LOST", "LOST", "CASHOUT"], weights=[25, 50, 50, 15])[0]
        make_bet(pid, stake, odds, dt, status)


# ── C3 bets: structuring (apostas moderadas) ─────────────────────────────

for p in players:
    if p["_scenario"] != "structuring":
        continue
    pid = p["external_player_id"]

    n_bets = random.randint(10, 20)
    for _ in range(n_bets):
        stake = random.uniform(50, 400)
        odds = round(random.uniform(1.5, 4.0), 2)
        dt = rand_dt(T_START, NOW)
        status = random.choices(["WON", "LOST", "LOST"], weights=[30, 50, 50])[0]
        make_bet(pid, stake, odds, dt, status)


# ── C4 bets: rede ────────────────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "network":
        continue
    pid = p["external_player_id"]

    n_bets = random.randint(8, 18)
    for _ in range(n_bets):
        stake = random.uniform(100, 2000)
        odds = round(random.uniform(1.5, 6.0), 2)
        dt = rand_dt(T_START, NOW)
        status = random.choices(["WON", "LOST", "LOST", "CASHOUT"], weights=[20, 50, 50, 10])[0]
        make_bet(pid, stake, odds, dt, status)


# ── C5 bets: bônus abuse ─────────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "bonus_abuse":
        continue
    pid = p["external_player_id"]

    # Muitas apostas de baixo risco (odds baixas) usando bônus
    n_bets = random.randint(25, 50)
    for _ in range(n_bets):
        stake = random.uniform(20, 200)
        odds = round(random.uniform(1.05, 1.5), 2)  # odds muito baixas = "aposta segura"
        dt = rand_dt(T_START, NOW)
        bt = random.choice(["SPORTSBOOK", "CASINO_LIVE", "SLOT"])
        status = random.choices(["WON", "WON", "LOST", "CASHOUT"], weights=[40, 40, 30, 20])[0]
        make_bet(pid, stake, odds, dt, status, bet_type=bt)


# ── C6 bets: PEP ─────────────────────────────────────────────────────────

for p in players:
    if p["_scenario"] != "pep_risk":
        continue
    pid = p["external_player_id"]

    n_bets = random.randint(10, 30)
    for _ in range(n_bets):
        stake = random.uniform(200, 10000)
        odds = round(random.uniform(1.5, 6.0), 2)
        dt = rand_dt(T_START, NOW)
        status = random.choices(["WON", "LOST", "LOST", "CASHOUT"], weights=[25, 50, 50, 15])[0]
        make_bet(pid, stake, odds, dt, status)


# ── Device events generation ──────────────────────────────────────────────

device_events: list[dict] = []

def make_device_event(
    pid: str,
    device: str,
    ip: str,
    occurred_at: datetime,
    event_type: str = "LOGIN",
    device_type: str | None = None,
    country: str = "BR",
) -> dict:
    e = {
        "external_event_id": dev_id(),
        "external_player_id": pid,
        "device_id": device,
        "ip_address": ip,
        "event_type": event_type,
        "device_type": device_type or random.choice(DEVICE_TYPES),
        "country_code": country,
        "user_agent": f"Mozilla/5.0 ({random.choice(['iPhone', 'Android', 'Windows', 'Mac'])})",
        "session_id": f"sess-{uid()}",
        "occurred_at": iso(occurred_at),
    }
    device_events.append(e)
    return e

# Assign stable personal devices
for p in players:
    pid = p["external_player_id"]
    p["_device"] = f"dev-{pid.lower()}"
    p["_ip"] = f"192.168.{random.randint(1,254)}.{random.randint(1,254)}"

# ── C1/C2/C3/C5/C6 devices: cada jogador com seu dispositivo pessoal ────

for p in players:
    if p["_scenario"] == "network":
        continue
    pid = p["external_player_id"]
    device = p["_device"]
    ip = p["_ip"]

    n_events = random.randint(4, 12)
    for _ in range(n_events):
        dt = rand_dt(T_START, NOW)
        evt = random.choices(
            ["LOGIN", "BET", "DEPOSIT", "WITHDRAWAL"],
            weights=[40, 30, 20, 10],
        )[0]
        make_device_event(pid, device, ip, dt, evt)


# ── C4 devices: rede compartilhada ────────────────────────────────────────

for p in players:
    if p["_scenario"] != "network":
        continue
    pid = p["external_player_id"]
    cluster = p.get("_cluster", 0)
    shared_dev = SHARED_DEVICES[cluster]
    shared_ip = SHARED_IPS[cluster]
    personal_dev = p["_device"]
    personal_ip = p["_ip"]

    # 60% dos logins no dispositivo compartilhado, 40% pessoal
    n_events = random.randint(8, 18)
    for _ in range(n_events):
        dt = rand_dt(T_START, NOW)
        use_shared = random.random() < 0.6
        d = shared_dev if use_shared else personal_dev
        ip = shared_ip if use_shared else personal_ip
        evt = random.choices(
            ["LOGIN", "BET", "DEPOSIT", "WITHDRAWAL"],
            weights=[40, 30, 20, 10],
        )[0]
        make_device_event(pid, d, ip, dt, evt)


# ── Write CSVs ────────────────────────────────────────────────────────────

def write_csv(filename: str, rows: list[dict], fieldnames: list[str]):
    path = OUT_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"  ✓ {filename}: {len(rows)} rows → {path}")


PLAYER_FIELDS = [
    "external_player_id", "cpf", "name", "birth_date",
    "declared_income_monthly", "pep_flag", "status", "profession",
    "nationality", "registration_date", "self_exclusion_flag",
    "email", "phone", "deposit_limit_daily",
]

TX_FIELDS = [
    "external_transaction_id", "external_player_id", "type", "amount",
    "currency", "method", "status", "occurred_at", "description",
    "bank_account_origin",
]

BET_FIELDS = [
    "external_bet_id", "external_player_id", "stake_amount", "odds",
    "potential_payout", "settled_payout", "bet_type", "product_type",
    "sport", "market_type", "event_id", "selection", "channel",
    "placed_at", "settled_at", "status",
    "game_id", "game_name", "game_provider", "game_category", "rtp_teorico",
]

DEVICE_FIELDS = [
    "external_event_id", "external_player_id", "device_id",
    "ip_address", "event_type", "device_type", "country_code",
    "user_agent", "session_id", "occurred_at",
]


if __name__ == "__main__":
    print("Generating BetAML synthetic test data (fictibet_pld)...\n")

    # Sort transactions and bets chronologically
    transactions.sort(key=lambda r: r["occurred_at"])
    bets.sort(key=lambda r: r["placed_at"])
    device_events.sort(key=lambda r: r["occurred_at"])

    write_csv("players.csv", players, PLAYER_FIELDS)
    write_csv("transactions.csv", transactions, TX_FIELDS)
    write_csv("bets.csv", bets, BET_FIELDS)
    write_csv("device_events.csv", device_events, DEVICE_FIELDS)

    # Summary
    scenarios = {}
    for p in players:
        s = p["_scenario"]
        scenarios[s] = scenarios.get(s, 0) + 1

    print(f"\nSummary:")
    print(f"  Players:      {len(players)}")
    print(f"  Transactions: {len(transactions)}")
    print(f"  Bets:         {len(bets)}")
    print(f"  DeviceEvents: {len(device_events)}")
    print(f"\n  Per scenario:")
    for s, c in sorted(scenarios.items()):
        print(f"    {s}: {c} players")

    print(f"\nFiles written to {OUT_DIR}/")
