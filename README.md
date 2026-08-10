# ESP32-S3 CAN + microSD Automotive Logger — Rev A

A single-CAN, single-microSD ESP32-S3 board with a motorsport-grade front end:
reverse-battery protection that survives being hooked up backwards indefinitely,
load-dump clamping, and four analog sensor inputs whose dividers are selected by
solder jumpers.

Feature set is deliberately close to the Autosport Labs ESP32-CAN-X2 and the
ESP32 Dual CAN-FD dev board — same ESP32-S3-WROOM-1 module, same 4-pin JST-PH
harness convention (12 V / GND / CAN_H / CAN_L), same default-on 120 Ω
termination jumper — but trades the second CAN channel for an onboard microSD
socket and conditioned analog inputs.

**Status: schematic complete and machine-verified. PCB is 84 x 72 mm,
4-layer, generated and placed (DRC-clean), not yet routed.**

---

## 1. Specification

| | |
|---|---|
| MCU | ESP32-S3-WROOM-1-N16R8 (16 MB flash, 8 MB octal PSRAM) |
| Supply input | 6–36 V continuous, reverse-protected to −65 V, clamped at 53.3 V |
| CAN | 1× CAN 2.0B, TJA1051T/3, ESP32-S3 TWAI controller, jumper-selectable split termination |
| Storage | microSD, 4-bit SDMMC, switchable card supply |
| Analog in | 4 channels, solder-jumper divider (0–3.3 V / 0–5 V / 0–16 V) + optional pull-up bias, shared by the ESP32 ADC and a 16-bit ADS1115 |
| Extras | Battery voltage monitor, USB-C (native USB), I²C/Qwiic header, UART0 header, spare-IO header |
| Rails | +5 V @ 1 A, +3V3 @ 1 A, +5 V sensor excitation (separately fused) |
| Board area | 155 components, 84 distinct BOM lines |

---

## 2. Power chain

```
J1.1 ──[F1 2A]──[FB1 ferrite]──┬── LM74700-Q1 + Q1 (ideal diode) ──┬── +VBAT
                               │                                    ├── D1 SMCJ33A clamp
                               │                                    ├── C2 100µF bulk
                               │                                    ├── U2 LM5164 ─→ +5V ─[PF1]─→ +5VS (sensors)
                               │                                    └── U3 LM5164 ─→ +3V3
```

### Reverse-battery protection

`U1` (LM74700-Q1) drives `Q1`, a 100 V N-channel MOSFET, as an ideal diode.
`Q1`'s **source faces the battery and its drain faces the load**, so the body
diode points forward: current passes in normal operation, and with the battery
connected backwards the body diode is reverse-biased and the FET is held off.
Nothing conducts, nothing gets hot, and the fuse does not blow — you can hook it
up backwards all day.

The forward drop is the FET's I·R (6.8 mΩ × 0.5 A ≈ 3 mV) rather than a diode's
0.4 V, so nothing is wasted in normal running either.

**Order matters here.** `D1`, the load-dump TVS, sits *after* the blocking FET,
not before it. A unidirectional TVS ahead of the FET would forward-conduct on
reverse polarity and blow `F1` — protection by sacrifice. Behind the FET, it
sees nothing during a reverse connection.

`R1`/`R2` set the LM74700 UVLO so the board enables at roughly 5.9 V and drops
out cleanly during cranking rather than browning out in an undefined state.

### Transient rating and its assumption

`D1` is an SMCJ33A: 33 V standoff, 53.3 V clamping at 1500 W (10/1000 µs).

That covers **ISO 7637-2 pulse 5b with centralised load-dump suppression**
(test level ~35 V), which is what any alternator with an internal avalanche
clamp produces — i.e. everything built in the last several decades. It also
covers pulses 1, 2a, 3a and 3b, and a 24 V jump-start sits below the standoff
voltage so it does not conduct.

It does **not** cover unsuppressed load dump (>100 V), which would need a
higher-energy clamp or a series pre-regulator. Say so if the target vehicle has
an external-regulator alternator.

Both bucks are LM5164s rated to **100 V**, so at the 53.3 V clamp level
there is nearly 2× headroom on the switcher inputs — the clamp fires long
before anything downstream is stressed.

### Why two bucks instead of a buck plus an LDO

