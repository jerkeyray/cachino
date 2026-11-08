"""Regenerate the report's data figures from saved run outputs.

Reads the private run directories under artifacts/ (attribution, sweep,
schedule, session-cap and GPU results) and writes aggregate-only SVG figures,
plus every plotted number in data.json, to docs/figures/. No prompt text
reaches the output. The reuse curve is recomputed from the capture with
reuse_curve.py, so run this in the tokenizer environment:

  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
    --python 3.12 --no-python-downloads python -B experiments/figures/make_figures.py

The two explanatory diagrams come from make_diagrams.py, which needs no data.
"""
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(HERE))

from concurrent_attribution import load_requests  # noqa: E402
from matrix_summary import run_row  # noqa: E402
from reconstruct import _load_tokenizer  # noqa: E402
from reuse_curve import curve  # noqa: E402
from svgkit import column, compact, diamond, hbar, legend, line, svg, text  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402

A = ROOT / "artifacts"
OUT = ROOT / "docs/figures"
W, H = 760, 420
L, R, T, B = 78, W - 36, 120, H - 58  # plot area


class Frame:
    """Linear scales for the plot area, with hairline grid and tick labels."""

    def __init__(self, xmin, xmax, ymin, ymax, left=L, right=R, top=T, bottom=B):
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax
        self.left, self.right, self.top, self.bottom = left, right, top, bottom

    def x(self, v):
        return self.left + (v - self.xmin) / (self.xmax - self.xmin) * (self.right - self.left)

    def y(self, v):
        return self.bottom - (v - self.ymin) / (self.ymax - self.ymin) * (self.bottom - self.top)

    def yaxis(self, ticks, fmt=compact, title=None):
        out = [line(self.left, self.y(v), self.right, self.y(v)) for v in ticks]
        out += [text(self.left - 8, self.y(v) + 4, fmt(v), "tick muted", "end") for v in ticks]
        if title:
            out.append(text(self.left, self.top - 14, title, "lab ink2"))
        return "".join(out)

    def xaxis(self, ticks, fmt=compact, title=None, grid=False):
        out = [line(self.left, self.bottom, self.right, self.bottom, "axis")]
        if grid:
            out += [line(self.x(v), self.top, self.x(v), self.bottom) for v in ticks]
        out += [text(self.x(v), self.bottom + 17, fmt(v), "tick muted", "middle") for v in ticks]
        if title:
            out.append(text((self.left + self.right) / 2, self.bottom + 40, title, "lab ink2", "middle"))
        return "".join(out)


def kilo(v):
    """One tick style per axis: compact thousands for axes that reach 10,000."""
    return "0" if v == 0 else ("%gK" % (v / 1000))


def dot(x, y, cls, r=4.5):
    return '<circle cx="%.1f" cy="%.1f" r="%.1f" class="%s ring"/>' % (x, y, r, cls)


