"""Print every pin on the given nets, with part value, pin name, and pin type.

Run from the repo root:  python scripts\show_nets.py NET [NET ...]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrenchboard.graph import GraphError, load_netlist  # noqa: E402


def main() -> int:
    graph = load_netlist("boards/uno_r3/netlist.xml")
    for name in sys.argv[1:]:
        try:
            net = graph.net(name)
        except GraphError as e:
            print(f"error: {e}")
            continue
        print(name)
        for pin in net.pins:
            part = graph.part(pin.ref)
            print(f"  {str(pin):8} {part.value:24} name={pin.name}  type={pin.type}"
                  f"{'  DNP' if part.dnp else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())