"""Validate the released results/ tables and recompute the report's numbers from them.

Three layers of checks, all on public files with the standard library:

1. Integrity: every file listed in results/SHA256SUMS exists and matches, and
   no unlisted table is present.
2. Structure: each table has its documented columns, and every row obeys the
   cache's arithmetic: hits, predictions and bounds are whole 32-token blocks,
   never above prompt length minus 1, and parity flags are 0 or 1. Empty cells
   are allowed only where the data dictionary says a value can be unknown.
3. Claims: each headline number the report quotes is recomputed from the rows
   and compared with the value the report states.

These checks establish that the tables are internally consistent and support
the report's arithmetic. They cannot establish that the private runs happened
as recorded; that needs the captures. Run from the repository root:

  python3 analysis/validate_results.py
"""
from collections import defaultdict
import csv
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
BLOCK = 32

CONCURRENT = ["run", "seed", "sessions", "limit", "position", "session", "turn", "start_s", "end_s", "waited_ms",
              "prompt_tokens", "prompt_tokens_engine", "completion_tokens", "cached_tokens", "predicted_overlap",
              "waited_for_blocks", "predicted_sequential", "session_bound", "any_session_bound",
              "overlapping_requests", "engine_ttft_ms"]
# Columns per table, and which of them may be empty (an unknown value).
TABLES = {
    "cache-sweep/requests.csv": (["num_blocks", "position", "session", "turn", "prompt_tokens", "cached_tokens", "predicted",
                                  "session_bound", "any_session_bound", "parity"], set()),
    "gpu-t4/sizes.csv": (["num_blocks", "requests", "prompt_tokens", "cached_tokens", "recomputed_tokens", "eviction_tokens",
                          "predictor_exact", "parity_requests", "ttft_round1_s", "ttft_round2_s"], set()),
    "concurrency-matrix/requests.csv": (CONCURRENT, {"waited_ms", "engine_ttft_ms", "predicted_overlap"}),
    "session-limits/requests.csv": (CONCURRENT, {"engine_ttft_ms", "predicted_overlap"}),
    "session-limits/runs.csv": (["run", "seed", "sessions", "limit", "requests", "wall_clock_s", "total_wait_s"], set()),
    "single-sessions/turns.csv": (["session", "turn", "prompt_tokens", "prompt_tokens_engine", "completion_tokens",
                                   "cached_tokens", "predicted_cached", "best_lcp", "adjacent_lcp", "prompt_bound",
                                   "recomputed", "new_content", "previous_reply", "block_rounding", "eviction"], set()),
    "overlap-replay/requests.csv": (["run", "position", "session", "turn", "send_s", "recv_s", "prompt_tokens",
                                     "generated_replay", "engine", "parity", "session_bound", "predicted_admission",
                                     "predicted_completion", "predicted_prompt_only"], set()),
    "schedule-replay/requests.csv": (["order", "position", "session", "turn", "prompt_tokens", "predicted", "engine",
                                      "session_bound", "delay", "parity"], set()),
}
# Columns holding a prefix-cache hit or bound: whole blocks, at most prompt length - 1.
HITS = {"cached_tokens", "predicted", "session_bound", "any_session_bound", "predicted_overlap", "predicted_sequential",
        "predicted_cached", "prompt_bound", "engine", "predicted_admission", "predicted_completion", "predicted_prompt_only"}
FLAGS = {"parity", "waited_for_blocks"}
TEXT = {"session", "run", "limit", "order"}


class Checks:
    def __init__(self):
        self.failures, self.passed = [], 0

    def equal(self, name, got, want):
        if got == want:
            self.passed += 1
        else:
            self.failures.append("%s: got %r, report states %r" % (name, got, want))


