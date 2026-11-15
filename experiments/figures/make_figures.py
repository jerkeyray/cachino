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
from svgkit import column, diamond, hbar, line, svg, text  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402

A = ROOT / "artifacts"
OUT = ROOT / "docs/figures"


class Frame:
    """Linear scales from data values to the plot area's SVG coordinates."""

    def __init__(self, xmin, xmax, ymin, ymax, left, right, top, bottom):
        self.xmin, self.xmax, self.ymin, self.ymax = xmin, xmax, ymin, ymax
        self.left, self.right, self.top, self.bottom = left, right, top, bottom

    def x(self, v):
        return self.left + (v - self.xmin) / (self.xmax - self.xmin) * (self.right - self.left)

    def y(self, v):
        return self.bottom - (v - self.ymin) / (self.ymax - self.ymin) * (self.bottom - self.top)


def kilo(v):
    """One tick style per axis: compact thousands for axes that reach 10,000."""
    return "0" if v == 0 else ("%gK" % (v / 1000))


def dot(x, y, cls, r=4.5):
    return '<circle cx="%.1f" cy="%.1f" r="%.1f" class="%s ring"/>' % (x, y, r, cls)


# Every data figure shares the eviction curve's layout: 560 units wide, a 22px
# title, a 15px subtitle and 13 to 14px labels, so all figures render at the
# same type size when a page scales them to its column width.
FW = 560


def t(x, y, s, size=14, cls="ink2", anchor="start"):
    """Text at an explicit size; the svgkit classes are the older, smaller scale."""
    return text(x, y, s, cls, anchor, ' font-size="%g"' % size)


def header(title, subtitle):
    return t(24, 32, title, 22, "ink b") + t(24, 58, subtitle, 15)


def key(x, y, items):
    """One-row legend at 14px; items are (mark, slot, label) with mark 'sq', 'dot' or 'diamond'."""
    out = []
    for mark, slot, label in items:
        if mark == "dot":
            out.append(dot(x + 6, y - 5, slot, 6))
        elif mark == "diamond":
            out.append(diamond(x + 6, y - 5, 6, slot + " ring"))
        else:
            out.append('<rect x="%.1f" y="%.1f" width="12" height="12" rx="2" class="%s"/>' % (x, y - 11, slot))
        out.append(t(x + 20, y, label))
        x += 20 + 7.6 * len(label) + 22
    return "".join(out)


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
    return draw_eviction_curve(data["reuse_curve"])


