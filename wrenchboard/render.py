"""Draw the board as an SVG, highlighting parts and a net.

Everything is drawn from the same PCB data the highlights come from, in board
millimetres, so a highlight can only sit on the part it names.
"""

from __future__ import annotations

import math
from xml.sax.saxutils import escape, quoteattr

from .pcb import Pcb, PcbError, _pad_corners

STATES = ("suspect", "cleared", "found")

_STYLE = """
  .outline { fill: #1f5130; stroke: #c9b458; stroke-width: 0.3; }
  .hole { fill: #ffffff; stroke: #c9b458; stroke-width: 0.2; }
  .part { fill: none; stroke: #dfe8e1; stroke-width: 0.12; }
  .bottom { stroke-dasharray: 0.5 0.4; }
  .pad { fill: #b9a25a; }
  .ref { fill: #dfe8e1; font-family: sans-serif; text-anchor: middle; dominant-baseline: central; }
  .suspect { fill: rgba(230, 57, 70, 0.45); stroke: #e63946; stroke-width: 0.35; }
  .cleared { fill: rgba(160, 160, 160, 0.55); stroke: #9a9a9a; stroke-width: 0.25; }
  .found { fill: rgba(42, 157, 90, 0.6); stroke: #2a9d5a; stroke-width: 0.45; }
  .measure { fill: #3a86ff; stroke: #ffffff; stroke-width: 0.15; }
  .label { fill: #222222; font-family: sans-serif; }
"""


def render_svg(pcb: Pcb, parts: dict[str, str] | None = None,
               measure_net: str | None = None, title: str = "") -> str:
    """parts maps a reference to one of STATES. measure_net marks that net's pads."""
    parts = parts or {}
    for ref, state in parts.items():
        if ref not in pcb.footprints:
            raise PcbError(f"cannot highlight {ref!r}: no such part on the PCB")
        if state not in STATES:
            raise PcbError(f"unknown highlight state {state!r}")
    measure_pads = pcb.pads_on_net(measure_net) if measure_net else []
    if measure_net and not measure_pads:
        raise PcbError(f"cannot mark {measure_net!r}: no pads on that net")

    x0, y0, x1, y1 = pcb.bounds
    m = 3.0
    legend_h = 9.0
    vb_x, vb_y, vb_w = x0 - m, y0 - m, (x1 - x0) + 2 * m
    vb_h = (y1 - y0) + 2 * m + legend_h
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb_x:.3f} {vb_y:.3f} {vb_w:.3f} {vb_h:.3f}" '
        f'width="{vb_w * 12:.0f}" height="{vb_h * 12:.0f}">',
        f"<style>{_STYLE}</style>",
        f'<rect x="{vb_x:.3f}" y="{vb_y:.3f}" width="{vb_w:.3f}" height="{vb_h:.3f}" fill="#ffffff"/>',
    ]
    out += _outline(pcb)

    for fp in pcb.footprints.values():
        bx0, by0, bx1, by1 = fp.box
        cls = "part bottom" if fp.side == "bottom" else "part"
        out.append(f'<g data-ref={quoteattr(fp.ref)}>')
        out.append(f'<rect class="{cls}" x="{bx0:.3f}" y="{by0:.3f}" '
                   f'width="{bx1 - bx0:.3f}" height="{by1 - by0:.3f}"/>')
        for pad in fp.pads:
            pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in _pad_corners(pad))
            out.append(f'<polygon class="pad" points="{pts}"/>')
        out.append("</g>")

    for ref, state in parts.items():
        bx0, by0, bx1, by1 = pcb.footprints[ref].box
        pad_ = 0.4
        out.append(f'<rect class="{state}" data-highlight={quoteattr(ref)} '
                   f'x="{bx0 - pad_:.3f}" y="{by0 - pad_:.3f}" '
                   f'width="{bx1 - bx0 + 2 * pad_:.3f}" height="{by1 - by0 + 2 * pad_:.3f}" rx="0.4"/>')

    for ref, pad in measure_pads:
        r = max(0.45, min(pad.w, pad.h) / 2)
        out.append(f'<circle class="measure" data-pad={quoteattr(f"{ref}.{pad.number}")} '
                   f'cx="{pad.x:.3f}" cy="{pad.y:.3f}" r="{r:.3f}"/>')

    for fp in pcb.footprints.values():
        bx0, by0, bx1, by1 = fp.box
        size = max(0.7, min(1.6, min(bx1 - bx0, by1 - by0) * 0.45))
        weight = ' font-weight="bold"' if fp.ref in parts else ""
        out.append(f'<text class="ref" x="{(bx0 + bx1) / 2:.3f}" y="{(by0 + by1) / 2:.3f}" '
                   f'font-size="{size:.2f}"{weight}>{escape(fp.ref)}</text>')

    out += _legend(vb_x + 1, y1 + m + 1.5, title, measure_net)
    out.append("</svg>")
    return "\n".join(out)


