"""Command line interface.

    python -m wrenchboard diagnose RAIL SYMPTOM [--source NAME] [--board DIR]
    python -m wrenchboard session  RAIL SYMPTOM [--source NAME] [--board DIR]
                                   [--replay FILE [--scenario NAME]]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .board import load_board
from .diagnosis import SYMPTOMS, diagnose
from .graph import GraphError, load_netlist
from .session import Session, SessionError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wrenchboard")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("diagnose", "rank suspect parts for a faulty rail"),
                       ("session", "find the fault one measurement at a time")):
        p = sub.add_parser(name, help=text)
        p.add_argument("rail", help="net name of the faulty rail, e.g. +5V")
        p.add_argument("symptom", choices=SYMPTOMS)
        p.add_argument("--source", help="active power source (required for a dead rail)")
        p.add_argument("--board", default="boards/uno_r3", help="board folder")
        if name == "session":
            p.add_argument("--replay", help="JSON file of readings instead of typing them")
            p.add_argument("--scenario", help="scenario name, if the file holds a list")
    args = parser.parse_args(argv)

    try:
        board_dir = Path(args.board)
        board = load_board(board_dir / "board.json", load_netlist(board_dir / "netlist.xml"))
        if args.command == "diagnose":
            return _diagnose(board, args)
        readings = _load_readings(args.replay, args.scenario) if args.replay else None
        return _session(board, args, readings)
    except (GraphError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _diagnose(board, args) -> int:
    suspects = diagnose(board, args.rail, args.symptom, args.source)
    where = f" on {args.source} power" if args.source else ""
    if not suspects:
        print(f"No suspects for {args.rail} {args.symptom}{where}.")
        return 0
    print(f"Suspects for {args.rail} {args.symptom}{where}:")
    for i, s in enumerate(suspects, 1):
        print(f"  {i}. {s.reason}")
    return 0


def _load_readings(path: str, scenario: str | None) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "scenarios" in data:
        matches = [s for s in data["scenarios"] if s.get("name") == scenario]
        if not matches:
            names = ", ".join(s.get("name", "?") for s in data["scenarios"])
            raise SessionError(f"choose a scenario with --scenario: {names}")
        data = matches[0]
    if not isinstance(data.get("readings"), dict):
        raise SessionError(f"{path} has no 'readings' object")
    return data["readings"]


def _session(board, args, readings: dict | None) -> int:
    session = Session(board, args.rail, args.symptom, args.source)
    print("Suspects: " + ", ".join(session.result))
    while (step := session.next_step()) is not None:
        if readings is not None:
            if step.key not in readings:
                raise SessionError(f"replay file has no reading for {step.key}")
            value = float(readings[step.key])
            print(f"{step.prompt}: {value:g}")
        else:
            raw = input(f"{step.prompt}: ").strip()
            if raw.lower() in ("q", "quit"):
                print("Stopped.")
                return 1
            try:
                value = float(raw)
            except ValueError:
                print("  Please enter a number, or q to quit.")
                continue
        rec = session.record(value)
        if step.kind == "lift":
            verdict = "still shorted" if rec.good else "short is gone"
        else:
            verdict = "as expected" if rec.good else "not as expected"
        print(f"  {verdict}. Cleared: {', '.join(rec.cleared)}. "
              f"Remaining: {', '.join(session.result)}")

    result = session.result
    if len(result) == 1:
        print(f"Fault found: {result[0]}")
    elif not result:
        print("Every suspect is cleared; the fault is outside what this tool can find.")
    else:
        print("Narrowed to " + ", ".join(result) + "; no measurement can separate these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