def fig_curve(data):
    pin = load_pin(ROOT / "experiments/tokenizer/qwen3-1.7b.json")
    rows = load_requests(A / "live-concurrent-20261107-01", _load_tokenizer(A / "qwen3-1.7b-tokenizer", pin)[0], pin)
    points = curve(rows, range(320, 701, 4), 32)
    exact = {p["num_blocks"]: p["eviction"] for p in curve(rows, [487, 488], 32)}
    cpu = {}
    for f in list((A / "cache-sweep-20261107-01").glob("sweep-*.json")) + list((A / "cache-sweep-20261108-01").glob("sweep-*.json")):
        d = json.loads(f.read_text())
        cpu[d["num_blocks"]] = d["eviction_tokens"]
    gpu = {s["num_blocks"]: s["eviction_tokens"] for s in json.loads((A / "gpu-result-kaggle-20261108/summary.json").read_text())["sizes"]}
    data["reuse_curve"] = {"predicted": [(p["num_blocks"], p["eviction"]) for p in points], "predicted_487_488": exact,
                           "engine_cpu": sorted(cpu.items()), "engine_gpu": sorted(gpu.items())}
    threshold = next(p["num_blocks"] for p in points if p["eviction"] == 0)
    f = Frame(320, 700, 0, 25000)
    body = [f.yaxis(range(0, 25001, 5000), fmt=kilo, title="Tokens lost to eviction"),
            f.xaxis(range(320, 701, 40), fmt=str, title="Cache size (blocks of 32 tokens)")]
    body.append('<path d="M%s L%.1f %.1f L%.1f %.1f Z" class="w1"/>' % (
        " L".join("%.1f %.1f" % (f.x(b), f.y(e)) for b, e in data["reuse_curve"]["predicted"]),
        f.x(700), f.y(0), f.x(320), f.y(0)))
    body.append('<polyline fill="none" class="l1" stroke-width="2" stroke-linejoin="round" points="%s"/>' % " ".join(
        "%.1f,%.1f" % (f.x(b), f.y(e)) for b, e in data["reuse_curve"]["predicted"]))
    tx = f.x(488)
    body.append(line(tx, f.top, tx, f.bottom, "lm", ' stroke-width="1"'))
    body.append(text(tx + 8, f.top + 14, "488 blocks: no eviction from here up", "lab ink"))
    body.append(text(tx + 8, f.top + 30, "487 blocks: 32 tokens lost (predicted and on the engine)", "small ink2"))
    # CPU and T4 measured the same values at shared sizes; the larger CPU dot stays visible around the diamond.
    body += [dot(f.x(b), f.y(e), "f2", 7) for b, e in sorted(cpu.items()) if 320 <= b <= 700]
    body += [diamond(f.x(b), f.y(e), 5, "f3 ring") for b, e in sorted(gpu.items())]
    body.append(legend(24, 76, [("line", "l1", "Predicted from the capture"), ("dot", "f2", "vLLM, Mac CPU"),
                                ("diamond", "f3", "vLLM, Tesla T4")]))
    return svg(W, H, "Eviction against cache size",
               "One captured three-session trace (30 requests) replayed at each size; the engine matched every prediction.",
               "".join(body), desc="Line of predicted tokens lost to eviction falling to zero at %d blocks, with CPU and "
                                   "T4 engine measurements on the line." % threshold)


def split_totals(path):
    live = json.loads(path.read_text())
    totals = {k: 0 for k in ("new_content", "previous_reply", "block_rounding", "eviction")}
    for row in live[1:]:
        for k in totals:
            if row["split"][k] is None:
                raise ValueError("%s: request %s has an unknown %s" % (path, row["index"], k))
            totals[k] += row["split"][k]
    # Turn 0 is a cold cache; reuse is counted from turn 1, as in the run records.
    prompt = sum(r["prompt_tokens_local"] for r in live[1:])
    cached = sum(r["cached_tokens"] for r in live[1:])
    return totals, round(100 * cached / prompt)


def fig_split(data):
    sessions = [("Qwen3-0.6B, 30 turns, greedy", A / "live-cpu-readonly-20261107-01/attribution/live.json"),
                ("Qwen3-0.6B, 30 turns, sampled", A / "live-cpu-sampled-20261107-01/attribution/live.json"),
                ("Qwen3-1.7B, 10 turns, varied", A / "live-cpu-1.7b-20261107-02/attribution/live.json")]
    data["recompute_split"], reused = {}, {}
    for name, path in sessions:
        data["recompute_split"][name], reused[name] = split_totals(path)
    data["reused_pct"] = reused
    h = 330
    f = Frame(0, 100, 0, 1, left=232, right=W - 150, top=112, bottom=h - 52)
    body = [f.xaxis(range(0, 101, 25), fmt=lambda v: "%d%%" % v, title="Share of recomputed tokens", grid=True)]
    keys = (("new_content", "f1"), ("previous_reply", "f2"), ("block_rounding", "f3"))
    for i, (name, totals) in enumerate(data["recompute_split"].items()):
        y = f.top + 12 + i * 52
        body.append(text(f.left - 12, y + 16, name, "lab ink", "end"))
        total = sum(totals[k] for k, _ in keys) or 1
        x = f.left
        for j, (k, cls) in enumerate(keys):
            width = (f.right - f.left) * totals[k] / total
            body.append(hbar(x, y, width - (2 if j < 2 else 0), 24, cls, round_end=j == 2))
            if width > 42:
                body.append(text(x + 8, y + 16.5, "%d%%" % round(100 * totals[k] / total), "small on b"))
            x += width
        body.append(text(f.right + 12, y + 11, "%d%% of prompt" % reused[name], "small ink"))
        body.append(text(f.right + 12, y + 25, "tokens reused", "small ink2"))
    body.append(legend(24, 80, [("sq", "f1", "New content"), ("sq", "f2", "Previous reply, prefilled again"),
                                ("sq", "f3", "Block rounding")]))
    return svg(W, h, "What a single session recomputes",
               "Recomputed tokens by cause from turn 1 on, mini-swe-agent on vLLM (Mac CPU); nothing was evicted.",
               "".join(body), desc="Three stacked bars: new content is 64 to 91 percent of recomputation, the "
                                   "re-prefilled reply and block rounding the rest.")


