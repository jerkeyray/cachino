"""Draw the report's two explanatory diagrams and the README banner.

They hold no measured data, so they need nothing but the standard library:

  python3 experiments/figures/make_diagrams.py

Output: docs/figures/pipeline.svg, docs/figures/reuse-loss.svg and the README banner,
docs/figures/banner.svg.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from svgkit import arrow, line, svg, text  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs/figures"


def label(x, y, value, anchor="start"):
    """Set prominent diagram text at a size that survives Markdown scaling."""
    return text(x, y, value, "ink b", anchor, ' font-size="18"')


def detail(x, y, value, anchor="start"):
    """Set secondary diagram text in the same theme-aware ink as the charts."""
    return text(x, y, value, "ink2", anchor, ' font-size="15"')


def pipeline():
    """Show what flows through the live proxy and what gets checked offline."""
    body = [label(24, 31, "Live · record traffic"),
            label(104, 75, "Coding agent", "middle"),
            label(280, 75, "Cachino proxy", "middle"),
            label(456, 75, "vLLM", "middle"),
            arrow([(164, 65), (213, 65)]), arrow([(213, 76), (164, 76)]),
            arrow([(347, 65), (410, 65)]), arrow([(410, 76), (347, 76)]),
            detail(280, 104, "Forwards request bodies unchanged", "middle"),
            arrow([(280, 114), (280, 135)]),
            '<rect x="99" y="135" width="362" height="38" rx="7" class="w1"/>',
            label(280, 160, "Trace · request and response bytes", "middle"),
            arrow([(280, 173), (280, 197)]),
            line(24, 197, 260, 197), line(300, 197, 536, 197),
            label(24, 231, "Offline · explain prefix reuse")]
    steps = [
        ("Render", "Rebuild prompt token IDs from the capture"),
        ("Predict", "Simulate the pinned vLLM cache"),
        ("Attribute", "Explain which tokens had to be recomputed"),
        ("Verify", "Replay against vLLM; compare IDs and hits"),
    ]
    # The trace is the boundary between phases. Verification reads the same
    # rendered IDs as prediction and uses a separate engine replay as reference.
    body.append(line(39, 263, 39, 392, "lm"))
    for i, (name, description) in enumerate(steps, 1):
        y = 263 + (i - 1) * 43
        body.append(f'<circle cx="39" cy="{y}" r="14" class="panel"/>')
        body.append(detail(39, y + 5, str(i), "middle"))
        body.append(label(66, y + 6, name))
        body.append(detail(162, y + 5, description))
    return svg(560, 423, "", "", "".join(body),
               desc="The agent exchanges requests and responses with vLLM through Cachino. "
                    "Cachino saves the bytes as a trace. Offline, it renders prompt token IDs, "
                    "predicts cache hits, and attributes recomputation. An engine replay checks "
                    "the token IDs and hit counts.", accessible_title="How Cachino measures prefix reuse")


# Block classes distinguish reuse, prompt divergence, eviction, rounding and new content.
REUSED, CHANGED, EVICTED, ROUNDING, NEW = "f1", "f2", "f3", "panel", "fm"


def block(x, y, width, size, kind):
    """Draw rounding as an outlined block so it stands apart from new content."""
    outline = ' stroke="var(--muted)"' if kind == ROUNDING else ""
    return f'<rect x="{x}" y="{y}" width="{width}" height="{size}" rx="3" class="{kind}"{outline}/>'


def reuse_loss():
    """Illustrate the same prompt length under three different reuse conditions."""
    examples = [
        ("Context appended", 8, [REUSED] * 8 + [ROUNDING] + [NEW] * 3),
        ("Earlier tokens changed", 5, [REUSED] * 5 + [ROUNDING] + [CHANGED] * 3 + [NEW] * 3),
        ("Matching blocks were evicted", 3, [REUSED] * 3 + [EVICTED] * 5 + [ROUNDING] + [NEW] * 3),
    ]
    body = [label(24, 31, "What cuts prefix reuse?"),
            detail(24, 54, "Each cell represents one 32-token block of the next prompt.")]
    for i, (name, hits, blocks) in enumerate(examples):
        y = 92 + i * 74
        body.extend([label(24, y, name), detail(536, y, f"{hits} of 12 reused", "end")])
        body.extend(block(24 + j * 43, y + 12, 39, 22, kind) for j, kind in enumerate(blocks))
    body.append(line(24, 294, 536, 294))
    for x, y, kind, name in [
        (24, 320, REUSED, "Reused"), (175, 320, CHANGED, "After a change"),
        (356, 320, EVICTED, "Evicted"), (24, 349, ROUNDING, "Block rounding"),
        (175, 349, NEW, "New context"),
    ]:
        body.append(block(x, y - 12, 14, 14, kind))
        body.append(detail(x + 22, y, name))
    return svg(560, 372, "", "", "".join(body),
               desc="Three illustrative 12-block prompts. Eight blocks are reused when context "
                    "is appended, five when earlier tokens change, and three when matching "
                    "blocks are evicted. The remaining blocks are recomputed.",
               accessible_title="What cuts prefix reuse?")


def banner():
    """README header: type only, on a transparent background.

    The project name once, as the headline, over the question the study asks.
    The text takes the page's own ink in GitHub's light and dark themes, so the
    header has no box of its own.
    """
    W, H = 1280, 270
    serif = 'font-family="&quot;Iowan Old Style&quot;,&quot;Palatino Linotype&quot;,Palatino,Georgia,serif"'
    sans = 'font-family="-apple-system,BlinkMacSystemFont,&quot;Segoe UI&quot;,Helvetica,Arial,sans-serif"'
    style = ("<style>svg{--ink:#1f2328;--ink2:#59636e;--rule:#d1d9e0}"
             "@media (prefers-color-scheme: dark){svg{--ink:#f0f6fc;--ink2:#9198a1;--rule:#3d444d}}"
             ".ink{fill:var(--ink)}.ink2{fill:var(--ink2)}.rule{stroke:var(--rule)}</style>")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" aria-labelledby="t d">'
            '<title id="t">Cachino</title>'
            '<desc id="d">Cachino: where coding agents lose prefix reuse. A measurement study of vLLM prefix caching '
            'under coding-agent traffic.</desc>%s'
            '<text x="0" y="40" %s font-size="17" letter-spacing="3" class="ink2">A MEASUREMENT STUDY</text>'
            '<text x="-4" y="136" %s font-size="96" class="ink">Cachino</text>'
            '<text x="0" y="188" %s font-size="36" class="ink">Where coding agents lose prefix reuse</text>'
            '<text x="0" y="230" %s font-size="22" class="ink2">Per-request, engine-validated attribution of '
            'vLLM prefix-cache misses</text>'
            '<line x1="0" y1="262" x2="%d" y2="262" class="rule" stroke-width="1"/></svg>\n'
            % (W, H, style, sans, serif, serif, sans, W))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pipeline.svg").write_text(pipeline())
    (OUT / "reuse-loss.svg").write_text(reuse_loss())
    (OUT / "banner.svg").write_text(banner())
    print("wrote", OUT / "pipeline.svg", OUT / "reuse-loss.svg", OUT / "banner.svg")


if __name__ == "__main__":
    main()
