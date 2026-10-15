# Wrench Board Lite

A Python tool that reads a KiCad schematic, takes a symptom like "the 5 V rail is dead," and guides a step by step diagnosis: which parts to suspect, what to measure next, and where to put the probe on the board. Every part and net it names comes from the schematic itself.

![Fault found on the Uno R3](docs/fault_found.png)

## What it does

```text
> python -m wrenchboard session +5V dead --source usb --svg board.svg
Suspects: Q1, U5, F2
Measure the voltage on USBVCC (expected about 5 V): 5
  as expected. Cleared: F2. Remaining: Q1, U5
Measure the voltage on GATE_CMD (expected about 0 V): 0
  as expected. Cleared: U5. Remaining: Q1
Fault found: Q1
```

With `--svg`, it also draws the board and updates the picture after every reading: suspects in red, cleared parts in gray, the fault in green, and blue dots on the exact pads to measure.

## Setup (Windows)

Requires Python 3.10 or newer and KiCad 8 or newer (KiCad is only needed to export the netlist).

```bat
git clone https://github.com/Rowninator/wrenchboard.git
cd wrenchboard
py -m venv .venv
.venv\Scripts\activate
python -m pip install pytest anthropic
python -m pytest -q
```

On Linux or macOS, use `python3 -m venv .venv` and `source .venv/bin/activate`.

The exported netlist is committed, so the tests run without KiCad. To regenerate it after changing the schematic, run `python scripts\export_netlist.py`.

## Commands

| Command | What it does |
| --- | --- |
| `python -m wrenchboard diagnose +5V dead --source barrel` | Ranks suspect parts, with a reason for each |
| `python -m wrenchboard session +5V dead --source usb` | Asks for one reading at a time until one part remains |
| `python -m wrenchboard session +3.3V shorted` | Asks you to lift one suspect at a time and remeasure |
| `... --svg board.svg` | Writes a picture of the board with highlights |
| `... --replay tests\fixtures\uno_scenarios.json --scenario "Q1 stuck open"` | Runs a session from recorded readings |
| `python -m wrenchboard ask "nothing turns on when I plug in USB"` | Optional AI layer: describe the problem in your own words |

Symptoms are `dead` (no voltage) and `shorted` (near zero ohms to ground). A dead rail needs the active power source: `usb` or `barrel` on the Uno.

## How it works
 
1. **Graph:** parses KiCad's exported netlist into parts, pins, and nets.
2. **Diagnosis:** traces the power path back from the faulty rail to the active power source and ranks the parts along it. For a short, every part between the rail and ground is a suspect.
3. **Session:** picks the measurement that best splits the suspects, so every step rules at least one out.
4. **Board picture:** reads part positions from the PCB file, checks every pad against the schematic, and draws the highlights from that same data.
5. **AI layer (optional):** Gemini (`gemini-3.5-flash-lite`, free tier) turns a description into the engine's terms and explains the results. It never picks suspects. Set `GEMINI_API_KEY` to use it; Claude works too with `ANTHROPIC_API_KEY`.

## Accuracy

The engine cannot invent a part or net. Names come only from the graph, and a final check rejects any output naming something that isn't in it. The tests confirm this on every rail, symptom, and source combination of the real board.


## Limits
 
One fault at a time. Expected voltages are worked out by hand (assuming a 9 V adapter). Faults in the tests are simulated, since there is no physical board. Only the Uno R3 is configured.

## Tests

```bat
python -m pytest -q
```

180 tests cover the parser, config validation, diagnosis rules, the measurement loop, PCB reading, drawing, the command line, and the AI layer (with a fake client, so no API calls). The Uno tests include 10 nets checked by hand against the schematic, three faults diagnosed and found with no dead end steps, and highlights landing on the right parts. One live API test runs only with `WRENCHBOARD_LIVE=1` set.

## Project layout

```text
wrenchboard/        graph, board, diagnosis, session, pcb, render, ai, command line
tests/              tests and fixtures (hand made netlists, PCB, scenarios)
boards/uno_r3/      KiCad files, exported netlist, board.json
scripts/            netlist export and a net inspection helper
docs/               spec and images
```

## Credits

The Uno R3 design files come from [KiCad Arduino Boards](https://github.com/sabogalc/KiCad-Arduino-Boards), a KiCad recreation of the Arduino boards. See that repository for its license.
