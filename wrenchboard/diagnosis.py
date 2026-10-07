"""Rule based diagnosis: rank suspect parts for a faulty power rail.

Every part and net name in the output comes from the graph, and is checked
against it again before being returned.

Rules
  dead rail     Walk backward from the rail toward the active power source,
                through parts that can carry power onto the rail:
                  regulator  a power_out pin on the rail, power_in pins upstream
                  switch     exactly 3 connected pins: one input (the control)
                             and two passive, on different nets
                  series     exactly 2 connected pins, on 2 different nets
                Ground is never crossed. Only parts on a path that actually
                reaches the source net are kept. For a switch, the part driving
                its control input is added as a control suspect.
  shorted rail  Every part with a pin on the rail and a pin on ground.
Unpopulated (DNP) parts are ignored everywhere.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .board import Board
from .graph import GraphError, Pin, natural_key

SYMPTOMS = ("dead", "shorted")

# Order within the same distance: parts in the power path before control parts.
_ROLE_ORDER = {"regulator": 0, "switch": 0, "series": 0, "control": 1}
# Order for shorts, by reference prefix: capacitors, protection, ICs, other, connectors.
_SHORT_ORDER = {"C": 0, "D": 1, "Z": 1, "U": 2, "J": 4}


class DiagnosisError(GraphError):
    """The request is invalid or the result would name something not in the graph."""


@dataclass(frozen=True)
class Suspect:
    ref: str
    role: str                  # regulator, switch, series, control, or shunt
    distance: int | None       # parts away from the rail (1 = directly on it)
    nets: tuple[str, ...]      # every net named in the reason
    reason: str


@dataclass(frozen=True)
class _Edge:
    down: str   # net nearer the rail
    up: str     # net nearer the source
    ref: str
    role: str
    control: str | None = None  # control net, for switches


def diagnose(board: Board, rail: str, symptom: str, source: str | None = None) -> list[Suspect]:
    graph = board.graph
    graph.net(rail)  # raises NetNotFound for an unknown rail
    if rail == board.ground:
        raise DiagnosisError("the ground net cannot be the faulty rail")
    if symptom not in SYMPTOMS:
        raise DiagnosisError(f"unknown symptom {symptom!r}; expected one of {SYMPTOMS}")

    if symptom == "shorted":
        suspects = _shorted(board, rail)
    else:
        if source not in board.sources:
            raise DiagnosisError(
                f"a dead rail needs the active source, one of {sorted(board.sources)}"
            )
        suspects = _dead(board, rail, board.sources[source].net)

    _check_names(board, suspects)
    return suspects


def _pins_of(board: Board, ref: str) -> list[tuple[Pin, str]]:
    """Connected pins of a part, each with the net it sits on."""
    out = []
    for net in board.graph.nets_on_part(ref):
        if net.unconnected:
            continue
        out.extend((pin, net.name) for pin in net.pins if pin.ref == ref)
    return out


def _upstream_edges(board: Board, net: str) -> list[_Edge]:
    graph, ground = board.graph, board.ground
    edges = []
    for part in graph.parts_on_net(net):
        if part.dnp:
            continue
        pins = _pins_of(board, part.ref)
        types = [p.type for p, _ in pins]
        nets_here = {n for _, n in pins}

        if any(p.type == "power_out" and n == net for p, n in pins):
            for p, n in pins:
                if p.type == "power_in" and n not in (net, ground):
                    edges.append(_Edge(net, n, part.ref, "regulator"))

        elif len(pins) == 3 and types.count("input") == 1 and types.count("passive") == 2:
            control = next(n for p, n in pins if p.type == "input")
            passive = [n for p, n in pins if p.type == "passive"]
            if net in passive and len(set(passive)) == 2:
                other = passive[0] if passive[1] == net else passive[1]
                if other != ground:
                    edges.append(_Edge(net, other, part.ref, "switch", control))

        elif len(pins) == 2 and len(nets_here) == 2 and ground not in nets_here:
            names = {p.name: n for p, n in pins}
            if set(names) == {"A", "K"} and names["K"] != net:
                continue  # a diode only carries power from anode to cathode
            other = next(n for n in nets_here if n != net)
            edges.append(_Edge(net, other, part.ref, "series"))
    return edges


def _dead(board: Board, rail: str, source_net: str) -> list[Suspect]:
    graph = board.graph
    # 1. Explore upstream from the rail, recording every edge and each net's distance.
    dist = {rail: 0}
    edges: list[_Edge] = []
    queue = deque([rail])
    while queue:
        net = queue.popleft()
        if net == source_net:
            continue  # the source is the end of the line
        for e in _upstream_edges(board, net):
            if e.up not in dist:
                dist[e.up] = dist[net] + 1
                queue.append(e.up)
            if dist[e.up] == dist[net] + 1:  # only move away from the rail
                edges.append(e)
    if source_net not in dist:
        raise DiagnosisError(f"no power path from {source_net} to {rail}")

    # 2. Keep only edges whose upstream end can still reach the source.
    reaches = {source_net}
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e.up in reaches and e.down not in reaches:
                reaches.add(e.down)
                changed = True
    kept = [e for e in edges if e.up in reaches]

    # 3. Turn edges into suspects, nearest first, one entry per part.
    best: dict[str, Suspect] = {}

    def add(s: Suspect) -> None:
        if s.ref not in best or s.distance < best[s.ref].distance:
            best[s.ref] = s

    for e in kept:
        d = dist[e.down] + 1
        if e.role == "regulator":
            reason = f"{e.ref} regulates {e.up} down to {e.down}"
            add(Suspect(e.ref, e.role, d, (e.up, e.down), reason))
        elif e.role == "series":
            reason = f"{e.ref} is in series between {e.up} and {e.down}"
            add(Suspect(e.ref, e.role, d, (e.up, e.down), reason))
        else:
            reason = f"{e.ref} switches {e.up} onto {e.down}, controlled by {e.control}"
            add(Suspect(e.ref, e.role, d, (e.up, e.down, e.control), reason))
            for pin in graph.net(e.control).pins:
                if pin.type == "output" and pin.ref != e.ref and not graph.part(pin.ref).dnp:
                    reason = f"{pin.ref} drives {e.control}, the control input of {e.ref}"
                    add(Suspect(pin.ref, "control", d, (e.control,), reason))

    return sorted(
        best.values(),
        key=lambda s: (s.distance, _ROLE_ORDER[s.role], natural_key(s.ref)),
    )


def _shorted(board: Board, rail: str) -> list[Suspect]:
    graph, ground = board.graph, board.ground
    suspects = []
    for part in graph.parts_on_net(rail):
        if part.dnp:
            continue
        if any(n.name == ground for n in graph.nets_on_part(part.ref)):
            reason = f"{part.ref} connects {rail} to {ground}"
            suspects.append(Suspect(part.ref, "shunt", None, (rail, ground), reason))

    def order(s: Suspect):
        prefix = s.ref.rstrip("0123456789")
        return (_SHORT_ORDER.get(prefix, 3), natural_key(s.ref))

    return sorted(suspects, key=order)


def _check_names(board: Board, suspects: list[Suspect]) -> None:
    graph = board.graph
    for s in suspects:
        if not graph.has_part(s.ref):
            raise DiagnosisError(f"internal error: suspect {s.ref!r} is not in the graph")
        for n in s.nets:
            if not graph.has_net(n):
                raise DiagnosisError(f"internal error: net {n!r} is not in the graph")