def fig_gpu(data):
    sizes = json.loads((A / "gpu-result-kaggle-20261108/summary.json").read_text())["sizes"]
    rows = [(s["num_blocks"], sum(s["ttft_ms_total_by_round"]) / len(s["ttft_ms_total_by_round"]) / 1000,
             s["eviction_tokens"]) for s in sizes]
    data["gpu_ttft"] = rows
    base = next(sec for b, sec, ev in rows if ev == 0)
    f = Frame(0, len(rows), 0, 56, bottom=H - 74)
    body = [f.yaxis(range(0, 51, 10), fmt=lambda v: "%d s" % v, title="Total time to first token, 30 requests"),
            line(f.left, f.bottom, f.right, f.bottom, "axis")]
    for i, (blocks, seconds, evicted) in enumerate(rows):
        cx = f.x(i + 0.5)
        body.append(column(cx - 12, f.bottom, f.y(seconds), 24, "f1"))
        body.append(text(cx, f.y(seconds) - 24, "%.1f s" % seconds, "lab ink b", "middle"))
        body.append(text(cx, f.y(seconds) - 9, ("+%d%%" % round(100 * (seconds / base - 1))) if evicted else "no eviction",
                         "small ink2", "middle"))
        body.append(text(cx, f.bottom + 18, "%d blocks" % blocks, "lab ink", "middle"))
        body.append(text(cx, f.bottom + 34, "{:,} tokens evicted".format(evicted) if evicted else "0 evicted", "small ink2", "middle"))
    return svg(W, H, "What eviction costs on a Tesla T4",
               "The same 30 requests sent one at a time at four cache sizes; vLLM's time to first token, mean of two rounds.",
               "".join(body), desc="Columns: total time to first token falls from 43.9 s at 384 blocks to 23.6 s once "
                                   "nothing is evicted.")


