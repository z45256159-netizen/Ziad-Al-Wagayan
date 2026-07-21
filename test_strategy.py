"""
Unit tests for the pure logic (multi-indicator strategy + position sizing).

These run WITHOUT any API keys or network access:
    python -m unittest test_strategy -v
"""

import unittest

from sizing import build_trade_plan, size_position
from strategy import Bar, find_candidate, score_symbol


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


if __name__ == "__main__":
    unittest.main()
