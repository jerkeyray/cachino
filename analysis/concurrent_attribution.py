"""Attribute prefix-cache reuse across concurrent agent sessions on one engine.

Reads each session's Cachino capture, renders exact local token IDs, and orders
all requests on one timeline from capture start times and durations. Each
request is admitted at its start and released at its end. Two predictors replay
that timeline:

- overlap: FreeQueuePredictor.allocate/free, so in-flight requests hold blocks
  and other sessions cannot evict them;
- sequential: each request served to completion in start order, ignoring
  overlap.

Proxy start times are not engine scheduling times, and decode blocks are taken
at admission rather than as generated, so both are approximations; the gap to
the engine's cached_tokens is the measurement.

For every request it also reports two prompt bounds, rounded down to blocks and
capped at prompt length - 1: the longest exact-ID prefix shared with an earlier
prompt of the same session, and with an earlier prompt of any session. Reuse
below the session bound is lost to eviction or to blocks not yet stored; reuse
above it came from another session.

Run in the tokenizer environment:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \\
    --python 3.12 --no-python-downloads python -B analysis/concurrent_attribution.py \\
    --run artifacts/live-concurrent-NEW --num-blocks 438 --block-size 32 \\
    --pin experiments/tokenizer/qwen3-1.7b.json --assets artifacts/qwen3-1.7b-tokenizer
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from live_attribution import details  # noqa: E402
from predict import FreeQueuePredictor  # noqa: E402
from reconstruct import _load_tokenizer, lcp, render  # noqa: E402
from report import body_bytes, read_events  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402


def load_requests(run_dir, tokenizer, pin):
    """Return completed requests from every session with timing and token IDs."""
    run_dir = Path(run_dir).resolve()
    shared = (run_dir / "capture").is_dir()
    # A shared proxy writes one capture for every session; separate proxies
    # write one per session directory.
    captures = [(run_dir / "capture", None)] if shared else \
        [((d / "capture").resolve(), d.name) for d in sorted(run_dir.glob("session-*"))]
    # With the active-session limit, a captured request starts on arrival but is
    # forwarded only after its logged wait; the engine sees the later time.
    waits = {}
    if (run_dir / "result.json").exists():
        waits = {w["request_id"]: w["waited_ms"] for w in json.loads((run_dir / "result.json").read_text()).get("waits", [])}
    rows, turns = [], {}
    for capture, label in captures:
        records, warnings = read_events(capture)
        if warnings:
            raise ValueError("capture warnings in %s" % capture)
        for record in records:
            if record.get("http_status") != 200:
                continue
            bodies = []
            for name in ("request", "response"):
                data, integrity = body_bytes(capture, record, name)
                if integrity != "verified":
                    raise ValueError("unverified body in %s" % capture)
                bodies.append(json.loads(data))
            session = label or record.get("session_id")
            # The limiter logs only requests that waited, so absent means none.
            waited = waits.get(record["request_id"], 0.0)
            arrival = datetime.fromisoformat(record["started_at"].replace("Z", "+00:00")).timestamp()
            rows.append({"session": session, "turn": turns.get(session, 0), "start": arrival + waited / 1000.0,
                         "end": arrival + record["duration_ms"] / 1000.0, "waited_ms": waited,
                         "ids": render(tokenizer, bodies[0], pin)[1], **details(bodies[1])})
            turns[session] = turns.get(session, 0) + 1
    return sorted(rows, key=lambda row: row["start"])


def attribute(rows, num_blocks, block):
    """Fill predictions and bounds into rows (already sorted by start)."""
    events = [(row["start"], 1, i) for i, row in enumerate(rows)] + [(row["end"], 0, i) for i, row in enumerate(rows)]
    overlap, held, waiting = FreeQueuePredictor(num_blocks, block), {}, []

    def admit(i):
        """Admit request i if its blocks fit; otherwise it waits, as in the scheduler queue."""
        try:
            prediction, held[i] = overlap.allocate(rows[i]["ids"], generated=max(rows[i]["completion_tokens"] or 1, 1))
        except ValueError:
            return False
        rows[i]["predicted_overlap"] = prediction["cached_tokens"]
        return True

    for _, kind, i in sorted(events):  # frees sort before admissions at equal times
        if kind == 1:
            rows[i]["waited_for_blocks"] = not (not waiting and admit(i))
            if rows[i]["waited_for_blocks"]:
                waiting.append(i)
        elif i in held:
            overlap.free(held.pop(i))
            while waiting and admit(waiting[0]):
                waiting.pop(0)
        else:
            # The request never fit before its captured end; record that.
            waiting.remove(i)
            rows[i]["predicted_overlap"] = None
    sequential = FreeQueuePredictor(num_blocks, block)
    for i, row in enumerate(rows):
        row["predicted_sequential"] = sequential.request(row["ids"], generated=max(row["completion_tokens"] or 1, 1))
        row["predicted_sequential"] = row["predicted_sequential"]["cached_tokens"]
        n = len(row["ids"])
        same = [lcp(row["ids"], other["ids"]) for other in rows[:i] if other["session"] == row["session"]]
        anyone = [lcp(row["ids"], other["ids"]) for other in rows[:i]]
        row["session_bound"] = min(max(same, default=0), n - 1) // block * block
        row["any_session_bound"] = min(max(anyone, default=0), n - 1) // block * block
        row["overlapping_requests"] = sum(1 for other in rows if other is not row
                                          and other["start"] < row["end"] and other["end"] > row["start"])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="live_concurrent.py output directory")
    parser.add_argument("--num-blocks", type=int, required=True)
    parser.add_argument("--block-size", type=int, required=True)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    args = parser.parse_args()
    pin = load_pin(args.pin)
    tokenizer = _load_tokenizer(args.assets, pin)[0]
    rows = attribute(load_requests(args.run, tokenizer, pin), args.num_blocks, args.block_size)
    for row in rows:
        row["prompt_tokens_local"] = len(row.pop("ids"))
    out = args.run / "concurrent-attribution.json"
    out.write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
