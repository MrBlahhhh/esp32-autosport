# First-power bring-up checklist

Work through this in order on the first board back from assembly. Every
step has an expected value; stop at the first one that misses and debug
there — later stages assume the earlier ones.

**Bench setup:** current-limited supply, leads to J1 pin 1 (+) and pin 2
(GND). No card, no sensors, nothing on USB yet.

## Stage 0 — before power

- [ ] Visual against `plots/board-iso.png`: connector orientations, the two
      220 µF cans, no tombstones on the SOT-23s.
- [ ] Meter, diode mode, across every rail TP to GND (TP7): none reads a
      dead short. `+VBAT` (TP3) shows the bucks' input caps charging; `+5V`
      (TP4) and `+3V3` (TP5) each read a diode-ish ~0.4–0.6 V from the
      converters' body diodes — 0.000 V here means a solder bridge.

## Stage 1 — front end only (current limit 100 mA, 13.5 V)

- [ ] Input current after inrush: **≤ 60 mA** (module idle + LED).
- [ ] TP3 `+VBAT` = supply minus ~10 mV (the ideal diode is enhancing;
      0.4–0.7 V low means the LM74700 isn't driving Q1 — check R1/R2).
- [ ] TP4 `+5V` = **5.00 ± 0.15 V**, TP5 `+3V3` = **3.28 ± 0.10 V**
      (tolerance windows from the Monte Carlo, study 8).
- [ ] TP1 `PG_5V`, TP2 `PG_3V3` both high (~3.3 V).
- [ ] Green LED on.
- [ ] TP6 `+5VS` = **0 V** — the sensor rail is off until firmware asserts
      SENS_EN. If it reads 5 V, Q2/Q3 are misplaced or R7 is missing.
- [ ] J7 pin 4 `PWR_FAIL` = **low** at 13.5 V in. Lower the supply: it must
      snap high at **11.0 ± 0.3 V** and snap back low ~0.3 V higher.
- [ ] Nothing warm to the touch after 5 minutes.

## Stage 2 — reverse and ride-through (still no firmware)

- [ ] Swap the supply leads (current limit still 100 mA): input current
      **0 mA**, nothing heats. Swap back.
- [ ] Scope TP3 and kill the supply: `+VBAT` should coast down over tens of
      ms (the 540 µF bank), not collapse instantly.

## Stage 3 — USB (no J1 power)

- [ ] Plug a known 5 V USB source: board boots (LED on), TP4 ≈ 4.5 V
      (through the OR-ing diode — this is normal on USB power).
- [ ] The port enumerates as the ESP32-S3 CDC device.
- [ ] If you have a 9 V-capable brick and a sacrificial attitude: the OVP
      should simply disconnect (board dead, no damage, recovers on a 5 V
      source). Simulated trip 5.95 V.

## Stage 4 — firmware smoke test (J1 power)

- [ ] Flash over USB. Boot straps verified by design (audit_straps), so a
      failure to enter download mode means the BOOT button, not the straps.
- [ ] Firmware asserts `SENS_EN` (IO16): TP6 `+5VS` rises to ~4.9 V.
- [ ] Insert a card, fire `SD_PWR_EN`: card mounts. If not, scope the
      **SD_CLK / SD_CMD test points** by the socket — CMD0 out and a
      response back tells you card vs. bus in one look.
- [ ] CAN: with a second node and 120 Ω at the far end, send a frame.
      TP10/TP11 (CAN_H/CAN_L) show ~2.5 V recessive, splitting to
      ~3.5/1.5 V dominant. (TP8/TP9 are SD_CLK/SD_CMD, not the bus.)
- [ ] Power-cut drill: with a file open and logging, kill J1. The file
      must be intact on the card. `PWR_FAIL` on J7.4 shows the warning
      edge on a scope; the bank buys ~100 ms (study 4).

## Stage 5 — analog channels

- [ ] Jumpers as shipped = all open = 0–3.3 V range floating: readings
      near zero, not railed.
- [ ] Feed 2.500 V into AIN1: internal ADC and ADS1115 agree within 2 %.
      Set RANGE A (0–5 V): 5.000 V in reads 2.86 V at the pin (divider
      confirmed to three digits in study 2).
- [ ] Feed −5 V (reversed sensor): channel reads ~0, board unbothered.

Everything in this list traces to a study in `gen/simulate.py` or an audit
in `gen/`; if a measurement misses its window, the matching study says
what the value is made of.
