from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from template_check import (APPEND_ONLY, GENERATION_PROMPT_DROPPED, HISTORY_CHANGED,  # noqa: E402
                            REPLY_DROPPED, classify, summarize)

# A prompt is history followed by a generation suffix; tokens are integers.
HISTORY = [1, 2, 3, 4, 5]
SUFFIX = [90, 91, 92, 93]


class TemplateCheckTests(unittest.TestCase):
    def test_previous_prompt_kept_whole_is_append_only(self):
        previous = HISTORY + [90, 91]  # template without a cue: suffix is reused
        current = previous + [7, 8, 9]
        row = classify(previous, len(HISTORY), current)
        self.assertEqual((row["category"], row["previous_unshared"]), (APPEND_ONLY, 0))

    def test_divergence_inside_generation_suffix_is_flagged(self):
        previous = HISTORY + SUFFIX
        current = HISTORY + [90, 91] + [7, 8]  # history renders without the cue
        row = classify(previous, len(HISTORY), current)
        self.assertEqual(row["category"], GENERATION_PROMPT_DROPPED)
        self.assertEqual((row["divergence"], row["previous_unshared"]), (7, 2))

    def test_suffix_divergence_without_the_reply_is_a_dropped_reply(self):
        previous = HISTORY + [90, 91]
        current = HISTORY + [90, 50, 51]  # a user message instead of the reply
        self.assertEqual(classify(previous, len(HISTORY), current, reply_kept=False)["category"], REPLY_DROPPED)

    def test_divergence_before_suffix_means_history_changed(self):
        previous = HISTORY + SUFFIX
        current = [1, 2, 99] + SUFFIX
        row = classify(previous, len(HISTORY), current)
        self.assertEqual((row["category"], row["divergence"]), (HISTORY_CHANGED, 2))

    def test_summary_counts_and_tokens(self):
        rows = [classify(HISTORY + SUFFIX, 5, HISTORY + [90, 91, 7]),
                classify(HISTORY + SUFFIX, 5, HISTORY + [90, 91, 8]),
                classify(HISTORY, 5, HISTORY + [6])]
        summary = summarize(rows)
        self.assertEqual(summary[GENERATION_PROMPT_DROPPED], {"transitions": 2, "previous_unshared_tokens": 4})
        self.assertEqual(summary[APPEND_ONLY]["transitions"], 1)


if __name__ == "__main__":
    unittest.main()