`U2` (+5 V) and `U3` (+3V3) both run directly off `+VBAT`. It costs one extra
inductor versus 12 V→5 V→3.3 V cascading, and buys two things:

1. **Fault isolation.** A shorted 5 V sensor wire in the harness loads `U2`
   and trips `PF1`, but `U3` and the MCU never see it. A cascaded 3.3 V rail
   would collapse with the 5 V rail.
2. **No heat.** A 5 V→3.3 V linear at 600 mA burns ~1 W, which in an engine
   bay at 85 °C ambient is a thermal problem in a small package. A switcher
   burns almost nothing.

Both parts are the same LM5164, so it is one line on the BOM in quantity 20
rather than two different regulators.

### 5 V sensor excitation

`+5VS` is the +5 V rail behind `PF1` (200 mA hold / 400 mA trip polyfuse), a
ferrite, and `D3` (SMAJ5.0A). A sensor wire shorted to chassis or to battery
trips the polyfuse and clamps the transient without taking the board down.

Note this is a *fused tap off the 5 V rail*, not a separately regulated
reference. For ratiometric sensors where absolute accuracy matters, the ADC
should ratio against a measurement of `+5VS` — or add a dedicated precision
reference. See §7.

---

## 3. Analog input conditioning

Each of the four channels is identical:

```
AINn_IN ──[1k series]──┬─────────[10k]────┬── AINn ──→ ADC
                       │                  │
                  [JPn PULLUP]        [JPn BYPASS] (shorts the 10k)
                       │                  │
                    2.49k             [JPn RANGE] ─┬─[15k]─ GND   (position A)
                       │                           └─[2.21k]─ GND (position B)
                    +5VS                  │
                                    [100nF] + BAT54S clamp to GND / +3V3
```

### Jumper matrix

| RANGE | BYPASS | Input range | At the ADC | Typical sensor |
|---|---|---|---|---|
| open | **closed** | 0–3.3 V | 1:1 | 3.3 V-native sensor, ratiometric output |
| **A** | open | 0–5.0 V | ≈2.88 V at 5.0 V in | MAP, TPS, wideband AFR, most 5 V sensors |
| **B** | open | 0–16 V | ≈2.68 V at 16.0 V in | Battery-referenced signals, 12 V switch inputs |

`PULLUP` is independent of the above: closing it puts 2.49 kΩ to `+5VS` on the
input node, turning the channel into a bias network for **2-wire NTC sensors**
(coolant, oil, air temp) or open-collector/switch-to-ground inputs. Leave it
open for anything that drives its own output.

Every channel lands on **ADC1** (`GPIO1`, `GPIO2`, `GPIO4`, `GPIO5`), which is
the half of the ESP32-S3 ADC that keeps working while WiFi is active. ADC2 is
unusable with WiFi up — that constraint drove the pin assignment.

The same four conditioned nodes also feed `U7`, an **ADS1115** (16-bit
delta-sigma, on the existing I²C bus at 0x48). The ESP32-S3's internal ADC is
only good for ±1–2 % even after calibration — ±0.2 AFR on a 0–5 V wideband
output — so firmware picks per channel: fast-and-rough on the internal ADC, or
slow-and-accurate (up to 860 SPS) on the ADS1115. The divider resistors are
0.1 % thin-film so the front end does not throw away what the converter buys,
and the note in the schematic records that the 1 k series resistor is part of
the divider chain — the exact scale factor is a firmware calibration constant.

`R62`/`R63` divide `+VBAT` by 11 onto `GPIO6` for battery-voltage logging:
14.0 V reads 1.27 V, and the 36 V top of the input range reads 3.27 V.

---

## 4. CAN

`U6` is a TJA1051T/3 — 5 V bus drive with a separate `VIO` pin tied to +3V3, so
the ESP32 sees 3.3 V logic with no level shifter. `S` is pulled low by `R39` so
the transceiver comes up in normal mode with the MCU still in reset; `GPIO21`
can raise it for silent (listen-only) sniffing.

Bus side, in order from the transceiver out:

- `L3` common-mode choke (Würth WE-SL2)
- Split termination: two 60.4 Ω in series with `C27` 4.7 nF to ground at the
  midpoint. Split termination beats a single 120 Ω resistor because it gives
  the common-mode noise somewhere to go instead of reflecting it.
