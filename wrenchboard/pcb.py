"""Read a KiCad .kicad_pcb file: part positions, pads, and the board outline.

Coordinates are in millimetres, x to the right and y downward, as in KiCad.
check_against_graph confirms the PCB matches the schematic before anything
is drawn, so highlights can only land on parts that really exist.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .graph import Graph, GraphError


class PcbError(GraphError):
    """The PCB file is missing, malformed, or disagrees with the schematic."""


@dataclass(frozen=True)
class Pad:
    number: str
    net: str | None
    x: float
    y: float
    w: float
    h: float
    angle: float


@dataclass(frozen=True)
class Footprint:
    ref: str
    x: float
    y: float
    angle: float
    side: str                                  # "top" or "bottom"
    pads: tuple[Pad, ...]
    box: tuple[float, float, float, float]     # min x, min y, max x, max y

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1


@dataclass(frozen=True)
class Pcb:
    footprints: dict[str, Footprint]
    outline: tuple[tuple, ...]   # ("line", x1, y1, x2, y2), ("arc", sx, sy, mx, my, ex, ey), ("circle", cx, cy, r)
    bounds: tuple[float, float, float, float]

    def pads_on_net(self, net: str) -> list[tuple[str, Pad]]:
        return [(fp.ref, p) for fp in self.footprints.values() for p in fp.pads if p.net == net]


_KICAD_ESCAPES = {"{slash}": "/", "{backslash}": "\\", "{lt}": "<", "{gt}": ">",
                  "{colon}": ":", "{dblquote}": '"', "{quote}": "'", "{tab}": "\t",
                  "{return}": "\n"}


def _unescape(name: str) -> str:
    """KiCad escapes some characters in PCB net names, like AD4{slash}SDA for AD4/SDA."""
    for token, char in _KICAD_ESCAPES.items():
        name = name.replace(token, char)
    return name

# S-expression parsing

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+')


def _parse(text: str) -> list:
    stack: list[list] = [[]]
    for m in _TOKEN.finditer(text):
        t = m.group()
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) == 1:
                raise PcbError("unbalanced parentheses")
            node = stack.pop()
            stack[-1].append(node)
        elif t.startswith('"'):
            stack[-1].append(t[1:-1].replace('\\"', '"').replace("\\\\", "\\"))
        else:
            stack[-1].append(t)
    if len(stack) != 1 or not stack[0]:
        raise PcbError("unbalanced parentheses or empty file")
    return stack[0][0]


def _kids(node: list, tag: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def _kid(node: list, tag: str) -> list | None:
    found = _kids(node, tag)
    return found[0] if found else None


def _xy(node: list | None) -> tuple[float, float]:
    if node is None or len(node) < 3:
        raise PcbError("expected a coordinate pair")
    return float(node[1]), float(node[2])


# Geometry

def _to_board(fx: float, fy: float, angle: float, x: float, y: float) -> tuple[float, float]:
    """Footprint local coordinates to board coordinates (KiCad: y down, angles counterclockwise)."""
    r = math.radians(angle)
    return (fx + x * math.cos(r) + y * math.sin(r),
            fy - x * math.sin(r) + y * math.cos(r))


def _pad_corners(p: Pad) -> list[tuple[float, float]]:
    r = math.radians(p.angle)
    out = []
    for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = dx * p.w / 2, dy * p.h / 2
        out.append((p.x + x * math.cos(r) + y * math.sin(r),
                    p.y - x * math.sin(r) + y * math.cos(r)))
    return out


def _box(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


# Loading

def load_pcb(path: str | Path) -> Pcb:
    path = Path(path)
    if not path.is_file():
        raise PcbError(f"PCB file not found: {path}")
    try:
        root = _parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise PcbError(f"cannot read {path}: {e}") from None
    if not isinstance(root, list) or not root or root[0] != "kicad_pcb":
        raise PcbError(f"{path} is not a KiCad PCB file")

    try:
        footprints = {}
        for node in _kids(root, "footprint"):
            fp = _footprint(node)
            if fp is None:
                continue
            if fp.ref in footprints:
                raise PcbError(f"duplicate footprint reference {fp.ref!r}")
            footprints[fp.ref] = fp
        outline = tuple(_outline(root))
    except (ValueError, IndexError) as e:
        raise PcbError(f"{path}: malformed item: {e}") from None

    if not footprints:
        raise PcbError(f"{path} contains no footprints")
    if not outline:
        raise PcbError(f"{path} has no board outline on Edge.Cuts")
    points = []
    for s in outline:
        if s[0] == "circle":
            points += [(s[1] - s[3], s[2] - s[3]), (s[1] + s[3], s[2] + s[3])]
        else:
            points += list(zip(s[1::2], s[2::2]))
    return Pcb(footprints, outline, _box(points))


def _footprint(node: list) -> Footprint | None:
    ref = None
    for prop in _kids(node, "property"):
        if len(prop) > 2 and prop[1] == "Reference":
            ref = prop[2]
    if ref is None:  # KiCad 5 style
        for text in _kids(node, "fp_text"):
            if len(text) > 2 and text[1] == "reference":
                ref = text[2]
    if not ref:
        return None

    at = _kid(node, "at")
    fx, fy = _xy(at)
    angle = float(at[3]) if len(at) > 3 else 0.0
    layer = _kid(node, "layer")
    side = "bottom" if layer and layer[1].startswith("B.") else "top"

    pads = []
    for p in _kids(node, "pad"):
        number = p[1] if len(p) > 1 and isinstance(p[1], str) else ""
        if not number:
            continue  # paste only and mechanical pads
        pat = _kid(p, "at")
        lx, ly = _xy(pat)
        size = _kid(p, "size")
        w, h = _xy(size) if size else (0.0, 0.0)
        net = _kid(p, "net")
        x, y = _to_board(fx, fy, angle, lx, ly)
        pad_angle = float(pat[3]) if len(pat) > 3 else angle
        pads.append(Pad(number, _unescape(net[-1]) if net else None, x, y, w, h, pad_angle))

    points = [c for p in pads for c in _pad_corners(p)]
    for tag in ("fp_line", "fp_rect"):
        for item in _kids(node, tag):
            item_layer = _kid(item, "layer")
            if item_layer and item_layer[1].endswith("CrtYd"):
                for end in ("start", "end"):
                    points.append(_to_board(fx, fy, angle, *_xy(_kid(item, end))))
    if not points:
        points = [(fx - 0.5, fy - 0.5), (fx + 0.5, fy + 0.5)]
    return Footprint(ref, fx, fy, angle, side, tuple(pads), _box(points))


def _outline(root: list):
    for tag in ("gr_line", "gr_arc", "gr_circle", "gr_rect"):
        for item in _kids(root, tag):
            layer = _kid(item, "layer")
            if not layer or layer[1] != "Edge.Cuts":
                continue
            if tag == "gr_line":
                yield ("line", *_xy(_kid(item, "start")), *_xy(_kid(item, "end")))
            elif tag == "gr_arc":
                yield ("arc", *_xy(_kid(item, "start")), *_xy(_kid(item, "mid")),
                       *_xy(_kid(item, "end")))
            elif tag == "gr_circle":
                cx, cy = _xy(_kid(item, "center"))
                ex, ey = _xy(_kid(item, "end"))
                yield ("circle", cx, cy, math.hypot(ex - cx, ey - cy))
            else:
                (x0, y0), (x1, y1) = _xy(_kid(item, "start")), _xy(_kid(item, "end"))
                for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
                    yield ("line", *a, *b)


# Cross check

def check_against_graph(pcb: Pcb, graph: Graph) -> None:
    """Raise PcbError unless every part and every pad's net agree with the schematic."""
    pcb_refs, sch_refs = set(pcb.footprints), set(graph.part_refs)
    if pcb_refs != sch_refs:
        only_pcb = sorted(pcb_refs - sch_refs)
        only_sch = sorted(sch_refs - pcb_refs)
        raise PcbError(f"PCB and schematic disagree: only on PCB {only_pcb}, "
                       f"only in schematic {only_sch}")
    problems = []
    for fp in pcb.footprints.values():
        for pad in fp.pads:
            if not pad.net or pad.net.startswith("unconnected-"):
                continue
            if not graph.has_net(pad.net):
                problems.append(f"{fp.ref}.{pad.number} is on {pad.net}, which is not in the schematic")
            elif not any(p.ref == fp.ref and p.number == pad.number
                         for p in graph.net(pad.net).pins):
                problems.append(f"{fp.ref}.{pad.number} is on {pad.net} on the PCB but not in the schematic")
    if problems:
        raise PcbError("PCB and schematic disagree:\n  " + "\n  ".join(sorted(set(problems))))
