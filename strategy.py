"""
Scanning strategy — the "brain" of the bot, kept deliberately separate so you
can swap it out without touching the trading/plumbing code.

HOW IT WORKS (default strategy: momentum + volume)
--------------------------------------------------
For each ticker we look at the last N daily bars (N = LOOKBACK_DAYS, default 20)
and compute a score. A ticker is a valid candidate only if BOTH of these hold:

  1. MOMENTUM: today's close is ABOVE its N-day simple moving average (SMA).
     -> The stock is trending up, not falling.
  2. VOLUME:   today's volume is ABOVE its average daily volume over the window.
     -> There is real participation behind today's move, not a quiet drift.

Among the tickers that pass both filters, we rank by a combined score:

     score = momentum_strength * volume_ratio

where
     momentum_strength = (last_close - sma) / sma      (how far above the SMA)
     volume_ratio      = today_volume / avg_volume      (how heavy today is)

The single highest-scoring ticker is returned as THE candidate.

HOW TO TUNE / SWAP
------------------
* To tune the default: edit `score_symbol()` below. Everything the strategy
  needs is passed in as a list of bars, so it stays pure and testable.
* To write a whole new strategy: implement a function with the same shape as
  `score_symbol(bars) -> Optional[SymbolScore]` and point `SCORING_FN` at it.
  `find_candidate()` will use whatever `SCORING_FN` references.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence


@dataclass
class Bar:
    """A single daily OHLCV bar. Strategy code only depends on this shape,
    not on Alpaca's SDK objects, which keeps it easy to unit-test."""

    close: float
    volume: float


@dataclass
class SymbolScore:
    """The scanner's verdict for one ticker."""

    symbol: str
    score: float
    last_price: float
    sma: float
    momentum_strength: float  # fractional distance above the SMA (e.g. 0.03 = 3%)
    volume_ratio: float       # today's volume / average volume
    reason: str               # human-readable explanation of the signal


def _sma(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def score_symbol(symbol: str, bars: List[Bar]) -> Optional[SymbolScore]:
    """
    Score a single ticker. Returns a SymbolScore if it PASSES the filters,
    or None if it does not qualify (or there isn't enough data).

    >>> THIS IS THE FUNCTION TO EDIT WHEN TUNING THE DEFAULT STRATEGY. <<<
    """
    # Need at least 2 bars to have a moving average plus "today".
    if len(bars) < 2:
        return None

    closes = [b.close for b in bars]
    volumes = [b.volume for b in bars]

    last_price = closes[-1]
    # SMA and average volume over the full lookback window.
    sma = _sma(closes)
    avg_volume = _sma(volumes)
    today_volume = volumes[-1]

    if sma <= 0 or avg_volume <= 0:
        return None

    momentum_strength = (last_price - sma) / sma
    volume_ratio = today_volume / avg_volume

    # --- Filters: both must pass to be a candidate ---
    passes_momentum = last_price > sma          # trending up
    passes_volume = today_volume > avg_volume   # above-average participation
    if not (passes_momentum and passes_volume):
        return None

    # Combined score: reward stocks that are both well above their average
    # AND trading on heavy volume. Multiplying couples the two conditions so a
    # weak reading on either axis drags the score down.
    score = momentum_strength * volume_ratio

    reason = (
        f"Price ${last_price:.2f} is {momentum_strength * 100:.1f}% above its "
        f"{len(bars)}-day SMA (${sma:.2f}); today's volume is "
        f"{volume_ratio:.2f}x the {len(bars)}-day average."
    )

    return SymbolScore(
        symbol=symbol,
        score=score,
        last_price=last_price,
        sma=sma,
        momentum_strength=momentum_strength,
        volume_ratio=volume_ratio,
        reason=reason,
    )


# Point this at any function with the signature (symbol, bars) -> Optional[SymbolScore]
# to swap the strategy. `find_candidate` uses whatever this references.
SCORING_FN: Callable[[str, List[Bar]], Optional[SymbolScore]] = score_symbol


def rank_candidates(bars_by_symbol: dict[str, List[Bar]]) -> List[SymbolScore]:
    """
    Score every symbol we have data for and return all that pass the filters,
    sorted best-first. Returns an empty list if nothing qualifies.
    """
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
