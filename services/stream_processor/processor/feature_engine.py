"""Feature computation engine for player risk features."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from dateutil.parser import parse as parse_dt

logger = logging.getLogger(__name__)


class FeatureEngine:
    """Stateless engine that computes player features from passed-in data."""

    def compute_player_features(
        self,
        tenant_id: str,
        player_id: str,
        transactions: list[dict[str, Any]],
        bets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute all player features for the given window of transactions and bets.

        Parameters
        ----------
        tenant_id:
            Tenant identifier (used to namespace the output).
        player_id:
            Player identifier.
        transactions:
            List of transaction dicts (last 30d recommended).  Each dict must
            contain ``amount`` (numeric), ``transaction_type`` or ``type``
            (str), and ``occurred_at`` or ``occurredAt`` (datetime or ISO str).
        bets:
            List of bet dicts.  Each must contain ``stake_amount`` or
            ``stakeAmount`` (numeric) and ``placed_at`` or ``placedAt``
            (datetime or ISO str).

        Returns
        -------
        dict
            Feature dict with all computed features as native Python types
            (float, int, bool) – safe for JSON serialisation.
        """
        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_7d = now - timedelta(days=7)
        cutoff_30d = now - timedelta(days=30)

        # ------------------------------------------------------------------ #
        # Helpers
        # ------------------------------------------------------------------ #

        def _to_dt(val: Any) -> datetime:
            if isinstance(val, datetime):
                return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            if isinstance(val, str):
                dt = parse_dt(val)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return now

        def _to_decimal(val: Any) -> Decimal:
            try:
                return Decimal(str(val))
            except Exception:
                return Decimal("0")

        def _tx_type(tx: dict) -> str:
            return str(tx.get("transaction_type") or tx.get("type") or "").upper()

        def _tx_dt(tx: dict) -> datetime:
            return _to_dt(tx.get("occurred_at") or tx.get("occurredAt"))

        def _bet_dt(bet: dict) -> datetime:
            return _to_dt(bet.get("placed_at") or bet.get("placedAt"))

        def _bet_stake(bet: dict) -> Decimal:
            return _to_decimal(bet.get("stake_amount") or bet.get("stakeAmount") or 0)

        # ------------------------------------------------------------------ #
        # Transaction window filters
        # ------------------------------------------------------------------ #

        def _filter_tx(cutoff: datetime, tx_type: str | None = None) -> list[dict]:
            return [
                tx for tx in transactions
                if _tx_dt(tx) >= cutoff and (tx_type is None or _tx_type(tx) == tx_type)
            ]

        deposits_24h = _filter_tx(cutoff_24h, "DEPOSIT")
        deposits_7d = _filter_tx(cutoff_7d, "DEPOSIT")
        deposits_30d = _filter_tx(cutoff_30d, "DEPOSIT")
        withdrawals_24h = _filter_tx(cutoff_24h, "WITHDRAWAL")
        withdrawals_7d = _filter_tx(cutoff_7d, "WITHDRAWAL")

        deposit_sum_24h = sum(_to_decimal(tx.get("amount", 0)) for tx in deposits_24h)
        deposit_sum_7d = sum(_to_decimal(tx.get("amount", 0)) for tx in deposits_7d)
        deposit_sum_30d = sum(_to_decimal(tx.get("amount", 0)) for tx in deposits_30d)
        deposit_count_24h = len(deposits_24h)
        deposit_count_7d = len(deposits_7d)
        withdrawal_sum_24h = sum(_to_decimal(tx.get("amount", 0)) for tx in withdrawals_24h)
        withdrawal_sum_7d = sum(_to_decimal(tx.get("amount", 0)) for tx in withdrawals_7d)

        # ------------------------------------------------------------------ #
        # Bet window filters
        # ------------------------------------------------------------------ #

        bets_24h = [b for b in bets if _bet_dt(b) >= cutoff_24h]
        bets_7d = [b for b in bets if _bet_dt(b) >= cutoff_7d]
        bet_stake_sum_24h = sum(_bet_stake(b) for b in bets_24h)
        bet_stake_sum_7d = sum(_bet_stake(b) for b in bets_7d)

        # ------------------------------------------------------------------ #
        # Derived ratios
        # ------------------------------------------------------------------ #

        ratio_withdrawal_to_deposit_7d = (
            float(withdrawal_sum_7d / deposit_sum_7d) if deposit_sum_7d > 0 else 0.0
        )

        # ------------------------------------------------------------------ #
        # Baseline stats: daily deposit aggregates over 30d
        # ------------------------------------------------------------------ #

        daily_deposits: dict[str, Decimal] = {}
        for tx in deposits_30d:
            day_key = _tx_dt(tx).strftime("%Y-%m-%d")
            daily_deposits[day_key] = daily_deposits.get(day_key, Decimal("0")) + _to_decimal(
                tx.get("amount", 0)
            )

        daily_values = list(daily_deposits.values())
        if daily_values:
            baseline_avg_daily_deposit = float(sum(daily_values) / len(daily_values))
            if len(daily_values) > 1:
                mean_d = sum(daily_values) / len(daily_values)
                variance = sum((v - mean_d) ** 2 for v in daily_values) / len(daily_values)
                baseline_stddev_deposit = float(Decimal(str(math.sqrt(float(variance)))))
            else:
                baseline_stddev_deposit = 0.0
        else:
            baseline_avg_daily_deposit = 0.0
            baseline_stddev_deposit = 0.0

        # Z-score: today's deposit vs baseline
        today_key = now.strftime("%Y-%m-%d")
        current_day_deposit = float(daily_deposits.get(today_key, Decimal("0")))
        if baseline_stddev_deposit > 0:
            zscore_current_deposit_vs_baseline = (
                current_day_deposit - baseline_avg_daily_deposit
            ) / baseline_stddev_deposit
        else:
            zscore_current_deposit_vs_baseline = 0.0

        # ------------------------------------------------------------------ #
        # Payment instrument novelty
        # ------------------------------------------------------------------ #

        new_payment_instrument_flag = False
        if len(transactions) > 1:
            sorted_tx = sorted(transactions, key=_tx_dt)
            latest_fp = self._instrument_fingerprint(sorted_tx[-1])
            historical_fps = {self._instrument_fingerprint(t) for t in sorted_tx[:-1]}
            if latest_fp and latest_fp not in historical_fps:
                new_payment_instrument_flag = True

        # new_device_flag requires device_events data (not passed here)
        new_device_flag = False
        # shared_* require cross-player aggregation (not available here)
        shared_device_count = 0
        shared_bank_account_count = 0

        # ------------------------------------------------------------------ #
        # Derived ratio features (used by DSL rules to avoid arithmetic ops)
        # ------------------------------------------------------------------ #

        # bet_stake_sum_7d / (baseline_avg_daily_deposit * 7) → ratio > 3 = spike
        weekly_baseline = baseline_avg_daily_deposit * 7
        bet_stake_vs_7d_deposit_baseline = (
            float(bet_stake_sum_7d) / weekly_baseline if weekly_baseline > 0 else 0.0
        )

        # deposit_sum_24h / baseline_avg_daily_deposit → ratio > 5 = spike
        deposit_24h_vs_baseline = (
            float(deposit_sum_24h) / baseline_avg_daily_deposit
            if baseline_avg_daily_deposit > 0
            else 0.0
        )

        return {
            "tenant_id": str(tenant_id),
            "player_id": str(player_id),
            "deposit_sum_24h": float(deposit_sum_24h),
            "deposit_sum_7d": float(deposit_sum_7d),
            "deposit_sum_30d": float(deposit_sum_30d),
            "deposit_count_24h": deposit_count_24h,
            "deposit_count_7d": deposit_count_7d,
            "withdrawal_sum_24h": float(withdrawal_sum_24h),
            "withdrawal_sum_7d": float(withdrawal_sum_7d),
            "bet_stake_sum_24h": float(bet_stake_sum_24h),
            "bet_stake_sum_7d": float(bet_stake_sum_7d),
            "ratio_withdrawal_to_deposit_7d": ratio_withdrawal_to_deposit_7d,
            "baseline_avg_daily_deposit": baseline_avg_daily_deposit,
            "baseline_stddev_deposit": baseline_stddev_deposit,
            "zscore_current_deposit_vs_baseline": zscore_current_deposit_vs_baseline,
            "new_payment_instrument_flag": new_payment_instrument_flag,
            "new_device_flag": new_device_flag,
            "shared_device_count": shared_device_count,
            "shared_bank_account_count": shared_bank_account_count,
            "bet_stake_vs_7d_deposit_baseline": bet_stake_vs_7d_deposit_baseline,
            "deposit_24h_vs_baseline": deposit_24h_vs_baseline,
        }

    @staticmethod
    def _instrument_fingerprint(tx: dict[str, Any]) -> str:
        """Return a stable fingerprint for the payment instrument of *tx*."""
        inst = tx.get("payment_instrument") or tx.get("paymentInstrument") or {}
        if isinstance(inst, dict):
            return (
                inst.get("id")
                or inst.get("fingerprint")
                or inst.get("accountNumber")
                or inst.get("token")
                or ""
            )
        return str(inst)
