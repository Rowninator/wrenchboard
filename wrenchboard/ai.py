"""Optional AI layer on top of the rule based engine.

The AI never decides which parts are suspects. It does two narrow jobs:

  interpret  turn a free text description into the engine's terms by filling
             in a form whose choices are lists built from the board, so it
             cannot name a rail or source that isn't there
  explain    rephrase the engine's results; any part reference in its text
             that isn't one of the suspects causes the text to be rejected

Two providers are supported, chosen by which key is set:
  GEMINI_API_KEY     Google Gemini (free tier), package google-genai
  ANTHROPIC_API_KEY  Anthropic Claude, package anthropic
If both are set, Gemini is used. GEMINI_MODEL overrides the Gemini model name.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .board import Board
from .diagnosis import SYMPTOMS, Suspect
from .graph import GraphError, natural_key

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
GEMINI_MODEL = "gemini-3.5-flash-lite"
UNKNOWN = "unknown"
_FORM = "set_diagnosis"
_REF_LIKE = re.compile(r"\b[A-Z]{1,3}\d+[A-Z]?\b")


class AIError(GraphError):
    """The AI layer is unavailable, failed, or returned something invalid."""


@dataclass(frozen=True)
class Interpretation:
    rail: str | None
    symptom: str | None
    source: str | None
    missing: str | None  # what the description didn't make clear, if anything

    @property
    def complete(self) -> bool:
        if self.missing or not self.rail or not self.symptom:
            return False
        return self.symptom != "dead" or self.source is not None


# Backends: each has fill_form(system, text, schema) -> dict and say(system, text) -> str

class AnthropicBackend:
    name = "Claude"

    def __init__(self, client, model: str = ANTHROPIC_MODEL):
        self.client, self.model = client, model

    def fill_form(self, system: str, text: str, schema: dict) -> dict:
        response = self.client.messages.create(
            model=self.model, max_tokens=300, system=system,
            tools=[{"name": _FORM, "description": "Record the diagnosis request.",
                    "input_schema": schema}],
            tool_choice={"type": "tool", "name": _FORM},
            messages=[{"role": "user", "content": text}],
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _FORM:
                return block.input
        raise AIError("the AI did not fill in the form")

    def say(self, system: str, text: str) -> str:
        response = self.client.messages.create(
            model=self.model, max_tokens=300, system=system,
            messages=[{"role": "user", "content": text}],
        )
        return "".join(getattr(b, "text", "") for b in response.content
                       if getattr(b, "type", None) == "text")


class GeminiBackend:
    name = "Gemini"

    def __init__(self, client, model: str = GEMINI_MODEL):
        self.client, self.model = client, model

    def fill_form(self, system: str, text: str, schema: dict) -> dict:
        from google.genai import types
        config = types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=1024,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(
                name=_FORM, description="Record the diagnosis request.",
                parameters_json_schema=schema)])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY", allowed_function_names=[_FORM])),
        )
        response = self.client.models.generate_content(
            model=self.model, contents=text, config=config)
        for call in response.function_calls or []:
            if call.name == _FORM:
                return dict(call.args or {})
        raise AIError("the AI did not fill in the form")

    def say(self, system: str, text: str) -> str:
        from google.genai import types
        config = types.GenerateContentConfig(system_instruction=system, max_output_tokens=1024)
        response = self.client.models.generate_content(
            model=self.model, contents=text, config=config)
        return response.text or ""


def make_client():
    """Pick a backend from whichever API key is set."""
    if os.environ.get("GEMINI_API_KEY"):
        try:
            from google import genai
        except ImportError:
            raise AIError("the google-genai package is not installed: "
                          "python -m pip install google-genai") from None
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        return GeminiBackend(client, os.environ.get("GEMINI_MODEL") or GEMINI_MODEL)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
        except ImportError:
            raise AIError("the anthropic package is not installed: "
                          "python -m pip install anthropic") from None
        return AnthropicBackend(anthropic.Anthropic())
    raise AIError("no API key set: set GEMINI_API_KEY or ANTHROPIC_API_KEY (see the README)")


def _ask(fn, *args):
    try:
        return fn(*args)
    except AIError:
        raise
    except Exception as e:  # network, auth, quota: report, never crash
        raise AIError(f"AI request failed: {e}") from None


# The two jobs

def rail_choices(board: Board) -> list[str]:
    """Nets the user can name as a faulty rail: those with an expected voltage."""
    nets = {net for readings in board.expected.values() for net in readings}
    return sorted(nets, key=natural_key)


def form_schema(board: Board) -> dict:
    """The form the AI fills in. Every choice comes from the board."""
    return {
        "type": "object",
        "properties": {
            "rail": {"type": "string", "enum": rail_choices(board) + [UNKNOWN],
                     "description": "The faulty rail."},
            "symptom": {"type": "string", "enum": list(SYMPTOMS) + [UNKNOWN]},
            "source": {"type": "string", "enum": sorted(board.sources) + [UNKNOWN],
                       "description": "Which power source is connected."},
            "missing": {"type": "string",
                        "description": "What the description leaves unclear; empty if nothing."},
        },
        "required": ["rail", "symptom", "source", "missing"],
    }


def interpret(board: Board, text: str, backend) -> Interpretation:
    rails = rail_choices(board)
    if not rails:
        raise AIError("board.json has no expected voltages, so there are no rails to choose from")
    source_lines = "\n".join(
        f"- {name}: power comes in through connector {s.connector}"
        for name, s in sorted(board.sources.items()))
    system = (
        "You map a technician's description of a broken circuit board onto a fixed form. "
        f"Fill in the form with the {_FORM} function.\n"
        f"Power sources on this board:\n{source_lines}\n"
        "Symptoms: 'dead' means a rail has no voltage or the board won't power on; "
        "'shorted' means a rail measures near zero ohms to ground.\n"
        "If the board won't power on at all, the main logic rail is the usual choice. "
        f"Use '{UNKNOWN}' for anything the description does not support, and say what is "
        "missing in 'missing'. Never guess a power source that wasn't mentioned."
    )
    data = _ask(backend.fill_form, system, text, form_schema(board))
    if not isinstance(data, dict):
        raise AIError("the AI did not fill in the form")

    def pick(field):
        value = data.get(field)
        return None if value in (None, "", UNKNOWN) else value

    rail, symptom, source = pick("rail"), pick("symptom"), pick("source")
    missing = (data.get("missing") or "").strip() or None
    if rail is not None and rail not in rails:
        raise AIError(f"the AI chose a rail that isn't on the board: {rail!r}")
    if symptom is not None and symptom not in SYMPTOMS:
        raise AIError(f"the AI chose an unknown symptom: {symptom!r}")
    if source is not None and source not in board.sources:
        raise AIError(f"the AI chose a source that isn't on the board: {source!r}")
    if symptom == "shorted":
        source = None  # the short rule doesn't use the source
    if symptom == "dead" and source is None and not missing:
        missing = f"which power source is connected ({', '.join(sorted(board.sources))})"
    return Interpretation(rail, symptom, source, missing)


def explain(rail: str, symptom: str, source: str | None,
            suspects: list[Suspect], backend) -> str | None:
    """A short plain language explanation, or None if it named a part it shouldn't."""
    if not suspects:
        return None
    allowed = {s.ref for s in suspects}
    reasons = "\n".join(f"{i}. {s.reason}" for i, s in enumerate(suspects, 1))
    where = f" on {source} power" if source else ""
    system = (
        "You explain a circuit board diagnosis to a student technician in 2 to 4 short "
        "sentences of plain text. Explain why the first suspects are the most likely "
        "and what each does in the circuit. Only mention these part references: "
        f"{', '.join(sorted(allowed, key=natural_key))}. Do not mention any other parts."
    )
    text = _ask(backend.say, system,
                f"Problem: {rail} is {symptom}{where}.\nRanked suspects:\n{reasons}").strip()
    if not text or any(ref not in allowed for ref in _REF_LIKE.findall(text)):
        return None
    return text
