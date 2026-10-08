from pathlib import Path

import pytest

from wrenchboard.graph import load_netlist
from wrenchboard.pcb import PcbError, _unescape, check_against_graph, load_pcb

FIXTURES = Path(__file__).parent / "fixtures"
MINI_PCB = FIXTURES / "mini.kicad_pcb"
UNO_DIR = Path(__file__).parent.parent / "boards" / "uno_r3"
UNO_PCB = UNO_DIR / "Arduino UNO.kicad_pcb"


@pytest.fixture
def pcb():
    return load_pcb(MINI_PCB)


def pad(pcb, ref, number):
    return next(p for p in pcb.footprints[ref].pads if p.number == number)


def test_loads_footprints_and_outline(pcb):
    assert sorted(pcb.footprints) == ["C1", "D1", "FID1", "J1", "U1"]
    assert pcb.bounds == (0, 0, 50, 30)


def test_rotated_pad_positions(pcb):
    # D1 sits at (20, 10) rotated 90 degrees counterclockwise.
    assert (pad(pcb, "D1", "1").x, pad(pcb, "D1", "1").y) == pytest.approx((20, 12))
    assert (pad(pcb, "D1", "2").x, pad(pcb, "D1", "2").y) == pytest.approx((20, 8))


def test_side(pcb):
    assert pcb.footprints["U1"].side == "bottom"
    assert pcb.footprints["C1"].side == "top"


def test_paste_only_pads_skipped(pcb):
    assert [p.number for p in pcb.footprints["U1"].pads] == ["1", "2", "3"]


def test_boxes_contain_their_pads(pcb):
    for fp in pcb.footprints.values():
        assert all(fp.contains(p.x, p.y) for p in fp.pads)


def test_part_without_pads_uses_courtyard(pcb):
    assert pcb.footprints["FID1"].box == pytest.approx((4, 4, 6, 6))


def test_pads_on_net(pcb):
    assert sorted(ref for ref, _ in pcb.pads_on_net("GND")) == ["C1", "J1", "U1"]


def test_unescape_net_names():
    assert _unescape("AD4{slash}SDA") == "AD4/SDA"
    assert _unescape("+5V") == "+5V"


def test_matches_mini_schematic(pcb):
    check_against_graph(pcb, load_netlist(FIXTURES / "mini_netlist.xml"))


def test_part_mismatch_raises(pcb):
    with pytest.raises(PcbError, match="only on PCB"):
        check_against_graph(pcb, load_netlist(FIXTURES / "power_netlist.xml"))


def test_pad_on_wrong_net_raises(tmp_path):
    text = MINI_PCB.read_text().replace(
        '(at -1 0 0) (size 0.8 0.8) (net 5 "+5V")', '(at -1 0 0) (size 0.8 0.8) (net 2 "GND")')
    f = tmp_path / "bad.kicad_pcb"
    f.write_text(text)
    with pytest.raises(PcbError, match=r"C1\.1 is on GND"):
        check_against_graph(load_pcb(f), load_netlist(FIXTURES / "mini_netlist.xml"))


@pytest.mark.parametrize("text, message", [
    ("(kicad_sch (version 1))", "not a KiCad PCB"),
    ("(kicad_pcb (version 1)", "unbalanced"),
    ('(kicad_pcb (gr_rect (start 0 0) (end 1 1) (layer "Edge.Cuts")))', "no footprints"),
])
def test_bad_files(tmp_path, text, message):
    f = tmp_path / "x.kicad_pcb"
    f.write_text(text)
    with pytest.raises(PcbError, match=message):
        load_pcb(f)


def test_missing_file(tmp_path):
    with pytest.raises(PcbError, match="not found"):
        load_pcb(tmp_path / "nope.kicad_pcb")


@pytest.mark.skipif(not (UNO_DIR / "netlist.xml").is_file(), reason="run scripts/export_netlist.py first")
def test_uno_pcb_matches_schematic():
    pcb = load_pcb(UNO_PCB)
    assert len(pcb.footprints) == 56
    check_against_graph(pcb, load_netlist(UNO_DIR / "netlist.xml"))
