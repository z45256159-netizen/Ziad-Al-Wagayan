"""
Unit tests for the pure logic (multi-indicator strategy + position sizing).

These run WITHOUT any API keys or network access:
    python -m unittest test_strategy -v
"""

import unittest

from sizing import build_trade_plan, size_position
from strategy import (Bar, detect_pattern, direction_of, find_candidate,
                      rank_candidates, rank_relaxed, score_symbol)


class TestAnalysis(unittest.TestCase):
    def test_explain_setup_cites_real_levels(self):
        from analysis import explain_setup
        marks = [(3, 187.20, "bottom"), (9, 188.05, "bottom")]
        info = explain_setup("Double bottom", support=187.0, resistance=210.0,
                             entry=195.0, marks=marks)
        self.assertIsNotNone(info)
        joined = " ".join(info)
        self.assertIn("187", joined)   # actual support / swing-low price
        self.assertIn("210", joined)   # actual resistance / target

    def test_explain_setup_unknown(self):
        from analysis import explain_setup
        self.assertIsNone(explain_setup("Range / no clear pattern", 1, 2, 1.5, []))

    def test_invest_analysis_uptrend_is_a_buy(self):
        from analysis import invest_analysis
        bars = [Bar(close=100 + i, volume=1000, high=100 + i + 1, low=100 + i - 1)
                for i in range(60)]
        a = invest_analysis(bars, atr=2.0)
        self.assertIn(a.verdict, ("BUY", "STRONG BUY"))
        self.assertGreater(a.perf_pct, 0)
        self.assertGreater(a.exp_return_pct, 0)
        self.assertGreater(a.downside_pct, 0)
        self.assertTrue(a.reasons)

    def test_invest_analysis_relative_strength_vs_benchmark(self):
        from analysis import invest_analysis
        stock = [Bar(close=100 + i, volume=1000, high=100 + i + 1, low=100 + i - 1)
                 for i in range(60)]                    # +59%
        flat = [Bar(close=100, volume=1000, high=101, low=99) for _ in range(60)]
        a = invest_analysis(stock, atr=2.0, benchmark_bars=flat)
        self.assertGreater(a.rel_strength, 0)           # beats a flat market

    def test_news_sentiment_labels(self):
        from analysis import news_sentiment
        bull = news_sentiment([("Company beats earnings, surges to record", "x")])
        bear = news_sentiment([("Company misses, faces lawsuit and probe", "x")])
        none = news_sentiment([])
        self.assertEqual(bull[0], "BULLISH")
        self.assertEqual(bear[0], "BEARISH")
        self.assertEqual(none[0], "NONE")


