"""Summarize concurrent-session attributions across a seed x session-count matrix.

Reads <base>/s<seed>-n<sessions>/concurrent-attribution.json (from
concurrent_attribution.py) and result.json (from live_concurrent.py) and prints
one row per run plus per-session-count ranges. Eviction is reuse below each
request's own-session prompt bound; cross-session reuse is reuse above it.
"""
import argparse
import json
from pathlib import Path
import re


def run_row(path):
    rows = json.loads((path / "concurrent-attribution.json").read_text())
    result = json.loads((path / "result.json").read_text())
    prompt = sum(r["prompt_tokens_local"] for r in rows)
    cached = sum(r["cached_tokens"] for r in rows)
    recomputed = prompt - cached
    eviction = sum(max(r["session_bound"] - r["cached_tokens"], 0) for r in rows)
    return {"run": path.name, "requests": len(rows), "sessions_completed": sum(
                1 for r in result.values() if r.get("exit_status") == "LimitsExceeded"),
            "prompt_tokens": prompt, "reused_pct": round(100 * cached / prompt, 1),
            "recomputed": recomputed, "eviction": eviction,
            "eviction_pct_of_recomputed": round(100 * eviction / recomputed, 1) if recomputed else 0.0,
            "cross_session": sum(max(r["cached_tokens"] - r["session_bound"], 0) for r in rows),
            "overlap_exact": sum(r["cached_tokens"] == r["predicted_overlap"] for r in rows),
            "sequential_exact": sum(r["cached_tokens"] == r["predicted_sequential"] for r in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path)
    args = parser.parse_args()
    runs = sorted(p for p in args.base.iterdir() if re.fullmatch(r"s\d+-n\d+", p.name)
                  and (p / "concurrent-attribution.json").exists())
    rows = [run_row(p) for p in runs]
    by_n = {}
    for row in rows:
        by_n.setdefault(int(row["run"].split("-n")[1]), []).append(row)
    ranges = {n: {k: [min(r[k] for r in rs), max(r[k] for r in rs)]
                  for k in ("reused_pct", "eviction_pct_of_recomputed", "prompt_tokens")}
              for n, rs in sorted(by_n.items())}
    print(json.dumps({"runs": rows, "ranges_by_sessions": ranges}, indent=2))


if __name__ == "__main__":
    main()