def load(name):
    with (RESULTS / name).open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    fields, nullable = TABLES[name]
    if reader.fieldnames != fields:
        raise AssertionError("%s: columns %s, expected %s" % (name, reader.fieldnames, fields))
    parsed = []
    for i, row in enumerate(rows):
        out = {}
        for k, v in row.items():
            if v == "":
                if k not in nullable:
                    raise AssertionError("%s row %d: %s is empty but cannot be unknown" % (name, i, k))
                out[k] = None
            elif k in TEXT:
                out[k] = v
            else:
                out[k] = float(v) if any(c in v for c in ".e") else int(v)
        for k in HITS & set(out):
            v = out[k]
            if v is None:
                continue
            if v % BLOCK or v < 0 or (out["prompt_tokens"] and v > out["prompt_tokens"] - 1):
                raise AssertionError("%s row %d: %s=%s is not a whole-block hit below the prompt" % (name, i, k, v))
        for k in FLAGS & set(out):
            if out[k] not in (0, 1):
                raise AssertionError("%s row %d: %s must be 0 or 1" % (name, i, k))
        parsed.append(out)
    return parsed


def integrity(c):
    listed = {}
    for line in (RESULTS / "SHA256SUMS").read_text().splitlines():
        digest, path = line.split("  ", 1)
        listed[path] = digest
    for path, digest in listed.items():
        c.equal("sha256 " + path, hashlib.sha256((RESULTS / path).read_bytes()).hexdigest(), digest)
    present = {p.relative_to(RESULTS).as_posix() for p in RESULTS.rglob("*") if p.is_file()} - {"SHA256SUMS", "README.md"}
    c.equal("files listed in SHA256SUMS", sorted(present), sorted(listed))


def evicted(rows, hit="cached_tokens"):
    """Reuse lost below each request's own-session bound."""
    return sum(max(r["session_bound"] - r[hit], 0) for r in rows)


def score(rows, key):
    scored = [r for r in rows if r[key] is not None]
    return sum(r[key] == r["cached_tokens"] for r in scored), sum(abs(r[key] - r["cached_tokens"]) for r in scored)


