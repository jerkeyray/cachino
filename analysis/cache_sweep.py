"""Replay one captured multi-session run at a given cache size.

The captured prompts of a concurrent run are rendered to exact token IDs and
replayed one at a time, in their recorded start order, as token-ID prompts to
/v1/completions with max_tokens 1, after a cache reset. Traffic is therefore
fixed and only the engine's cache capacity varies between invocations.
Overlap and generated tokens are left out on purpose: the replay holds the
prompt sequence constant so that eviction can be read as a function of cache
size alone.

Before any request, the free-queue predictor computes the expected cached
tokens for the stated block count; the result records both, plus each
request's own-session and any-session prompt bounds.

The replay model may differ from the captured one if the tokenizer files are
identical and the per-token KV size is the same, since cache accounting then
depends only on token IDs and block geometry.
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from concurrent_attribution import load_requests  # noqa: E402
from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import _load_tokenizer, lcp  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="live_concurrent.py output directory")
    parser.add_argument("--pin", type=Path, required=True, help="tokenizer pin of the captured model")
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--engine", default="http://127.0.0.1:18000")
    parser.add_argument("--served-model", required=True, help="model name the replay engine serves")
    parser.add_argument("--num-blocks", type=int, required=True, help="engine num_gpu_blocks, including the null block")
    parser.add_argument("--block-size", type=int, default=32)
    parser.add_argument("--out", type=Path, required=True, help="new JSON result file")
    args = parser.parse_args()
    pin = load_pin(args.pin)
    tokenizer = _load_tokenizer(args.assets, pin)[0]
    rows = load_requests(args.run, tokenizer, pin)
    model = FreeQueuePredictor(args.num_blocks, args.block_size)
    for i, row in enumerate(rows):
        n = len(row["ids"])
        row["predicted"] = model.request(row["ids"], generated=1)["cached_tokens"]
        same = [lcp(row["ids"], o["ids"]) for o in rows[:i] if o["session"] == row["session"]]
        row["session_bound"] = min(max(same, default=0), n - 1) // args.block_size * args.block_size
        row["any_session_bound"] = min(max((lcp(row["ids"], o["ids"]) for o in rows[:i]), default=0), n - 1) \
            // args.block_size * args.block_size
    opener = build_opener(ProxyHandler({}))

    def post(path, body):
        req = Request(args.engine + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=900) as reply:
            return json.loads(reply.read())

    if post("/reset_prefix_cache", {}).get("success") is not True:
        raise RuntimeError("reset failed")
    results = []
    for row in rows:
        response = post("/v1/completions", {"model": args.served_model, "prompt": row["ids"], "max_tokens": 1,
                                            "temperature": 0, "return_token_ids": True})
        choice = response["choices"][0]
        details = (response.get("usage") or {}).get("prompt_tokens_details") or {}
        results.append({"session": row["session"], "turn": row["turn"], "prompt_tokens": len(row["ids"]),
                        "cached_tokens": details.get("cached_tokens"), "predicted": row["predicted"],
                        "session_bound": row["session_bound"], "any_session_bound": row["any_session_bound"],
                        "parity": choice.get("prompt_token_ids") == row["ids"]})
    prompt = sum(r["prompt_tokens"] for r in results)
    cached = sum(r["cached_tokens"] for r in results)
    summary = {"num_blocks": args.num_blocks, "block_size": args.block_size, "requests": len(results),
               "prompt_tokens": prompt, "cached_tokens": cached, "recomputed_tokens": prompt - cached,
               "eviction_tokens": sum(max(r["session_bound"] - r["cached_tokens"], 0) for r in results),
               "cross_session_tokens": sum(max(r["cached_tokens"] - r["session_bound"], 0) for r in results),
               "predictor_exact": sum(r["cached_tokens"] == r["predicted"] for r in results),
               "parity": sum(r["parity"] for r in results), "rows": results}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(summary, stream, indent=2)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}))


if __name__ == "__main__":
    main()