def fig_schedule(data):
    schedules = json.loads((A / "schedule-replay-20261108-01/result.json").read_text())["schedules"]
    data["schedules"] = {n: (e["engine"]["mean_delay"], e["engine"]["recomputed"]) for n, e in schedules.items()}
    f = Frame(-0.3, 6.3, 21000, 27000)
    body = [f.yaxis(range(21000, 27001, 1000), fmt=kilo, title="Recomputed tokens"),
            f.xaxis(range(0, 7), fmt=str, title="Mean delay per request (positions later than recorded)")]
    floor = min(r for _, r in data["schedules"].values())
    body.append(line(f.left, f.y(floor), f.right, f.y(floor), "lm"))
    body.append(text(f.right, f.y(floor) + 16, "{:,}: nothing evicted".format(floor), "small ink2", "end"))
    labels = {"recorded": ("Recorded interleaving", 10, 4, "start"), "bounded-3": ("At most 3 sessions", 10, 4, "start"),
              "bounded-2": ("At most 2", 10, -40, "start"), "greedy": ("Greedy", -10, -40, "end"),
              "serial": ("One at a time", 0, -12, "middle")}
    for name, (delay, recomputed) in data["schedules"].items():
        label, dx, dy, anchor = labels[name]
        if abs(dy) > 20:  # close neighbors get a leader line instead of a label on the point
            body.append(line(f.x(delay), f.y(recomputed) - 7, f.x(delay) + dx, f.y(recomputed) + dy + 4, "lm"))
        body.append(dot(f.x(delay), f.y(recomputed), "f1", 5))
        body.append(text(f.x(delay) + dx, f.y(recomputed) + dy, label, "lab ink", anchor))
    return svg(W, H, "Scheduling sessions to avoid eviction",
               "One evicting four-session trace replayed on vLLM in five orders; each session keeps its own turn order.",
               "".join(body), desc="Two sessions at a time or a greedy order removes eviction, cutting recomputation "
                                   "16.5 percent, for a mean delay of about four to five positions.")


def fig_cap(data):
    base = A / "limit-matrix-20261108"
    rows = []
    for seed in range(1, 6):
        # -drain is the second token-budget version, the one the report's table shows.
        off, cap, budget = (run_row(base / ("s%d-n4%s" % (seed, s))) for s in ("", "-limit2", "-drain"))
        rows.append({"seed": seed, "wall_off_s": off["wall_clock_s"], "wall_cap_s": cap["wall_clock_s"],
                     "wall_budget_s": budget["wall_clock_s"], "evicted_off": off["eviction"],
                     "evicted_cap": cap["eviction"], "evicted_budget": budget["eviction"]})
    data["session_limits"] = rows
    h = 390
    f = Frame(0, 1300, 0, 1, left=110, right=W - 214, top=126, bottom=h - 52)
    body = [f.xaxis(range(0, 1301, 250), fmt=lambda v: "%d s" % v, title="Wall clock for all four sessions (Mac CPU)", grid=True)]
    body.append(text(f.right + 14, f.top - 4, "Tokens lost to eviction", "small ink2"))
    body.append(text(f.right + 14, f.top + 10, "off / cap / budget", "small muted"))
    for i, r in enumerate(rows):
        y = f.top + 26 + i * 42
        xs = [f.x(r[k]) for k in ("wall_off_s", "wall_cap_s", "wall_budget_s")]
        body.append(text(f.left - 14, y + 4, "Seed %d" % r["seed"], "lab ink", "end"))
        body.append(line(min(xs), y, max(xs), y, "lm", ' stroke-width="2"'))
        body.append(dot(xs[0], y, "f2", 5))
        body.append(dot(xs[1], y, "f1", 5))
        body.append(diamond(xs[2], y, 5.5, "f3 ring"))
        lost = " / ".join("{:,}".format(r[k]) for k in ("evicted_off", "evicted_cap", "evicted_budget"))
        body.append(text(f.right + 14, y + 4, lost, "small " + ("ink" if r["evicted_off"] else "ink2")))
    body.append(legend(24, 80, [("dot", "f2", "No limit"), ("dot", "f1", "At most 2 active sessions"),
                                ("diamond", "f3", "Token budget, 14,016")]))
    return svg(W, h, "Session limits pay off only when sessions outgrow the cache",
               "Four Qwen3-1.7B sessions through one proxy and one 438-block cache, five engine seeds.",
               "".join(body), desc="Per seed, wall clock with no limit, a two-session cap and a token budget. The cap "
                                   "removed eviction in every seed; the budget avoided waits but lost tokens from "
                                   "sessions it paused in seeds 4 and 5.")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = {}
    for name, make in (("eviction-curve", fig_curve), ("recompute-split", fig_split), ("gpu-ttft", fig_gpu),
                       ("scheduling", fig_schedule), ("session-cap", fig_cap)):
        (OUT / (name + ".svg")).write_text(make(data))
    (OUT / "data.json").write_text(json.dumps(data, indent=1) + "\n")
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
