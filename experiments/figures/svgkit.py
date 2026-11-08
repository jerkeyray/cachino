"""Shared drawing kit for the report's SVG figures and diagrams.

Every figure uses one theme: CSS custom properties for the light chart surface,
redefined under prefers-color-scheme: dark, so the same file reads correctly in
GitHub's light and dark themes. Figures have no background of their own; the
surface colors match GitHub's page (#ffffff, #0d1117), so marker rings and
gaps, which are drawn in the surface color, disappear into the page. Colors are referenced by role (series slot,
ink, grid), never as raw hex in figure code. The three series slots are the
first three of a validated categorical order (blue, orange, aqua), which pass
adjacent and all-pairs colorblind separation in both modes; aqua sits below
3:1 contrast on the light surface, so every figure carries direct labels and
its numbers are written to data.json.

Mark conventions: 2px lines, markers of radius >= 4 with a 2px surface ring,
bars at most 24px thick with a 4px rounded data end, a 2px surface gap between
touching fills, hairline solid gridlines. Text always uses ink roles, never a
series color. Only the standard library is used.
"""
from html import escape

STYLE = """<style>
svg{--surface:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
--panel:#f3f2ee;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s1wash:#2a78d61f;--s2wash:#eb68341f;--on:#ffffff}
@media (prefers-color-scheme: dark){svg{--surface:#0d1117;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
--grid:#21262d;--axis:#3d444d;--panel:#161b22;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s1wash:#3987e526;
--s2wash:#d9592626;--on:#ffffff}}
.bg{fill:var(--surface)}.panel{fill:var(--panel)}
.ink{fill:var(--ink)}.ink2{fill:var(--ink2)}.muted{fill:var(--muted)}
.grid{stroke:var(--grid);stroke-width:1}.axis{stroke:var(--axis);stroke-width:1}
.box{fill:var(--surface);stroke:var(--axis);stroke-width:1}
.f1{fill:var(--s1)}.f2{fill:var(--s2)}.f3{fill:var(--s3)}.fm{fill:var(--axis)}
.l1{stroke:var(--s1)}.l2{stroke:var(--s2)}.l3{stroke:var(--s3)}.lm{stroke:var(--muted)}
.w1{fill:var(--s1wash)}.w2{fill:var(--s2wash)}
.ring{stroke:var(--surface);stroke-width:2}.on{fill:var(--on)}
.arrow{stroke:var(--muted);stroke-width:1.5;fill:none}.head{fill:var(--muted)}
text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;font-variant-numeric:tabular-nums}
.t{font-size:15px;font-weight:600}.st{font-size:12px}.lab{font-size:12px}.tick{font-size:11px}.small{font-size:10.5px}
.b{font-weight:600}
</style>"""


def svg(width, height, title, subtitle, body, desc=None):
    """Wrap a figure body with the theme and a title block, on a transparent background."""
    head = '<text x="24" y="30" class="t ink">%s</text>' % escape(title)
    if subtitle:
        head += '<text x="24" y="49" class="st ink2">%s</text>' % escape(subtitle)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" aria-labelledby="title desc">'
            '<title id="title">%s</title><desc id="desc">%s</desc>%s'
            '%s%s</svg>\n'
            % (width, height, escape(title), escape(desc or subtitle or title), STYLE, head, body))


def text(x, y, s, cls="lab ink", anchor="start", extra=""):
    return '<text x="%.1f" y="%.1f" class="%s" text-anchor="%s"%s>%s</text>' % (x, y, cls, anchor, extra, escape(str(s)))


def line(x1, y1, x2, y2, cls="grid", extra=""):
    return '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" class="%s"%s/>' % (x1, y1, x2, y2, cls, extra)


def legend(x, y, items):
    """Swatch legend in one row; items are (kind, slot class, label), kind 'sq', 'dot', 'line' or 'diamond'."""
    out, cx = [], x
    for kind, slot, label in items:
        if kind == "line":
            out.append(line(cx, y - 4, cx + 18, y - 4, slot, ' stroke-width="2" stroke-linecap="round"'))
            w = 18
        elif kind == "dot":
            out.append('<circle cx="%.1f" cy="%.1f" r="4.5" class="%s"/>' % (cx + 5, y - 4, slot))
            w = 10
        elif kind == "diamond":
            out.append(diamond(cx + 5, y - 4, 5, slot))
            w = 10
        else:
            out.append('<rect x="%.1f" y="%.1f" width="10" height="10" rx="2" class="%s"/>' % (cx, y - 9, slot))
            w = 10
        out.append(text(cx + w + 6, y, label, "lab ink2"))
        cx += w + 22 + 6.4 * len(label)
    return "".join(out)


def diamond(cx, cy, r, cls):
    return '<path d="M%.1f %.1fL%.1f %.1fL%.1f %.1fL%.1f %.1fZ" class="%s"/>' % (
        cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy, cls)


def column(x, base, top, width, cls):
    """Vertical bar growing up from base to top, rounded 4px at the data end only."""
    r = min(4, (base - top) / 2, width / 2)
    return ('<path d="M%.1f %.1fV%.1fQ%.1f %.1f %.1f %.1fH%.1fQ%.1f %.1f %.1f %.1fV%.1fZ" class="%s"/>'
            % (x, base, top + r, x, top, x + r, top, x + width - r, x + width, top, x + width, top + r, base, cls))


def hbar(x, y, length, height, cls, round_end=True):
    """Horizontal bar segment from x; the data end (right) is rounded 4px when round_end."""
    if not round_end or length < 8:
        return '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" class="%s"/>' % (x, y, max(length, 0), height, cls)
    r = 4
    return ('<path d="M%.1f %.1fH%.1fQ%.1f %.1f %.1f %.1fV%.1fQ%.1f %.1f %.1f %.1fH%.1fZ" class="%s"/>'
            % (x, y, x + length - r, x + length, y, x + length, y + r, y + height - r, x + length, y + height,
               x + length - r, y + height, x, cls))


def arrow(points, cls="arrow"):
    """Polyline with an arrowhead at the last point."""
    (x1, y1), (x2, y2) = points[-2], points[-1]
    dx, dy = x2 - x1, y2 - y1
    n = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / n, dy / n
    bx, by = x2 - 7 * ux, y2 - 7 * uy  # the shaft stops where the head begins
    shaft = "M" + " L".join("%.1f %.1f" % p for p in list(points[:-1]) + [(bx, by)])
    head = '<path d="M%.1f %.1fL%.1f %.1fL%.1f %.1fZ" class="head"/>' % (
        x2, y2, bx - 4 * uy, by + 4 * ux, bx + 4 * uy, by - 4 * ux)
    return '<path d="%s" class="%s"/>%s' % (shaft, cls, head)


def compact(v):
    """Axis tick: in full under 10,000, compact (10K) above."""
    v = int(v)
    return "{:,}".format(v) if abs(v) < 10000 else "%dK" % (v // 1000)
