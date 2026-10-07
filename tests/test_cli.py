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
