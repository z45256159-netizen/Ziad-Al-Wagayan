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
    "double bottom": ("two swing lows at about the same price forming support",
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
    "double top": ("two swing highs at about the same price forming resistance",
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


def explain_setup(label: str, support: float, resistance: float, entry: float,
                  marks):
    """
    Real, level-specific analysis of THIS stock — cites the actual swing-point
    prices, support, and resistance rather than a generic shape description.
    Returns (what, why, how) or None for an unrecognized label.
    """
    low = label.lower()
    pts = [m[1] for m in (marks or [])]

    def lvls(default):
        return ", ".join(f"${p:,.2f}" for p in pts) if pts else default

    if "bottom" in low:  # double / triple bottom
        return (
            f"{len(pts) or 2} swing lows near {lvls(f'${support:,.2f}')} held as "
            f"support around ${support:,.2f}, and price has recovered to ${entry:,.2f}",
            f"repeated lows at the same level show buyers defending it; reclaiming "
            f"the level confirms the reversal",
            f"long above the base, stop below ${support:,.2f}, target the prior "
            f"high near ${resistance:,.2f}")
    if "top" in low and "triangle" not in low:  # double / triple top
        return (
            f"{len(pts) or 2} swing highs near {lvls(f'${resistance:,.2f}')} were "
            f"rejected at resistance ${resistance:,.2f}, with price now at ${entry:,.2f}",
            f"repeated highs at the same level show sellers defending it; failing "
            f"there signals a turn down",
            f"short below the pattern, stop above ${resistance:,.2f}, target the "
            f"prior low near ${support:,.2f}")
    if "head" in low:  # head & shoulders
        if len(pts) >= 3:
            what = (f"three peaks — left shoulder ${pts[0]:,.2f}, head "
                    f"${pts[1]:,.2f} (highest), right shoulder ${pts[2]:,.2f} — "
                    f"over a neckline near ${support:,.2f}")
        else:
            what = (f"three peaks with the middle highest over a neckline near "
                    f"${support:,.2f}")
        return (what,
                "the lower right shoulder shows momentum fading after the head",
                f"short on a break below the neckline ${support:,.2f}, stop above "
                f"the right shoulder, target a measured move down")
    if "ascending triangle" in low:
        return (f"a flat resistance near ${resistance:,.2f} with rising lows up to "
                f"${entry:,.2f}",
                "buyers keep paying higher prices while sellers cap one level — "
                "pressure builds for an upside break",
                f"long on a break above ${resistance:,.2f}, stop under the last "
                f"higher low, target a measured move up")
    if "descending triangle" in low:
        return (f"a flat support near ${support:,.2f} with falling highs down to "
                f"${entry:,.2f}",
                "sellers keep pressing lower while buyers defend one level — "
                "pressure builds for a downside break",
                f"short on a break below ${support:,.2f}, stop above the last "
                f"lower high, target a measured move down")
    if "breakout" in low:
        return (f"price reached ${entry:,.2f}, clearing recent resistance at "
                f"${resistance:,.2f}",
                "with no sellers left overhead, breakouts often keep running",
                f"long on the break, stop back below ${resistance:,.2f}, target "
                f"a continuation higher")
    if "breakdown" in low:
        return (f"price fell to ${entry:,.2f}, losing recent support at "
                f"${support:,.2f}",
                "with the floor gone, breakdowns often keep falling",
                f"short the break, stop back above ${support:,.2f}, target lower")
    if "bull flag" in low:
        return (f"a shallow pullback to ${entry:,.2f} after a run up, holding "
                f"above ${support:,.2f}",
                "a brief rest inside an uptrend usually resolves upward",
                f"long as it turns up, stop below ${support:,.2f}, target "
                f"${resistance:,.2f}")
    if "bear flag" in low:
        return (f"a shallow bounce to ${entry:,.2f} after a drop, capped below "
                f"${resistance:,.2f}",
                "a brief bounce inside a downtrend usually resolves downward",
                f"short as it rolls over, stop above ${resistance:,.2f}, target "
                f"${support:,.2f}")
    if "uptrend" in low:
        return (f"higher highs and higher lows, price ${entry:,.2f} above support "
                f"${support:,.2f}",
                "the trend is up; pullbacks tend to get bought",
                f"long, stop under the last higher low near ${support:,.2f}, "
                f"target the highs near ${resistance:,.2f}")
    if "downtrend" in low:
        return (f"lower highs and lower lows, price ${entry:,.2f} below resistance "
                f"${resistance:,.2f}",
                "the trend is down; bounces tend to get sold",
                f"short, stop over the last lower high near ${resistance:,.2f}, "
                f"target the lows near ${support:,.2f}")
    return None


_POS_WORDS = ("beat", "beats", "surge", "surges", "record", "jumps", "jump",
              "upgrade", "upgraded", "raises", "raise", "growth", "strong",
              "rally", "wins", "win", "approval", "approved", "gains", "gain",
              "soars", "soar", "outperform", "buy", "high", "boost", "profit",
              "tops", "rises", "rise", "positive", "expands", "deal")
_NEG_WORDS = ("miss", "misses", "falls", "fall", "plunge", "plunges", "drop",
              "drops", "cuts", "cut", "downgrade", "downgraded", "lawsuit",
              "probe", "recall", "warning", "warn", "weak", "loss", "losses",
              "slump", "layoffs", "investigation", "sell", "sinks", "sink",
              "declines", "decline", "concern", "risk", "halts", "delay")


def news_sentiment(headlines):
    """Rough good/bad read of news from headline keywords (heuristic).
    Returns (label, emoji, score). label in GOOD/MIXED/BAD/NONE."""
    if not headlines:
        return ("NONE", "⚪", 0)
    text = " ".join(h.lower() for h, _ in headlines)
    pos = sum(text.count(w) for w in _POS_WORDS)
    neg = sum(text.count(w) for w in _NEG_WORDS)
    score = pos - neg
    if score > 0:
        return ("GOOD", "🟢", score)
    if score < 0:
        return ("BAD", "🔴", score)
    return ("MIXED", "🟡", score)


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