def draw_eviction_curve(series):
    """Plot the broad sweep and label the adjacent threshold probes separately."""
    points = series["predicted"]
    exact = series["predicted_487_488"]
    threshold = next(b for b, evicted in points if evicted == 0)
    f = Frame(320, 700, 0, 25000, left=55, right=532, top=163, bottom=344)
    body = [
        text(24, 32, "Where eviction stops", "ink b", extra=' font-size="22"'),
        text(24, 58, "One captured trace · 30 requests · sequential replay", "ink2",
             extra=' font-size="15"'),
    ]
    for x, blocks in [(24, threshold - 1), (283, threshold)]:
        loss = exact[blocks]
        outcome = f"{loss:,} tokens lost" if loss else "no eviction"
        body.append(text(x, 91, f"{blocks} blocks: {outcome}", "ink b", extra=' font-size="16"'))
    body.append(line(24, 107, 536, 107))
    body.append(text(f.left, 141, "Tokens lost to eviction", "ink2", extra=' font-size="14"'))
    body.extend(line(f.left, f.y(v), f.right, f.y(v)) for v in range(0, 25001, 5000))
    body.extend(text(f.left - 8, f.y(v) + 4, kilo(v), "ink2", "end", ' font-size="13"')
                for v in range(0, 25001, 5000))
    body.append(line(f.left, f.bottom, f.right, f.bottom, "axis"))
    for v in [320, 400, 488, 600, 700]:
        body.append(text(f.x(v), 365, v, "ink b" if v == threshold else "ink2", "middle",
                         ' font-size="14"'))
    body.append(text(294, 388, "Cache capacity (32-token blocks)", "ink2", "middle", ' font-size="14"'))
    body.append('<path d="M%s L%.1f %.1f L%.1f %.1f Z" class="w1"/>' % (
        " L".join("%.1f %.1f" % (f.x(b), f.y(e)) for b, e in points),
        f.x(700), f.y(0), f.x(320), f.y(0)))
    body.append('<polyline fill="none" class="l1" stroke-width="2" stroke-linejoin="round" points="%s"/>' % " ".join(
        "%.1f,%.1f" % (f.x(b), f.y(e)) for b, e in points))
    body.append(line(f.x(threshold), f.top, f.x(threshold), f.bottom, "lm",
                     ' stroke-width="1.25" stroke-dasharray="4 4"'))
    # The adjacent 487/488 probes share a marker footprint at this scale; the
    # callouts above the plot give the exact values without shifting the dots.
    body += [dot(f.x(b), f.y(e), "f2", 7) for b, e in series["engine_cpu"] if 320 <= b <= 700]
    body += [diamond(f.x(b), f.y(e), 5, "f3 ring") for b, e in series["engine_gpu"]]
    for x, kind, name in [(88, "line", "Prediction"), (247, "cpu", "vLLM CPU"), (403, "t4", "vLLM T4")]:
        mark = (line(x - 8, 416, x + 8, 416, "l1", ' stroke-width="2"') if kind == "line"
                else dot(x, 416, "f2", 7) if kind == "cpu" else diamond(x, 416, 5, "f3 ring"))
        body.extend([mark, text(x + 14, 421, name, "ink2", extra=' font-size="14"')])
    return svg(560, 442, "", "", "".join(body), accessible_title="Where eviction stops",
               desc=f"For this trace, the prediction falls to zero at {threshold} blocks. "
                    f"The CPU engine confirmed {exact[threshold - 1]} tokens lost at "
                    f"{threshold - 1} blocks and zero at {threshold}; the T4 confirmed zero at {threshold}.")


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
    h = 372
    left, right = 24, FW - 24
    body = [header("What a single session recomputes", "Recomputed tokens by cause, three sessions, no eviction"),
            key(24, 92, [("sq", "f1", "New content"), ("sq", "f2", "Previous reply"), ("sq", "f3", "Block rounding")]),
            line(24, 110, FW - 24, 110)]
    keys = (("new_content", "f1"), ("previous_reply", "f2"), ("block_rounding", "f3"))
    for i, (name, totals) in enumerate(data["recompute_split"].items()):
        y = 142 + i * 66
        body.append(t(left, y, name, 14, "ink"))
        body.append(t(right, y, "%d%% of prompt reused" % reused[name], 13, "ink2", "end"))
        total = sum(totals[k] for k, _ in keys) or 1
        x, top = left, y + 10
        for j, (k, cls) in enumerate(keys):
            width = (right - left) * totals[k] / total
            body.append(hbar(x, top, width - (2 if j < 2 else 0), 26, cls, round_end=j == 2))
            if width > 46:
                body.append(t(x + 9, top + 18, "%d%%" % round(100 * totals[k] / total), 13, "on b"))
            x += width
    base = 142 + 2 * 66 + 46
    body.append(line(left, base, right, base, "axis"))
    for v in range(0, 101, 25):
        body.append(t(left + (right - left) * v / 100, base + 20, "%d%%" % v, 13, "ink2",
                      "start" if v == 0 else "end" if v == 100 else "middle"))
    body.append(t(FW / 2, base + 44, "Share of recomputed tokens", 14, "ink2", "middle"))
    return svg(FW, h, "", "", "".join(body), accessible_title="What a single session recomputes",
               desc="Three stacked bars: new content is 64 to 91 percent of recomputation, the "
                    "re-prefilled reply and block rounding the rest.")


def fig_gpu(data):
    sizes = json.loads((A / "gpu-result-kaggle-20261108/summary.json").read_text())["sizes"]
    rows = [(s["num_blocks"], sum(s["ttft_ms_total_by_round"]) / len(s["ttft_ms_total_by_round"]) / 1000,
             s["eviction_tokens"]) for s in sizes]
    data["gpu_ttft"] = rows
    base = next(sec for b, sec, ev in rows if ev == 0)
    f = Frame(0, len(rows), 0, 52, left=60, right=FW - 24, top=130, bottom=336)
    body = [header("What eviction costs on a Tesla T4", "The same 30 requests, sent one at a time"),
            t(f.left, 104, "Total time to first token (mean of two rounds)")]
    body += [line(f.left, f.y(v), f.right, f.y(v)) for v in range(0, 51, 10)]
    body += [t(f.left - 8, f.y(v) + 4, "%d s" % v, 13, "ink2", "end") for v in range(0, 51, 10)]
    body.append(line(f.left, f.bottom, f.right, f.bottom, "axis"))
    for i, (blocks, seconds, evicted) in enumerate(rows):
        cx = f.x(i + 0.5)
        body.append(column(cx - 20, f.bottom, f.y(seconds), 40, "f1" if evicted else "fm"))
        body.append(t(cx, f.y(seconds) - 26, "%.1f s" % seconds, 15, "ink b", "middle"))
        body.append(t(cx, f.y(seconds) - 9, ("+%d%%" % round(100 * (seconds / base - 1))) if evicted
                      else "no eviction", 13, "ink2", "middle"))
        body.append(t(cx, f.bottom + 22, "%d blocks" % blocks, 14, "ink", "middle"))
        body.append(t(cx, f.bottom + 40, "{:,} evicted".format(evicted) if evicted else "0 evicted", 13, "ink2", "middle"))
    return svg(FW, 392, "", "", "".join(body), accessible_title="What eviction costs on a Tesla T4",
               desc="Columns: total time to first token falls from 43.9 s at 384 blocks to 23.6 s once "
                    "nothing is evicted.")


