"""Measurement loop: pick one measurement at a time and narrow the suspects.

Assumes a single fault. Every step is chosen so that both possible results
remove at least one suspect, so no step is ever a dead end.

Dead rail
  measure a path net   good: power reaches it, so every part upstream is cleared
                       bad:  the fault is upstream of it, so everything else is cleared
  measure a control net (a switch's control input)
                       good: the part driving it is cleared
                       bad:  that driver is the fault, everything else is cleared
Shorted rail
  lift a suspect and remeasure the rail's resistance to ground
                       still shorted: that part is cleared
                       short gone:    that part is the fault
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .board import Board
from .diagnosis import Suspect, diagnose
from .graph import GraphError, natural_key

TOLERANCE = 0.10      # a voltage within 10% of expected is good...
FLOOR_VOLTS = 0.3     # ...or within 0.3 V, so readings near 0 V can still be good
SHORT_OHMS = 10.0     # below this, the rail still counts as shorted

_PATH_ROLES = ("regulator", "series", "switch")


class SessionError(GraphError):
    """A reading was invalid or the session is already finished."""


@dataclass(frozen=True)
class Step:
    kind: str                 # "measure" (volts) or "lift" (ohms after lifting a part)
    net: str                  # net to measure; for a lift, the shorted rail
    ref: str | None = None    # part to lift (lift steps only)
    expected: float | None = None

    @property
    def key(self) -> str:
        """Identifier used by scenario files: the net name, or lift:REF."""
        return f"lift:{self.ref}" if self.kind == "lift" else self.net

    @property
    def prompt(self) -> str:
        if self.kind == "lift":
            return (f"Lift {self.ref}, then measure resistance from {self.net} "
                    f"to ground (ohms)")
        return f"Measure the voltage on {self.net} (expected about {self.expected:g} V)"


@dataclass(frozen=True)
class Record:
    step: Step
    reading: float
    good: bool                # measure: reading as expected; lift: rail still shorted
    cleared: tuple[str, ...]  # suspects removed by this step


@dataclass
class Session:
    board: Board
    rail: str
    symptom: str
    source: str | None = None
    suspects: list[Suspect] = field(init=False)
    history: list[Record] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.suspects = diagnose(self.board, self.rail, self.symptom, self.source)
        self._expected = self.board.expected.get(self.source, {}) if self.source else {}

    # Public interface

    @property
    def done(self) -> bool:
        return self.next_step() is None

    @property
    def result(self) -> list[str]:
        """Remaining suspects. One means found; zero means outside what can be found."""
        return [s.ref for s in self.suspects]

    def next_step(self) -> Step | None:
        if len(self.suspects) <= 1:
            return None
        if self.symptom == "shorted":
            return Step("lift", self.rail, ref=self.suspects[0].ref)
        best = None
        for step, upstream in self._candidates():
            rest = len(self.suspects) - len(upstream)
            score = (min(len(upstream), rest), step.kind == "measure")
            if best is None or score > best[0]:
                best = (score, step)
        return best[1] if best else None

    def record(self, reading: float) -> Record:
        step = self.next_step()
        if step is None:
            raise SessionError("the session is finished; no measurement is pending")
        if isinstance(reading, bool) or not isinstance(reading, (int, float)):
            raise SessionError(f"reading must be a number, got {reading!r}")

        if step.kind == "lift":
            still_shorted = reading < SHORT_OHMS
            keep = (lambda s: s.ref != step.ref) if still_shorted else (lambda s: s.ref == step.ref)
            good = still_shorted
        else:
            good = abs(reading - step.expected) <= max(TOLERANCE * abs(step.expected), FLOOR_VOLTS)
            part = dict(self._candidates_by_key())[step.key]
            keep = (lambda s: s.ref not in part) if good else (lambda s: s.ref in part)

        cleared = tuple(s.ref for s in self.suspects if not keep(s))
        self.suspects = [s for s in self.suspects if keep(s)]
        rec = Record(step, float(reading), good, cleared)
        self.history.append(rec)
        return rec

    # Choosing measurements for a dead rail

    def _candidates(self) -> list[tuple[Step, set[str]]]:
        """Valid measurements, each with the suspects a good reading would clear."""
        measured = {r.step.net for r in self.history}
        out = []
        for key, part in self._candidates_by_key():
            if key in measured or key not in self._expected:
                continue
            if 0 < len(part) < len(self.suspects):  # both results remove someone
                out.append((Step("measure", key, expected=self._expected[key]), part))
        return out  # path nets first, then control nets; ties keep this order

    def _candidates_by_key(self) -> list[tuple[str, set[str]]]:
        """For every net touching the suspects: the suspects a good reading there clears."""
        path = [s for s in self.suspects if s.role in _PATH_ROLES]
        up_edges: dict[str, set[str]] = {}
        for s in path:
            up_edges.setdefault(s.nets[1], set()).add(s.nets[0])
        drivers: dict[str, set[str]] = {}        # control net -> parts driving it
        switch_of: dict[str, str] = {}           # control part -> switch it controls
        for s in self.suspects:
            if s.role == "control":
                drivers.setdefault(s.nets[0], set()).add(s.ref)
        for s in path:
            if s.role == "switch":
                for ref in drivers.get(s.nets[2], ()):
                    switch_of[ref] = s.ref

        out = []
        path_nets = {n for s in path for n in s.nets[:2]} - {self.rail}
        for net in sorted(path_nets, key=natural_key):
            upstream_nets = _closure(net, up_edges)
            upstream = {s.ref for s in path if s.nets[1] in upstream_nets}
            upstream |= {c for c, sw in switch_of.items() if sw in upstream}
            out.append((net, upstream))
        for net, refs in sorted(drivers.items(), key=lambda kv: natural_key(kv[0])):
            out.append((net, set(refs)))
        return out


def _closure(start: str, up_edges: dict[str, set[str]]) -> set[str]:
    seen, stack = {start}, [start]
    while stack:
        for up in up_edges.get(stack.pop(), ()):
            if up not in seen:
                seen.add(up)
                stack.append(up)
    return seen


def replay(session: Session, readings: dict[str, float]) -> Session:
    """Run a session to the end using readings keyed by Step.key (a scenario file)."""
    while (step := session.next_step()) is not None:
        if step.key not in readings:
            raise SessionError(f"scenario has no reading for {step.key}")
        session.record(readings[step.key])
    return session
