import json
from pathlib import Path

import pytest

from wrenchboard.graph import (
    NetlistFileError,
    NetlistFormatError,
    NetNotFound,
    PartNotFound,
    load_netlist,
    natural_key,
)

FIXTURES = Path(__file__).parent / "fixtures"
MINI = FIXTURES / "mini_netlist.xml"
UNO_NETLIST = Path(__file__).parent.parent / "boards" / "uno_r3" / "netlist.xml"
UNO_CHECKS = FIXTURES / "uno_spot_checks.json"


@pytest.fixture
def mini():
    return load_netlist(MINI)


# Loading and lookups (hand made netlist)

def test_loads_real_parts_and_skips_power_symbols(mini):
    assert mini.part_refs == ["C1", "D1", "FID1", "J1", "U1"]
    assert not mini.has_part("#PWR01")


def test_part_fields(mini):
    u1 = mini.part("U1")
    assert u1.value == "NCP1117-5.0"
    assert u1.footprint == "SOT223"
    assert u1.sheet == "/Power/"

def test_dnp_flag(mini):
    assert mini.part("C1").dnp
    assert not mini.part("U1").dnp

def test_electrical_parts_skip_dnp_and_unconnected(mini):
    assert mini.electrical_part_refs == ["D1", "J1", "U1"]

def test_resolve_ref(mini):
    assert mini.resolve_ref("U1") == "U1"
    assert mini.resolve_ref("U1A") == "U1"
    assert mini.resolve_ref("U1B") == "U1"


def test_resolve_ref_rejects_unknown(mini):
    for name in ["U9", "U9A", "X1", "U1A1", ""]:
        with pytest.raises(PartNotFound):
            mini.resolve_ref(name)

def test_natural_key():
    assert sorted(["C10", "C2", "C1"], key=natural_key) == ["C1", "C2", "C10"]

def test_net_pins_exclude_power_symbols(mini):
    assert {str(p) for p in mini.net("GND").pins} == {"J1.2", "U1.1", "C1.2"}


def test_pin_names_kept(mini):
    pins = {str(p): p.name for p in mini.net("VIN").pins}
    assert pins == {"D1.1": "K", "U1.3": "VI"}


def test_parts_on_net(mini):
    assert [p.ref for p in mini.parts_on_net("+5V")] == ["C1", "U1"]


def test_nets_on_part(mini):
    names = [n.name for n in mini.nets_on_part("U1")]
    assert names == ["+5V", "GND", "VIN"]


def test_net_crossing_sheets(mini):
    sheets = {p.sheet for p in mini.parts_on_net("+5V")}
    assert sheets == {"/", "/Power/"}


def test_unconnected_flag(mini):
    assert mini.net("unconnected-(J1-Pad3)").unconnected
    assert not mini.net("GND").unconnected


# Unknown names never return empty results

def test_unknown_part_raises(mini):
    with pytest.raises(PartNotFound):
        mini.part("R99")
    with pytest.raises(PartNotFound):
        mini.nets_on_part("R99")


def test_unknown_net_raises(mini):
    with pytest.raises(NetNotFound):
        mini.net("+12V")
    with pytest.raises(NetNotFound):
        mini.parts_on_net("+12V")


# Bad files

def _write(tmp_path, text):
    p = tmp_path / "net.xml"
    p.write_text(text)
    return p


def test_missing_file(tmp_path):
    with pytest.raises(NetlistFileError):
        load_netlist(tmp_path / "nope.xml")


def test_malformed_xml(tmp_path):
    with pytest.raises(NetlistFormatError):
        load_netlist(_write(tmp_path, "<export><components>"))


def test_wrong_root(tmp_path):
    with pytest.raises(NetlistFormatError):
        load_netlist(_write(tmp_path, "<kicad_sch/>"))


def test_no_parts(tmp_path):
    with pytest.raises(NetlistFormatError, match="no parts"):
        load_netlist(_write(tmp_path, "<export><components/><nets/></export>"))


def test_no_nets(tmp_path):
    xml = '<export><components><comp ref="R1"/></components><nets/></export>'
    with pytest.raises(NetlistFormatError, match="no nets"):
        load_netlist(_write(tmp_path, xml))


def test_duplicate_ref(tmp_path):
    xml = ('<export><components><comp ref="R1"/><comp ref="R1"/></components>'
           '<nets><net code="1" name="A"><node ref="R1" pin="1"/></net></nets></export>')
    with pytest.raises(NetlistFormatError, match="duplicate part"):
        load_netlist(_write(tmp_path, xml))


def test_net_references_unknown_part(tmp_path):
    xml = ('<export><components><comp ref="R1"/></components>'
           '<nets><net code="1" name="A"><node ref="R2" pin="1"/></net></nets></export>')
    with pytest.raises(NetlistFormatError, match="unknown part"):
        load_netlist(_write(tmp_path, xml))


# Phase 1 checkpoint: Uno R3 spot checks

@pytest.mark.skipif(not UNO_NETLIST.is_file(), reason="run scripts/export_netlist.py first")
def test_uno_spot_checks():
    graph = load_netlist(UNO_NETLIST)
    checks = json.loads(UNO_CHECKS.read_text())["checks"]
    checks = [c for c in checks if c["net"] != "EXAMPLE_NET_NAME"]
    assert len(checks) >= 10, f"only {len(checks)} spot checks filled in; need 10"
    failures = []
    for c in checks:
        if not graph.has_net(c["net"]):
            failures.append(f"{c['net']}: net not found")
            continue
        got = {str(p) for p in graph.net(c["net"]).pins}
        want = set(c["pins"])
        if got != want:
            failures.append(
                f"{c['net']}: missing {sorted(want - got)}, extra {sorted(got - want)}"
            )
    assert not failures, "\n".join(failures)

@pytest.mark.skipif(not UNO_NETLIST.is_file(), reason="run scripts/export_netlist.py first")
def test_uno_dnp_parts():
    graph = load_netlist(UNO_NETLIST)
    assert [r for r in graph.part_refs if graph.part(r).dnp] == ["R1", "R2"]

@pytest.mark.skipif(not UNO_NETLIST.is_file(), reason="run scripts/export_netlist.py first")
def test_uno_electrical_parts():
    refs = set(load_netlist(UNO_NETLIST).electrical_part_refs)
    assert refs.isdisjoint({"FID1", "FID2", "FID3", "R1", "R2"})
    assert "U4" in refs

@pytest.mark.skipif(not UNO_NETLIST.is_file(), reason="run scripts/export_netlist.py first")
def test_uno_resolve_units():
    graph = load_netlist(UNO_NETLIST)
    assert graph.resolve_ref("RN1A") == "RN1"
    assert graph.resolve_ref("RN2A") == "RN2"

@pytest.mark.skipif(not UNO_NETLIST.is_file(), reason="run scripts/export_netlist.py first")
def test_uno_refs_sorted_naturally():
    refs = load_netlist(UNO_NETLIST).part_refs
    assert refs.index("C2") < refs.index("C10")