"""Compare session scheduling orders for one captured multi-session run.

A schedule is an order in which every captured request is served, keeping each
session's own turns in their recorded order (a turn depends on the reply
before it). The prompts are unchanged, so model behavior cannot change; only
which prefixes are resident when each request arrives.

Schedules:
- recorded: start-time order from the capture;
- serial: each session runs to completion, in order of first arrival;
- bounded-K: at most K sessions in flight, others admitted as one finishes,
  round-robin among those in flight;
- greedy: at each step serve the waiting turn with the most predicted reuse
  given the current predicted cache state.

For each schedule the free-queue predictor gives reused and evicted tokens
(eviction is reuse below the request's own-session prompt bound), and the
delay cost is how many positions later than its recorded position each request
is served. With --engine, each schedule is also replayed as token-ID prompts
with max_tokens 1 after a reset, and the engine's counts are recorded.
"""
import argparse
import copy
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


def sessions_of(rows):
    """Return {session: [row indices in turn order]} and sessions by first arrival."""
    by = {}
    for i, row in enumerate(rows):
        by.setdefault(row["session"], []).append(i)
    return by, sorted(by, key=lambda s: by[s][0])


def recorded(rows, num_blocks, block):
    return list(range(len(rows)))


def serial(rows, num_blocks, block):
    by, order = sessions_of(rows)
    return [i for s in order for i in by[s]]


def bounded(k):
    def schedule(rows, num_blocks, block):
        by, order = sessions_of(rows)
        queues = {s: list(by[s]) for s in order}
        waiting, active, out = list(order), [], []
        while waiting or active:
            while waiting and len(active) < k:
                active.append(waiting.pop(0))
            for s in list(active):
                out.append(queues[s].pop(0))
                if not queues[s]:
                    active.remove(s)
        return out
    return schedule


def greedy(rows, num_blocks, block):
    by, order = sessions_of(rows)
    queues = {s: list(by[s]) for s in order}
    model, out = FreeQueuePredictor(num_blocks, block), []
    while any(queues.values()):
        best = None
        for s in order:
            if not queues[s]:
                continue
            i = queues[s][0]
            trial = copy.deepcopy(model)
            hit = trial.request(rows[i]["ids"], generated=1)["cached_tokens"]
            # Ties keep recorded arrival order, so greedy only reorders for a reason.
            if best is None or hit > best[0] or (hit == best[0] and i < best[1]):
                best = (hit, i, s)
        model.request(rows[best[1]]["ids"], generated=1)
        out.append(queues[best[2]].pop(0))
    return out


def evaluate(rows, order, num_blocks, block):
    """Predict reuse for one order; return totals, per-request rows and delay."""
    model = FreeQueuePredictor(num_blocks, block)
    seen, per = [], []
    for position, i in enumerate(order):
        row = rows[i]
        n = len(row["ids"])
        same = [lcp(row["ids"], rows[j]["ids"]) for j in seen if rows[j]["session"] == row["session"]]
        bound = min(max(same, default=0), n - 1) // block * block
        hit = model.request(row["ids"], generated=1)["cached_tokens"]
        per.append({"index": i, "session": row["session"], "turn": row["turn"], "prompt_tokens": n,
                    "predicted": hit, "session_bound": bound, "delay": max(position - i, 0)})
        seen.append(i)
    return per


def totals(per, key):
    prompt = sum(r["prompt_tokens"] for r in per)
    reused = sum(r[key] for r in per)
    return {"prompt_tokens": prompt, "reused": reused, "recomputed": prompt - reused,
            "eviction": sum(max(r["session_bound"] - r[key], 0) for r in per),
            "mean_delay": round(sum(r["delay"] for r in per) / len(per), 2), "max_delay": max(r["delay"] for r in per)}


def replay(rows, per, engine, model_name):
    opener = build_opener(ProxyHandler({}))

    def post(path, body):
        req = Request(engine + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=900) as reply:
            return json.loads(reply.read())

    if post("/reset_prefix_cache", {}).get("success") is not True:
        raise RuntimeError("reset failed")
    for r in per:
        ids = rows[r["index"]]["ids"]
        response = post("/v1/completions", {"model": model_name, "prompt": ids, "max_tokens": 1,
                                            "temperature": 0, "return_token_ids": True})
        r["engine"] = ((response.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
        r["parity"] = response["choices"][0].get("prompt_token_ids") == ids


SCHEDULES = {"recorded": recorded, "serial": serial, "bounded-2": bounded(2), "bounded-3": bounded(3), "greedy": greedy}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--num-blocks", type=int, required=True)
    parser.add_argument("--block-size", type=int, default=32)
    parser.add_argument("--engine", help="replay each schedule on this engine")
    parser.add_argument("--served-model")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    pin = load_pin(args.pin)
    rows = load_requests(args.run, _load_tokenizer(args.assets, pin)[0], pin)
    result = {"run": str(args.run), "num_blocks": args.num_blocks, "requests": len(rows), "schedules": {}}
    for name, schedule in SCHEDULES.items():
        order = schedule(rows, args.num_blocks, args.block_size)
        if sorted(order) != list(range(len(rows))):
            raise ValueError(name + " does not serve every request once")
        per = evaluate(rows, order, args.num_blocks, args.block_size)
        entry = {"predicted": totals(per, "predicted")}
        if args.engine:
            replay(rows, per, args.engine, args.served_model)
            entry["engine"] = totals(per, "engine")
            entry["exact"] = sum(r["engine"] == r["predicted"] for r in per)
            entry["parity"] = sum(r["parity"] for r in per)
        entry["rows"] = per
        result["schedules"][name] = entry
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({n: {k: v for k, v in e.items() if k != "rows"} for n, e in result["schedules"].items()}, indent=1))


if __name__ == "__main__":
    main()
