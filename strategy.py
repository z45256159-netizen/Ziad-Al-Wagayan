"""
Scanning strategy — the "brain" of the bot, kept deliberately separate so you
can swap it out without touching the trading/plumbing code.

DEFAULT STRATEGY: a multi-indicator momentum setup
--------------------------------------------------
For each ticker we look at the recent daily bars and compute several classic
technical indicators. A ticker is a valid candidate only if ALL of these agree
that it's in a healthy uptrend with real buying interest:

  1. TREND (moving-average crossover):
       price > 20-day SMA  AND  20-day SMA > 50-day SMA
     -> short-term average is above the long-term average = uptrend.

  2. MOMENTUM (RSI, 14-day Relative Strength Index):
       50 < RSI < 78
     -> rising strength, but NOT so overbought that it's about to snap back.

  3. TREND CONFIRMATION (MACD):
       MACD line > signal line  (positive histogram)
     -> the faster trend is pulling ahead of the slower one = bullish.

  4. PARTICIPATION (relative volume):
       today's volume > its recent average
     -> the move has real volume behind it, not a quiet drift.

Candidates that pass ALL FOUR are ranked by a combined score:

     score = momentum_strength * volume_ratio

where momentum_strength = (price - 20-day SMA) / 20-day SMA  (how far above trend)
      volume_ratio      = today's volume / average volume     (how heavy today is)

The single highest-scoring ticker is returned as THE candidate.

HOW TO TUNE / SWAP
------------------
* Tune the default: edit `score_symbol()` below. Every indicator is computed
  from a plain list of bars, so it stays easy to read and test. Change a
  threshold (e.g. require RSI < 70), add an indicator, or change the score.
* Whole new strategy: write a function with the same shape as
  `score_symbol(symbol, bars) -> Optional[SymbolScore]` and point `SCORING_FN`
  at it. `rank_candidates()` / `find_candidate()` will use it automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

# Indicator periods (trading days). Edit to tune.
SMA_FAST = 20
SMA_SLOW = 50
RSI_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
VOLUME_PERIOD = 20
# We need at least this many bars for the slow SMA to be meaningful.
MIN_BARS = SMA_SLOW


@dataclass
class Bar:
    """A single daily OHLCV bar. Strategy code only depends on this shape,
    not on Alpaca's SDK objects, which keeps it easy to unit-test. `high`/`low`
    are optional so tests can build a bar from just close+volume; when missing,
    the ATR falls back to close-to-close moves."""

    close: float
    volume: float
    high: Optional[float] = None
    low: Optional[float] = None
    open: Optional[float] = None


@dataclass
class SymbolScore:
    """The scanner's verdict for one ticker."""

    symbol: str
    score: float
    last_price: float
    sma: float                # the fast (20-day) SMA
    momentum_strength: float  # fractional distance above the fast SMA
    volume_ratio: float       # today's volume / average volume
    rsi: float                # 14-day RSI reading
    macd_hist: float          # MACD histogram (macd - signal); >0 is bullish
    atr: float                # 14-day Average True Range (volatility, in $)
    reason: str               # human-readable explanation of the signal
    support: float = 0.0      # recent swing low (price floor)
    resistance: float = 0.0   # recent swing high (price ceiling)
    breakout: bool = False    # price broke above prior resistance (fresh high)
    pattern: str = ""         # detected chart pattern label (set by the app)


# ------------------------------------------------------------------ indicators
def _sma(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _ema_series(values: Sequence[float], period: int) -> List[float]:
    """Exponential moving average, seeded with the first value (simple + stable)."""
    k = 2.0 / (period + 1)
    ema = values[0]
    out = [ema]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def _rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> float:
    """Relative Strength Index over the last `period` daily changes (0-100)."""
    if len(closes) <= period:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-period:]
    avg_gain = sum(d for d in recent if d > 0) / period
    avg_loss = sum(-d for d in recent if d < 0) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _macd_hist(closes: Sequence[float]) -> float:
    """MACD histogram = MACD line - signal line. Positive = bullish."""
    ema_fast = _ema_series(closes, MACD_FAST)
    ema_slow = _ema_series(closes, MACD_SLOW)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal = _ema_series(macd_line, MACD_SIGNAL)
    return macd_line[-1] - signal[-1]


def _atr(bars: List[Bar], period: int = 14) -> float:
    """Average True Range — how much the stock typically moves per day, in $.
    Used to place a volatility-aware stop-loss. Uses high/low when available,
    otherwise falls back to close-to-close moves."""
    trs: List[float] = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        high = bars[i].high if bars[i].high is not None else bars[i].close
        low = bars[i].low if bars[i].low is not None else bars[i].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    recent = trs[-period:]
    return sum(recent) / len(recent)


