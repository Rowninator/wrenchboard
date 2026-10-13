"""Optional AI layer on top of the rule based engine.

The AI never decides which parts are suspects. It does two narrow jobs:

  interpret  turn a free text description into the engine's terms by filling
             in a form whose choices are lists built from the board, so it
             cannot name a rail or source that isn't there
  explain    rephrase the engine's results; any part reference in its text
             that isn't one of the suspects causes the text to be rejected

Requires the anthropic package and the ANTHROPIC_API_KEY environment variable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .board import Board
from .diagnosis import SYMPTOMS, Suspect
from .graph import GraphError, natural_key

MODEL = "claude-haiku-4-5-20251001"
_REF_LIKE = re.compile(r"\b[A-Z]{1,3}\d+[A-Z]?\b")
_TOOL = "set_diagnosis"


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


def make_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AIError("ANTHROPIC_API_KEY is not set; see the README for setup")
    try:
        import anthropic
    except ImportError:
        raise AIError("the anthropic package is not installed: pip install anthropic") from None
    return anthropic.Anthropic()


def rail_choices(board: Board) -> list[str]:
    """Nets the user can name as a faulty rail: those with an expected voltage."""
    nets = {net for readings in board.expected.values() for net in readings}
    return sorted(nets, key=natural_key)


def _call(client, **kwargs):
    try:
        return client.messages.create(model=MODEL, **kwargs)
    except Exception as e:  # network, auth, rate limit: report, never crash
        raise AIError(f"AI request failed: {e}") from None


def interpret(board: Board, text: str, client) -> Interpretation:
    rails = rail_choices(board)
    sources = sorted(board.sources)
    if not rails:
        raise AIError("board.json has no expected voltages, so there are no rails to choose from")
    source_lines = "\n".join(
        f"- {name}: power comes in through connector {s.connector}"
        for name, s in sorted(board.sources.items()))
    system = (
        "You map a technician's description of a broken circuit board onto a fixed form. "
        "Fill in the form with the set_diagnosis tool.\n"
        f"Power sources on this board:\n{source_lines}\n"
        "Symptoms: 'dead' means a rail has no voltage or the board won't power on; "
        "'shorted' means a rail measures near zero ohms to ground.\n"
        "If the board won't power on at all, the main logic rail is the usual choice. "
        "Use null for anything the description does not support, and say what is "
        "missing in 'missing'. Never guess a power source that wasn't mentioned."
    )
    tool = {
        "name": _TOOL,
        "description": "Record the faulty rail, the symptom, and the active power source.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rail": {"enum": rails + [None], "description": "The faulty rail."},
                "symptom": {"enum": list(SYMPTOMS) + [None]},
                "source": {"enum": sources + [None],
                           "description": "Which power source is connected."},
                "missing": {"type": ["string", "null"],
                            "description": "What the description leaves unclear, or null."},
            },
            "required": ["rail", "symptom", "source", "missing"],
        },
    }
    response = _call(
        client, max_tokens=300, system=system, tools=[tool],
        tool_choice={"type": "tool", "name": _TOOL},
        messages=[{"role": "user", "content": text}],
    )
    block = next((b for b in response.content
                  if getattr(b, "type", None) == "tool_use" and b.name == _TOOL), None)
    if block is None or not isinstance(block.input, dict):
        raise AIError("the AI did not fill in the form")
    data = block.input

    rail, symptom, source = data.get("rail"), data.get("symptom"), data.get("source")
    missing = data.get("missing") or None
    if rail is not None and rail not in rails:
        raise AIError(f"the AI chose a rail that isn't on the board: {rail!r}")
    if symptom is not None and symptom not in SYMPTOMS:
        raise AIError(f"the AI chose an unknown symptom: {symptom!r}")
    if source is not None and source not in board.sources:
        raise AIError(f"the AI chose a source that isn't on the board: {source!r}")
    if symptom == "shorted":
        source = None  # the short rule doesn't use the source
    if symptom == "dead" and source is None and not missing:
        missing = f"which power source is connected ({', '.join(sources)})"
    return Interpretation(rail, symptom, source, missing)


def explain(rail: str, symptom: str, source: str | None,
            suspects: list[Suspect], client) -> str | None:
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
    response = _call(
        client, max_tokens=300, system=system,
        messages=[{"role": "user", "content":
                   f"Problem: {rail} is {symptom}{where}.\nRanked suspects:\n{reasons}"}],
    )
    text = "".join(getattr(b, "text", "") for b in response.content
                   if getattr(b, "type", None) == "text").strip()
    if not text:
        return None
    if any(ref not in allowed for ref in _REF_LIKE.findall(text)):
        return None
    return text
