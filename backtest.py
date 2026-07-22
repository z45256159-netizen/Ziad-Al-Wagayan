"""
backtest.py — a simple, honest backtester for the engine.

It walks historical bars one day at a time, asks the engine for a validated
setup at each step (exactly the same build_setup used live), then simulates the
outcome bar-by-bar: which came first, the stop or the target? From that it
reports the real trader metrics — win rate, average R multiple, expectancy, and
profit factor.

This is a teaching/what-if tool on daily bars, not a promise of future results:
it can't model slippage, gaps through the stop, or intraday order of touches.

Run:  python backtest.py            # backtests the default universe
      python backtest.py AAPL MSFT  # specific tickers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from engine import build_setup
from strategy import Bar, direction_of, detect_pattern

log = logging.getLogger("bot.backtest")

WARMUP = 60          # bars of history required before the first trade
MAX_HOLD = 15        # give a trade this many bars to hit stop or target
COOLDOWN = 5         # bars to wait after a trade closes before re-entering


@dataclass
class Trade:
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    exit_price: float
    outcome: str        # "win" / "loss" / "timeout"
    r_multiple: float   # profit/loss measured in units of initial risk


@dataclass
class Result:
    trades: List[Trade]

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.r_multiple > 0)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n * 100) if self.n else 0.0

    @property
    def avg_r(self) -> float:
        return (sum(t.r_multiple for t in self.trades) / self.n) if self.n else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(t.r_multiple for t in self.trades if t.r_multiple > 0)
        losses = -sum(t.r_multiple for t in self.trades if t.r_multiple < 0)
        return (gains / losses) if losses else float("inf") if gains else 0.0

    def summary(self) -> str:
        if not self.n:
            return "No trades were taken (nothing passed the quality gate)."
        pf = self.profit_factor
        pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
        return (f"{self.n} trades · win rate {self.win_rate:.0f}% · "
                f"avg {self.avg_r:+.2f}R · expectancy {self.avg_r:+.2f}R/trade · "
                f"profit factor {pf_s}")


def _simulate(entry_bars: List[Bar], setup) -> Optional[Trade]:
    """Walk forward from the bar AFTER entry; see if stop or target hits first."""
    risk = abs(setup.entry - setup.stop)
    if risk <= 0:
        return None
    for b in entry_bars[:MAX_HOLD]:
        hi = b.high if b.high is not None else b.close
        lo = b.low if b.low is not None else b.close
        if setup.direction == "long":
            hit_stop = lo <= setup.stop
            hit_tgt = hi >= setup.target
        else:
            hit_stop = hi >= setup.stop
            hit_tgt = lo <= setup.target
        # Conservative: if both are touched in the same bar, assume the stop.
        if hit_stop:
            return Trade(setup.symbol, setup.direction, setup.entry, setup.stop,
                         setup.target, setup.stop, "loss", -1.0)
        if hit_tgt:
            r = abs(setup.target - setup.entry) / risk
            return Trade(setup.symbol, setup.direction, setup.entry, setup.stop,
                         setup.target, setup.target, "win", round(r, 2))
    # Timed out — mark to the last close.
    last = entry_bars[min(MAX_HOLD, len(entry_bars)) - 1].close
    r = ((last - setup.entry) if setup.direction == "long"
         else (setup.entry - last)) / risk
    return Trade(setup.symbol, setup.direction, setup.entry, setup.stop,
                 setup.target, round(last, 2), "timeout", round(r, 2))


def backtest_symbol(symbol: str, bars: List[Bar], *, risk_pct: float = 0.01,
                    max_order_dollars: float = 100_000,
                    buying_power: float = 1_000_000,
                    forced: Optional[str] = None) -> List[Trade]:
    """Walk one ticker's history and collect the trades the engine would take."""
    trades: List[Trade] = []
    i = WARMUP
    while i < len(bars) - 1:
        window = bars[:i + 1]
        pattern, _ = detect_pattern(window[-40:])
        pd = direction_of(pattern)
        dirs = ([forced] if forced else
                ([pd] + [d for d in ("long", "short") if d != pd]
                 if pd in ("long", "short") else ["long", "short"]))
        took = False
        for d in dirs:
            setup = build_setup(symbol, window, d, buying_power, risk_pct,
                                max_order_dollars)
            if setup.ok:
                trade = _simulate(bars[i + 1:], setup)
                if trade is not None:
                    trades.append(trade)
                    i += COOLDOWN
                    took = True
                break
        i += 1 if not took else 0
        i += 1
    return trades


def backtest(book: Dict[str, List[Bar]], **kw) -> Result:
    """Backtest a whole {symbol: bars} book and pool the trades."""
    all_trades: List[Trade] = []
    for sym, bars in book.items():
        if len(bars) > WARMUP + 5:
            all_trades.extend(backtest_symbol(sym, bars, **kw))
    return Result(all_trades)


def _cli() -> None:  # pragma: no cover - needs live keys/network
    import sys

    from broker import Broker, BrokerError
    from config import load_config
    from universe import UNIVERSE

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    symbols = [s.upper() for s in sys.argv[1:]] or UNIVERSE
    try:
        cfg = load_config()
        broker = Broker(cfg)
        book = broker.fetch_bars(symbols)
    except (BrokerError, Exception) as exc:  # noqa: BLE001
        print(f"Could not fetch data: {exc}")
        return
    result = backtest(book)
    print("\n=== Backtest ===")
    print(result.summary())
    by_sym: Dict[str, List[Trade]] = {}
    for t in result.trades:
        by_sym.setdefault(t.symbol, []).append(t)
    for sym, ts in sorted(by_sym.items()):
        wr = sum(1 for t in ts if t.r_multiple > 0) / len(ts) * 100
        avg = sum(t.r_multiple for t in ts) / len(ts)
        print(f"  {sym:5s} {len(ts):3d} trades · {wr:3.0f}% win · {avg:+.2f}R")


if __name__ == "__main__":  # pragma: no cover
    _cli()
