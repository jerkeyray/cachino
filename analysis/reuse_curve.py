"""Predict prefix reuse over a range of cache sizes for one captured run.

Uses the same sequential replay as cache_sweep.py: every captured prompt in
recorded start order, prompt tokens only, after a reset. For each block count
it reports predicted reused tokens and the tokens lost to eviction (reuse below
the request's own-session prompt bound). The smallest block count with no
eviction is the cliff for this trace. No engine is contacted; the curve is a
prediction to confirm at a few points with cache_sweep.py.
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from concurrent_attribution import load_requests  # noqa: E402
from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import _load_tokenizer, lcp  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402


def curve(rows, block_counts, block):
    """Return [{num_blocks, reused, eviction}] for each count that fits every prompt."""
    bounds = []
    for i, row in enumerate(rows):
        same = [lcp(row["ids"], o["ids"]) for o in rows[:i] if o["session"] == row["session"]]
        bounds.append(min(max(same, default=0), len(row["ids"]) - 1) // block * block)
    out = []
    for count in block_counts:
        model = FreeQueuePredictor(count, block)
        try:
            hits = [model.request(row["ids"], generated=1)["cached_tokens"] for row in rows]
        except ValueError:
            continue  # a prompt does not fit in this cache at all
        out.append({"num_blocks": count, "reused": sum(hits),
                    "eviction": sum(max(b - h, 0) for b, h in zip(bounds, hits))})
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--min-blocks", type=int, required=True)
    parser.add_argument("--max-blocks", type=int, required=True)
    parser.add_argument("--block-size", type=int, default=32)
    args = parser.parse_args()
    pin = load_pin(args.pin)
    rows = load_requests(args.run, _load_tokenizer(args.assets, pin)[0], pin)
    points = curve(rows, range(args.min_blocks, args.max_blocks + 1), args.block_size)
    cliff = next((p["num_blocks"] for p in points if p["eviction"] == 0), None)
    print(json.dumps({"requests": len(rows), "prompt_tokens": sum(len(r["ids"]) for r in rows),
                      "cliff_num_blocks": cliff, "points": points}))


if __name__ == "__main__":
    main()
