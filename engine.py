"""
engine.py — the professional trading core.

Everything a trade decision needs, built from price/volume only (the data we
actually have), done properly:

  * Wilder-smoothed indicators: ATR, RSI, ADX/DI, EMA/SMA.
  * Swing structure (pivot highs/lows) for market-structure stops.
  * Market-regime detection (trending / ranging, high / low volatility) so the
    logic can adapt instead of using one fixed rule.
  * build_setup(): structure + ATR based stop-loss, a target at real structure,
    a hard minimum reward:risk gate (reject, don't force), risk-based sizing,
    a validated plan (a BUY stop is NEVER above entry; a SELL stop is NEVER
    below entry), and a confidence score with plain reasons.

Nothing here talks to the network or Streamlit — it's pure and unit-tested.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from strategy import Bar

log = logging.getLogger("bot.engine")

# --- Risk policy (tune here) ---
MIN_RR = 1.6            # reject any trade below this reward:risk
DEFAULT_TARGET_RR = 2.0  # measured-move target when no real structure is found
ATR_STOP_MULT = 1.5     # ATR buffer beyond structure for the stop
MIN_STOP_PCT = 0.004    # stop never tighter than 0.4% (avoid noise stop-outs)
MAX_STOP_PCT = 0.15     # stop never wider than 15% (sanity)
SWING_LOOKBACK = 12     # bars to search for the protective swing point
STRUCT_LOOKBACK = 30    # bars to search for the target structure


# ============================================================ indicators
def _hi(b: Bar) -> float:
    return b.high if b.high is not None else b.close


def _lo(b: Bar) -> float:
    return b.low if b.low is not None else b.close


def sma(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ema_last(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _true_ranges(bars: List[Bar]) -> List[float]:
    trs = []
    for i in range(1, len(bars)):
        h, lo, pc = _hi(bars[i]), _lo(bars[i]), bars[i - 1].close
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return trs


def atr(bars: List[Bar], period: int = 14) -> float:
    """Wilder-smoothed Average True Range."""
    trs = _true_ranges(bars)
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def rsi(closes: Sequence[float], period: int = 14) -> float:
    """Wilder RSI."""
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def adx(bars: List[Bar], period: int = 14) -> Tuple[float, float, float]:
    """Return (ADX, +DI, -DI). ADX>25 ~ trending, <18 ~ ranging."""
    if len(bars) < period * 2:
        return (0.0, 0.0, 0.0)
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(bars)):
        up = _hi(bars[i]) - _hi(bars[i - 1])
        down = _lo(bars[i - 1]) - _lo(bars[i])
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        pc = bars[i - 1].close
        trs.append(max(_hi(bars[i]) - _lo(bars[i]),
                       abs(_hi(bars[i]) - pc), abs(_lo(bars[i]) - pc)))

    def wilder(seq: List[float]) -> List[float]:
        s = sum(seq[:period])
        out = [s]
        for v in seq[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    atr_s, pdm_s, mdm_s = wilder(trs), wilder(plus_dm), wilder(minus_dm)
    dx = []
    for a, p, m in zip(atr_s, pdm_s, mdm_s):
        if a == 0:
            dx.append(0.0)
            continue
        pdi, mdi = 100 * p / a, 100 * m / a
        denom = pdi + mdi
        dx.append(100 * abs(pdi - mdi) / denom if denom else 0.0)
    if not dx:
        return (0.0, 0.0, 0.0)
    if len(dx) < period:
        adx_v = sum(dx) / len(dx)
    else:
        adx_v = sum(dx[:period]) / period
        for d in dx[period:]:
            adx_v = (adx_v * (period - 1) + d) / period
    last_atr = atr_s[-1] if atr_s else 0.0
    pdi = 100 * pdm_s[-1] / last_atr if last_atr else 0.0
    mdi = 100 * mdm_s[-1] / last_atr if last_atr else 0.0
    return (adx_v, pdi, mdi)


# ============================================================ structure
def swing_lows(bars: List[Bar], left: int = 2, right: int = 2) -> List[Tuple[int, float]]:
    lows = [_lo(b) for b in bars]
    out = []
    for i in range(left, len(lows) - right):
        if lows[i] == min(lows[i - left:i + right + 1]):
            out.append((i, lows[i]))
    return out


def swing_highs(bars: List[Bar], left: int = 2, right: int = 2) -> List[Tuple[int, float]]:
    highs = [_hi(b) for b in bars]
    out = []
    for i in range(left, len(highs) - right):
        if highs[i] == max(highs[i - left:i + right + 1]):
            out.append((i, highs[i]))
    return out


def protective_swing_low(bars: List[Bar], lookback: int = SWING_LOOKBACK) -> float:
    """Lowest low over the recent window — the natural stop area for a long."""
    window = bars[-lookback:] if len(bars) >= lookback else bars
    return min(_lo(b) for b in window)


def protective_swing_high(bars: List[Bar], lookback: int = SWING_LOOKBACK) -> float:
    window = bars[-lookback:] if len(bars) >= lookback else bars
    return max(_hi(b) for b in window)


# ============================================================ regime
@dataclass
class Regime:
    label: str        # e.g. "up-trending", "ranging"
    bias: str         # up / down / mixed
    trend: str        # trending / weak / ranging
    volatility: str   # high / normal / low
    adx: float
    atr_pct: float

    @property
    def is_trending(self) -> bool:
        return self.trend == "trending"


def detect_regime(bars: List[Bar]) -> Regime:
    closes = [b.close for b in bars]
    last = closes[-1]
    adx_v, pdi, mdi = adx(bars)
    a = atr(bars)
    atr_pct = (a / last * 100) if last else 0.0

    if adx_v >= 25:
        trend = "trending"
    elif adx_v < 18:
        trend = "ranging"
    else:
        trend = "weak"

    sma20 = sma(closes[-20:]) if len(closes) >= 20 else sma(closes)
    sma50 = sma(closes[-50:]) if len(closes) >= 50 else sma(closes)
    if last > sma20 > sma50 and pdi >= mdi:
        bias = "up"
    elif last < sma20 < sma50 and mdi > pdi:
        bias = "down"
    else:
        bias = "mixed"

    volatility = "high" if atr_pct > 4 else "low" if atr_pct < 1.5 else "normal"
    label = f"{bias}-{trend}" if trend != "ranging" else "ranging"
    return Regime(label, bias, trend, volatility, round(adx_v, 1), round(atr_pct, 2))


# ============================================================ setup
@dataclass
class Setup:
    symbol: str
    direction: str            # long / short
    side: str                 # buy / sell
    entry: float
    stop: float
    target: float
    qty: int
    risk_per_share: float
    risk_total: float
    reward_total: float
    rr: float
    stop_pct: float
    tp_pct: float
    confidence: int           # 0-100
    reasons: List[str] = field(default_factory=list)
    regime: str = ""
    limit_price: Optional[float] = None
    rejected: bool = False
    reject_reason: str = ""

    @property
    def ok(self) -> bool:
        return (not self.rejected) and self.qty > 0


def _reject(symbol: str, reason: str, regime: str = "") -> Setup:
    return Setup(symbol, "long", "buy", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                 reasons=[], regime=regime, rejected=True, reject_reason=reason)


def _confidence(direction: str, regime: Regime, rr: float,
                rsi_v: float, vol_ratio: float, news_score: int,
                struct_target: bool) -> Tuple[int, List[str]]:
    """Confidence 0-100 with plain reasons for WHY."""
    score = 40
    reasons = []
    # 1) Trade aligned with the regime bias
    if regime.bias == "up" and direction == "long":
        score += 18; reasons.append("✅ trades WITH an uptrend")
    elif regime.bias == "down" and direction == "short":
        score += 18; reasons.append("✅ trades WITH a downtrend")
    elif regime.bias == "mixed":
        score -= 5; reasons.append("⚠️ no clear trend (mixed) — lower edge")
    else:
        score -= 12; reasons.append("⚠️ trades AGAINST the prevailing trend")
    # 2) Trend strength
    if regime.is_trending:
        score += 12; reasons.append(f"✅ strong trend (ADX {regime.adx})")
    elif regime.trend == "ranging":
        score -= 6; reasons.append(f"⚠️ choppy/ranging market (ADX {regime.adx})")
    # 3) Reward:risk
    if rr >= 2.5:
        score += 12; reasons.append(f"✅ excellent reward:risk 1:{rr:g}")
    elif rr >= MIN_RR:
        score += 6; reasons.append(f"✅ acceptable reward:risk 1:{rr:g}")
    # 4) Volume conviction
    if vol_ratio >= 1.5:
        score += 8; reasons.append(f"✅ heavy volume ({vol_ratio:.1f}x avg)")
    elif vol_ratio < 0.8:
        score -= 6; reasons.append("⚠️ light volume — weak conviction")
    # 5) Momentum not exhausted
    if direction == "long" and rsi_v > 78:
        score -= 8; reasons.append(f"⚠️ overbought (RSI {rsi_v:.0f})")
    elif direction == "short" and rsi_v < 22:
        score -= 8; reasons.append(f"⚠️ oversold (RSI {rsi_v:.0f})")
    # 6) Volatility sanity
    if regime.volatility == "high":
        score -= 5; reasons.append("⚠️ high volatility — wider risk")
    # 7) Target sits at real structure
    if struct_target:
        score += 6; reasons.append("✅ target set at real chart structure")
    # 8) News
    if news_score > 0:
        score += 6; reasons.append("✅ recent news leans supportive")
    elif news_score < 0:
        score -= 8; reasons.append("⚠️ recent news leans negative")
    return (max(0, min(100, int(score))), reasons)


def build_setup(symbol: str, bars: List[Bar], direction: str,
                buying_power: float, risk_pct: float, max_order_dollars: float,
                vol_ratio: float = 1.0, news_score: int = 0,
                min_rr: float = MIN_RR,
                limit_on_dip_pct: Optional[float] = None) -> Setup:
    """
    Build a validated, risk-gated trade setup. Rejects (does not force) anything
    that doesn't make sense or doesn't meet the minimum reward:risk.
    """
    if len(bars) < 20:
        return _reject(symbol, "not enough price history to trade safely")
    if buying_power <= 0:
        return _reject(symbol, "no buying power")

    closes = [b.close for b in bars]
    last = round(closes[-1], 2)
    if last <= 0:
        return _reject(symbol, "invalid price")

    regime = detect_regime(bars)
    a = atr(bars)
    if a <= 0:
        a = last * 0.01
    rsi_v = rsi(closes)

    entry = round(last * (1 - limit_on_dip_pct / 100), 2) if limit_on_dip_pct else last
    limit_price = entry if limit_on_dip_pct else None

    # --- Stop: market structure + ATR buffer, clamped to sane bounds ---
    if direction == "long":
        struct = protective_swing_low(bars)
        raw_stop = min(struct - 0.25 * a, entry - ATR_STOP_MULT * a)
        stop = round(raw_stop, 2)
        # clamp distance
        dist = entry - stop
        dist = max(dist, entry * MIN_STOP_PCT)
        dist = min(dist, entry * MAX_STOP_PCT)
        stop = round(entry - dist, 2)
    else:  # short
        struct = protective_swing_high(bars)
        raw_stop = max(struct + 0.25 * a, entry + ATR_STOP_MULT * a)
        stop = round(raw_stop, 2)
        dist = stop - entry
        dist = max(dist, entry * MIN_STOP_PCT)
        dist = min(dist, entry * MAX_STOP_PCT)
        stop = round(entry + dist, 2)

    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return _reject(symbol, "could not place a valid stop", regime.label)

    # --- Target: nearest real structure in the trade direction ---
    # When real structure exists we aim there and let the R:R gate judge it.
    # When it doesn't, we fall back to a FIXED measured move (independent of
    # min_rr) so the gate stays meaningful — we never fabricate a target that
    # just barely clears the minimum.
    struct_target = False
    if direction == "long":
        highs = [h for _, h in swing_highs(bars[-STRUCT_LOOKBACK:])
                 if h > entry + 0.5 * risk_per_share]
        target = min(highs) if highs else entry + DEFAULT_TARGET_RR * risk_per_share
        struct_target = bool(highs)
        target = round(target, 2)
    else:
        lows = [lo for _, lo in swing_lows(bars[-STRUCT_LOOKBACK:])
                if lo < entry - 0.5 * risk_per_share]
        target = max(lows) if lows else entry - DEFAULT_TARGET_RR * risk_per_share
        struct_target = bool(lows)
        target = round(target, 2)

    reward_per_share = abs(target - entry)
    rr = round(reward_per_share / risk_per_share, 2) if risk_per_share else 0.0

    # --- Validate geometry (never wrong side) ---
    if direction == "long" and not (stop < entry < target):
        return _reject(symbol, "stop/target geometry invalid for a long", regime.label)
    if direction == "short" and not (target < entry < stop):
        return _reject(symbol, "stop/target geometry invalid for a short", regime.label)

    # --- Hard reward:risk gate — reject, don't force ---
    if rr < min_rr:
        return _reject(symbol, f"reward:risk only 1:{rr:g} (need ≥ 1:{min_rr:g}) — "
                               "no clean target far enough away", regime.label)

    # --- Risk-based position sizing ---
    risk_budget = risk_pct * buying_power
    qty = int(min(risk_budget / risk_per_share,
                  max_order_dollars / entry,
                  buying_power / entry))
    if qty < 1:
        if entry <= min(max_order_dollars, buying_power):
            qty = 1
        else:
            return _reject(symbol, f"one share (${entry:,.2f}) exceeds limits",
                           regime.label)

    risk_total = round(qty * risk_per_share, 2)
    reward_total = round(qty * reward_per_share, 2)
    confidence, reasons = _confidence(direction, regime, rr, rsi_v, vol_ratio,
                                      news_score, struct_target)

    return Setup(
        symbol=symbol, direction=direction,
        side=("buy" if direction == "long" else "sell"),
        entry=entry, stop=stop, target=target, qty=qty,
        risk_per_share=round(risk_per_share, 2), risk_total=risk_total,
        reward_total=reward_total, rr=rr,
        stop_pct=round(abs(stop - entry) / entry * 100, 2),
        tp_pct=round(abs(target - entry) / entry * 100, 2),
        confidence=confidence, reasons=reasons, regime=regime.label,
        limit_price=limit_price,
    )
