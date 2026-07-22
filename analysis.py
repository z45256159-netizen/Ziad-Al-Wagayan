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


# Weighted lexicon: term -> strength (positive = bullish, negative = bearish).
_LEXICON = {
    # strong bullish
    "beats": 2, "beat": 2, "surge": 2, "surges": 2, "record": 2, "soars": 2,
    "soar": 2, "upgrade": 2, "upgraded": 2, "raises guidance": 3, "raises": 2,
    "breakout": 2, "acquires": 2, "buyback": 2, "outperform": 2, "tops": 2,
    # mild bullish
    "growth": 1, "strong": 1, "rally": 1, "gains": 1, "gain": 1, "rises": 1,
    "rise": 1, "approval": 1, "approved": 1, "profit": 1, "expands": 1,
    "deal": 1, "partnership": 1, "wins": 1, "boost": 1, "higher": 1, "jumps": 1,
    "positive": 1, "bullish": 2, "buy rating": 2, "price target raised": 2,
    # strong bearish
    "misses": -2, "miss": -2, "plunge": -2, "plunges": -2, "crash": -3,
    "downgrade": -2, "downgraded": -2, "lawsuit": -2, "probe": -2, "recall": -2,
    "investigation": -2, "fraud": -3, "bankruptcy": -3, "layoffs": -2,
    "cuts guidance": -3, "guidance cut": -3, "sell rating": -2,
    # mild bearish
    "falls": -1, "fall": -1, "drop": -1, "drops": -1, "cuts": -1, "cut": -1,
    "warning": -1, "warn": -1, "weak": -1, "loss": -1, "losses": -1, "slump": -1,
    "declines": -1, "decline": -1, "concern": -1, "risk": -1, "halts": -1,
    "delay": -1, "lower": -1, "sinks": -1, "bearish": -2, "price target cut": -2,
}
_NEGATORS = ("no ", "not ", "n't", "without ", "avoids ", "denies ")


def news_sentiment(headlines):
    """
    Read whether news leans BULLISH / BEARISH / NEUTRAL (weighted, with simple
    negation), from Alpaca/Benzinga headlines.
    Returns (label, emoji, score). label in BULLISH/BEARISH/NEUTRAL/NONE.
    """
    if not headlines:
        return ("NONE", "⚪", 0)
    total = 0
    for headline, _ in headlines:
        h = " " + headline.lower() + " "
        for term, weight in _LEXICON.items():
            idx = h.find(term)
            while idx != -1:
                # crude negation: flip weight if a negator sits just before it
                pre = h[max(0, idx - 8):idx]
                w = -weight if any(neg in pre for neg in _NEGATORS) else weight
                total += w
                idx = h.find(term, idx + len(term))
    if total >= 2:
        return ("BULLISH", "🟢", total)
    if total <= -2:
        return ("BEARISH", "🔴", total)
    return ("NEUTRAL", "🟡", total)


def _perf(closes, back):
    if len(closes) <= back or closes[-back - 1] <= 0:
        base = closes[0]
    else:
        base = closes[-back - 1]
    return (closes[-1] - base) / base * 100 if base > 0 else 0.0


def _max_drawdown(closes) -> float:
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = min(mdd, (c - peak) / peak * 100 if peak else 0.0)
    return mdd


@dataclass
class InvestAnalysis:
    perf_pct: float          # ~3-month price change, %
    perf_1y_pct: float       # ~12-month change, %
    rel_strength: float      # stock 3m return minus benchmark 3m return, %
    verdict: str             # STRONG BUY / BUY / HOLD / AVOID
    score: int               # 0-100 quality score
    reasons: List[str]
    horizon: str
    exp_return_pct: float
    downside_pct: float
    reason: str


def invest_analysis(bars: List[Bar], atr: float,
                    benchmark_bars: Optional[List[Bar]] = None) -> InvestAnalysis:
    """
    A long-term quality read from price behaviour: trend structure, 3m & 1y
    performance, relative strength vs the market (SPY), volatility and drawdown.
    Estimates for learning — not forecasts, and not a substitute for fundamentals.
    """
    closes = [b.close for b in bars]
    n = len(closes)
    last = closes[-1]
    perf_3m = _perf(closes, 60)
    perf_1y = _perf(closes, min(252, n - 1))
    sma50 = sum(closes[-50:]) / min(50, n)
    sma200 = sum(closes[-200:]) / min(200, n)
    above_50 = last > sma50
    above_200 = last > sma200
    uptrend = above_50 and above_200 and sma50 >= sma200
    atr_pct = (atr / last * 100) if last > 0 else 2.0
    mdd = _max_drawdown(closes[-min(252, n):])

    rel = 0.0
    if benchmark_bars:
        b_closes = [b.close for b in benchmark_bars]
        rel = perf_3m - _perf(b_closes, 60)

    # Quality score (0-100) with reasons.
    score = 45
    reasons = []
    if uptrend:
        score += 18; reasons.append("✅ long-term uptrend (above rising 50 & 200-day averages)")
    elif above_50:
        score += 6; reasons.append("🟡 above the 50-day average but not clearly trending")
    else:
        score -= 18; reasons.append("⚠️ below its 50-day average (downtrend)")
    if rel > 3:
        score += 14; reasons.append(f"✅ outperforming the market by {rel:+.0f}% (3m)")
    elif rel < -3:
        score -= 10; reasons.append(f"⚠️ lagging the market by {rel:+.0f}% (3m)")
    if perf_1y > 15:
        score += 8; reasons.append(f"✅ strong 1-year return ({perf_1y:+.0f}%)")
    elif perf_1y < -10:
        score -= 8; reasons.append(f"⚠️ negative 1-year return ({perf_1y:+.0f}%)")
    if mdd < -35:
        score -= 8; reasons.append(f"⚠️ deep past drawdown ({mdd:.0f}%) — volatile")
    if atr_pct > 4:
        score -= 4; reasons.append("⚠️ high volatility")

    score = max(0, min(100, int(score)))
    if score >= 75 and uptrend:
        verdict = "STRONG BUY"
    elif score >= 60:
        verdict = "BUY"
    elif score >= 45:
        verdict = "HOLD"
    else:
        verdict = "AVOID"

    exp_return = max(5.0, min(perf_1y * 0.3 + rel * 0.3, 30.0)) if uptrend else 4.0
    downside = max(8.0, min(abs(mdd) * 0.4 + atr_pct * 3, 25.0))
    horizon = "months (long-term hold)"
    reason = (f"3-month {perf_3m:+.0f}%, 1-year {perf_1y:+.0f}%, "
              f"{'above' if above_200 else 'below'} its 200-day average; "
              f"{'leads' if rel >= 0 else 'lags'} the market by {rel:+.0f}%.")
    return InvestAnalysis(round(perf_3m, 1), round(perf_1y, 1), round(rel, 1),
                          verdict, score, reasons, horizon,
                          round(exp_return, 1), round(downside, 1), reason)
