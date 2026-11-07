from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/cache-eviction"))

from predict import FreeQueuePredictor, predict  # noqa: E402

B = 4


def prompt(tag, n):
    """Distinct token IDs whose every prefix block is unique to the tag."""
    return [tag * 1000 + i for i in range(n)]


class PredictorTests(unittest.TestCase):
    def test_repeat_hits_full_blocks_below_last_token(self):
        model = FreeQueuePredictor(16, B)
        a = prompt(1, 13)  # 3 full blocks and a partial
        self.assertEqual(model.request(a), {"cached_tokens": 0, "created_cache_tokens": 12})
        self.assertEqual(model.request(a), {"cached_tokens": 12, "created_cache_tokens": 0})

    def test_shared_prefix_hits_only_whole_shared_blocks(self):
        model = FreeQueuePredictor(16, B)
        a = prompt(1, 13)
        model.request(a)
        late = a[:10] + [9] + a[11:]  # differs inside block 3
        self.assertEqual(model.request(late), {"cached_tokens": 8, "created_cache_tokens": 4})

    def test_eviction_removes_the_tail_of_a_prompt_first(self):
        # 9 usable blocks. A takes 4 (3 full); 5 never-used remain, plus A's
        # partial goes to the head. A 7-block filler (6 full) then evicts
        # exactly one hashed block, which must be A's last. A 6-block filler
        # would fit without evicting anything.
        model = FreeQueuePredictor(10, B)
        a = prompt(1, 13)
        model.request(a)
        model.request(prompt(2, 25))
        self.assertEqual(model.request(a)["cached_tokens"], 8)
        fits = FreeQueuePredictor(10, B)
        fits.request(a)
        fits.request(prompt(2, 21))
        self.assertEqual(fits.request(a)["cached_tokens"], 12)

    def test_reset_clears_hashes(self):
        model = FreeQueuePredictor(16, B)
        a = prompt(1, 13)
        model.request(a)
        model.reset()
        self.assertEqual(model.request(a)["cached_tokens"], 0)

    def test_predict_follows_sequence_and_rejects_ambiguous_lengths(self):
        tokens = {"a": prompt(1, 13), "f": prompt(2, 25)}
        sequence = [{"step": "r", "action": "reset_prefix_cache"},
                    {"step": "a1", "action": "send", "probe": "a"},
                    {"step": "f1", "action": "send", "probe": "f"},
                    {"step": "a2", "action": "send", "probe": "a"}]
        out = predict(sequence, tokens, 10, B)
        self.assertEqual(out["a2"]["cached_tokens"], 8)
        with self.assertRaises(ValueError):
            FreeQueuePredictor(10, B).request(prompt(1, 12))


if __name__ == "__main__":
    unittest.main()