def fig_schedule(data):
    schedules = json.loads((A / "schedule-replay-20261108-01/result.json").read_text())["schedules"]
    data["schedules"] = {n: (e["engine"]["mean_delay"], e["engine"]["recomputed"]) for n, e in schedules.items()}
    f = Frame(-0.3, 6.3, 21000, 27000, left=60, right=FW - 24, top=120, bottom=318)
    body = [header("Scheduling sessions to avoid eviction", "One evicting trace, replayed on vLLM in five orders"),
            t(f.left, 100, "Recomputed tokens")]
    body += [line(f.left, f.y(v), f.right, f.y(v)) for v in range(21000, 27001, 1000)]
    body += [t(f.left - 8, f.y(v) + 4, kilo(v), 13, "ink2", "end") for v in range(21000, 27001, 1000)]
    body.append(line(f.left, f.bottom, f.right, f.bottom, "axis"))
    body += [t(f.x(v), f.bottom + 20, v, 13, "ink2", "middle") for v in range(0, 7)]
    body.append(t((f.left + f.right) / 2, f.bottom + 46, "Mean delay per request (positions later than recorded)",
                  14, "ink2", "middle"))
    floor = min(r for _, r in data["schedules"].values())
    body.append(line(f.left, f.y(floor), f.right, f.y(floor), "lm", ' stroke-dasharray="4 4"'))
    body.append(t(f.left + 8, f.y(floor) + 18, "{:,} tokens: nothing evicted".format(floor), 13))
    # Three orders land on the same floor a fraction of a position apart, so
    # they get stacked labels with leader lines instead of labels on the points.
    labels = {"recorded": ("Recorded order", 12, 5, "start"), "bounded-3": ("At most 3 at once", 12, 5, "start"),
              "greedy": ("Greedy", -14, -32, "end"), "bounded-2": ("At most 2 at once", -14, -54, "end"),
              "serial": ("One at a time", 0, -16, "middle")}
    for name, (delay, recomputed) in data["schedules"].items():
        label, dx, dy, anchor = labels[name]
        px, py = f.x(delay), f.y(recomputed)
        if dy < -20:
            body.append(line(px, py - 7, px + dx + (4 if anchor == "end" else -4), py + dy - 4, "lm"))
        body.append(dot(px, py, "f1" if recomputed > floor else "f3", 6))
        body.append(t(px + dx, py + dy, label, 14, "ink", anchor))
    return svg(FW, 384, "", "", "".join(body), accessible_title="Scheduling sessions to avoid eviction",
               desc="Two sessions at a time or a greedy order removes eviction, cutting recomputation "
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
    # Grouped bars rather than dots on one line: in seeds 1 to 3 the three wall
    # clocks fall within a few percent and dots would hide each other.
    f = Frame(0, 1300, 0, 1, left=78, right=372, top=152, bottom=388)
    body = [header("Session limits pay off past the threshold", "Four sessions, one 438-block cache, five seeds"),
            key(24, 92, [("sq", "f2", "No limit"), ("sq", "f1", "Cap of 2"), ("sq", "f3", "Token budget")]),
            line(24, 110, FW - 24, 110),
            t(398, 136, "Tokens lost to eviction", 13, "ink2"),
            t(398, 152, "off / cap / budget", 12.5, "muted")]
    body += [line(f.x(v), f.top, f.x(v), f.bottom) for v in range(0, 1301, 250)]
    body.append(line(f.left, f.bottom, f.right, f.bottom, "axis"))
    body += [t(f.x(v), f.bottom + 20, "%d s" % v if v else "0", 13, "ink2", "middle") for v in range(0, 1301, 250)]
    body.append(t((f.left + f.right) / 2, f.bottom + 46, "Wall clock, all four sessions (Mac CPU)", 14, "ink2", "middle"))
    for i, r in enumerate(rows):
        y = f.top + 10 + i * 46
        for j, (k, cls) in enumerate((("wall_off_s", "f2"), ("wall_cap_s", "f1"), ("wall_budget_s", "f3"))):
            body.append(hbar(f.left, y + j * 10, f.x(r[k]) - f.left, 8, cls))
        body.append(t(f.left - 14, y + 19, "Seed %d" % r["seed"], 14, "ink", "end"))
        lost = " / ".join("{:,}".format(r[k]) for k in ("evicted_off", "evicted_cap", "evicted_budget"))
        body.append(t(398, y + 19, lost, 13, "ink b" if r["evicted_off"] else "ink2"))
    return svg(FW, 446, "", "", "".join(body),
               accessible_title="Session limits pay off only when sessions outgrow the cache",
               desc="Per seed, wall clock with no limit, a two-session cap and a token budget. The cap "
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