def _outline(pcb: Pcb) -> list[str]:
    """The board shape as one filled path, plus any circles (mounting holes)."""
    segments = [s for s in pcb.outline if s[0] != "circle"]
    path = _chain(segments)
    out = [f'<path class="outline" fill-rule="evenodd" d="{path}"/>'] if path else []
    for s in pcb.outline:
        if s[0] == "circle":
            out.append(f'<circle class="hole" cx="{s[1]:.3f}" cy="{s[2]:.3f}" r="{s[3]:.3f}"/>')
    return out


def _chain(segments: list[tuple]) -> str:
    """Join outline segments end to end into closed SVG subpaths."""
    remaining = list(segments)
    d = []
    while remaining:
        seg = remaining.pop(0)
        start = (seg[1], seg[2])
        d.append(f"M {start[0]:.3f} {start[1]:.3f}")
        d.append(_draw(seg))
        end = (seg[-2], seg[-1])
        while remaining and math.dist(end, start) > 0.01:
            nxt = None
            for i, s in enumerate(remaining):
                if math.dist((s[1], s[2]), end) < 0.01:
                    nxt = remaining.pop(i)
                    break
                if math.dist((s[-2], s[-1]), end) < 0.01:
                    nxt = _reverse(remaining.pop(i))
                    break
            if nxt is None:
                break
            d.append(_draw(nxt))
            end = (nxt[-2], nxt[-1])
        d.append("Z")
    return " ".join(d)


def _reverse(seg: tuple) -> tuple:
    if seg[0] == "line":
        return ("line", seg[3], seg[4], seg[1], seg[2])
    return ("arc", seg[5], seg[6], seg[3], seg[4], seg[1], seg[2])


def _draw(seg: tuple) -> str:
    if seg[0] == "line":
        return f"L {seg[3]:.3f} {seg[4]:.3f}"
    (sx, sy), (mx, my), (ex, ey) = (seg[1], seg[2]), (seg[3], seg[4]), (seg[5], seg[6])
    cx, cy, r = _circle_through((sx, sy), (mx, my), (ex, ey))
    cross = (mx - sx) * (ey - my) - (my - sy) * (ex - mx)
    sweep = 1 if cross > 0 else 0
    side_mid = (ex - sx) * (my - sy) - (ey - sy) * (mx - sx)
    side_ctr = (ex - sx) * (cy - sy) - (ey - sy) * (cx - sx)
    large = 1 if side_mid * side_ctr > 0 else 0
    return f"A {r:.3f} {r:.3f} 0 {large} {sweep} {ex:.3f} {ey:.3f}"


def _circle_through(a, b, c) -> tuple[float, float, float]:
    (ax, ay), (bx, by), (cx, cy) = a, b, c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        raise PcbError("outline arc has collinear points")
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return ux, uy, math.dist((ux, uy), a)


def _legend(x: float, y: float, title: str, measure_net: str | None) -> list[str]:
    out = []
    if title:
        out.append(f'<text class="label" x="{x:.3f}" y="{y:.3f}" font-size="1.8" '
                   f'font-weight="bold">{escape(title)}</text>')
    y += 3.5
    items = [("suspect", "suspect"), ("cleared", "cleared"), ("found", "fault found")]
    for cls, text in items:
        out.append(f'<rect class="{cls}" x="{x:.3f}" y="{y - 1:.3f}" width="2" height="2" rx="0.3"/>')
        out.append(f'<text class="label" x="{x + 2.8:.3f}" y="{y + 0.5:.3f}" font-size="1.4">{text}</text>')
        x += 13
    if measure_net:
        out.append(f'<circle class="measure" cx="{x + 1:.3f}" cy="{y:.3f}" r="0.9"/>')
        out.append(f'<text class="label" x="{x + 2.8:.3f}" y="{y + 0.5:.3f}" font-size="1.4">'
                   f'measure {escape(measure_net)} here</text>')
    out.append(f'<text class="label" x="{x + (40 if measure_net else 0):.3f}" y="{y + 0.5:.3f}" '
               f'font-size="1.2" fill="#666">dashed = bottom side</text>')
    return out
