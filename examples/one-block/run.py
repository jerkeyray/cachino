"""Find the one-block eviction threshold of a small synthetic agent trace.

Three sessions share a 96-token system prompt, each adds its own 40-token task,
and every turn appends 37 to 70 new tokens. Turns interleave round-robin, as
sessions sharing one engine would. Token IDs are plain integers: no text,
tokenizer, model or engine is involved, so this runs on a fresh checkout with
the standard library alone.

It replays the trace through Cachino's free-queue predictor (the pinned vLLM
prefix-cache rules, 32-token blocks) at a range of cache sizes and prints:

- reused tokens and tokens lost to eviction at each size near the threshold;
- the smallest size with no eviction, and which request loses reuse one block
  below it;
- at that size, a naive predictor that assumes every matching prefix is still
  cached, against the free-queue prediction.

These are predictions only. In the study the same predictor was checked against
the engine's reported cached tokens; here there is no engine to check against.

Run from the repository root:
  python3 examples/one-block/run.py
Compare with examples/one-block/expected.txt.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))

from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import lcp  # noqa: E402
from reuse_curve import curve  # noqa: E402

BLOCK = 32


def trace():
    """Return the synthetic requests in arrival order, each with its token IDs."""
    system = list(range(1000, 1096))
    history = {s: system + [2000 + 100 * s + i for i in range(40)] for s in range(3)}
    rows, next_id = [], 10000
    for turn in range(6):
        for s in range(3):
            n = 37 + 11 * ((s + turn) % 4)
            history[s] = history[s] + list(range(next_id, next_id + n))
            next_id += n
            rows.append({"session": "s%d" % s, "turn": turn, "ids": list(history[s])})
    return rows


def bounds(rows, own_session):
    """Longest shared prefix with an earlier prompt, in whole blocks, capped at length - 1."""
    out = []
    for i, row in enumerate(rows):
        earlier = [o for o in rows[:i] if o["session"] == row["session"] or not own_session]
        best = max((lcp(row["ids"], o["ids"]) for o in earlier), default=0)
        out.append(min(best, len(row["ids"]) - 1) // BLOCK * BLOCK)
    return out


def hits(rows, num_blocks):
    model = FreeQueuePredictor(num_blocks, BLOCK)
    return [model.request(row["ids"], generated=1)["cached_tokens"] for row in rows]


def main():
    rows = trace()
    points = curve(rows, range(2, 101), BLOCK)
    threshold = next(p["num_blocks"] for p in points if p["eviction"] == 0)
    print("%d requests, %d prompt tokens, %d-token blocks" % (len(rows), sum(len(r["ids"]) for r in rows), BLOCK))
    print()
    print("| Cache blocks | Reused tokens | Lost to eviction |")
    print("| --- | --- | --- |")
    for p in points:
        if threshold - 3 <= p["num_blocks"] <= threshold + 1:
            print("| %d | %d | %d |" % (p["num_blocks"], p["reused"], p["eviction"]))
    print()
    print("No eviction from %d blocks up." % threshold)
    below, at = hits(rows, threshold - 1), hits(rows, threshold)
    for row, b, a in zip(rows, below, at):
        if b != a:
            print("At %d blocks, %s turn %d reuses %d tokens instead of %d." % (
                threshold - 1, row["session"], row["turn"], b, a))
    naive = bounds(rows, own_session=False)
    print()
    print("At %d blocks, a predictor that assumes every matching prefix is still cached" % (threshold - 1))
    print("agrees with the free-queue prediction on %d of %d requests; total difference %d tokens." % (
        sum(n == b for n, b in zip(naive, below)), len(rows), sum(abs(n - b) for n, b in zip(naive, below))))


if __name__ == "__main__":
    main()
