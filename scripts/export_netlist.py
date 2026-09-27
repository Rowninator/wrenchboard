"""Export the Uno R3 schematic to boards/uno_r3/netlist.xml using KiCad's CLI.

Run from the repo root:  python scripts/export_netlist.py
Commit the resulting netlist.xml so tests run without KiCad installed.
"""

import shutil
import subprocess
import sys
from pathlib import Path

BOARD = Path("boards/uno_r3")
SCHEMATIC = BOARD / "Arduino UNO.kicad_sch"
OUTPUT = BOARD / "netlist.xml"


def main() -> int:
    cli = shutil.which("kicad-cli")
    if cli is None:
        print("error: kicad-cli not found on PATH (installed with KiCad 8+)", file=sys.stderr)
        return 1
    if not SCHEMATIC.is_file():
        print(f"error: schematic not found: {SCHEMATIC}", file=sys.stderr)
        return 1
    result = subprocess.run(
        [cli, "sch", "export", "netlist", "--format", "kicadxml",
         "--output", str(OUTPUT), str(SCHEMATIC)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        print(f"error: kicad-cli exited with {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
