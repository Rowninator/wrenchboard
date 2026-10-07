from pathlib import Path

import pytest

from wrenchboard.board import Board, Source
from wrenchboard.diagnosis import DiagnosisError, diagnose
from wrenchboard.graph import NetNotFound, load_netlist

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def board():
    graph = load_netlist(FIXTURES / "power_netlist.xml")
    return Board(
        graph=graph,
        ground="GND",
        sources={"usb": Source("J1", "XUSB"), "barrel": Source("J2", "JIN")},
    )


def refs(suspects):
    return [s.ref for s in suspects]


# Dead rail

def test_dead_5v_on_barrel(board):
    # USB branch dropped; RN1 (pack) and R1 (DNP) never create a path.
    assert refs(diagnose(board, "+5V", "dead", "barrel")) == ["U1", "D1"]


def test_dead_5v_on_usb_includes_switch_control(board):
    assert refs(diagnose(board, "+5V", "dead", "usb")) == ["Q1", "U3", "F1"]


def test_dead_3v3_on_usb_walks_through_regulator(board):
    assert refs(diagnose(board, "+3V3", "dead", "usb")) == ["U2", "Q1", "U3", "F1"]


def test_roles_and_distances(board):
    by_ref = {s.ref: s for s in diagnose(board, "+5V", "dead", "usb")}
    assert (by_ref["Q1"].role, by_ref["Q1"].distance) == ("switch", 1)
    assert (by_ref["U3"].role, by_ref["U3"].distance) == ("control", 1)
    assert (by_ref["F1"].role, by_ref["F1"].distance) == ("series", 2)


def test_dead_ends_and_traps_excluded(board):
    for source in ("usb", "barrel"):
        found = set(refs(diagnose(board, "+5V", "dead", source)))
        assert found.isdisjoint({"RN1", "R1", "R2", "D2", "C1"})


def test_pullup_counts_as_a_path(board):
    # RST is fed from +5V through R2, so a dead RST traces back to the source.
    found = refs(diagnose(board, "RST", "dead", "barrel"))
    assert set(found[:2]) == {"R2", "D2"}
    assert found[2:] == ["U1", "D1"]


def test_no_path_raises(board):
    with pytest.raises(DiagnosisError, match="no power path"):
        diagnose(board, "SENSE", "dead", "barrel")


# Shorted rail

def test_shorted_3v3(board):
    assert refs(diagnose(board, "+3V3", "shorted")) == ["C2", "U2", "U3"]


def test_shorted_5v_orders_capacitors_first(board):
    assert refs(diagnose(board, "+5V", "shorted")) == ["C1", "U1", "U2", "U3", "RN1"]


# Bad requests

def test_unknown_rail(board):
    with pytest.raises(NetNotFound):
        diagnose(board, "+12V", "dead", "usb")


def test_ground_rail(board):
    with pytest.raises(DiagnosisError, match="ground"):
        diagnose(board, "GND", "shorted")


def test_unknown_symptom(board):
    with pytest.raises(DiagnosisError, match="unknown symptom"):
        diagnose(board, "+5V", "smoking")


@pytest.mark.parametrize("source", [None, "solar"])
def test_dead_needs_valid_source(board, source):
    with pytest.raises(DiagnosisError, match="active source"):
        diagnose(board, "+5V", "dead", source)


# Accuracy rule

@pytest.mark.parametrize("rail, symptom, source", [
    ("+5V", "dead", "usb"), ("+5V", "dead", "barrel"),
    ("+3V3", "dead", "usb"), ("+3V3", "dead", "barrel"),
    ("+5V", "shorted", None), ("+3V3", "shorted", None),
])
def test_every_name_is_in_the_graph(board, rail, symptom, source):
    graph = board.graph
    for s in diagnose(board, rail, symptom, source):
        assert graph.has_part(s.ref)
        assert all(graph.has_net(n) for n in s.nets)
        assert not graph.part(s.ref).dnp
