"""Draw the report's two explanatory diagrams and the README banner.

They hold no measured data, so they need nothing but the standard library:

  python3 experiments/figures/make_diagrams.py

Output: docs/figures/pipeline.svg, docs/figures/reuse-loss.svg and the README banner,
docs/figures/banner.svg.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from svgkit import arrow, legend, line, svg, text  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs/figures"


def box(x, y, w, h, title, lines, accent=False):
    cls = "box" if not accent else "box"
    out = '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8" class="%s"/>' % (x, y, w, h, cls)
    if accent:
        out += '<rect x="%.1f" y="%.1f" width="4" height="%.1f" rx="2" class="f1"/>' % (x, y + 10, h - 20)
    out += text(x + 16, y + 24, title, "lab ink b")
    for i, s in enumerate(lines):
        out += text(x + 16, y + 42 + 15 * i, s, "small ink2")
    return out


def pipeline():
    W, H = 900, 404
    b = []
    b.append(text(24, 88, "ONLINE", "small muted b", extra=' letter-spacing="1"'))
    b.append(text(24, 228, "OFFLINE", "small muted b", extra=' letter-spacing="1"'))
    w, h = 168, 76
    b.append(box(24, 98, w, h, "Coding agent", ["mini-swe-agent, smolagents", "OpenAI-compatible client"]))
    b.append(box(366, 98, w, h, "Cachino proxy", ["Go, standard library", "forwards bytes unchanged"], accent=True))
    b.append(box(708, 98, w, h, "vLLM, pinned", ["commit 2173d712", "prefix caching on"]))
    b.append(arrow([(192, 128), (366, 128)]))
    b.append(arrow([(366, 146), (192, 146)]))
    b.append(text(279, 121, "request", "small ink2", "middle"))
    b.append(text(279, 162, "response", "small ink2", "middle"))
    b.append(arrow([(534, 128), (708, 128)]))
    b.append(arrow([(708, 146), (534, 146)]))
    b.append(text(621, 121, "same bytes", "small ink2", "middle"))
    b.append(text(621, 162, "cached_tokens", "small ink2", "middle"))
    xs, w2 = [24, 202, 380, 558, 736], 140
    names = [("Capture", ["request, response bytes", "events, SHA-256 hashes"]),
             ("Render", ["exact token IDs", "pinned tokenizer, template"]),
             ("Predict", ["vLLM free-queue rules", "hit per request"]),
             ("Attribute", ["each recomputed token", "to one cause"]),
             ("Check", ["replay IDs to engine,", "compare hit and IDs"])]
    for x, (n, ls) in zip(xs, names):
        b.append(box(x, 238, w2, h, n, ls, accent=n == "Check"))
    for x in xs[:-1]:
        b.append(arrow([(x + w2, 276), (x + w2 + 38, 276)]))
    b.append(arrow([(450, 174), (450, 194), (124, 194), (124, 238)]))
    b.append(text(287, 189, "write-ahead capture", "small ink2", "middle"))
    b.append(arrow([(792, 174), (792, 238)]))
    b.append(text(800, 210, "ground truth", "small ink2"))
    b.append(line(24, 344, W - 24, 344))
    b.append(text(24, 366, "Missing cache metrics are recorded as unknown, never zero. Equal token counts are never taken as equal tokens.",
                  "small ink2"))
    b.append(text(24, 384, "Prediction and check run on CPU and on a Tesla T4; the same rules, cited to source lines, drive every result.",
                  "small ink2"))
    return svg(W, H, "How Cachino measures prefix reuse",
               "The proxy records agent traffic unchanged; offline, every request's cache hit is predicted, attributed and checked.",
               "".join(b), desc="Pipeline: agent, proxy and pinned vLLM online; capture, render, predict, attribute "
                               "and check offline, with the engine as ground truth for the check.")


# Block categories for the taxonomy diagram: (fill class, extra stroke attributes, legend label).
REUSED = ("f1", "", "Reused from cache")
CHANGED = ("f2", "", "Recomputed: prompt changed")
EVICTED = ("f3", "", "Recomputed: evicted")
ROUNDING = ("panel", ' stroke="var(--muted)" stroke-width="1"', "Recomputed: block rounding")
NEW = ("fm", "", "Recomputed: new content")


def row(y, label, sub, blocks, note, marker=None):
    out = [text(24, y + 15, label, "lab ink b"), text(24, y + 32, sub, "small ink2")]
    x0, size, gap = 250, 24, 4
    for i, (cls, extra, _) in enumerate(blocks):
        out.append('<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="4" class="%s"%s/>'
                   % (x0 + i * (size + gap), y + 2, size, size, cls, extra))
    if marker is not None:
        mx = x0 + marker * (size + gap) + size / 2  # inside the partly shared block
        out.append(line(mx, y - 6, mx, y + size + 10, "lm", ' stroke-width="1.5"'))
        out.append(text(mx, y - 10, "first changed token", "small ink2", "middle"))
    out.append(text(x0 + len(blocks) * (size + gap) + 12, y + 19, note, "small ink2"))
    return "".join(out)


def reuse_loss():
    W, H = 900, 400
    a = [REUSED] * 13 + [ROUNDING] + [NEW] * 4
    b = [REUSED] * 9 + [ROUNDING] + [CHANGED] * 4 + [NEW] * 4
    c = [REUSED] * 5 + [EVICTED] * 8 + [ROUNDING] + [NEW] * 4
    body = [row(96, "Append-only history", "the harness only appends", a, "13 of 18 reused"),
            row(176, "Prompt changed", "history re-rendered or edited", b, "9 of 18 reused", marker=9),
            row(256, "Evicted", "matched, but other requests took them", c, "5 of 18 reused")]
    body.append(legend(24, 334, [("sq",) + REUSED[:1] + (REUSED[2],), ("sq", "f2", CHANGED[2]),
                                 ("sq", "f3", EVICTED[2])]))
    body.append('<rect x="24" y="349" width="10" height="10" rx="2" class="panel" stroke="var(--muted)" stroke-width="1"/>')
    body.append(text(40, 358, ROUNDING[2], "lab ink2"))
    body.append('<rect x="250" y="349" width="10" height="10" rx="2" class="fm"/>')
    body.append(text(266, 358, NEW[2], "lab ink2"))
    body.append(text(24, 384, "Block rounding: the partly shared block, and the final prompt token, are never served from cache. "
                     "Only new content is unavoidable.", "small ink2"))
    return svg(W, H, "Three ways an agent turn loses prefix reuse",
               "Each square is one 32-token KV block of the next prompt; vLLM reuses the longest run of cached full blocks from the start.",
               "".join(body), desc="Three rows of blocks: append-only history reuses all shared blocks; a changed prompt "
                                   "recomputes everything after the first changed token; eviction recomputes blocks that "
                                   "matched but were no longer cached.")


def banner():
    """README header: type only, on a transparent background.

    The text takes the page's own ink in GitHub's light and dark themes, so the
    header has no box of its own. It does not repeat the project name, which
    GitHub already shows above the README.
    """
    W, H = 1280, 230
    serif = 'font-family="&quot;Iowan Old Style&quot;,&quot;Palatino Linotype&quot;,Palatino,Georgia,serif"'
    sans = 'font-family="-apple-system,BlinkMacSystemFont,&quot;Segoe UI&quot;,Helvetica,Arial,sans-serif"'
    style = ("<style>svg{--ink:#1f2328;--ink2:#59636e;--rule:#d1d9e0}"
             "@media (prefers-color-scheme: dark){svg{--ink:#f0f6fc;--ink2:#9198a1;--rule:#3d444d}}"
             ".ink{fill:var(--ink)}.ink2{fill:var(--ink2)}.rule{stroke:var(--rule)}</style>")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" aria-labelledby="t d">'
            '<title id="t">Where coding agents lose prefix reuse</title>'
            '<desc id="d">A measurement study of vLLM prefix caching under coding-agent traffic, November 2026.</desc>%s'
            '<text x="0" y="44" %s font-size="17" letter-spacing="3" class="ink2">A MEASUREMENT STUDY · NOVEMBER 2026</text>'
            '<text x="-3" y="126" %s font-size="66" class="ink">Where coding agents lose prefix reuse</text>'
            '<text x="0" y="180" %s font-size="25" class="ink2">Per-request, engine-validated attribution of '
            'vLLM prefix-cache misses</text>'
            '<line x1="0" y1="222" x2="%d" y2="222" class="rule" stroke-width="1"/></svg>\n'
            % (W, H, style, sans, serif, sans, W))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pipeline.svg").write_text(pipeline())
    (OUT / "reuse-loss.svg").write_text(reuse_loss())
    (OUT / "banner.svg").write_text(banner())
    print("wrote", OUT / "pipeline.svg", OUT / "reuse-loss.svg", OUT / "banner.svg")


if __name__ == "__main__":
    main()
