"""Regenerate the write-up figures from saved run outputs.

Reads the private run directories under artifacts/ (attribution, sweep,
schedule and GPU results) and writes aggregate-only SVG figures and their data
to docs/figures/. No prompt text reaches the output. The reuse curve is
recomputed from the capture with reuse_curve.py, so run this in the tokenizer
environment:

  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run --project analysis/tokenizer --frozen \
    --python 3.12 --no-python-downloads python -B experiments/figures/make_figures.py
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))

from concurrent_attribution import load_requests  # noqa: E402
from reconstruct import _load_tokenizer  # noqa: E402
from reuse_curve import curve  # noqa: E402
from tokenizer_assets import load_pin  # noqa: E402

A = ROOT / "artifacts"
OUT = ROOT / "docs/figures"
INK, MUTED, GRID = "#1f2328", "#59636e", "#d0d7de"
BLUE, ORANGE, GREEN, PURPLE = "#2f6fb5", "#d9822b", "#3a8f4d", "#8250df"
W, H, L, R, T, B = 680, 380, 70, 20, 40, 60


def svg(title, body, legend=()):
    items = "".join('<rect x="%d" y="%d" width="12" height="12" fill="%s"/><text x="%d" y="%d" font-size="12" fill="%s">%s</text>'
                    % (L + 10 + i * 190, 14, c, L + 28 + i * 190, 24, INK, t) for i, (c, t) in enumerate(legend))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" font-family="system-ui, sans-serif">'
            '<rect width="100%%" height="100%%" fill="#ffffff"/><title>%s</title>%s%s</svg>\n' % (W, H, title, items, body))


def axes(xmin, xmax, ymin, ymax, xticks, yticks, xlabel, ylabel, xfmt=str, yfmt=str):
    sx = lambda v: L + (v - xmin) / (xmax - xmin) * (W - L - R)  # noqa: E731
    sy = lambda v: H - B - (v - ymin) / (ymax - ymin) * (H - T - B)  # noqa: E731
    parts = []
    for v in yticks:
        parts.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (L, W - R, sy(v), sy(v), GRID))
        parts.append('<text x="%d" y="%.1f" font-size="11" fill="%s" text-anchor="end">%s</text>' % (L - 6, sy(v) + 4, MUTED, yfmt(v)))
    for v in xticks:
        parts.append('<text x="%.1f" y="%d" font-size="11" fill="%s" text-anchor="middle">%s</text>' % (sx(v), H - B + 16, MUTED, xfmt(v)))
    parts.append('<line x1="%d" x2="%d" y1="%d" y2="%d" stroke="%s"/>' % (L, W - R, H - B, H - B, MUTED))
    parts.append('<text x="%d" y="%d" font-size="12" fill="%s" text-anchor="middle">%s</text>' % ((L + W - R) / 2, H - 18, INK, xlabel))
    parts.append('<text x="16" y="%d" font-size="12" fill="%s" text-anchor="middle" transform="rotate(-90 16 %d)">%s</text>'
                 % ((T + H - B) / 2, INK, (T + H - B) / 2, ylabel))
    return sx, sy, "".join(parts)


def thousands(v):
    return "{:,}".format(int(v))


def fig_curve(data):
    pin = load_pin(ROOT / "experiments/tokenizer/qwen3-1.7b.json")
    rows = load_requests(A / "live-concurrent-20261107-01", _load_tokenizer(A / "qwen3-1.7b-tokenizer", pin)[0], pin)
    points = curve(rows, range(320, 701, 4), 32)
    cpu = {}
    for f in list((A / "cache-sweep-20261107-01").glob("sweep-*.json")) + list((A / "cache-sweep-20261108-01").glob("sweep-*.json")):
        d = json.loads(f.read_text())
        cpu[d["num_blocks"]] = d["eviction_tokens"]
    gpu = {s["num_blocks"]: s["eviction_tokens"] for s in json.loads((A / "gpu-result-kaggle-20261108/summary.json").read_text())["sizes"]}
    data["reuse_curve"] = {"predicted": [(p["num_blocks"], p["eviction"]) for p in points],
                           "engine_cpu": sorted(cpu.items()), "engine_gpu": sorted(gpu.items())}
    sx, sy, base = axes(320, 700, 0, 25000, range(320, 701, 40), range(0, 25001, 5000),
                        "Cache size (blocks of 32 tokens)", "Tokens lost to eviction", yfmt=thousands)
    line = " ".join("%.1f,%.1f" % (sx(b), sy(e)) for b, e in data["reuse_curve"]["predicted"])
    body = base + '<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>' % (BLUE, line)
    body += "".join('<circle cx="%.1f" cy="%.1f" r="5" fill="none" stroke="%s" stroke-width="2"/>' % (sx(b), sy(e), ORANGE)
                    for b, e in cpu.items() if 320 <= b <= 700)
    body += "".join('<rect x="%.1f" y="%.1f" width="7" height="7" fill="%s"/>' % (sx(b) - 3.5, sy(e) - 3.5, GREEN)
                    for b, e in gpu.items())
    return svg("Eviction against cache size", body,
               [(BLUE, "Predicted from capture"), (ORANGE, "Engine, Mac CPU"), (GREEN, "Engine, Tesla T4")])


def split_totals(path):
    live = json.loads(path.read_text())
    totals = {k: 0 for k in ("new_content", "previous_reply", "block_rounding", "eviction")}
    for row in live[1:]:
        for k in totals:
            totals[k] += row["split"][k]
    return totals


def fig_split(data):
    sessions = [("0.6B, 30 turns", A / "live-cpu-readonly-20261107-01/attribution/live.json"),
                ("0.6B sampled", A / "live-cpu-sampled-20261107-01/attribution/live.json"),
                ("1.7B, varied", A / "live-cpu-1.7b-20261107-02/attribution/live.json")]
    data["recompute_split"] = {name: split_totals(path) for name, path in sessions}
    sx, sy, base = axes(0, 3, 0, 100, [], range(0, 101, 20), "Session", "Share of recomputed tokens (%)",
                        yfmt=lambda v: "%d" % v)
    body = base
    for i, (name, totals) in enumerate(data["recompute_split"].items()):
        total = sum(totals.values()) or 1
        x, y = sx(i + 0.25), 0.0
        for key, color in (("new_content", BLUE), ("previous_reply", ORANGE), ("block_rounding", MUTED)):
            share = 100 * totals[key] / total
            body += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (
                x, sy(y + share), sx(0.5) - sx(0), sy(y) - sy(y + share), color)
            y += share
        body += '<text x="%.1f" y="%d" font-size="11" fill="%s" text-anchor="middle">%s</text>' % (sx(i + 0.5), H - B + 16, MUTED, name)
    return svg("Recomputed tokens by cause in single sessions", body,
               [(BLUE, "New content"), (ORANGE, "Previous reply again"), (MUTED, "Block rounding")])


def fig_gpu(data):
    sizes = json.loads((A / "gpu-result-kaggle-20261108/summary.json").read_text())["sizes"]
    data["gpu_ttft"] = [(s["num_blocks"], sum(s["ttft_ms_total_by_round"]) / len(s["ttft_ms_total_by_round"]) / 1000) for s in sizes]
    sx, sy, base = axes(0, len(sizes), 0, 50, [], range(0, 51, 10), "Cache size (blocks)", "Total time to first token (s)")
    body = base
    for i, (blocks, seconds) in enumerate(data["gpu_ttft"]):
        body += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (
            sx(i + 0.2), sy(seconds), sx(0.6) - sx(0), sy(0) - sy(seconds), GREEN)
        body += '<text x="%.1f" y="%.1f" font-size="11" fill="%s" text-anchor="middle">%.1f s</text>' % (sx(i + 0.5), sy(seconds) - 6, INK, seconds)
        body += '<text x="%.1f" y="%d" font-size="11" fill="%s" text-anchor="middle">%d</text>' % (sx(i + 0.5), H - B + 16, MUTED, blocks)
    return svg("Time to first token on a Tesla T4 by cache size", body)


def fig_schedule(data):
    schedules = json.loads((A / "schedule-replay-20261108-01/result.json").read_text())["schedules"]
    data["schedules"] = {n: (e["engine"]["mean_delay"], e["engine"]["recomputed"]) for n, e in schedules.items()}
    sx, sy, base = axes(0, 6, 20000, 27000, range(0, 7), range(20000, 27001, 1000), "Mean delay (positions)",
                        "Recomputed tokens", yfmt=thousands)
    body = base
    labels = {"recorded": "Recorded", "bounded-3": "At most 3", "bounded-2": "At most 2", "greedy": "Greedy", "serial": "One at a time"}
    offsets = {"bounded-2": -14, "greedy": 18, "serial": -14}
    for name, (delay, recomputed) in data["schedules"].items():
        body += '<circle cx="%.1f" cy="%.1f" r="6" fill="%s"/>' % (sx(delay), sy(recomputed), PURPLE)
        body += '<text x="%.1f" y="%.1f" font-size="11" fill="%s" text-anchor="middle">%s</text>' % (
            sx(delay), sy(recomputed) + offsets.get(name, -12), INK, labels[name])
    return svg("Session scheduling: recomputation against delay", body)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = {}
    for name, make in (("eviction-curve", fig_curve), ("recompute-split", fig_split),
                       ("gpu-ttft", fig_gpu), ("scheduling", fig_schedule)):
        (OUT / (name + ".svg")).write_text(make(data))
    (OUT / "data.json").write_text(json.dumps(data, indent=1) + "\n")
    print(json.dumps({k: (v if not isinstance(v, dict) or len(str(v)) < 400 else "...") for k, v in data.items()}, indent=1)[:2500])


if __name__ == "__main__":
    main()
