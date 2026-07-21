"""
Position sizing & risk rules.

Kept separate from both the strategy (what to buy) and the broker (how to buy)
so the risk logic is easy to read and adjust in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SizingResult:
    """Outcome of sizing a proposed order."""

    qty: int                 # whole shares to trade (0 => don't trade)
    estimated_cost: float    # qty * price
    explanation: str         # human-readable description of how qty was chosen
    skipped_reason: Optional[str] = None  # set if we decided NOT to trade

    @property
    def ok(self) -> bool:
        return self.qty > 0 and self.skipped_reason is None


def size_position(
    price: float,
    buying_power: float,
    position_size_pct: float,
    max_order_dollars: float,
) -> SizingResult:
    """
    Decide how many whole shares to buy.

    Rules (in order):
      1. Target dollar amount = position_size_pct * buying_power.
      2. Cap that target at max_order_dollars (hard risk ceiling).
      3. Never spend more than we actually have (buying_power).
      4. Convert to WHOLE shares (floor). If that's 0, skip the trade.
    """
    if price <= 0:
        return SizingResult(0, 0.0, "", skipped_reason="Invalid price (<= 0).")

    if buying_power <= 0:
        return SizingResult(
            0, 0.0, "", skipped_reason="No buying power available."
        )

    target_dollars = position_size_pct * buying_power
    budget = min(target_dollars, max_order_dollars, buying_power)

    qty = int(budget // price)  # whole shares only
    if qty < 1:
        return SizingResult(
            0,
            0.0,
            "",
            skipped_reason=(
                f"Budget ${budget:,.2f} is less than one share at "
                f"${price:,.2f}."
            ),
        )

    estimated_cost = qty * price
    explanation = (
        f"{position_size_pct * 100:.1f}% of ${buying_power:,.2f} buying power "
        f"= ${target_dollars:,.2f} target, capped at ${max_order_dollars:,.2f} "
        f"max order -> budget ${budget:,.2f}. At ${price:,.2f}/share that is "
        f"{qty} whole share(s) (~${estimated_cost:,.2f})."
    )
    return SizingResult(qty=qty, estimated_cost=estimated_cost, explanation=explanation)
