"""Export the Uno R3 schematic to boards/uno_r3/netlist.xml using KiCad's CLI.

Run from the repo root:  python scripts/export_netlist.py
Commit the resulting netlist.xml so tests run without KiCad installed.

kicad-cli is looked up in this order: the KICAD_CLI environment variable,
PATH, then the default Windows install folders (C:\\Program Files\\KiCad\\<ver>\\bin).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

BOARD = Path("boards/uno_r3")
SCHEMATIC = BOARD / "Arduino UNO.kicad_sch"
OUTPUT = BOARD / "netlist.xml"


def find_kicad_cli() -> str | None:
    env = os.environ.get("KICAD_CLI")
    if env:
        return env if Path(env).is_file() else None
    on_path = shutil.which("kicad-cli")
    if on_path:
        return on_path
    for base in (os.environ.get("ProgramFiles"), r"C:\Program Files"):
        if not base:
            continue
        candidates = sorted(Path(base, "KiCad").glob("*/bin/kicad-cli.exe"))
        if candidates:
            return str(candidates[-1])  # highest version folder
    return None


def main() -> int:
    cli = find_kicad_cli()
    if cli is None:
        print("error: kicad-cli not found. Set KICAD_CLI to its full path "
              "(KiCad 8+), or add KiCad's bin folder to PATH.", file=sys.stderr)
        return 1
    if not SCHEMATIC.is_file():
        print(f"error: schematic not found: {SCHEMATIC} "
              "(run this from the repo root)", file=sys.stderr)
        return 1
    print(f"using {cli}")
    result = subprocess.run(
        [cli, "sch", "export", "netlist", "--format", "kicadxml",
         "--output", str(OUTPUT.resolve()), str(SCHEMATIC.resolve())],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        print(f"error: kicad-cli exited with {result.returncode}", file=sys.stderr)
        return result.returncode
    if not OUTPUT.is_file():
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)
        print(f"error: kicad-cli reported success but {OUTPUT} was not created",
              file=sys.stderr)
        return 1
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