class TestEngine(unittest.TestCase):
    """The professional core: regime, validated geometry, R:R gate, sizing."""

    @staticmethod
    def _uptrend_bars(n=60, start=100.0):
        closes = [start]
        step = [2.0, -1.0]
        for i in range(n - 1):
            closes.append(closes[-1] + step[i % 2])
        return [Bar(close=c, volume=1000, high=c + 1.2, low=c - 1.2) for c in closes]

    def test_regime_detects_uptrend(self):
        from engine import detect_regime
        r = detect_regime(self._uptrend_bars())
        self.assertEqual(r.bias, "up")

    def test_long_setup_geometry_is_valid(self):
        from engine import build_setup
        s = build_setup("UP", self._uptrend_bars(), "long", buying_power=100_000,
                        risk_pct=0.01, max_order_dollars=5000)
        if s.ok:   # a valid long must have stop < entry < target
            self.assertLess(s.stop, s.entry)
            self.assertLess(s.entry, s.target)
            self.assertEqual(s.side, "buy")

    def test_never_buy_stop_above_entry(self):
        # Across many synthetic longs, a BUY stop is NEVER at/above entry.
        from engine import build_setup
        for shift in range(0, 40, 3):
            bars = self._uptrend_bars(start=50 + shift)
            s = build_setup("X", bars, "long", 100_000, 0.01, 5000)
            if s.ok:
                self.assertLess(s.stop, s.entry,
                                f"BUY stop {s.stop} not below entry {s.entry}")

    def test_never_sell_stop_below_entry(self):
        from engine import build_setup
        closes = [Bar(close=c, volume=1000, high=c + 1.2, low=c - 1.2)
                  for c in [160 - i * 0.8 for i in range(60)]]
        s = build_setup("DN", closes, "short", 100_000, 0.01, 5000)
        if s.ok:
            self.assertGreater(s.stop, s.entry)   # SELL stop ABOVE entry
            self.assertLess(s.target, s.entry)    # SELL target BELOW entry
            self.assertEqual(s.side, "sell")

    def test_reward_risk_gate_rejects_low_rr(self):
        # An impossibly high min_rr must be rejected, never forced.
        from engine import build_setup
        s = build_setup("UP", self._uptrend_bars(), "long", 100_000, 0.01, 5000,
                        min_rr=99.0)
        self.assertFalse(s.ok)
        self.assertTrue(s.rejected)
        self.assertIn("reward:risk", s.reject_reason.lower())

    def test_valid_setup_meets_min_rr(self):
        from engine import build_setup
        s = build_setup("UP", self._uptrend_bars(), "long", 100_000, 0.01, 5000,
                        min_rr=1.6)
        if s.ok:
            self.assertGreaterEqual(s.rr, 1.6)
            self.assertGreater(s.qty, 0)
            self.assertTrue(0 <= s.confidence <= 100)

    def test_rejects_when_no_history(self):
        from engine import build_setup
        s = build_setup("TINY", self._uptrend_bars(n=5), "long", 100_000, 0.01, 5000)
        self.assertFalse(s.ok)
        self.assertTrue(s.rejected)

    def test_supertrend_uptrend_is_long(self):
        from engine import supertrend
        res = supertrend(self._uptrend_bars(n=80))
        self.assertIsNotNone(res)
        direction, flipped, line = res
        self.assertEqual(direction, "long")          # rising series → uptrend
        self.assertLess(line, 200)                    # line sits below price
        self.assertIn(flipped, (True, False))

    def test_supertrend_setup_geometry_and_stop_is_line(self):
        from engine import build_supertrend_setup, supertrend
        bars = self._uptrend_bars(n=80)
        s = build_supertrend_setup("UP", bars, 100_000, 0.01, 5000)
        if s.ok:
            self.assertEqual(s.side, "buy")
            self.assertLess(s.stop, s.entry)          # long stop below entry
            self.assertLess(s.entry, s.target)        # target above
            self.assertGreater(s.qty, 0)

    def test_supertrend_respects_direction_block(self):
        from engine import build_supertrend_setup
        bars = self._uptrend_bars(n=80)               # a LONG signal
        s = build_supertrend_setup("UP", bars, 100_000, 0.01, 5000,
                                   allowed=lambda d: d == "short")  # only shorts
        self.assertFalse(s.ok)                         # long blocked → rejected
        self.assertTrue(s.rejected)


class TestBacktest(unittest.TestCase):
    def test_backtest_runs_and_reports(self):
        from backtest import backtest
        closes = [100.0]
        step = [2.0, -1.0]
        for i in range(120):
            closes.append(closes[-1] + step[i % 2])
        bars = [Bar(close=c, volume=1000, high=c + 1.2, low=c - 1.2) for c in closes]
        result = backtest({"UP": bars})
        self.assertGreaterEqual(result.n, 0)          # never crashes
        for t in result.trades:                       # every trade is coherent
            self.assertIn(t.outcome, ("win", "loss", "timeout"))
            if t.direction == "long":
                self.assertLess(t.stop, t.entry)      # never a wrong-side stop
            else:
                self.assertGreater(t.stop, t.entry)
        self.assertIsInstance(result.summary(), str)


