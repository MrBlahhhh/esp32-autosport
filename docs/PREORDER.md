# Pre-order checklist

Everything worth checking before `fab/` is uploaded and money is spent. Run
top to bottom; each stage says what it proves and, where it can be automated,
the command that does it.

The board has never been fabricated, so the point of this list is not
confidence — it is making sure the *checkable* things have been checked, and
being explicit about what is left resting on judgement.

```sh
python gen/validate.py            # schematic vs netlist.txt, plus ERC
python gen/audit_docs.py          # docs vs design: designators and numbers
python gen/audit_polarity.py      # every polarised part, plus a fab checklist
python gen/simulate.py            # 12 circuit studies (needs ngspice)
python gen/simulate_firmware.py   # 49 firmware-in-the-loop checks
python gen/mutate_firmware.py     # proves those checks would fail if the firmware broke
python gen/audit_paste.py         # stencil apertures: IPC-7525 + thermal-pad coverage
python gen/audit_mechanical.py    # vibration screen for the tall capacitors
"…/KiCad/9.0/bin/python.exe" gen/audit_pcb.py
"…/KiCad/9.0/bin/python.exe" gen/audit_routes.py
"…/KiCad/9.0/bin/python.exe" gen/audit_straps.py
"…/KiCad/9.0/bin/python.exe" gen/overstress.py
```

All of the above pass as of 2026-08-12.

---

## 1. Automated, and currently green

| Check | Script | What it would catch |
|---|---|---|
| Schematic matches the netlist, ERC clean | `validate.py` | a generator edit that did not land |
| Docs match the design | `audit_docs.py` | the `U7`/`U12` class of error, and any number in the README rotting |
| Polarity of every diode, electrolytic, IC | `audit_polarity.py` | a part designed in backwards |
| Circuit behaviour, 12 studies | `simulate.py` | ISO 7637-2 survival, ripple, ride-through, inrush, crank, tolerances |
| Firmware against a model of this board | `simulate_firmware.py` | wrong GPIO, wrong divider constant, an unspent power-fail window |
| Whether those firmware checks are worth anything | `mutate_firmware.py` | a test suite that passes because it asserts nothing — 16 of 16 injected defects caught |
| Stencil apertures | `audit_paste.py` | starved fine-pitch joints; a thermal pad given so much paste the part floats |
| Vibration on the tall parts | `audit_mechanical.py` | solder-joint fatigue on the 22 mm capacitor cans |
| Copper: current capacity, decoupling distance, thermals, overlapping drills | `audit_pcb.py` | a 1 A rail on 0.2 mm track; two vias drilled on one point |
| Routing quality | `audit_routes.py` | |
| Strapping pins | `audit_straps.py` | a boot pin held the wrong way |
| Worst-case part stress | `overstress.py` | a part run past its rating at the clamp level |

## 2. Done by hand this session, worth repeating if parts change

- **Every JLC part number checked against the live catalogue**, one at a time,
  confirming MPN and package. This found `C8544` (an NPN sold as our PNP) and
  `C7975` (a quad op-amp sold as our logic buffer). §7 records the outcome.
  **Repeat this whenever a part number changes** — nothing offline can do it,
  because it is a question about someone else's inventory.
- **Footprint-vs-package sanity**: the catalogue lookup returns a package for
  each part; compare it with the footprint in `bom.csv`. That is what would
  have caught `C7975` (TSSOP-14 against a SOT-23-5 land) even without noticing
  the MPN was wrong.

## 3. Not yet done — worth doing before ordering

Ranked by what they would cost if skipped. Items 1-3 of the original list are
now automated and green (see above); what is left is the part that needs
someone else's data or a physical model.

1. **JLC's own DFM report.** Free at upload, and it checks their actual
   process: minimum annular ring, solder-mask sliver, silkscreen-on-pad,
   acid traps. Nothing here models their fab. **Upload the gerbers and read
   the report before paying** — the single highest-value remaining check, and
   it costs nothing.
2. **Placement preview, part by part.** `audit_polarity.py` prints the 32
   parts that have an orientation with board position and which net pin 1
   sits on. Every polarised part is placed at 0°, so anything that appears
   rotated in JLC's previewer is their library, not this design.
3. **3D clearance against the enclosure.** `fab/board.step` is current, but
   the ride-through capacitors went from 16×17.5 mm to 16×22 mm. Re-check the
   dash enclosure against the new STEP.
4. **A behavioural COT loop model.** `simulate.py` drives the LM5164 power
   stage at an ideal duty cycle because TI's model is encrypted, so loop
   stability and load-step recovery are answered by nothing at all. A
   behavioural constant-on-time model in ngspice would not be silicon-exact
   but would close that gap.
5. **2-D thermal solve at 70 °C ambient.** `audit_pcb.py` does closed-form
   per-part dissipation into copper area; a finite-difference solve over the
   real pours would catch interactions, including whether the taller cans now
   shadow `U3`/`U4`.

## 4. Things that cannot be checked without hardware

State them rather than pretend otherwise:

- **Nothing has been powered on.** Every check in this repository is a
  software check.
- **The LM5164 control loop.** Constant-on-time with an encrypted TI model;
  `simulate.py` drives the power stage at an ideal duty cycle, so it answers
  what the passives survive, not whether the loop is stable or how it recovers
  from a load step. Needs TI's PSpice model or a bench.
- **RF.** The antenna is inside the module and overhangs the board edge; no
  RF simulation was done or is planned.
- **Real ESD and real transients.** Simulated against ISO 7637-2 waveforms,
  never injected.
- **The firmware on real silicon.** `fwsim` models the API contract, not the
  implementation: no NimBLE bug, stack overflow, RMT conflict, PSRAM init
  problem or FreeRTOS scheduling issue can appear in it.
- **`+5VS` accuracy for ratiometric sensors** (§7 item 7), still open.
- **The vibration screen is a screen.** `audit_mechanical.py` idealises the
  board as a simply-supported plate and uses Steinberg's empirical fatigue
  limit. It says "fine with 1.7x margin", not "qualified". Its assumptions are
  printed with every run for exactly that reason.

## 5. Ordering-day gotchas already known

From §11, repeated here because they are the ones that cost money:

- The BOM's part-number column must be headed exactly **`JLCPCB Part #`**.
  Any other name is silently ignored and every line falls through to the
  fuzzy matcher.
- **Check the placement preview for polarity** — see §2 above.
- Take *Basic* over *Extended* where value and package match; Extended parts
  carry a setup fee each.
- `R43`–`R62` must stay **0.1 %**; `C3`/`C6`/`C7` must stay **100 V**.
- The eight through-hole connectors are deliberately in neither the BOM nor
  the CPL. `export_fab.py` now re-execs itself under KiCad's Python to
  guarantee that — it used to return an empty exclusion set when `pcbnew` was
  missing, producing fab files that looked perfect and quietly asked JLC to
  assemble all eight.
