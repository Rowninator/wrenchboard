import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from wrenchboard.board import load_board
from wrenchboard.graph import load_netlist
from wrenchboard.pcb import PcbError, load_pcb
from wrenchboard.render import render_svg
from wrenchboard.session import Session, replay

FIXTURES = Path(__file__).parent / "fixtures"
UNO_DIR = Path(__file__).parent.parent / "boards" / "uno_r3"
NS = {"s": "http://www.w3.org/2000/svg"}


@pytest.fixture
def pcb():
    return load_pcb(FIXTURES / "mini.kicad_pcb")


def highlights(svg):
    """Each highlight rect: ref, state, and its box."""
    out = {}
    for r in ET.fromstring(svg).iterfind(".//s:rect[@data-highlight]", NS):
        x, y = float(r.get("x")), float(r.get("y"))
        out[r.get("data-highlight")] = (r.get("class"), (x, y, x + float(r.get("width")),
                                                         y + float(r.get("height"))))
    return out


def assert_highlights_on_parts(pcb, svg):
    """The Phase 4 checkpoint: every highlight box surrounds all of that part's pads."""
    for ref, (_, (x0, y0, x1, y1)) in highlights(svg).items():
        fp = pcb.footprints[ref]
        assert all(x0 <= p.x <= x1 and y0 <= p.y <= y1 for p in fp.pads), ref
        assert x0 <= fp.box[0] and y0 <= fp.box[1] and fp.box[2] <= x1 and fp.box[3] <= y1, ref


def test_valid_svg_with_every_part(pcb):
    root = ET.fromstring(render_svg(pcb))
    refs = {g.get("data-ref") for g in root.iterfind(".//s:g[@data-ref]", NS)}
    assert refs == set(pcb.footprints)


def test_highlights_land_on_parts(pcb):
    svg = render_svg(pcb, {"U1": "suspect", "D1": "cleared", "J1": "found"})
    assert {r: s for r, (s, _) in highlights(svg).items()} == {
        "U1": "suspect", "D1": "cleared", "J1": "found"}
    assert_highlights_on_parts(pcb, svg)


def test_measure_net_marks_exactly_its_pads(pcb):
    root = ET.fromstring(render_svg(pcb, measure_net="GND"))
    marked = {c.get("data-pad") for c in root.iterfind(".//s:circle[@data-pad]", NS)}
    assert marked == {"J1.2", "U1.1", "C1.2"}


def test_bottom_side_parts_dashed(pcb):
    root = ET.fromstring(render_svg(pcb))
    g = root.find(".//s:g[@data-ref='U1']", NS)
    assert "bottom" in g.find("s:rect", NS).get("class")


def test_text_is_escaped(pcb):
    ET.fromstring(render_svg(pcb, title="<5V & falling>"))  # must stay valid XML


@pytest.mark.parametrize("kwargs, message", [
    ({"parts": {"U9": "suspect"}}, "no such part"),
    ({"parts": {"U1": "smoking"}}, "unknown highlight state"),
    ({"measure_net": "+12V"}, "no pads on that net"),
])
def test_bad_requests(pcb, kwargs, message):
    with pytest.raises(PcbError, match=message):
        render_svg(pcb, **kwargs)


@pytest.mark.skipif(not (UNO_DIR / "netlist.xml").is_file(), reason="run scripts/export_netlist.py first")
@pytest.mark.parametrize("sc", json.loads((FIXTURES / "uno_scenarios.json").read_text())["scenarios"],
                         ids=lambda s: s["name"])
def test_uno_highlights_land_on_parts(sc):
    board = load_board(UNO_DIR / "board.json", load_netlist(UNO_DIR / "netlist.xml"))
    pcb = load_pcb(UNO_DIR / "Arduino UNO.kicad_pcb")
    session = Session(board, sc["rail"], sc["symptom"], sc["source"])
    start = render_svg(pcb, {ref: "suspect" for ref in session.result})
    assert set(highlights(start)) == set(session.result)
    assert_highlights_on_parts(pcb, start)
    replay(session, sc["readings"])
    end = render_svg(pcb, {sc["fault"]: "found"})
    assert_highlights_on_parts(pcb, end)
