"""Default AML rule definitions to be seeded into the database.

Each dict maps directly to a ``rule_definitions`` row.  The ``condition_dsl``
strings use only operators supported by :mod:`dsl.parser` (comparisons,
``and``/``or``/``not``, field access, boolean literals).  Arithmetic
operations are expressed as pre-computed features in the feature engine.
"""

from __future__ import annotations

DEFAULT_RULES: list[dict] = [
    # 1 – Z-score deposit spike
    {
        "name": "zscore_deposit_spike",
        "description": (
            "Deposit amount deviates significantly from player's historical baseline "
            "(z-score > 3.0 standard deviations)."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "features.zscore_current_deposit_vs_baseline > 3.0",
        "params": {"zscore_threshold": 3.0},
        "version": 1,
    },
    # 2 – Structuring: many small deposits in 24 h
    {
        "name": "structuring_small_deposits",
        "description": (
            "10 or more deposits totalling over R$1,000 within 24 hours – "
            "classic structuring / smurfing pattern."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "features.deposit_count_24h >= 10 and features.deposit_sum_24h > 1000"
        ),
        "params": {"min_count": 10, "min_sum": 1000},
        "version": 1,
    },
    # 3 – Rapid withdrawal after deposit
    {
        "name": "rapid_withdrawal_after_deposit",
        "description": (
            "Withdrawal-to-deposit ratio above 80% within 7 days with meaningful "
            "deposit volume – suggests round-trip / money laundering."
        ),
        "status": "ACTIVE",
        "severity": "MEDIUM",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "features.ratio_withdrawal_to_deposit_7d > 0.8 "
            "and features.deposit_sum_7d > 500"
        ),
        "params": {"min_ratio": 0.8, "min_deposit_sum": 500},
        "version": 1,
    },
    # 4 – New payment instrument with high amount
    {
        "name": "new_instrument_high_amount",
        "description": (
            "First use of an unrecognised payment instrument combined with a "
            "transaction amount above R$5,000."
        ),
        "status": "ACTIVE",
        "severity": "MEDIUM",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "features.new_payment_instrument_flag == true "
            "and transaction.amount > 5000"
        ),
        "params": {"min_amount": 5000},
        "version": 1,
    },
    # 5 – PEP deviation
    {
        "name": "pep_deviation",
        "description": (
            "Politically exposed person shows unusual deposit activity "
            "(z-score > 2.0)."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "player.pepFlag == true "
            "and features.zscore_current_deposit_vs_baseline > 2.0"
        ),
        "params": {"zscore_threshold": 2.0},
        "version": 1,
    },
    # 6 – Shared bank account
    {
        "name": "shared_bank_account",
        "description": (
            "The player's bank account details are shared with more than 2 "
            "other players – possible money-mule network."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "features.shared_bank_account_count > 2",
        "params": {"max_shared": 2},
        "version": 1,
    },
    # 7 – Shared device with multiple CPFs
    {
        "name": "shared_device_multiple_cpf",
        "description": (
            "A single device is associated with more than 3 distinct player "
            "accounts – indicates coordinated account farming."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "DEVICE_EVENT",
        "condition_dsl": "features.shared_device_count > 3",
        "params": {"max_shared": 3},
        "version": 1,
    },
    # 8 – High withdrawal ratio
    {
        "name": "high_withdrawal_ratio",
        "description": (
            "Withdrawal-to-deposit ratio exceeds 90% over the last 7 days."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": "features.ratio_withdrawal_to_deposit_7d > 0.9",
        "params": {"max_ratio": 0.9},
        "version": 1,
    },
    # 9 – Betting stake spike vs deposit baseline
    # Uses pre-computed feature `bet_stake_vs_7d_deposit_baseline`
    # = bet_stake_sum_7d / (baseline_avg_daily_deposit * 7)
    {
        "name": "stake_spike_7d",
        "description": (
            "7-day cumulative bet stakes exceed 3× the expected 7-day deposit "
            "baseline – indicates sudden high-risk betting."
        ),
        "status": "ACTIVE",
        "severity": "MEDIUM",
        "scope": "BET",
        "condition_dsl": "features.bet_stake_vs_7d_deposit_baseline > 3.0",
        "params": {"multiplier": 3.0},
        "version": 1,
    },
    # 10 – Chargebacks above normal
    {
        "name": "chargebacks_above_normal",
        "description": (
            "Multiple deposit attempts in 24 hours combined with a z-score spike "
            "suggests chargeback fraud pattern."
        ),
        "status": "ACTIVE",
        "severity": "CRITICAL",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "features.deposit_count_24h >= 3 "
            "and features.zscore_current_deposit_vs_baseline > 2.0"
        ),
        "params": {"min_count": 3, "zscore_threshold": 2.0},
        "version": 1,
    },
    # 11 – Failed deposit then large success
    # Uses pre-computed feature `deposit_24h_vs_baseline` = deposit_sum_24h / baseline
    {
        "name": "failed_deposit_then_large",
        "description": (
            "Today's deposit sum is more than 5× the player's daily baseline "
            "with at least 3 deposit attempts – indicates failed-then-success "
            "fraud pattern."
        ),
        "status": "ACTIVE",
        "severity": "MEDIUM",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "features.deposit_24h_vs_baseline > 5.0 "
            "and features.deposit_count_24h > 3"
        ),
        "params": {"deposit_multiplier": 5.0, "min_attempts": 3},
        "version": 1,
    },
    # 12 – Round-trip pattern
    {
        "name": "round_trip_pattern",
        "description": (
            "High withdrawal-to-deposit ratio (> 85%) with a meaningful 7-day "
            "deposit sum – classic round-trip money-laundering indicator."
        ),
        "status": "ACTIVE",
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "condition_dsl": (
            "features.ratio_withdrawal_to_deposit_7d > 0.85 "
            "and features.deposit_sum_7d > 500"
        ),
        "params": {"min_ratio": 0.85, "min_deposit_sum": 500},
        "version": 1,
    },
]