def _support_resistance(bars: List[Bar], lookback: int = 20):
    """Recent support (lowest low) and resistance (highest high) — the price
    floor and ceiling day traders watch. Uses highs/lows when available."""
    window = bars[-lookback:] if len(bars) >= lookback else bars
    highs = [(b.high if b.high is not None else b.close) for b in window]
    lows = [(b.low if b.low is not None else b.close) for b in window]
    return (min(lows), max(highs))


# --------------------------------------------------------------------- scoring
def score_symbol(symbol: str, bars: List[Bar]) -> Optional[SymbolScore]:
    """
    Score a single ticker. Returns a SymbolScore if it PASSES every filter,
    or None if it does not qualify (or there isn't enough data).

    >>> THIS IS THE FUNCTION TO EDIT WHEN TUNING THE DEFAULT STRATEGY. <<<
    """
    if len(bars) < MIN_BARS:
        return None

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]

    last_price = closes[-1]
    sma_fast = _sma(closes[-SMA_FAST:])
    sma_slow = _sma(closes[-SMA_SLOW:])
    avg_volume = _sma(volumes[-VOLUME_PERIOD:])
    today_volume = volumes[-1]

    if sma_fast <= 0 or sma_slow <= 0 or avg_volume <= 0:
        return None

    momentum_strength = (last_price - sma_fast) / sma_fast
    volume_ratio = today_volume / avg_volume
    rsi = _rsi(closes)
    macd_hist = _macd_hist(closes)
    atr = _atr(bars)
    support, resistance = _support_resistance(bars)
    # Breakout: today closes above the ceiling of the PRIOR days (a fresh high).
    _, prior_resistance = _support_resistance(bars[:-3] if len(bars) > 3 else bars)
    breakout = last_price >= prior_resistance

    # --- Filters: ALL must pass to be a candidate ---
    trend_up = last_price > sma_fast and sma_fast > sma_slow  # MA crossover uptrend
    momentum_ok = 50.0 < rsi < 78.0                           # rising, not overbought
    macd_bull = macd_hist > 0                                 # MACD confirms
    volume_ok = today_volume > avg_volume                     # real participation
    if not (trend_up and momentum_ok and macd_bull and volume_ok):
        return None

    # Combined score: reward stocks well above trend AND on heavy volume, with a
    # bonus for a fresh breakout above resistance (a strong day-trade setup).
    score = momentum_strength * volume_ratio * (1.2 if breakout else 1.0)

    reason = (
        f"Uptrend: ${last_price:.2f} > {SMA_FAST}-day SMA ${sma_fast:.2f} > "
        f"{SMA_SLOW}-day SMA ${sma_slow:.2f}. "
        f"RSI {rsi:.0f} (momentum, not overbought). "
        f"MACD bullish (histogram {macd_hist:+.2f}). "
        f"Volume {volume_ratio:.2f}x its {VOLUME_PERIOD}-day average. "
        f"Support ${support:.2f} / resistance ${resistance:.2f}"
        + (" — 🔼 breaking to new highs. " if breakout else ". ")
        + f"Typical daily move (ATR) ${atr:.2f}."
    )

    return SymbolScore(
        symbol=symbol,
        score=score,
        last_price=last_price,
        sma=sma_fast,
        momentum_strength=momentum_strength,
        volume_ratio=volume_ratio,
        rsi=rsi,
        macd_hist=macd_hist,
        atr=atr,
        reason=reason,
        support=support,
        resistance=resistance,
        breakout=breakout,
    )


# Point this at any function with the signature (symbol, bars) -> Optional[SymbolScore]
# to swap the strategy. rank_candidates() uses whatever this references.
SCORING_FN: Callable[[str, List[Bar]], Optional[SymbolScore]] = score_symbol


def rank_candidates(bars_by_symbol: dict[str, List[Bar]]) -> List[SymbolScore]:
    """Score every symbol and return all that pass the filters, best-first."""
    scored: List[SymbolScore] = []
    for symbol, bars in bars_by_symbol.items():
        result = SCORING_FN(symbol, bars)
        if result is not None:
            scored.append(result)
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def find_candidate(bars_by_symbol: dict[str, List[Bar]]) -> Optional[SymbolScore]:
    """Return the single best candidate, or None if nothing passes the filters."""
    ranked = rank_candidates(bars_by_symbol)
    return ranked[0] if ranked else None


