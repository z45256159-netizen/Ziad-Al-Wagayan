"""
Unit tests for the pure logic (strategy scoring + position sizing).

These run WITHOUT any API keys or network access:
    python -m unittest test_strategy -v
"""

import unittest

from sizing import size_position
from strategy import Bar, find_candidate, score_symbol


class TestScoring(unittest.TestCase):
    def test_uptrend_high_volume_passes(self):
        # Rising closes ending above the average, with a big volume spike today.
        bars = [Bar(close=c, volume=1000) for c in range(90, 110)]
        bars[-1] = Bar(close=bars[-1].close, volume=5000)  # volume spike today
        result = score_symbol("TEST", bars)
        self.assertIsNotNone(result)
        self.assertGreater(result.momentum_strength, 0)
        self.assertGreater(result.volume_ratio, 1)

    def test_downtrend_fails_momentum(self):
        # Falling prices -> last close below the SMA -> filtered out.
        bars = [Bar(close=c, volume=2000) for c in range(110, 90, -1)]
        self.assertIsNone(score_symbol("TEST", bars))

    def test_low_volume_fails(self):
        # Uptrend but today's volume is below average -> filtered out.
        bars = [Bar(close=c, volume=2000) for c in range(90, 110)]
        bars[-1] = Bar(close=bars[-1].close, volume=100)
        self.assertIsNone(score_symbol("TEST", bars))

    def test_find_candidate_picks_highest_score(self):
        strong = [Bar(close=c, volume=1000) for c in range(90, 110)]
        strong[-1] = Bar(close=130, volume=9000)  # far above SMA + huge volume
        weak = [Bar(close=c, volume=1000) for c in range(90, 110)]
        weak[-1] = Bar(close=110, volume=1100)
        best = find_candidate({"WEAK": weak, "STRONG": strong})
        self.assertEqual(best.symbol, "STRONG")


class TestSizing(unittest.TestCase):
    def test_basic_sizing(self):
        r = size_position(price=100, buying_power=10000,
                          position_size_pct=0.05, max_order_dollars=1000)
        # 5% of 10k = $500 -> 5 shares at $100.
        self.assertEqual(r.qty, 5)
        self.assertTrue(r.ok)

    def test_max_order_cap(self):
        r = size_position(price=100, buying_power=100000,
                          position_size_pct=0.05, max_order_dollars=1000)
        # 5% of 100k = $5000 but capped at $1000 -> 10 shares.
        self.assertEqual(r.qty, 10)

    def test_too_expensive_skips(self):
        r = size_position(price=5000, buying_power=10000,
                          position_size_pct=0.05, max_order_dollars=1000)
        # Budget $500 < one $5000 share -> skip.
        self.assertEqual(r.qty, 0)
        self.assertFalse(r.ok)
        self.assertIsNotNone(r.skipped_reason)

    def test_no_buying_power_skips(self):
        r = size_position(price=100, buying_power=0,
                          position_size_pct=0.05, max_order_dollars=1000)
        self.assertFalse(r.ok)


if __name__ == "__main__":
    unittest.main()