class TestPatterns(unittest.TestCase):
    def test_breakout_detected(self):
        bars = [Bar(close=100 + i, volume=1000, high=100 + i + 1, low=100 + i - 1)
                for i in range(40)]
        self.assertEqual(detect_pattern(bars)[0], "Breakout to new highs")

    def test_uptrend_not_mislabeled_as_triangle(self):
        # Higher highs AND higher lows = an uptrend, NOT an ascending triangle.
        closes = [100.0]
        for i in range(44):
            closes.append(closes[-1] + [3, 3, -1][i % 3])
        bars = [Bar(close=c, volume=1000, high=c + 1.5, low=c - 1.5) for c in closes]
        self.assertNotIn("triangle", detect_pattern(bars)[0].lower())

    def test_double_or_triple_bottom_detected(self):
        # Two equal lows (~104), recovering but staying BELOW the prior high (~120)
        # so it's a bottom, not a breakout.
        seq = [120, 117, 113, 109, 106, 104, 107, 111, 112, 109, 106, 104, 108,
               111, 114, 115]
        bars = [Bar(close=c, volume=1000, high=c + 1, low=c - 1) for c in seq]
        label = detect_pattern(bars)[0]
        self.assertIn("bottom", label.lower())


def _bars(closes, volumes):
    return [Bar(close=c, volume=v) for c, v in zip(closes, volumes)]


def _uptrend_closes(n=60, start=100.0):
    """A realistic uptrend: rises overall but with pullbacks, so RSI stays
    healthy (not pinned at 100) — the shape the strategy is designed to catch."""
    closes = [start]
    step = [2.0, -1.0]  # up two, back one -> net up, mixed gains/losses
    for i in range(n - 1):
        closes.append(closes[-1] + step[i % 2])
    return closes


class TestScoring(unittest.TestCase):
    def test_clean_uptrend_high_volume_passes(self):
        # Rising-with-pullbacks -> price > 20SMA > 50SMA, RSI in a healthy band,
        # MACD bullish; final bar has a volume spike.
        closes = _uptrend_closes()
        volumes = [1000] * 60
        volumes[-1] = 5000                              # heavy volume today
        result = score_symbol("UP", _bars(closes, volumes))
        self.assertIsNotNone(result)
        self.assertGreater(result.momentum_strength, 0)
        self.assertGreater(result.volume_ratio, 1)
        self.assertGreater(result.macd_hist, 0)         # MACD confirms
        self.assertTrue(50 < result.rsi < 78)

    def test_downtrend_fails(self):
        # Falling prices -> price below SMAs, MACD bearish -> filtered out.
        closes = [160 - i for i in range(60)]
        volumes = [2000] * 60
        self.assertIsNone(score_symbol("DOWN", _bars(closes, volumes)))

    def test_uptrend_but_low_volume_fails(self):
        # Healthy uptrend, but today's volume is below average -> filtered out.
        closes = _uptrend_closes()
        volumes = [2000] * 60
        volumes[-1] = 100
        self.assertIsNone(score_symbol("UP", _bars(closes, volumes)))

    def test_overbought_rsi_fails(self):
        # A near-vertical spike pushes RSI to ~100 (overbought) -> filtered out.
        closes = [100 + i * 5 for i in range(60)]
        volumes = [3000] * 60
        result = score_symbol("HOT", _bars(closes, volumes))
        # RSI here is 100 (only gains), which is >= 78, so it must be rejected.
        self.assertIsNone(result)

    def test_not_enough_bars_returns_none(self):
        closes = [100 + i for i in range(10)]
        volumes = [1000] * 10
        self.assertIsNone(score_symbol("SHORT", _bars(closes, volumes)))

    def test_relaxed_returns_something_when_strict_is_empty(self):
        # A downtrend passes NO strict filters, but the relaxed fallback still
        # ranks it so the user always has something to look at.
        closes = [160 - i for i in range(60)]
        volumes = [2000] * 60
        book = {"DOWN": _bars(closes, volumes)}
        self.assertEqual(rank_candidates(book), [])      # strict: nothing
        relaxed = rank_relaxed(book)
        self.assertEqual(len(relaxed), 1)                # fallback: one result
        self.assertIn("not a full setup", relaxed[0].reason)

    def test_find_candidate_picks_highest_score(self):
        closes = _uptrend_closes()
        strong_v = [1000] * 60
        strong_v[-1] = 9000                             # huge volume
        weak_v = [1000] * 60
        weak_v[-1] = 1100                               # barely above average
        best = find_candidate({
            "WEAK": _bars(closes, weak_v),
            "STRONG": _bars(closes, strong_v),
        })
        self.assertEqual(best.symbol, "STRONG")


