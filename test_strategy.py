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

    def test_invest_analysis_uptrend_is_good(self):
        from analysis import invest_analysis
        bars = [Bar(close=100 + i, volume=1000, high=100 + i + 1, low=100 + i - 1)
                for i in range(60)]
        a = invest_analysis(bars, atr=2.0)
        self.assertEqual(a.verdict, "GOOD")
        self.assertGreater(a.perf_pct, 0)
        self.assertGreater(a.exp_return_pct, 0)
        self.assertGreater(a.downside_pct, 0)


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
