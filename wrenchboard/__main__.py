"""Command line interface.

    python -m wrenchboard diagnose RAIL SYMPTOM [--source NAME] [--board DIR]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .board import load_board
from .diagnosis import SYMPTOMS, diagnose
from .graph import GraphError, load_netlist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wrenchboard")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diagnose", help="rank suspect parts for a faulty rail")
    d.add_argument("rail", help="net name of the faulty rail, e.g. +5V")
    d.add_argument("symptom", choices=SYMPTOMS)
    d.add_argument("--source", help="active power source (required for a dead rail)")
    d.add_argument("--board", default="boards/uno_r3", help="board folder")
    args = parser.parse_args(argv)

    try:
        board_dir = Path(args.board)
        graph = load_netlist(board_dir / "netlist.xml")
        board = load_board(board_dir / "board.json", graph)
        suspects = diagnose(board, args.rail, args.symptom, args.source)
    except GraphError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    where = f" on {args.source} power" if args.source else ""
    if not suspects:
        print(f"No suspects for {args.rail} {args.symptom}{where}.")
        return 0
    print(f"Suspects for {args.rail} {args.symptom}{where}:")
    for i, s in enumerate(suspects, 1):
        print(f"  {i}. {s.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