- `JP1` in series with the top half — **bridged by default**, matching the
  Autosport Labs convention of shipping terminated. Cut the trace when the
  board is a mid-bus node rather than an end node.
- `D8`/`D9` SMAJ26CA bidirectional clamps to ground on each line.

CAN_H/CAN_L run to `J1` pins 3 and 4, so one 4-pin JST-PH carries power and bus
in a single harness.

---

## 5. microSD

Wired for **4-bit SDMMC** (`GPIO9`–`GPIO14`), not SPI — roughly 4× the write
throughput, which matters when logging a busy bus.

- 33 Ω series damping on CLK, CMD and D0–D3.
- 47 kΩ pull-ups on CMD and D0–D3. **These pull to `SD_VDD`, the switched
  rail, not to +3V3** — pull-ups to a permanent rail would back-feed a
  powered-down card through its ESD structures.
- `Q2`/`Q3` form a high-side switch on the card supply, driven by `GPIO7`, so
  firmware can hard power-cycle a card that has locked up mid-write. This is
  the single most useful recovery mechanism in a logger; SD cards do wedge.
- `GPIO8` reads the socket's card-detect switch.

**Firmware note:** tristate the SDMMC pins before dropping `SD_PWR_EN`, or the
MCU will back-feed the card through its I/O pins while its supply is off.

---

## 6. GPIO map

| Module pin | GPIO | Net | Function |
|---|---|---|---|
| 39 | IO1 | `AIN1` | Analog ch 1 (ADC1_CH0) |
| 38 | IO2 | `AIN2` | Analog ch 2 (ADC1_CH1) |
| 4 | IO4 | `AIN3` | Analog ch 3 (ADC1_CH3) |
| 5 | IO5 | `AIN4` | Analog ch 4 (ADC1_CH4) |
| 6 | IO6 | `VBAT_SNS` | Battery voltage ÷11 (ADC1_CH5) |
| 7 | IO7 | `SD_PWR_EN` | microSD supply enable (high = on) |
| 12 | IO8 | `SD_CD` | Card detect |
| 17–22 | IO9–IO14 | `SD_D3,D2,D1,D0,CMD,CLK` | SDMMC 4-bit |
| 8, 9 | IO15, IO16 | `LED1`, `LED2` | Status LEDs |
| 10, 11 | IO17, IO18 | `CAN_TX`, `CAN_RX` | TWAI |
| 23 | IO21 | `CAN_S` | Transceiver silent mode (low = normal) |
| 13, 14 | IO19, IO20 | `USB_DM`, `USB_DP` | Native USB |
| 31, 32 | IO38, IO39 | `I2C_SDA`, `I2C_SCL` | Qwiic header |
| 36, 37 | IO44, IO43 | `UART_RX`, `UART_TX` | UART0 header |
| 27 | IO0 | `MCU_BOOT` | BOOT button |
| 3 | EN | `MCU_EN` | RESET button |
| 15, 16, 24, 25, 26, 33–35 | IO3, IO46, IO47, IO48, IO45, IO40–42 | — | Spare-IO header |
| 28, 29, 30 | IO35, IO36, IO37 | — | **Unusable** — octal PSRAM |

`IO3`, `IO45` and `IO46` are strapping pins and are broken out with no pull
resistors; do not hang anything on them that drives a level at boot.

---

## 7. Review before layout

Resolved in the rev A review (2026-08, against datasheet SNVSAU4D and TI
drawing 4214849/B):

1. ~~RON placeholders~~ — computed from Eq. 12: **31.6 kΩ → 396 kHz** on the
   5 V rail, **20.5 kΩ → 402 kHz** on the 3.3 V rail. Minimum on-time at the
   53.3 V clamp is 237 ns / 154 ns, both far above the 50 ns floor.
2. ~~Footprint~~ — the original `EP2.41x3.3mm` exposed pad was *smaller* than
   the DDA0008B pad itself (max 2.71 × 3.4 mm). Now on
   `EP2.95x4.9mm_Mask2.71x3.4mm_ThermalVias`, which matches TI's example land
   pattern exactly (2.95 × 4.9 copper, 2.71 × 3.4 mask-defined opening).
3. ~~Load-dump assumption~~ — confirmed: the target vehicle's alternator never
   exceeds 20 V, so the SMCJ33A's 33 V standoff has comfortable margin and the
   centralised-suppression assumption in §2 holds.
