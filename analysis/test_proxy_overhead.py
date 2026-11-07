from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/proxy-overhead"))

from measure import bootstrap_median_ci, percentile, summarize  # noqa: E402


class OverheadStatsTests(unittest.TestCase):
    def test_nearest_rank_percentile(self):
        values = list(range(1, 101))
        self.assertEqual(percentile(values, 50), 50)
        self.assertEqual(percentile(values, 95), 95)
        self.assertEqual(percentile([7], 95), 7)

    def test_bootstrap_is_seeded_and_brackets_the_median(self):
        values = [1.0, 2.0, 2.5, 3.0, 10.0]
        first = bootstrap_median_ci(values, resamples=500, seed=3)
        self.assertEqual(first, bootstrap_median_ci(values, resamples=500, seed=3))
        median, low, high = first
        self.assertTrue(low <= median <= high)

    def test_differences_are_paired_by_round(self):
        out = summarize({"direct": [1.0, 5.0, 9.0], "proxy": [1.5, 5.5, 9.5]})
        self.assertEqual(out["paired_differences_vs_direct"]["proxy"]["median_ms"], 0.5)
        self.assertNotIn("direct", out["paired_differences_vs_direct"])


if __name__ == "__main__":
    unittest.main()
