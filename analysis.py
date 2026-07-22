"""
Extra analysis helpers:
  * explain_pattern() — plain-English "what it is / why it's a signal / how to
    trade it" for each detected chart pattern (used in Technical mode).
  * invest_analysis() — a long-term investing read for Best-performers mode:
    verdict, holding horizon, rough expected return, downside, and a reason.

The investing numbers are HEURISTIC estimates for learning — not predictions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from strategy import Bar

# what it is · why it's a signal · how you'd trade it
PATTERN_INFO = {
    "double bottom": ("a 'W' shape — price fell to about the same low twice and bounced",
                      "buyers keep defending that floor, so a move up is likely",
                      "enter as it turns up, stop just below the double low, target the prior high"),
    "triple bottom": ("three touches of about the same low",
                      "a very well-defended floor — reversal up is likely",
                      "enter on the bounce, stop below the lows, target the prior high"),
    "breakout": ("price pushed above its recent ceiling (resistance)",
                 "fresh highs on volume often keep running",
                 "enter on the break, stop back under the old ceiling, target a measured move up"),
    "bull flag": ("a small, calm pullback after a strong run up",
                  "trends usually resume after a short rest",
                  "enter as it turns back up, stop under the flag, target trend continuation"),
    "double top": ("an 'M' shape — price hit about the same high twice and rejected",
                   "sellers keep defending that ceiling, so a drop is likely",
                   "short the break down, stop above the highs, target the prior low"),
    "triple top": ("three rejections of about the same high",
                   "a very well-defended ceiling — reversal down is likely",
                   "short the breakdown, stop above the highs, target the prior low"),
    "head & shoulders": ("three peaks with the middle one highest",
                         "a classic topping pattern — momentum is fading",
                         "short the neckline break, stop above the right shoulder, target the measured move down"),
    "bear flag": ("a small bounce after a sharp drop",
                  "downtrends usually resume after a pause",
                  "short as it rolls over, stop above the bounce, target continuation down"),
    "breakdown": ("price broke below recent support (the floor)",
                  "fresh lows often keep falling",
                  "short the break, stop back above old support, target lower"),
    "uptrend": ("higher highs and higher lows",
                "the path of least resistance is up",
                "buy pullbacks, stop under the last swing low"),
    "downtrend": ("lower highs and lower lows",
                  "the path of least resistance is down",
                  "short bounces, stop over the last swing high"),
}


def explain_pattern(label: str):
    """Return (what, why, how) for a pattern label, or None."""
    low = label.lower()
    for key, info in PATTERN_INFO.items():
        if key in low:
            return info
    return None


@dataclass
class InvestAnalysis:
    perf_pct: float          # ~3-month price change, %
    verdict: str             # GOOD / OKAY / AVOID
    horizon: str             # suggested holding period
    exp_return_pct: float    # rough expected upside, %
    downside_pct: float      # rough downside risk, %
    reason: str


def invest_analysis(bars: List[Bar], atr: float) -> InvestAnalysis:
    """A rough long-term read from the price history. Estimates, not forecasts."""
    closes = [b.close for b in bars]
    n = len(closes)
    last = closes[-1]
    base = closes[-60] if n >= 60 else closes[0]
    perf = (last - base) / base * 100 if base > 0 else 0.0
    sma_slow = sum(closes[-50:]) / min(50, n)
    up = last > sma_slow
    atr_pct = (atr / last * 100) if last > 0 else 2.0

    if up and perf > 8:
        verdict = "GOOD"
    elif up:
        verdict = "OKAY"
    else:
        verdict = "AVOID"

    # Rough scenario numbers, clearly labeled as estimates elsewhere.
    exp_return = max(4.0, min(perf * 0.4, 25.0)) if up else 0.0
    downside = max(6.0, min(atr_pct * 4, 20.0))
    horizon = "a few weeks to a few months"
    reason = (f"{'Up' if up else 'Down'}trend vs its 50-day average; "
              f"{perf:+.0f}% over ~3 months; typical daily swing ~{atr_pct:.1f}%.")
    return InvestAnalysis(perf, verdict, horizon, exp_return, downside, reason)