4. ~~Clamp injection~~ — quantified: one analog input shorted to 20 V battery
   back-feeds ≈1.6 mA through its BAT54S into +3V3. With the MCU running
   (≥ 20 mA) the rail cannot lift, but in deep sleep the always-on load is only
   ≈1.3 mA (power LED), so one faulted channel floats the rail to ≈3.6 V and
   two would exceed the ESP32's absolute maximum. Fixed with `D2`, a 3.6 V
   zener rail clamp (MM3Z3V6T1G).

Two more issues surfaced while reading the datasheet, both fixed:

- **Bootstrap capacitors were 100 nF.** The LM5164 datasheet mandates exactly
  2.2 nF and warns that larger values overstress the internal VCC regulator
  and damage the device. Both `CBST` are now 2.2 nF 50 V X7R.
- **No feedback ripple injection.** The outputs are all-ceramic, so FB had no
  in-phase ripple — the datasheet (Table 6-1) says a COT converter is unstable
  without it. Both rails now carry a Type-3 network (RA from SW, CA to the
  output, CB into FB: 121 k / 3.3 nF / 270 pF on 5 V, 95.3 k / 3.3 nF / 270 pF
  on 3.3 V), sized per TI's design example for ≈20 mV of ramp at 14 V in.

5. ~~ESP32-S3 ADC linearity~~ — resolved for the AFR use case: `U7`, an
   onboard ADS1115 (16-bit, I²C), now shares the four conditioned input
   nodes, and the divider resistors are 0.1 % thin-film. See §3.

Still open for a second pair of eyes:

6. `+5VS` accuracy (§2) if ratiometric sensors are used. (AFR is absolute, not
   ratiometric, so this does not affect the wideband channel.)
7. This is a schematic, not a layout. The usual switching-regulator rules
   apply: tight input-cap loops on `U2`/`U3`, `C4` right at the pin, ground
   pour under the ideal-diode block, CAN pair routed as a differential pair
   with the choke close to the connector.

### Sourcing (JLCPCB assembly)

The board is targeted at JLCPCB pick-and-place, so the critical semiconductors
were checked against the LCSC catalog (2026-08):

| Part | MPN | LCSC # | Notes |
|---|---|---|---|
| ESP32 module | ESP32-S3-WROOM-1-N16R8 | C2913202 | In stock |
| Buck ×2 | LM5164DDAR | C477928 | Non-automotive variant — the Q1 is backordered at DigiKey and nearly dry at Mouser; same silicon, minus AEC-Q100 |
| Ideal-diode ctrl | LM74700QDBVRQ1 | C2941042 | In stock (~$0.72) |
| Reverse-batt FET | IPD068N10N3G | C88066 | Replaces PSMN4R3-100BSE, **which does not exist** (nearest real Nexperia part is a D2PAK); DPAK, drops into the same footprint, 6.8 mΩ costs ~3 mV at load |
| CAN transceiver | TJA1051T/3/1J | C38695 | NXP original, in stock |
| Precision ADC | ADS1115IDGSR | C37593 | Non-automotive variant — the Q1 needs a manufacturer quote at DigiKey |

Still to pick from the live JLC catalog at layout time (generic, many options):
the two buck inductors (33 µH / 22 µH shielded molded, ≥ 2 A Isat — Coilcraft
XAL7030 is the reference part but LCSC stock is thin), the 100 µF 100 V bulk
electrolytic (Nichicon UCD is the reference), and the 0.1 % thin-film divider
resistors (commodity at LCSC).


---

## 8. Files

