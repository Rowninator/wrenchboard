"""Parts and connections graph built from a KiCad XML netlist.

The netlist is produced by KiCad itself (see scripts/export_netlist.py), so
connectivity across wires, labels, power symbols and hierarchical sheets is
already resolved. This module only reads it; it never invents names.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


class GraphError(Exception):
    """Base class for every error raised by this module."""


class NetlistFileError(GraphError):
    """The netlist file is missing or cannot be read."""


class NetlistFormatError(GraphError):
    """The file was read but is not a usable KiCad XML netlist."""


class PartNotFound(GraphError):
    def __init__(self, ref: str):
        super().__init__(f"part {ref!r} is not in the schematic")
        self.ref = ref


class NetNotFound(GraphError):
    def __init__(self, name: str):
        super().__init__(f"net {name!r} is not in the schematic")
        self.name = name


@dataclass(frozen=True)
class Pin:
    ref: str
    number: str
    name: str | None = None
    type: str | None = None

    def __str__(self) -> str:
        return f"{self.ref}.{self.number}"


@dataclass(frozen=True)
class Part:
    ref: str
    value: str
    footprint: str
    sheet: str


@dataclass(frozen=True)
class Net:
    name: str
    code: str
    pins: tuple[Pin, ...]

    @property
    def unconnected(self) -> bool:
        """KiCad names single pin nets 'unconnected-(...)'; nothing to measure."""
        return self.name.startswith("unconnected-")


class Graph:
    def __init__(self, parts: dict[str, Part], nets: dict[str, Net]):
        self._parts = parts
        self._nets = nets
        self._part_nets: dict[str, list[Net]] = {ref: [] for ref in parts}
        for net in nets.values():
            for pin in net.pins:
                if pin.ref not in parts:
                    raise NetlistFormatError(
                        f"net {net.name!r} references unknown part {pin.ref!r}"
                    )
                if net not in self._part_nets[pin.ref]:
                    self._part_nets[pin.ref].append(net)

    @property
    def part_refs(self) -> list[str]:
        return sorted(self._parts)

    @property
    def net_names(self) -> list[str]:
        return sorted(self._nets)

    def has_part(self, ref: str) -> bool:
        return ref in self._parts

    def has_net(self, name: str) -> bool:
        return name in self._nets

    def part(self, ref: str) -> Part:
        try:
            return self._parts[ref]
        except KeyError:
            raise PartNotFound(ref) from None

    def net(self, name: str) -> Net:
        try:
            return self._nets[name]
        except KeyError:
            raise NetNotFound(name) from None

    def parts_on_net(self, name: str) -> list[Part]:
        refs = {pin.ref for pin in self.net(name).pins}
        return [self._parts[r] for r in sorted(refs)]

    def nets_on_part(self, ref: str) -> list[Net]:
        self.part(ref)  # raises PartNotFound
        return sorted(self._part_nets[ref], key=lambda n: n.name)


def load_netlist(path: str | Path) -> Graph:
    """Read a KiCad XML netlist (kicadxml export) into a Graph."""
    path = Path(path)
    if not path.is_file():
        raise NetlistFileError(f"netlist file not found: {path}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise NetlistFormatError(f"{path} is not valid XML: {e}") from None
    except OSError as e:
        raise NetlistFileError(f"cannot read {path}: {e}") from None

    if root.tag != "export":
        raise NetlistFormatError(
            f"{path} is not a KiCad XML netlist (root is <{root.tag}>)"
        )

    parts: dict[str, Part] = {}
    for comp in root.iterfind("./components/comp"):
        ref = comp.get("ref")
        if not ref:
            raise NetlistFormatError("component without a ref attribute")
        if ref.startswith("#"):  # power and flag symbols, not real parts
            continue
        if ref in parts:
            raise NetlistFormatError(f"duplicate part reference {ref!r}")
        sheetpath = comp.find("sheetpath")
        parts[ref] = Part(
            ref=ref,
            value=comp.findtext("value", default=""),
            footprint=comp.findtext("footprint", default=""),
            sheet=sheetpath.get("names", "/") if sheetpath is not None else "/",
        )
    if not parts:
        raise NetlistFormatError(f"{path} contains no parts")

    nets: dict[str, Net] = {}
    for net_el in root.iterfind("./nets/net"):
        name = net_el.get("name")
        if not name:
            raise NetlistFormatError("net without a name attribute")
        if name in nets:
            raise NetlistFormatError(f"duplicate net name {name!r}")
        pins = tuple(
            Pin(
                ref=node.get("ref", ""),
                number=node.get("pin", ""),
                name=node.get("pinfunction"),
                type=node.get("pintype"),
            )
            for node in net_el.iterfind("node")
            if not node.get("ref", "").startswith("#")
        )
        nets[name] = Net(name=name, code=net_el.get("code", ""), pins=pins)
    if not nets:
        raise NetlistFormatError(f"{path} contains no nets")

    return Graph(parts, nets)
