"""Replay a captured concurrent run with its recorded timing, and test overlap models.

Each captured request is sent at its recorded start offset, in its own thread,
as a token-ID prompt to /v1/completions with greedy decoding, ignore_eos and
max_tokens equal to its recorded completion length. Generated text differs
from the live run, but each request holds the same number of KV blocks for a
similar time, and the traffic is fixed, so a replay can be repeated.

The replay's own send and receive times then drive three variants of the
free-queue predictor, which differ only in when decode blocks are taken:

- admission: all prompt and decode blocks at send (the concurrent attribution's model);
- prompt-only: prompt blocks at send, decode blocks ignored;
- completion: prompt blocks at send, decode blocks taken at receive and freed
  tail first before the prompt blocks, the order the engine frees them.

The engine decodes block by block, so the truth lies between "prompt-only" and
"admission". The variant closest to the engine's counts says how much decode
timing matters for exactness under overlap.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "analysis"), str(ROOT / "experiments/cache-eviction"), str(HERE)]

from concurrent_attribution import load_requests  # noqa: E402
from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import _load_tokenizer, lcp  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402


def replay(rows, engine, model_name):
    """Send every request at its recorded offset; fill send/recv times and counts."""
    opener = build_opener(ProxyHandler({}))

    def post(path, body):
        req = Request(engine + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=1800) as reply:
            return json.loads(reply.read())

    if post("/reset_prefix_cache", {}).get("success") is not True:
        raise RuntimeError("reset failed")
    base, origin = rows[0]["start"], time.monotonic()

    def worker(row):
        delay = (row["start"] - base) - (time.monotonic() - origin)
        if delay > 0:
            time.sleep(delay)
        row["send"] = time.monotonic() - origin
        response = post("/v1/completions", {"model": model_name, "prompt": row["ids"],
                                            "max_tokens": max(row["completion_tokens"] or 1, 1),
                                            "temperature": 0, "ignore_eos": True, "return_token_ids": True})
        row["recv"] = time.monotonic() - origin
        choice = response["choices"][0]
        row["engine"] = ((response.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
        row["parity"] = choice.get("prompt_token_ids") == row["ids"]
        row["generated_replay"] = len(choice.get("token_ids") or [])

    threads = [threading.Thread(target=worker, args=(row,)) for row in rows]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def predict_variant(rows, variant, num_blocks, block):
    """Return predicted cached tokens per row for one decode-timing variant."""
    model, held, waiting, out = FreeQueuePredictor(num_blocks, block), {}, [], {}
    events = sorted([(r["send"], 1, i) for i, r in enumerate(rows)] + [(r["recv"], 0, i) for i, r in enumerate(rows)])

    def admit(i):
        g = max(rows[i]["generated_replay"], 1) if variant == "admission" else 1
        try:
            prediction, held[i] = model.allocate(rows[i]["ids"], generated=g)
        except ValueError:
            return False
        out[i] = prediction["cached_tokens"]
        return True

    for _, kind, i in events:
        if kind == 1:
            if waiting or not admit(i):
                waiting.append(i)
            continue
        if i not in held:
            waiting.remove(i)
            out[i] = None
            continue
        if variant == "completion" and rows[i]["generated_replay"] > 1:
            decode = [("decode", i, k) for k in range(rows[i]["generated_replay"] - 1)]
            try:
                _, decode_blocks = model.allocate(decode, generated=1)
                model.free(decode_blocks)
            except ValueError:
                pass  # no room left; the engine would have preempted, not modelled
        model.free(held.pop(i))
        while waiting and admit(waiting[0]):
            waiting.pop(0)
    return [out.get(i) for i in range(len(rows))]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--engine", default="http://127.0.0.1:18000")
    parser.add_argument("--served-model", required=True)
    parser.add_argument("--num-blocks", type=int, required=True)
    parser.add_argument("--block-size", type=int, default=32)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    pin = load_pin(args.pin)
    rows = load_requests(args.run, _load_tokenizer(args.assets, pin)[0], pin)
    replay(rows, args.engine, args.served_model)
    variants = {v: predict_variant(rows, v, args.num_blocks, args.block_size)
                for v in ("admission", "prompt-only", "completion")}
    for i, row in enumerate(rows):
        n = len(row["ids"])
        same = [lcp(row["ids"], o["ids"]) for o in rows if o["session"] == row["session"] and o["send"] < row["send"]]
        row["session_bound"] = min(max(same, default=0), n - 1) // args.block_size * args.block_size
        for v, values in variants.items():
            row["predicted_" + v] = values[i]
    summary = {"run": str(args.run), "num_blocks": args.num_blocks, "requests": len(rows),
               "parity": sum(r["parity"] for r in rows),
               "engine_eviction": sum(max(r["session_bound"] - r["engine"], 0) for r in rows),
               "exact": {v: sum(r["engine"] == r["predicted_" + v] for r in rows) for v in variants},
               "abs_error_tokens": {v: sum(abs(r["engine"] - (r["predicted_" + v] or 0)) for r in rows) for v in variants},
               "max_overlap": max(sum(1 for o in rows if o["send"] < r["recv"] and o["recv"] > r["send"]) for r in rows)}
    for row in rows:
        row["prompt_tokens"] = len(row.pop("ids"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump({**summary, "rows": rows}, stream, indent=2)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
