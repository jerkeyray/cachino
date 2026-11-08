from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_attribution  # noqa: E402

BLOCK = 4
END = 99  # end-of-turn token


class FakeTokenizer:
    """Tokens are the integers written in the text, one per word."""

    def encode(self, text, add_special_tokens=False):
        return [int(word) for word in text.split()]

    def convert_tokens_to_ids(self, token):
        return END

    def decode(self, ids, skip_special_tokens=False):
        return " ".join(map(str, ids))


def exchange(prompt, reply, cached):
    request = {"messages": [{"role": "user", "content": "x"}], "ids": prompt}
    response = {"choices": [{"message": {"content": " ".join(map(str, reply))}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(reply) + 1,
                          "prompt_tokens_details": {"cached_tokens": cached}}}
    return request, response


def fake_render(tokenizer, request, pin, chat_template=None):
    return None, request["ids"]


class ReplyIdsTests(unittest.TestCase):
    def setUp(self):
        # The second prompt continues the first prompt and its whole reply, as
        # a template that renders history like the generation prompt would.
        first = list(range(1, 7))
        reply = list(range(20, 27))
        second = first + reply + [END, 40, 41, 42]
        self.rows = [exchange(first, reply, 0), exchange(second, [50], 12)]

    def run_attribution(self, **kwargs):
        with mock.patch.object(live_attribution, "render", fake_render):
            return live_attribution.attribute(self.rows, FakeTokenizer(), None, 64, BLOCK, **kwargs)

    def test_default_gives_replies_unique_ids(self):
        rows = self.run_attribution()
        self.assertEqual(rows[1]["predicted_cached"], 4)  # first prompt only, block-rounded
        self.assertFalse(rows[1]["matches_prediction"])

    def test_reply_ids_let_the_reply_blocks_be_reused(self):
        rows = self.run_attribution(use_reply_ids=True)
        self.assertEqual(rows[1]["predicted_cached"], 12)
        self.assertTrue(rows[1]["matches_prediction"])
        self.assertEqual(rows[1]["split"]["previous_reply"], 0)


class MissingCountTests(unittest.TestCase):
    """A count the engine did not report stays unknown in the split, never zero."""

    def attribute(self, rows):
        with mock.patch.object(live_attribution, "render", fake_render):
            return live_attribution.attribute(rows, FakeTokenizer(), None, 64, BLOCK)

    def test_missing_cached_tokens_leave_recomputed_unknown(self):
        request, response = exchange(list(range(1, 7)), [20], 0)
        del response["usage"]["prompt_tokens_details"]
        row = self.attribute([(request, response)])[0]
        self.assertIsNone(row["split"]["recomputed"])
        self.assertIsNone(row["matches_prediction"])

    def test_missing_reply_size_leaves_reply_and_new_content_unknown(self):
        first = exchange(list(range(1, 7)), [20, 21], 0)
        del first[1]["usage"]["completion_tokens"]
        second = exchange(list(range(1, 7)) + [30, 31, 32], [50], 4)
        second[0]["messages"] = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "20 21"}]
        row = self.attribute([first, second])[1]
        self.assertIsNone(row["split"]["previous_reply"])
        self.assertIsNone(row["split"]["new_content"])
        self.assertEqual(row["split"]["recomputed"], 5)


if __name__ == "__main__":
    unittest.main()
