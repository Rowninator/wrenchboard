import json
from pathlib import Path

import pytest

from wrenchboard.board import BoardConfigError, load_board
from wrenchboard.graph import load_netlist

FIXTURES = Path(__file__).parent / "fixtures"
UNO_DIR = Path(__file__).parent.parent / "boards" / "uno_r3"


@pytest.fixture
def mini():
    return load_netlist(FIXTURES / "mini_netlist.xml")


def write(tmp_path, data):
    p = tmp_path / "board.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    return p


def test_loads_valid_config(tmp_path, mini):
    board = load_board(write(tmp_path, {"ground": "GND", "sources": {"barrel": "J1"}}), mini)
    assert board.ground == "GND"
    assert board.sources == {"barrel": "J1"}


@pytest.mark.parametrize("data, message", [
    ({"ground": "GND0", "sources": {"barrel": "J1"}}, "ground net"),
    ({"ground": "GND", "sources": {"barrel": "J7"}}, "connector"),
    ({"ground": "GND", "sources": {}}, "sources"),
    ({"sources": {"barrel": "J1"}}, "ground"),
])
def test_rejects_bad_config(tmp_path, mini, data, message):
    with pytest.raises(BoardConfigError, match=message):
        load_board(write(tmp_path, data), mini)


def test_missing_file(tmp_path, mini):
    with pytest.raises(BoardConfigError, match="not found"):
        load_board(tmp_path / "nope.json", mini)


def test_bad_json(tmp_path, mini):
    with pytest.raises(BoardConfigError, match="not valid JSON"):
        load_board(write(tmp_path, "{not json"), mini)


@pytest.mark.skipif(not (UNO_DIR / "netlist.xml").is_file(), reason="run scripts/export_netlist.py first")
def test_uno_board_config():
    graph = load_netlist(UNO_DIR / "netlist.xml")
    board = load_board(UNO_DIR / "board.json", graph)
    assert board.ground == "GND"
    assert board.sources == {"usb": "J8", "barrel": "J9"}