# The generator pipeline

Everything in this project is generated: the Python in this directory is the
source of truth and every `.kicad_sch` / `.kicad_pcb` / `fab/` / `plots/`
file is a build artifact. Nothing is ever hand-edited in KiCad — if it were,
the next regeneration would silently discard it. This file documents how the
pipeline works and, more importantly, the rules that were learned the hard
way, so the next board can reuse all of it.

## The one-command loops

```
# schematic loop (any Python 3)
python gen/generate_schematic.py && python gen/validate.py

# board loop (needs KiCad 9 installed, freerouting.jar in the repo root)
python -u gen/build_board.py --passes 60

# the checks DRC cannot make (after a build)
"C:\Program Files\KiCad\9.0\bin\python.exe" gen/audit_pcb.py
python gen/simulate.py            # needs ngspice, numpy, matplotlib

# outputs
python gen/export_plots.py        # schematic.pdf + three board renders
"...\bin\python.exe" gen/export_fab.py   # JLCPCB gerbers/BOM/CPL
```

## What each file owns

| file | owns |
|---|---|
| `generate_schematic.py` | the part and net tables (the design itself), sheet packing, symbol/wire/label emission, netclasses, title blocks |
| `sch_blocks.py` | hand-drawn schematic layouts for every recurring circuit — coordinates only, no electrical content |
| `generate_pcb.py` | board outline, placement zones, fixed placements (`FIXED`, `BUCK_FIXED`, `PIN_FIXED`), planes, keepouts |
| `build_board.py` | the 10-stage build driver, with a lock so two builds cannot fight |
| `route_bucks.py` | hand-shaped copper for the switching loops |
| `finish_routing.py` | ties for duplicated connector pins (runs before AND after autorouting) |
| `stitch_planes.py` | a via from every GND/+3V3 pad to its plane, with hole-collision checks |
| `maze_route.py` | rip-up-and-retry router for whatever freerouting leaves open |
| `tidy_silk.py` | reference designator declutter; touches no copper |
| `validate.py` | compares KiCad's own netlist node-for-node against `netlist.txt`, runs ERC |
| `audit_pcb.py` | current capacity, antenna keepout, decoupling distance, thermal, drill overlaps |
| `simulate.py` | eight ngspice/numpy studies of the circuits themselves |
| `overstress.py` | closed-form worst-case for every external input |

## Design rules the pipeline enforces mechanically

- Parts are identified by **(sheet, value, exact pad-net signature)**
  everywhere — placement tables, schematic blocks, buck routing. References
  renumber on every run and must never be used as keys.
- Values are **display-short** ("600R", "0.2A PTC", "AO3401A"); ratings live
  in the hidden Voltage/Tolerance/Note/MPN fields. `split_value()` separates
  voltage and tolerance automatically. Because values are matching keys, a
  value rename must be applied to `generate_schematic.py`, `sch_blocks.py`,
  `generate_pcb.py` and `audit_pcb.py` in the same commit.
- Every recurring or nontrivial circuit gets a **hand-drawn block** in
  `sch_blocks.py`. The column packer is only for true one-liners (bypass
  caps, single pull-resistors that belong to no connector) — anything that
  reads as "parts floating in space" should be blocked or attached.

## How to draw a schematic block (the hard-won rules)

A block is a dict: `sheet`, `anchor` (value, netset), `parts`
[(value, netset, dx, dy, rot)], `wires` [polylines], `junctions`, `rails`
[(net, x, y, facing)], `labels` {net: (x, y, angle)}. All coordinates are
mm relative to the anchor, on the **1.27 mm grid** — 12.07 is not a grid
point, 12.70 is; off-grid ends throw ERC "off connection grid" warnings.

**Rotation → pin positions** (sheet coordinates, y grows downward):

- Two-pin R/C/L/fuse (pins 1/2 at lib (0, ±3.81)):
  rot 0 → pin 1 top, pin 2 bottom; rot 180 → flipped;
  rot 90 → pin 1 left; rot 270 → pin 1 right.
- GSD FETs / BEC BJTs (G/B lib (−5.08, 0), D/C (2.54, 5.08), S/E (2.54, −5.08)):
  rot 0 → gate left, drain top, source bottom;
  rot 90 → gate bottom, drain left-top, source right-top;
  rot 270 → gate top, source left-bottom, drain right-bottom.
  The mapping is: rot 270 ≡ (lx,ly)→(ly,lx), rot 90 ≡ (lx,ly)→(−ly,−lx).
- TL431DBZ: A left, K right, REF top (rot 0).

**Connectivity rules** (each of these broke a build before it was learned):

- A wire must **end** at every junction on it. To tap a wire mid-span,
  split it into two wires that both end at the tap point and add a junction
  there. Three or more wire-ends (or wire-ends plus a pin) at one point
  need a junction; exactly two objects sharing an endpoint connect without
  one, and adding a junction to a bare two-object meeting point *breaks*
  the netlist.
- Crossing wires without a junction are legal and do not connect — use
  crossings freely, the way any dense schematic does.
- A wire routed through a third part's **pin point** connects to it. Route
  around pins you do not mean to touch.
- Labels attach anywhere along a wire or at a pin end. One label position
  per net per block. The emitter picks local vs global form automatically
  from whether the net leaves the sheet.
- Pins on power nets (GND, +3V3, +5V, +VBAT, +5VS, VBUS) may simply be
  left unwired: the emitter hangs the right power symbol on them. Only
  signal nets need wires or labels.
- **The anchor keeps its natural rotation** — only member parts get the
  block's `rot`. If the drawing needs a part rotated, that part cannot be
  the anchor (this is why SENSW anchors on the 2N7002, not the P-FET).
- Value text is placed above/below the body; leave a grid step of air
  between parallel runs or the text lands on the neighbouring wire.

**Never put a control character in any emitted string.** A literal newline
inside a symbol property is accepted by KiCad's loader and then silently
breaks connectivity for every symbol after it in the file — the netlist
drops them with no error pointing anywhere near the cause. The emitter now
flattens all whitespace in properties; keep it that way.

## Verification ladder

Every change climbs the same ladder, and each rung catches a class of
mistake the rung below cannot:

1. `generate_schematic.py` — dies loudly on unmatched block parts.
2. `validate.py` — ERC plus node-for-node netlist comparison.
3. `build_board.py` — DRC to 0 violations / 0 unconnected.
4. `audit_pcb.py` — the physics DRC does not know about.
5. `simulate.py` — does the circuit *work*, across tolerance and abuse.

## Starting a new board from this pipeline

1. Copy `gen/` wholesale; delete the project-specific tables in
   `generate_schematic.py` (sheets/parts) and `generate_pcb.py` (zones,
   FIXED/BUCK_FIXED/PIN_FIXED, HOLES, board size) and `sch_blocks.py`.
2. Write the part tables first; run the schematic loop until validate
   passes with everything column-packed and ugly.
3. Draw blocks for each circuit cluster; re-run the loop after each one.
4. Define zones roughly, build the board, then iterate placement from the
   courtyard-overlap warnings and the renders.
5. Keep `simulate.py`'s decks in step with the schematic tables — the
   component values are duplicated there on purpose, so a disagreement is
   a bug report.