def score_relaxed(symbol: str, bars: List[Bar]) -> Optional[SymbolScore]:
    """
    A NON-rejecting scorer: computes the same indicators but never filters a
    stock out. Used as a fallback so the bot can always surface *something* to
    look at (for practice) when nothing meets the strict setup. The `reason`
    spells out which checks passed and which didn't.
    """
    if len(bars) < MIN_BARS:
        return None
    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]
    last_price = closes[-1]
    sma_fast = _sma(closes[-SMA_FAST:])
    sma_slow = _sma(closes[-SMA_SLOW:])
    avg_volume = _sma(volumes[-VOLUME_PERIOD:])
    today_volume = volumes[-1]
    if sma_fast <= 0 or sma_slow <= 0 or avg_volume <= 0:
        return None

    momentum_strength = (last_price - sma_fast) / sma_fast
    volume_ratio = today_volume / avg_volume
    rsi = _rsi(closes)
    macd_hist = _macd_hist(closes)
    atr = _atr(bars)
    support, resistance = _support_resistance(bars)

    # Soft score: mostly momentum, nudged by volume.
    score = momentum_strength + 0.1 * (volume_ratio - 1.0)

    checks = [
        "✓ uptrend" if (last_price > sma_fast > sma_slow) else "✗ weak trend",
        f"RSI {rsi:.0f}",
        "MACD bullish" if macd_hist > 0 else "MACD bearish",
        f"vol {volume_ratio:.2f}x",
    ]
    reason = ("Closest match (not a full setup) — " + ", ".join(checks) +
              f". Support ${support:.2f} / resistance ${resistance:.2f}. "
              f"ATR ${atr:.2f}.")

    return SymbolScore(
        symbol=symbol, score=score, last_price=last_price, sma=sma_fast,
        momentum_strength=momentum_strength, volume_ratio=volume_ratio,
        rsi=rsi, macd_hist=macd_hist, atr=atr, reason=reason,
        support=support, resistance=resistance,
    )


def _local_min_idx(vals: Sequence[float], w: int = 3) -> List[int]:
    return [i for i in range(w, len(vals) - w)
            if vals[i] == min(vals[i - w:i + w + 1])]


def _local_max_idx(vals: Sequence[float], w: int = 3) -> List[int]:
    return [i for i in range(w, len(vals) - w)
            if vals[i] == max(vals[i - w:i + w + 1])]


def detect_pattern(bars: List[Bar]):
    """
    Best-effort chart-pattern label for the given window of bars, plus points to
    mark on the chart. Heuristic — meant as a helpful read, not a guarantee.
    Returns (label, marks) where marks is a list of (index, price, text).
    """
    n = len(bars)
    if n < 15:
        return ("not enough data", [])
    closes = [b.close for b in bars]
    highs = [(b.high if b.high is not None else b.close) for b in bars]
    lows = [(b.low if b.low is not None else b.close) for b in bars]
    last = closes[-1]
    sma = sum(closes[-20:]) / min(20, n)

    # Breakout above the recent ceiling.
    prior_high = max(highs[:-2])
    if last >= prior_high:
        return ("Breakout to new highs", [(n - 1, last, "breakout")])

    mins, maxs = _local_min_idx(lows), _local_max_idx(highs)

    # Double / triple bottom: 2-3 similar swing lows, price now recovering.
    if len(mins) >= 2:
        recent = mins[-3:]
        lvls = [lows[i] for i in recent]
        base = min(lvls)
        if base > 0 and (max(lvls) - min(lvls)) / base < 0.04 and last > base * 1.02:
            name = "Triple bottom" if len(recent) >= 3 else "Double bottom"
            return (name, [(i, lows[i], "bottom") for i in recent])

    # Head & shoulders (bearish): three peaks, middle highest, shoulders even.
    if len(maxs) >= 3:
        p = maxs[-3:]
        h = [highs[i] for i in p]
        if h[1] > h[0] and h[1] > h[2] and abs(h[0] - h[2]) / h[1] < 0.05:
            return ("Head & shoulders (bearish)",
                    [(p[0], h[0], "L shoulder"), (p[1], h[1], "head"),
                     (p[2], h[2], "R shoulder")])

    # Bull flag / pullback within an uptrend.
    if (last > sma and closes[-1] > closes[-4]
            and (max(closes[-12:]) - last) / max(closes[-12:]) < 0.06):
        return ("Bull flag / pullback", [])

    if last > sma:
        return ("Uptrend (higher lows)", [])
    return ("Range / no clear pattern", [])


def rank_relaxed(bars_by_symbol: dict[str, List[Bar]]) -> List[SymbolScore]:
    """Score every symbol without filtering; best-first. Never empty if there's
    data — the safety net so `find` always has something to show."""
    scored = [score_relaxed(s, b) for s, b in bars_by_symbol.items()]
    scored = [x for x in scored if x is not None]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