| Path | What |
|---|---|
| `esp32s3-can-sd-logger.kicad_pro` | KiCad 7/8 project (netclasses preset for Power and CAN) |
| `esp32s3-can-sd-logger.kicad_sch` | Root sheet |
| `power.kicad_sch` | Protection and rails |
| `mcu.kicad_sch` | ESP32-S3, USB-C, headers |
| `sdcard.kicad_sch` | microSD |
| `can.kicad_sch` | CAN transceiver and bus network |
| `analog.kicad_sch` | 4 sensor channels + battery monitor |
| `bom.csv` | BOM with quantities for 1 and 10 boards |
| `netlist.txt` | Human-readable net list (the design's source of truth) |
| `plots/schematic.pdf` | Rendered schematic, all six sheets |
| `gen/generate_schematic.py` | Generator — edit this, not the `.kicad_sch` files |
| `gen/validate.py` | Verifies the output with KiCad itself |
| `gen/generate_pcb.py` | Board generator — run with KiCad's bundled Python |
| `esp32s3-can-sd-logger.kicad_pcb` | Generated 4-layer board, placed, unrouted |
| `footprints/esp32autosport.pretty` | Project footprints (TDK ACT45B CAN choke) |
| `plots/board-placement.png` | Render of the placed board |

### KiCad version

The committed files are **KiCad 7.0 schematic format** (`version 20230121`).
KiCad 8 and 9 open them directly and upgrade the format on load, so nothing
needs converting — just open `esp32s3-can-sd-logger.kicad_pro`. Note that once
KiCad 9 saves, the files are rewritten in 9's format; re-running the generator
would put them back to 7's.

### Running ERC

`kicad-cli` gained an `erc` subcommand in 8.0. On KiCad 8 or 9, `validate.py`
picks it up automatically and reports violations as failures. To run it alone:

```sh
kicad-cli sch erc --output erc.json --format json \
    --severity-error --severity-warning esp32s3-can-sd-logger.kicad_sch
```

On KiCad 7 the check is skipped with a notice, since the subcommand does not
exist there. **ERC passes clean on KiCad 9.0** (no errors, no warnings) as of
rev A; the committed files embed symbol definitions from the KiCad 9 libraries,
so opening them on an older KiCad may report harmless lib-mismatch warnings.

### Regenerating

The schematic is generated. The netlist tables in `gen/generate_schematic.py`
are authoritative; symbol geometry and **all pin numbering** are pulled from the
installed KiCad libraries, so no pin number is ever hand-typed.

```sh
python3 gen/generate_schematic.py
python3 gen/validate.py
```

`validate.py` loads every sheet through KiCad, exports the hierarchy's netlist,
asserts that KiCad's own extracted connectivity matches `netlist.txt`
node-for-node, and runs ERC where available. It currently passes on 104 nets /
162 component instances.

The generator embeds symbol definitions copied verbatim from the installed
libraries, so the embedded copies always match whatever KiCad generation you
regenerate on (it handles both the 7/8 space-indented and the 9 tab-indented
library formats, and resolves the generic MOSFET symbols from `Transistor_FET`
where KiCad 9 moved them out of `Device`). The generator prints a warning when
the library generation differs from the one last validated against — run
`validate.py` afterwards; it will catch a sheet that fails to load and runs a
full ERC.

Editing the `.kicad_sch` files by hand is fine — they are ordinary KiCad files —
but the next generator run will overwrite them.

---

## 9. Board layout

The board is generated too: `gen/generate_pcb.py` (run with KiCad's bundled
Python) reads the same part/net tables as the schematic generator, loads real
footprints, assigns every pad its net, and places parts into functional zones.
84 x 72 mm, 4 layers (F.Cu / GND / +3V3 / B.Cu — inner planes are drawn as
zones, press `B` in KiCad to fill), JLCPCB JLC04161H-7628 stackup assumed,
0.2 mm minimum drill to match the LM5164 thermal vias.

Placement logic, all encoded in the generator:

- **Left edge:** sensor harness (`J8`) and power/CAN harness (`J1`) — one
  wiring direction toward the car.
- **Top:** four identical analog channel columns with their solder jumpers
  facing up for probing; ESP32 module top-center with the **antenna
  overhanging the board edge** (its keepout area falls entirely off-board);
  Spare-IO header left of it.
- **Right edge:** USB-C and microSD for bench access, then RESET/BOOT and the
  status LEDs.
- **Bottom band:** battery front end (fuse, ideal diode, TVS, bulk) beside
  `J1`, then the 5 V and 3.3 V bucks each packed with their own RON/FB/ripple
  network and output caps; UART/I2C/rail headers on the bottom edge.

`kicad-cli pcb drc` passes with **zero violations** (335 unconnected items
are the ratsnest — routing has not been done yet). Routing order when it
starts: buck power loops first, then SDMMC (length-matched-ish, short), USB
differential pair, CAN pair, analog last, stitching vias around the planes.
