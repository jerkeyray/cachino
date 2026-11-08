"""Summaries keep a total unknown (None) when any value it needs is missing."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "experiments/gpu-confirmation"))

import gpu_replay  # noqa: E402
import matrix_summary  # noqa: E402


class GpuTotalTests(unittest.TestCase):
    def test_total_is_none_when_any_value_is_missing(self):
        self.assertEqual(gpu_replay.total([1, 2, 3]), 6)
        self.assertIsNone(gpu_replay.total([1, None, 3]))


class MatrixSummaryTests(unittest.TestCase):
    def run_row(self, ttfts):
        rows = [{"prompt_tokens_local": 64, "cached_tokens": 32, "session_bound": 32, "predicted_overlap": 32,
                 "predicted_sequential": 32, "engine_ttft_ms": t} for t in ttfts]
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "s1-n2"
            run.mkdir()
            (run / "concurrent-attribution.json").write_text(json.dumps(rows))
            (run / "result.json").write_text(json.dumps({"sessions": {}}))
            return matrix_summary.run_row(run)

    def test_engine_ttft_total_needs_every_request(self):
        self.assertEqual(self.run_row([1000.0, 500.0])["engine_ttft_total_s"], 1.5)
        self.assertIsNone(self.run_row([1000.0, None])["engine_ttft_total_s"])


if __name__ == "__main__":
    unittest.main()
