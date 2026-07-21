"""
Position sizing & the full trade plan.

Kept separate from both the strategy (what to buy) and the broker (how to buy)
so the risk logic is easy to read and adjust in one place.

The star function is `build_trade_plan()`: given a scored candidate and your
account, it produces a complete, trader-style plan — entry, a volatility-based
**stop-loss**, a **take-profit** at a fixed reward:risk ratio, how many shares
to buy (sized by risk), the dollar cost, the dollars at risk, the potential
profit, and the risk/reward ratio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from strategy import SymbolScore

# --- Risk model (edit to tune how the bot trades) ---
STOP_ATR_MULT = 1.5     # stop-loss = entry - 1.5 x ATR (wider for choppy stocks)
REWARD_RISK = 2.0       # take-profit aims for 2x the risk (a 1:2 reward:risk)
MIN_STOP_PCT = 0.005    # never place the stop tighter than 0.5% from entry


@dataclass
class TradePlan:
    symbol: str
    qty: int
    entry: float
    stop: float
    take_profit: float
    cost: float             # qty * entry
    risk_total: float       # dollars lost if the stop is hit
    reward_total: float     # dollars gained if the take-profit is hit
    rr_ratio: float         # reward / risk (e.g. 2.0 = 1:2)
    stop_pct: float         # stop distance as a % of entry
    tp_pct: float           # take-profit distance as a % of entry
    skipped_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.qty > 0 and self.skipped_reason is None


def _skip(symbol: str, reason: str) -> TradePlan:
    return TradePlan(symbol, 0, 0, 0, 0, 0, 0, 0, 0, 0, skipped_reason=reason)


def build_trade_plan(
    score: SymbolScore,
    buying_power: float,
    risk_pct: float,
    max_order_dollars: float,
    stop_atr_mult: float = STOP_ATR_MULT,
    reward_risk: float = REWARD_RISK,
) -> TradePlan:
    """
    Turn a scored candidate into a full trade plan.

    Sizing is RISK-BASED (how real traders do it): we risk a small fixed % of
    the account per trade, and buy as many shares as that budget allows given
    the distance to the stop-loss — then cap by the max order size and buying
    power. Tighter stop -> more shares; wider stop -> fewer.
    """
    entry = round(score.last_price, 2)
    if entry <= 0:
        return _skip(score.symbol, "Invalid price.")
    if buying_power <= 0:
        return _skip(score.symbol, "No buying power available.")

    # Volatility-based stop distance (with a minimum so it's never razor-thin).
    atr = score.atr if score.atr > 0 else entry * 0.01
    risk_per_share = max(stop_atr_mult * atr, entry * MIN_STOP_PCT)
    stop = round(entry - risk_per_share, 2)
    take_profit = round(entry + reward_risk * risk_per_share, 2)
    if stop <= 0:
        return _skip(score.symbol, "Stop-loss would be below zero.")

    # Risk-based share count, then cap by order size and buying power.
    risk_budget = risk_pct * buying_power
    qty_by_risk = int(risk_budget // risk_per_share)
    qty_by_cost = int(max_order_dollars // entry)
    qty_by_bp = int(buying_power // entry)
    qty = min(qty_by_risk, qty_by_cost, qty_by_bp)

    # Training-friendly: if the risk math rounds to 0 but a share is affordable,
    # take at least one.
    if qty < 1:
        if entry <= min(max_order_dollars, buying_power):
            qty = 1
        else:
            return _skip(score.symbol,
                         f"One share (${entry:,.2f}) exceeds the order cap / "
                         f"buying power.")

    cost = round(qty * entry, 2)
    risk_total = round(qty * risk_per_share, 2)
    reward_total = round(qty * (take_profit - entry), 2)
    rr_ratio = round(reward_total / risk_total, 2) if risk_total else 0.0

    return TradePlan(
        symbol=score.symbol,
        qty=qty,
        entry=entry,
        stop=stop,
        take_profit=take_profit,
        cost=cost,
        risk_total=risk_total,
        reward_total=reward_total,
        rr_ratio=rr_ratio,
        stop_pct=round((entry - stop) / entry * 100, 2),
        tp_pct=round((take_profit - entry) / entry * 100, 2),
    )


# ---------------------------------------------------------------------------
# Kept for the CLI (bot.py): simple % sizing without a stop/target.
# ---------------------------------------------------------------------------
@dataclass
class SizingResult:
    qty: int
    estimated_cost: float
    explanation: str
    skipped_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.qty > 0 and self.skipped_reason is None


def size_position(
    price: float,
    buying_power: float,
    position_size_pct: float,
    max_order_dollars: float,
) -> SizingResult:
    """Simple fixed-% sizing (used by the terminal CLI)."""
    if price <= 0:
        return SizingResult(0, 0.0, "", skipped_reason="Invalid price (<= 0).")
    if buying_power <= 0:
        return SizingResult(0, 0.0, "", skipped_reason="No buying power available.")

    budget = min(position_size_pct * buying_power, max_order_dollars, buying_power)
    qty = int(budget // price)
    if qty < 1:
        return SizingResult(
            0, 0.0, "",
            skipped_reason=(f"Budget ${budget:,.2f} is less than one share at "
                            f"${price:,.2f}."))
    estimated_cost = qty * price
    explanation = (
        f"{position_size_pct * 100:.1f}% of ${buying_power:,.2f} buying power, "
        f"capped at ${max_order_dollars:,.2f} -> {qty} share(s) "
        f"(~${estimated_cost:,.2f}).")
    return SizingResult(qty=qty, estimated_cost=estimated_cost, explanation=explanation)
