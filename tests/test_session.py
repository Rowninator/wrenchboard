import json

from pathlib import Path

import pytest

from wrenchboard.board import Board, Source, load_board
from wrenchboard.graph import load_netlist
from wrenchboard.session import Session, SessionError, replay

FIXTURES = Path(__file__).parent / "fixtures"

EXPECTED = {
    "usb": {"XUSB": 5.0, "USBVCC": 5.0, "GATE": 0.0, "+5V": 5.0, "+3V3": 3.3},
    "barrel": {"JIN": 9.0, "VCC": 8.3, "GATE": 5.0, "+5V": 5.0, "+3V3": 3.3},
}


@pytest.fixture
def board():
    return Board(
        graph=load_netlist(FIXTURES / "power_netlist.xml"),
        ground="GND",
        sources={"usb": Source("J1", "XUSB"), "barrel": Source("J2", "JIN")},
        expected=EXPECTED,
    )


def steps(session):
    return [r.step.key for r in session.history]


# Each case: rail, symptom, source, readings under the fault, faulty part, steps taken.
CASES = {
    "U1 open":  ("+5V", "dead", "barrel", {"VCC": 8.3}, "U1", ["VCC"]),
    "D1 open":  ("+5V", "dead", "barrel", {"VCC": 0.0}, "D1", ["VCC"]),
    "Q1 open":  ("+5V", "dead", "usb", {"USBVCC": 5.0, "GATE": 0.05}, "Q1", ["USBVCC", "GATE"]),
    "U3 wrong": ("+5V", "dead", "usb", {"USBVCC": 5.0, "GATE": 4.9}, "U3", ["USBVCC", "GATE"]),
    "F1 open":  ("+5V", "dead", "usb", {"USBVCC": 0.0}, "F1", ["USBVCC"]),
    "U2 open":  ("+3V3", "dead", "usb", {"+5V": 5.0}, "U2", ["+5V"]),
    "U2 short": ("+3V3", "shorted", None, {"lift:C2": 0.2, "lift:U2": 1500}, "U2",
                 ["lift:C2", "lift:U2"]),
}


@pytest.mark.parametrize("name", CASES)
def test_finds_fault(board, name):
    rail, symptom, source, readings, part, taken = CASES[name]
    session = replay(Session(board, rail, symptom, source), readings)
    assert session.result == [part]
    assert steps(session) == taken


@pytest.mark.parametrize("name", CASES)
def test_no_dead_end_steps(board, name):
    rail, symptom, source, readings, _, _ = CASES[name]
    session = replay(Session(board, rail, symptom, source), readings)
    assert all(r.cleared for r in session.history), "a step removed no suspect"
    keys = steps(session)
    assert len(keys) == len(set(keys)), "a net or part was tested twice"


@pytest.mark.parametrize("name", CASES)
def test_steps_use_only_graph_names(board, name):
    rail, symptom, source, readings, _, _ = CASES[name]
    session = replay(Session(board, rail, symptom, source), readings)
    for r in session.history:
        assert board.graph.has_net(r.step.net)
        assert r.step.ref is None or board.graph.has_part(r.step.ref)


def test_reading_near_zero_counts_as_good(board):
    session = Session(board, "+5V", "dead", "usb")
    session.record(5.0)                      # USBVCC good
    assert session.next_step().net == "GATE"
    assert session.record(0.2).good          # within 0.3 V of 0 V


def test_prompt_text(board):
    step = Session(board, "+5V", "dead", "barrel").next_step()
    assert step.prompt == "Measure the voltage on VCC (expected about 8.3 V)"
    lift = Session(board, "+3V3", "shorted").next_step()
    assert lift.prompt == "Lift C2, then measure resistance from +3V3 to ground (ohms)"


def test_record_after_done_raises(board):
    session = replay(Session(board, "+5V", "dead", "barrel"), {"VCC": 8.3})
    with pytest.raises(SessionError, match="finished"):
        session.record(1.0)


@pytest.mark.parametrize("bad", ["5", None, True])
def test_non_number_reading_raises(board, bad):
    with pytest.raises(SessionError, match="must be a number"):
        Session(board, "+5V", "dead", "barrel").record(bad)


def test_scenario_missing_reading_raises(board):
    with pytest.raises(SessionError, match="no reading for VCC"):
        replay(Session(board, "+5V", "dead", "barrel"), {})


UNO_DIR = Path(__file__).parent.parent / "boards" / "uno_r3"
UNO_SCENARIOS = json.loads((FIXTURES / "uno_scenarios.json").read_text())["scenarios"]


@pytest.mark.skipif(not (UNO_DIR / "netlist.xml").is_file(), reason="run scripts/export_netlist.py first")
@pytest.mark.parametrize("sc", UNO_SCENARIOS, ids=lambda s: s["name"])
def test_uno_scenario(sc):
    board = load_board(UNO_DIR / "board.json", load_netlist(UNO_DIR / "netlist.xml"))
    session = replay(Session(board, sc["rail"], sc["symptom"], sc["source"]), sc["readings"])
    assert session.result == [sc["fault"]]
    assert all(r.cleared for r in session.history), "dead end step"
    keys = steps(session)
    assert len(keys) == len(set(keys)), "tested something twice"
