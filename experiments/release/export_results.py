"""Export the per-request numbers behind the report into public CSV tables.

Reads the private run directories under artifacts/ and writes results/ with
token counts, predictions, bounds, parity flags and engine timings only. No
prompt or reply text, token IDs, hashes of either, or absolute timestamps leave
artifacts/: request times are written as seconds since the run's first request.
A missing engine value is written as an empty cell, never as zero.

Run from the repository root with the standard library:

  python3 experiments/release/export_results.py

Then check the release with `python3 analysis/validate_results.py`.
"""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "artifacts"
OUT = ROOT / "results"
sys.path.insert(0, str(ROOT / "analysis"))

from matrix_summary import run_row  # noqa: E402

ENGINE = "vLLM 0.33.0rc1.dev595+g2173d7121, prefix caching, 32-token blocks, SHA-256 block hashes"


def cell(v):
    """CSV cell: empty for unknown, 1/0 for booleans, the value otherwise."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    return v


def write(folder, name, fields, rows, metadata=None):
    path = OUT / folder
    path.mkdir(parents=True, exist_ok=True)
    with (path / name).open("w", newline="") as handle:
        out = csv.writer(handle, lineterminator="\n")
        out.writerow(fields)
        for row in rows:
            out.writerow([cell(row.get(f)) for f in fields])
    if metadata is not None:
        (path / "metadata.json").write_text(json.dumps(metadata, indent=1) + "\n")


def cache_sweep():
    fields = ["num_blocks", "position", "session", "turn", "prompt_tokens", "cached_tokens", "predicted",
              "session_bound", "any_session_bound", "parity"]
    rows = []
    for f in sorted(list((A / "cache-sweep-20261107-01").glob("sweep-*.json")) +
                    list((A / "cache-sweep-20261108-01").glob("sweep-*.json")), key=lambda p: json.loads(p.read_text())["num_blocks"]):
        d = json.loads(f.read_text())
        for i, r in enumerate(d["rows"]):
            rows.append(dict(r, num_blocks=d["num_blocks"], position=i))
    write("cache-sweep", "requests.csv", fields, rows, {
        "runs": ["cache-sweep-20261107-01", "cache-sweep-20261108-01"],
        "trace": "live-concurrent-20261107-01 (three Qwen3-1.7B mini-swe-agent sessions, 30 requests)",
        "protocol": "sequential replay: recorded start order, one request at a time, max_tokens 1, cache reset per size",
        "engine": ENGINE + "; Mac CPU (Apple M5), built from source",
        "replay_model": "Qwen/Qwen3-0.6B at c1899de289a04d12100db370d81485cdf75e47ca, FP16",
        "dates_utc": ["2026-11-07", "2026-11-08"], "report": "3.1, 3.5; result card 487-488"})


def gpu():
    s = json.loads((A / "gpu-result-kaggle-20261108/summary.json").read_text())
    rows = [dict(x, parity_requests=x["parity"], ttft_round1_s=round(x["ttft_ms_total_by_round"][0] / 1000, 4),
                 ttft_round2_s=round(x["ttft_ms_total_by_round"][1] / 1000, 4)) for x in s["sizes"]]
    write("gpu-t4", "sizes.csv", ["num_blocks", "requests", "prompt_tokens", "cached_tokens", "recomputed_tokens",
                                  "eviction_tokens", "predictor_exact", "parity_requests", "ttft_round1_s", "ttft_round2_s"], rows, {
        "trace": "live-concurrent-20261107-01, 30 requests, bundle gpu-bundle-20261108-01",
        "protocol": "sequential replay, two rounds per size after a reset, capture off",
        "engine": ENGINE + "; Kaggle Tesla T4 (one of two used), driver 580.178.04, torch 2.14.0+cu130",
        "replay_model": "Qwen/Qwen3-0.6B at c1899de289a04d12100db370d81485cdf75e47ca, FP16",
        "ttft": "sum of vLLM's per-request time_to_first_token_ms over the 30 requests",
        "ttft_fit": s["ttft_fit"], "per_request_rows": "not retained: only the printed summary was saved",
        "date_utc": "2026-11-08", "report": "3.6"})


CONCURRENT_FIELDS = ["run", "seed", "sessions", "limit", "position", "session", "turn", "start_s", "end_s", "waited_ms",
                     "prompt_tokens", "prompt_tokens_engine", "completion_tokens", "cached_tokens", "predicted_overlap",
                     "waited_for_blocks", "predicted_sequential", "session_bound", "any_session_bound",
                     "overlapping_requests", "engine_ttft_ms"]
LIMITS = {"": "none", "-limit2": "cap-2", "-budget": "budget-v1", "-drain": "budget-v2"}


def concurrent_rows(run_dir, run, seed, sessions, limit):
    rows = json.loads((run_dir / "concurrent-attribution.json").read_text())
    t0 = min(r["start"] for r in rows)
    out = []
    for i, r in enumerate(rows):
        out.append(dict(r, run=run, seed=seed, sessions=sessions, limit=limit, position=i,
                        start_s=round(r["start"] - t0, 3), end_s=round(r["end"] - t0, 3),
                        prompt_tokens=r["prompt_tokens_local"], prompt_tokens_engine=r["prompt_tokens"]))
    return out


def matrix(base, folder, report):
    rows, runs = [], []
    for d in sorted(p for p in (A / base).iterdir() if (p / "concurrent-attribution.json").exists()):
        m = re.fullmatch(r"s(\d+)-n(\d+)(-limit2|-budget|-drain)?", d.name)
        if not m:
            continue
        limit = LIMITS[m.group(3) or ""]
        rows += concurrent_rows(d, d.name, int(m.group(1)), int(m.group(2)), limit)
        s = run_row(d)
        runs.append({"run": d.name, "seed": int(m.group(1)), "sessions": int(m.group(2)), "limit": limit,
                     "requests": s["requests"], "wall_clock_s": s["wall_clock_s"], "total_wait_s": s["total_wait_s"]})
    return rows, runs


def concurrency():
    rows, runs = matrix("concurrency-matrix-20261107", "concurrency-matrix", "3.1, 3.5")
    write("concurrency-matrix", "requests.csv", CONCURRENT_FIELDS, rows, {
        "run": "concurrency-matrix-20261107: engine seeds 1-3 x 2, 3, 4 concurrent sessions",
        "protocol": "live run: Qwen3-1.7B mini-swe-agent sessions, temperature 0.7, step limit 10, read-only observations, one proxy per session",
        "engine": ENGINE + "; Mac CPU (Apple M5); 438 blocks (14,016 tokens)",
        "order": "rows in proxy start order; start_s and end_s are seconds since the run's first request",
        "date_utc": "2026-11-07", "report": "3.1, 3.5"})
    rows, runs = matrix("limit-matrix-20261108", "session-limits", "3.8")
    write("session-limits", "requests.csv", CONCURRENT_FIELDS, rows, {
        "run": "limit-matrix-20261108: engine seeds 1-5, four sessions through one shared proxy",
        "limits": {"none": "no limit", "cap-2": "-max-active-sessions 2", "budget-v1": "-session-token-budget 14016, first version",
                   "budget-v2": "-session-token-budget 14016, waits for younger in-flight requests (the report's table)"},
        "protocol": "live run: Qwen3-1.7B mini-swe-agent sessions, temperature 0.7, read-only observations",
        "engine": ENGINE + "; Mac CPU (Apple M5); 438 blocks (14,016 tokens)",
        "timing": "engine_ttft_ms is vLLM's per-request metric; wall_clock_s is the harness's; Mac CPU timing compares conditions only",
        "date_utc": "2026-11-08", "report": "3.8"})
    write("session-limits", "runs.csv", ["run", "seed", "sessions", "limit", "requests", "wall_clock_s", "total_wait_s"], runs)


def single_sessions():
    sessions = [("qwen3-0.6b-greedy", "live-cpu-readonly-20261107-01"), ("qwen3-0.6b-sampled", "live-cpu-sampled-20261107-01"),
                ("qwen3-1.7b-varied", "live-cpu-1.7b-20261107-02")]
    fields = ["session", "turn", "prompt_tokens", "prompt_tokens_engine", "completion_tokens", "cached_tokens",
              "predicted_cached", "best_lcp", "adjacent_lcp", "prompt_bound", "recomputed", "new_content",
              "previous_reply", "block_rounding", "eviction"]
    rows = []
    for label, run in sessions:
        for r in json.loads((A / run / "attribution/live.json").read_text()):
            split = r.get("split") or {}
            rows.append(dict(r, session=label, turn=r["index"], prompt_tokens=r["prompt_tokens_local"],
                             prompt_tokens_engine=r["prompt_tokens"], **{k: split.get(k) for k in
                             ("recomputed", "new_content", "previous_reply", "block_rounding", "eviction")}))
    write("single-sessions", "turns.csv", fields, rows, {
        "runs": {label: run for label, run in sessions},
        "protocol": "live single session, mini-swe-agent, read-only observations",
        "counting": "turn 0 starts from a cold cache; the report's reuse and split shares count from turn 1",
        "engine": ENGINE + "; Mac CPU (Apple M5)", "date_utc": "2026-11-07", "report": "3.2"})


def overlap():
    fields = ["run", "position", "session", "turn", "send_s", "recv_s", "prompt_tokens", "generated_replay", "engine",
              "parity", "session_bound", "predicted_admission", "predicted_completion", "predicted_prompt_only"]
    rows = []
    for f in sorted((A / "overlap-replay-20261108-01").glob("*.json")):
        d = json.loads(f.read_text())
        t0 = min(r["send"] for r in d["rows"])
        for i, r in enumerate(d["rows"]):
            rows.append(dict(r, run=f.stem, position=i, send_s=round(r["send"] - t0, 3), recv_s=round(r["recv"] - t0, 3),
                             predicted_prompt_only=r["predicted_prompt-only"]))
    write("overlap-replay", "requests.csv", fields, rows, {
        "run": "overlap-replay-20261108-01: three evicting runs replayed at their recorded offsets",
        "protocol": "timed overlap replay with ignore_eos and recorded completion lengths, Qwen3-0.6B, 438 blocks",
        "engine": ENGINE + "; Mac CPU (Apple M5)",
        "predictors": "decode blocks taken at admission, at completion, or ignored (prompt only)",
        "date_utc": "2026-11-08", "report": "3.1"})


def schedules():
    d = json.loads((A / "schedule-replay-20261108-01/result.json").read_text())
    fields = ["order", "position", "session", "turn", "prompt_tokens", "predicted", "engine", "session_bound", "delay", "parity"]
    rows = [dict(r, order=name, position=r["index"]) for name, s in d["schedules"].items() for r in s["rows"]]
    write("schedule-replay", "requests.csv", fields, rows, {
        "run": "schedule-replay-20261108-01: trace concurrency-matrix-20261107 s2-n4 in five orders",
        "protocol": "reordered sequential replay; each session keeps its turn order; delay counts positions",
        "engine": ENGINE + "; Mac CPU (Apple M5); Qwen3-0.6B replay; %d blocks" % d["num_blocks"],
        "date_utc": "2026-11-08", "report": "3.7"})


def checksums():
    files = sorted(p for p in OUT.rglob("*") if p.is_file() and p.name not in ("SHA256SUMS", "README.md"))
    lines = ["%s  %s" % (hashlib.sha256(p.read_bytes()).hexdigest(), p.relative_to(OUT).as_posix()) for p in files]
    (OUT / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def main():
    for step in (cache_sweep, gpu, concurrency, single_sessions, overlap, schedules):
        step()
    checksums()
    print("wrote", ", ".join(sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*.csv"))))


if __name__ == "__main__":
    main()