def claims(c):
    # 3.1 and 3.5, result card: the fixed trace at seven sizes on the Mac CPU.
    sweep = defaultdict(list)
    for r in load("cache-sweep/requests.csv"):
        sweep[r["num_blocks"]].append(r)
    c.equal("CPU sweep sizes", sorted(sweep), [384, 438, 487, 488, 512, 640, 896])
    for blocks, rows in sweep.items():
        c.equal("CPU %d blocks exact" % blocks, sum(r["predicted"] == r["cached_tokens"] for r in rows), 30)
        c.equal("CPU %d blocks parity" % blocks, sum(r["parity"] for r in rows), 30)
    c.equal("487 blocks lost to eviction", evicted(sweep[487]), 32)
    c.equal("488 blocks lost to eviction", evicted(sweep[488]), 0)
    c.equal("CPU eviction at 384 and 438", [evicted(sweep[384]), evicted(sweep[438])], [12832, 4512])

    # 3.6: the same trace on the T4.
    gpu = {r["num_blocks"]: r for r in load("gpu-t4/sizes.csv")}
    c.equal("T4 exact and parity", [(r["predictor_exact"], r["parity_requests"]) for r in gpu.values()], [(30, 30)] * 4)
    c.equal("T4 eviction", [gpu[b]["eviction_tokens"] for b in (384, 438, 488, 640)], [12832, 4512, 0, 0])
    c.equal("T4 cached tokens equal the CPU", [gpu[b]["cached_tokens"] for b in (384, 438, 488)],
            [sum(r["cached_tokens"] for r in sweep[b]) for b in (384, 438, 488)])
    mean = {b: round((r["ttft_round1_s"] + r["ttft_round2_s"]) / 2, 1) for b, r in gpu.items()}
    c.equal("T4 mean time to first token (s)", [mean[b] for b in (384, 438, 488)], [43.9, 30.9, 23.6])
    c.equal("T4 increase over no eviction (%)", [round(100 * (mean[b] / mean[488] - 1)) for b in (384, 438)], [86, 31])

    # 3.1 and 3.5: nine concurrent runs and the naive baseline.
    conc = load("concurrency-matrix/requests.csv")
    c.equal("concurrent requests", len(conc), 265)
    c.equal("predictors exact and error", {k: score(conc, k) for k in
            ("any_session_bound", "session_bound", "predicted_sequential", "predicted_overlap")},
            {"any_session_bound": (256, 8320), "session_bound": (226, 24064),
             "predicted_sequential": (259, 1504), "predicted_overlap": (259, 352)})
    by_run = defaultdict(list)
    for r in conc:
        by_run[r["run"]].append(r)
    c.equal("runs that evicted", {k: evicted(v) for k, v in by_run.items() if evicted(v)}, {"s1-n3": 3680, "s2-n4": 4640})

    # 3.8: session limits.
    lim = load("session-limits/requests.csv")
    groups = defaultdict(list)
    for r in lim:
        groups[(r["limit"], r["seed"])].append(r)
    c.equal("no-limit eviction by seed", [evicted(groups[("none", s)]) for s in range(1, 6)], [0, 4640, 0, 66528, 256])
    c.equal("cap-2 eviction by seed", [evicted(groups[("cap-2", s)]) for s in range(1, 6)], [0] * 5)
    c.equal("budget-v2 eviction by seed", [evicted(groups[("budget-v2", s)]) for s in range(1, 6)], [0, 0, 0, 3456, 3616])
    off_cap = [r for r in lim if r["limit"] in ("none", "cap-2")]
    c.equal("no-limit and cap runs exact", (score(off_cap, "predicted_overlap")[0], len(off_cap)), (370, 384))
    budget = [r for r in lim if r["limit"].startswith("budget")]
    c.equal("budget runs exact", (score(budget, "predicted_overlap")[0], len(budget)), (304, 315))
    runs = {(r["limit"], r["seed"]): r["wall_clock_s"] for r in load("session-limits/runs.csv")}
    c.equal("seed 4 wall clock, none and cap-2 (s)", [runs[("none", 4)], runs[("cap-2", 4)]], [1242.8, 299.0])

    for name in ("concurrency-matrix/requests.csv", "session-limits/requests.csv", "single-sessions/turns.csv"):
        rows = load(name)
        c.equal(name + ": engine prompt length equals the local render",
                sum(r["prompt_tokens_engine"] != r["prompt_tokens"] for r in rows), 0)

    # 3.2: single sessions, counted from turn 1.
    turns = [r for r in load("single-sessions/turns.csv") if r["turn"] >= 1]
    shares = {}
    for name in ("qwen3-0.6b-greedy", "qwen3-0.6b-sampled", "qwen3-1.7b-varied"):
        rows = [r for r in turns if r["session"] == name]
        total = sum(r["new_content"] + r["previous_reply"] + r["block_rounding"] for r in rows)
        shares[name] = (round(100 * sum(r["cached_tokens"] for r in rows) / sum(r["prompt_tokens"] for r in rows)),
                        tuple(round(100 * sum(r[k] for r in rows) / total) for k in ("new_content", "previous_reply", "block_rounding")),
                        sum(r["eviction"] for r in rows))
    c.equal("single-session reuse, split and eviction", shares,
            {"qwen3-0.6b-greedy": (95, (68, 16, 16), 0), "qwen3-0.6b-sampled": (95, (64, 19, 17), 0),
             "qwen3-1.7b-varied": (79, (91, 7, 2), 0)})

    # 3.1: timed overlap replay.
    over = load("overlap-replay/requests.csv")
    c.equal("overlap replay requests and parity", (len(over), sum(r["parity"] for r in over)), (98, 98))
    c.equal("overlap predictors exact", [sum(r[k] == r["engine"] for r in over) for k in
            ("predicted_admission", "predicted_completion", "predicted_prompt_only")], [93, 92, 88])

    # 3.7: scheduling orders.
    orders = defaultdict(list)
    for r in load("schedule-replay/requests.csv"):
        orders[r["order"]].append(r)
    c.equal("orders exact on all 39", {k: sum(r["predicted"] == r["engine"] for r in v) for k, v in orders.items()},
            {k: 39 for k in ("recorded", "bounded-3", "bounded-2", "greedy", "serial")})
    c.equal("recomputed and evicted by order", {k: (sum(r["prompt_tokens"] - r["engine"] for r in v), evicted(v, "engine"))
                                                 for k, v in orders.items()},
            {"recorded": (26363, 4352), "bounded-3": (26171, 4160), "bounded-2": (22011, 0), "greedy": (22011, 0),
             "serial": (22011, 0)})


def main():
    c = Checks()
    integrity(c)
    claims(c)
    for f in c.failures:
        print("FAIL", f)
    print("%d checks passed, %d failed" % (c.passed, len(c.failures)))
    return 1 if c.failures else 0


if __name__ == "__main__":
    sys.exit(main())
