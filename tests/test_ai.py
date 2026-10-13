import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from wrenchboard import ai
from wrenchboard.ai import AIError, explain, interpret, make_client, rail_choices
from wrenchboard.board import Board, Source
from wrenchboard.diagnosis import diagnose
from wrenchboard.graph import load_netlist

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = {
    "usb": {"XUSB": 5.0, "USBVCC": 5.0, "GATE": 0.0, "+5V": 5.0, "+3V3": 3.3},
    "barrel": {"JIN": 9.0, "VCC": 8.3, "GATE": 5.0, "+5V": 5.0, "+3V3": 3.3},
}


class FakeClient:
    """Stands in for anthropic.Anthropic: returns canned responses, records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def form(**fields):
    data = {"rail": None, "symptom": None, "source": None, "missing": None, **fields}
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="set_diagnosis", input=data)])


def said(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


@pytest.fixture
def board():
    return Board(
        graph=load_netlist(FIXTURES / "power_netlist.xml"),
        ground="GND",
        sources={"usb": Source("J1", "XUSB"), "barrel": Source("J2", "JIN")},
        expected=EXPECTED,
    )


# interpret

def test_choices_come_from_the_board(board):
    client = FakeClient(form(rail="+5V", symptom="dead", source="usb"))
    interpret(board, "dead on usb", client)
    call = client.calls[0]
    schema = call["tools"][0]["input_schema"]["properties"]
    assert schema["rail"]["enum"] == rail_choices(board) + [None]
    assert schema["source"]["enum"] == ["barrel", "usb", None]
    assert call["tool_choice"] == {"type": "tool", "name": "set_diagnosis"}
    assert call["model"] == ai.MODEL


def test_complete_interpretation(board):
    meaning = interpret(board, "x", FakeClient(form(rail="+5V", symptom="dead", source="usb")))
    assert (meaning.rail, meaning.symptom, meaning.source) == ("+5V", "dead", "usb")
    assert meaning.complete


def test_dead_without_source_asks_for_it(board):
    meaning = interpret(board, "x", FakeClient(form(rail="+5V", symptom="dead")))
    assert not meaning.complete
    assert "power source" in meaning.missing


def test_short_ignores_source(board):
    meaning = interpret(board, "x", FakeClient(form(rail="+3V3", symptom="shorted", source="usb")))
    assert meaning.source is None and meaning.complete


@pytest.mark.parametrize("fields, message", [
    ({"rail": "+12V", "symptom": "dead"}, "rail that isn't on the board"),
    ({"rail": "+5V", "symptom": "smoking"}, "unknown symptom"),
    ({"rail": "+5V", "symptom": "dead", "source": "solar"}, "source that isn't on the board"),
])
def test_invalid_choices_rejected(board, fields, message):
    with pytest.raises(AIError, match=message):
        interpret(board, "x", FakeClient(form(**fields)))


def test_no_form_rejected(board):
    with pytest.raises(AIError, match="did not fill in the form"):
        interpret(board, "x", FakeClient(said("I think it's the regulator")))


def test_api_failure_reported(board):
    with pytest.raises(AIError, match="AI request failed"):
        interpret(board, "x", FakeClient(RuntimeError("connection reset")))


# explain

def test_explanation_with_suspect_names_kept(board):
    suspects = diagnose(board, "+5V", "dead", "usb")
    text = "Q1 switches USB power onto +5V and U3 controls it, so check those first."
    assert explain("+5V", "dead", "usb", suspects, FakeClient(said(text))) == text


@pytest.mark.parametrize("text", [
    "U9 is probably damaged.",           # not on the board at all
    "Check C1 first, then Q1.",          # real part, but not a suspect
])
def test_explanation_naming_other_parts_rejected(board, text):
    suspects = diagnose(board, "+5V", "dead", "usb")
    assert explain("+5V", "dead", "usb", suspects, FakeClient(said(text))) is None


def test_explanation_prompt_lists_only_suspects(board):
    suspects = diagnose(board, "+5V", "dead", "barrel")
    client = FakeClient(said("U1 is first."))
    explain("+5V", "dead", "barrel", suspects, client)
    assert "references: D1, U1." in client.calls[0]["system"]


# setup

def test_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AIError, match="ANTHROPIC_API_KEY"):
        make_client()


# Live test: costs a fraction of a cent. Runs only with WRENCHBOARD_LIVE=1 and a key.

@pytest.mark.skipif(not (os.environ.get("WRENCHBOARD_LIVE") == "1"
                         and os.environ.get("ANTHROPIC_API_KEY")),
                    reason="set WRENCHBOARD_LIVE=1 to call the real API")
def test_live_interpret(board):
    meaning = interpret(board, "Nothing turns on when I plug it into USB.", make_client())
    assert meaning.rail in rail_choices(board)
    assert meaning.source == "usb"
