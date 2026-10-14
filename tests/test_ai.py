import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from wrenchboard import ai
from wrenchboard.ai import (AIError, AnthropicBackend, explain, form_schema, interpret,
                            make_client, rail_choices)
from wrenchboard.board import Board, Source
from wrenchboard.diagnosis import diagnose
from wrenchboard.graph import load_netlist

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = {
    "usb": {"XUSB": 5.0, "USBVCC": 5.0, "GATE": 0.0, "+5V": 5.0, "+3V3": 3.3},
    "barrel": {"JIN": 9.0, "VCC": 8.3, "GATE": 5.0, "+5V": 5.0, "+3V3": 3.3},
}


class FakeBackend:
    """Stands in for a provider: returns canned answers and records what was asked."""
    name = "Fake"

    def __init__(self, form=None, text="", error=None):
        self.form, self.text, self.error = form, text, error
        self.calls = []

    def fill_form(self, system, text, schema):
        self.calls.append(("form", system, text, schema))
        if self.error:
            raise self.error
        return self.form

    def say(self, system, text):
        self.calls.append(("say", system, text))
        if self.error:
            raise self.error
        return self.text


def form(**fields):
    return {"rail": "unknown", "symptom": "unknown", "source": "unknown", "missing": "", **fields}


@pytest.fixture
def board():
    return Board(
        graph=load_netlist(FIXTURES / "power_netlist.xml"),
        ground="GND",
        sources={"usb": Source("J1", "XUSB"), "barrel": Source("J2", "JIN")},
        expected=EXPECTED,
    )


# interpret

def test_form_choices_come_from_the_board(board):
    props = form_schema(board)["properties"]
    assert props["rail"]["enum"] == rail_choices(board) + ["unknown"]
    assert props["source"]["enum"] == ["barrel", "usb", "unknown"]
    assert props["symptom"]["enum"] == ["dead", "shorted", "unknown"]


def test_complete_interpretation(board):
    meaning = interpret(board, "x", FakeBackend(form(rail="+5V", symptom="dead", source="usb")))
    assert (meaning.rail, meaning.symptom, meaning.source) == ("+5V", "dead", "usb")
    assert meaning.complete


def test_unknown_becomes_none(board):
    meaning = interpret(board, "x", FakeBackend(form(missing="which rail")))
    assert (meaning.rail, meaning.symptom, meaning.source) == (None, None, None)
    assert meaning.missing == "which rail" and not meaning.complete


def test_dead_without_source_asks_for_it(board):
    meaning = interpret(board, "x", FakeBackend(form(rail="+5V", symptom="dead")))
    assert not meaning.complete
    assert "power source" in meaning.missing


def test_short_ignores_source(board):
    meaning = interpret(board, "x", FakeBackend(form(rail="+3V3", symptom="shorted", source="usb")))
    assert meaning.source is None and meaning.complete


@pytest.mark.parametrize("fields, message", [
    ({"rail": "+12V", "symptom": "dead"}, "rail that isn't on the board"),
    ({"rail": "+5V", "symptom": "smoking"}, "unknown symptom"),
    ({"rail": "+5V", "symptom": "dead", "source": "solar"}, "source that isn't on the board"),
])
def test_invalid_choices_rejected(board, fields, message):
    with pytest.raises(AIError, match=message):
        interpret(board, "x", FakeBackend(form(**fields)))


def test_api_failure_reported(board):
    with pytest.raises(AIError, match="AI request failed"):
        interpret(board, "x", FakeBackend(error=RuntimeError("quota exceeded")))


# explain

def test_explanation_with_suspect_names_kept(board):
    suspects = diagnose(board, "+5V", "dead", "usb")
    text = "Q1 switches USB power onto +5V and U3 controls it, so check those first."
    assert explain("+5V", "dead", "usb", suspects, FakeBackend(text=text)) == text


@pytest.mark.parametrize("text", [
    "U9 is probably damaged.",      # not on the board at all
    "Check C1 first, then Q1.",     # real part, but not a suspect
    "",                             # nothing said
])
def test_explanation_rejected(board, text):
    suspects = diagnose(board, "+5V", "dead", "usb")
    assert explain("+5V", "dead", "usb", suspects, FakeBackend(text=text)) is None


def test_explanation_prompt_lists_only_suspects(board):
    backend = FakeBackend(text="U1 is first.")
    explain("+5V", "dead", "barrel", diagnose(board, "+5V", "dead", "barrel"), backend)
    assert "references: D1, U1." in backend.calls[0][1]


# Anthropic backend builds its request correctly

def test_anthropic_backend(board):
    calls = []
    reply = SimpleNamespace(content=[SimpleNamespace(
        type="tool_use", name="set_diagnosis", input=form(rail="+5V"))])
    client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or reply))
    data = AnthropicBackend(client).fill_form("sys", "text", form_schema(board))
    assert data["rail"] == "+5V"
    assert calls[0]["model"] == ai.ANTHROPIC_MODEL
    assert calls[0]["tool_choice"] == {"type": "tool", "name": "set_diagnosis"}


def test_anthropic_backend_without_form():
    reply = SimpleNamespace(content=[SimpleNamespace(type="text", text="hmm")])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply))
    with pytest.raises(AIError, match="did not fill in the form"):
        AnthropicBackend(client).fill_form("sys", "text", {})


# Gemini backend builds its request correctly (needs google-genai installed)

def test_gemini_backend(board):
    pytest.importorskip("google.genai")
    calls = []
    reply = SimpleNamespace(function_calls=[SimpleNamespace(name="set_diagnosis",
                                                            args=form(rail="+5V"))], text=None)
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kw: calls.append(kw) or reply))
    data = ai.GeminiBackend(client).fill_form("sys", "text", form_schema(board))
    assert data["rail"] == "+5V"
    assert calls[0]["model"] == ai.GEMINI_MODEL
    fc = calls[0]["config"].tool_config.function_calling_config
    assert str(fc.mode).endswith("ANY") and fc.allowed_function_names == ["set_diagnosis"]


def test_gemini_backend_without_form():
    pytest.importorskip("google.genai")
    reply = SimpleNamespace(function_calls=None, text="hmm")
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: reply))
    with pytest.raises(AIError, match="did not fill in the form"):
        ai.GeminiBackend(client).fill_form("sys", "text", {})


# Choosing a provider

def test_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AIError, match="no API key"):
        make_client()


def test_gemini_preferred_and_model_override(monkeypatch):
    pytest.importorskip("google.genai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "some-other-model")
    backend = make_client()
    assert backend.name == "Gemini" and backend.model == "some-other-model"


# Live test: runs only with WRENCHBOARD_LIVE=1 and a key set.

@pytest.mark.skipif(not (os.environ.get("WRENCHBOARD_LIVE") == "1" and
                         (os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))),
                    reason="set WRENCHBOARD_LIVE=1 to call the real API")
def test_live_interpret(board):
    meaning = interpret(board, "Nothing turns on when I plug it into USB.", make_client())
    assert meaning.rail in rail_choices(board)
    assert meaning.source == "usb"