class TestSizing(unittest.TestCase):
    def test_basic_sizing(self):
        r = size_position(price=100, buying_power=10000,
                          position_size_pct=0.05, max_order_dollars=1000)
        self.assertEqual(r.qty, 5)   # 5% of 10k = $500 -> 5 shares
        self.assertTrue(r.ok)

    def test_max_order_cap(self):
        r = size_position(price=100, buying_power=100000,
                          position_size_pct=0.05, max_order_dollars=1000)
        self.assertEqual(r.qty, 10)  # 5% = $5000 capped at $1000 -> 10 shares

    def test_too_expensive_skips(self):
        r = size_position(price=5000, buying_power=10000,
                          position_size_pct=0.05, max_order_dollars=1000)
        self.assertEqual(r.qty, 0)
        self.assertFalse(r.ok)
        self.assertIsNotNone(r.skipped_reason)

    def test_no_buying_power_skips(self):
        r = size_position(price=100, buying_power=0,
                          position_size_pct=0.05, max_order_dollars=1000)
        self.assertFalse(r.ok)


class TestTradePlan(unittest.TestCase):
    def _score(self):
        # A qualifying candidate with a known price and ATR.
        return score_symbol("UP", [
            Bar(close=c, volume=v, high=c + 1, low=c - 1)
            for c, v in zip(_uptrend_closes(), [1000] * 59 + [5000])
        ])

    def test_plan_has_stop_below_and_target_above(self):
        s = self._score()
        self.assertIsNotNone(s)
        plan = build_trade_plan(s, buying_power=100_000, risk_pct=0.01,
                                max_order_dollars=2000)
        self.assertTrue(plan.ok)
        self.assertLess(plan.stop, plan.entry)          # stop below entry
        self.assertGreater(plan.take_profit, plan.entry)  # target above entry
        self.assertGreaterEqual(plan.qty, 1)

    def test_reward_risk_is_two_to_one(self):
        s = self._score()
        plan = build_trade_plan(s, buying_power=100_000, risk_pct=0.01,
                                max_order_dollars=2000)
        # take-profit distance is 2x the stop distance -> RR ~ 2.0
        self.assertAlmostEqual(plan.rr_ratio, 2.0, places=1)
        self.assertAlmostEqual(plan.reward_total, plan.risk_total * 2, delta=0.05)

    def test_more_than_one_share_with_a_real_account(self):
        s = self._score()
        plan = build_trade_plan(s, buying_power=100_000, risk_pct=0.01,
                                max_order_dollars=2000)
        self.assertGreater(plan.qty, 1)  # not stuck on 1 share

    def test_short_plan_flips_stop_and_target(self):
        s = self._score()
        plan = build_trade_plan(s, buying_power=100_000, risk_pct=0.01,
                                max_order_dollars=2000, direction="short")
        self.assertEqual(plan.side, "sell")
        self.assertGreater(plan.stop, plan.entry)          # stop ABOVE for a short
        self.assertLess(plan.take_profit, plan.entry)      # target BELOW for a short
        self.assertAlmostEqual(plan.rr_ratio, 2.0, places=1)

    def test_direction_mapping(self):
        self.assertEqual(direction_of("Double bottom"), "long")
        self.assertEqual(direction_of("Breakout to new highs"), "long")
        self.assertEqual(direction_of("Head & shoulders (bearish)"), "short")
        self.assertEqual(direction_of("Double top"), "short")
        self.assertEqual(direction_of("Range / no clear pattern"), "none")


if __name__ == "__main__":
    unittest.main()
