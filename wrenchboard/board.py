"""Board configuration: facts about a board, read from its schematic by hand.

Every name in the config is checked against the graph on load, so the config
follows the same rule as everything else: no names that aren't in the schematic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .graph import Graph, GraphError


class BoardConfigError(GraphError):
    """The board config is missing, malformed, or names something not in the graph."""


@dataclass(frozen=True)
class Source:
    connector: str  # part reference of the power input connector
    net: str        # the net that connector supplies power on


@dataclass(frozen=True)
class Board:
    graph: Graph
    ground: str
    sources: dict[str, Source]


def load_board(path: str | Path, graph: Graph) -> Board:
    path = Path(path)
    if not path.is_file():
        raise BoardConfigError(f"board config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BoardConfigError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise BoardConfigError(f"{path}: expected a JSON object")

    ground = data.get("ground")
    if not isinstance(ground, str) or not ground:
        raise BoardConfigError(f"{path}: 'ground' must be a net name")
    if not graph.has_net(ground):
        raise BoardConfigError(f"{path}: ground net {ground!r} is not in the schematic")

    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise BoardConfigError(f"{path}: 'sources' must name at least one connector")
    parsed = {
        name: _load_source(path, graph, ground, name, spec)
        for name, spec in sources.items()
    }
    return Board(graph=graph, ground=ground, sources=parsed)


def _load_source(path: Path, graph: Graph, ground: str, name: str, spec) -> Source:
    where = f"{path}: source {name!r}"
    if not isinstance(spec, dict):
        raise BoardConfigError(f"{where} must be an object with 'connector' and 'net'")
    connector, net = spec.get("connector"), spec.get("net")
    if not isinstance(connector, str) or not graph.has_part(connector):
        raise BoardConfigError(f"{where}: connector {connector!r} is not in the schematic")
    if not isinstance(net, str) or not graph.has_net(net):
        raise BoardConfigError(f"{where}: supply net {net!r} is not in the schematic")
    if net == ground:
        raise BoardConfigError(f"{where}: supply net cannot be the ground net")
    if all(n.name != net for n in graph.nets_on_part(connector)):
        raise BoardConfigError(f"{where}: connector {connector} has no pin on {net}")
    return Source(connector=connector, net=net)