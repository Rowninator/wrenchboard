import json
import shutil
from pathlib import Path

import pytest

from wrenchboard.__main__ import main

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def board_dir(tmp_path):
    shutil.copy(FIXTURES / "power_netlist.xml", tmp_path / "netlist.xml")
    (tmp_path / "board.json").write_text(json.dumps({
        "ground": "GND",
        "sources": {
            "usb": {"connector": "J1", "net": "XUSB"},
            "barrel": {"connector": "J2", "net": "JIN"},
        },
        "expected": {
            "usb": {"XUSB": 5.0, "USBVCC": 5.0, "GATE": 0.0, "+5V": 5.0},
            "barrel": {"JIN": 9.0, "VCC": 8.3, "GATE": 5.0, "+5V": 5.0},
        },
    }))
    return str(tmp_path)


def run(capsys, *args):
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_dead_rail(capsys, board_dir):
    code, out, _ = run(capsys, "diagnose", "+5V", "dead", "--source", "barrel", "--board", board_dir)
    assert code == 0
    assert "1. U1 regulates VCC down to +5V" in out
    assert "2. D1 is in series between JIN and VCC" in out


def test_shorted_rail(capsys, board_dir):
    code, out, _ = run(capsys, "diagnose", "+3V3", "shorted", "--board", board_dir)
    assert code == 0
    assert "1. C2 connects +3V3 to GND" in out


@pytest.mark.parametrize("args, message", [
    (["+12V", "dead", "--source", "usb"], "not in the schematic"),
    (["+5V", "dead"], "active source"),
])
def test_bad_request_prints_error(capsys, board_dir, args, message):
    code, out, err = run(capsys, "diagnose", *args, "--board", board_dir)
    assert code == 1
    assert message in err
    assert out == ""


def test_missing_board(capsys, tmp_path):
    code, _, err = run(capsys, "diagnose", "+5V", "shorted", "--board", str(tmp_path / "nope"))
    assert code == 1
    assert "not found" in err


def test_unknown_symptom_rejected(capsys, board_dir):
    with pytest.raises(SystemExit) as e:
        main(["diagnose", "+5V", "smoking", "--board", board_dir])
    assert e.value.code == 2


# session command

def test_session_interactive(capsys, monkeypatch, board_dir):
    answers = iter(["abc", "5.0", "0.1"])  # one bad entry, then USBVCC, then GATE
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    code, out, _ = run(capsys, "session", "+5V", "dead", "--source", "usb", "--board", board_dir)
    assert code == 0
    assert "Please enter a number" in out
    assert "Cleared: F1" in out
    assert "Fault found: Q1" in out


def test_session_quit(capsys, monkeypatch, board_dir):
    monkeypatch.setattr("builtins.input", lambda prompt: "q")
    code, out, _ = run(capsys, "session", "+5V", "dead", "--source", "barrel", "--board", board_dir)
    assert code == 1
    assert "Stopped." in out


def test_session_replay_list(capsys, tmp_path, board_dir):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"scenarios": [
        {"name": "D1 open", "readings": {"VCC": 0.0}},
        {"name": "U1 open", "readings": {"VCC": 8.3}},
    ]}))
    code, out, _ = run(capsys, "session", "+5V", "dead", "--source", "barrel",
                       "--board", board_dir, "--replay", str(f), "--scenario", "U1 open")
    assert code == 0
    assert "Measure the voltage on VCC (expected about 8.3 V): 8.3" in out
    assert "Fault found: U1" in out


def test_session_replay_needs_scenario_name(capsys, tmp_path, board_dir):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"scenarios": [{"name": "U1 open", "readings": {}}]}))
    code, _, err = run(capsys, "session", "+5V", "dead", "--source", "barrel",
                       "--board", board_dir, "--replay", str(f))
    assert code == 1
    assert "choose a scenario" in err


def test_session_replay_missing_reading(capsys, tmp_path, board_dir):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"readings": {}}))
    code, _, err = run(capsys, "session", "+5V", "dead", "--source", "barrel",
                       "--board", board_dir, "--replay", str(f))
    assert code == 1
    assert "no reading for VCC" in err
