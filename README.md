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

**Status: schematic complete and machine-verified. No PCB layout yet.**

---

## 1. Specification

| | |
|---|---|
| MCU | ESP32-S3-WROOM-1-N16R8 (16 MB flash, 8 MB octal PSRAM) |
| Supply input | 6–36 V continuous, reverse-protected to −65 V, clamped at 53.3 V |
| CAN | 1× CAN 2.0B, TJA1051T/3, ESP32-S3 TWAI controller, jumper-selectable split termination |
| Storage | microSD, 4-bit SDMMC, switchable card supply |
| Analog in | 4 channels, solder-jumper divider (0–3.3 V / 0–5 V / 0–16 V) + optional pull-up bias |
| Extras | Battery voltage monitor, USB-C (native USB), I²C/Qwiic header, UART0 header, spare-IO header |
| Rails | +5 V @ 1 A, +3V3 @ 1 A, +5 V sensor excitation (separately fused) |
| Board area | 146 components, 74 distinct BOM lines |

---

## 2. Power chain

```
J1.1 ──[F1 2A]──[FB1 ferrite]──┬── LM74700-Q1 + Q1 (ideal diode) ──┬── +VBAT
                               │                                    ├── D1 SMCJ33A clamp
                               │                                    ├── C2 100µF bulk
                               │                                    ├── U2 LM5164-Q1 ─→ +5V ─[PF1]─→ +5VS (sensors)
                               │                                    └── U3 LM5164-Q1 ─→ +3V3
```

### Reverse-battery protection

`U1` (LM74700-Q1) drives `Q1`, a 100 V N-channel MOSFET, as an ideal diode.
`Q1`'s **source faces the battery and its drain faces the load**, so the body
diode points forward: current passes in normal operation, and with the battery
connected backwards the body diode is reverse-biased and the FET is held off.
Nothing conducts, nothing gets hot, and the fuse does not blow — you can hook it
up backwards all day.

The forward drop is the FET's I·R (4.3 mΩ × 0.5 A ≈ 2 mV) rather than a diode's
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

Both bucks are LM5164-Q1s rated to **100 V**, so at the 53.3 V clamp level
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

Both parts are the same LM5164-Q1, so it is one line on the BOM in quantity 20
rather than two different regulators.

### 5 V sensor excitation

`+5VS` is the +5 V rail behind `PF1` (200 mA hold / 400 mA trip polyfuse), a
ferrite, and `D2` (SMAJ5.0A). A sensor wire shorted to chassis or to battery
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
| **A** | open | 0–5.0 V | 3.00 V at 5.0 V in | MAP, TPS, most 5 V ratiometric sensors |
| **B** | open | 0–16 V | 2.89 V at 16.0 V in | Battery-referenced signals, 12 V switch inputs |

`PULLUP` is independent of the above: closing it puts 2.49 kΩ to `+5VS` on the
input node, turning the channel into a bias network for **2-wire NTC sensors**
(coolant, oil, air temp) or open-collector/switch-to-ground inputs. Leave it
open for anything that drives its own output.

Every channel lands on **ADC1** (`GPIO1`, `GPIO2`, `GPIO4`, `GPIO5`), which is
the half of the ESP32-S3 ADC that keeps working while WiFi is active. ADC2 is
unusable with WiFi up — that constraint drove the pin assignment.

`R60`/`R61` divide `+VBAT` by 11 onto `GPIO6` for battery-voltage logging:
14.0 V reads 1.27 V, and the 36 V top of the input range reads 3.27 V.

---

## 4. CAN

`U6` is a TJA1051T/3 — 5 V bus drive with a separate `VIO` pin tied to +3V3, so
the ESP32 sees 3.3 V logic with no level shifter. `S` is pulled low by `R37` so
the transceiver comes up in normal mode with the MCU still in reset; `GPIO21`
can raise it for silent (listen-only) sniffing.

Bus side, in order from the transceiver out:

- `L3` common-mode choke (Würth WE-SL2)
- Split termination: two 60.4 Ω in series with `C23` 4.7 nF to ground at the
  midpoint. Split termination beats a single 120 Ω resistor because it gives
  the common-mode noise somewhere to go instead of reflecting it.
- `JP1` in series with the top half — **bridged by default**, matching the
  Autosport Labs convention of shipping terminated. Cut the trace when the
  board is a mid-bus node rather than an end node.
- `D7`/`D8` SMAJ26CA bidirectional clamps to ground on each line.

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

Honest list of what a second pair of eyes should check:

1. **`R4`/`R9` (RON = 100 kΩ)** are placeholders targeting ~400 kHz at 14 V in.
   Compute the real value from the LM5164 datasheet's on-time equation for each
   output voltage, and confirm the minimum on-time is still respected at the
   53 V clamp level (worst case ≈ 156 ns for the 3.3 V rail).
2. **`U2`/`U3` footprint.** The exposed-pad dimensions on
   `SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.3mm_ThermalVias` were chosen as the
   closest stock match and must be confirmed against TI's DDA package drawing.
3. **Load-dump assumption** (§2) — verify the target vehicle uses a centrally
   suppressed alternator.
4. **Clamp injection.** The BAT54S clamps dump current into +3V3 when an input
   is overdriven. With ~44 µF and the MCU always drawing current the rail will
   not run away, but if the board can be powered with the MCU held in reset,
   check the rail does not lift.
5. **ESP32-S3 ADC linearity** is mediocre even calibrated. If any channel needs
   better than roughly ±1–2 %, put an external SAR ADC on the I²C/SPI header
   rather than fighting the internal one.
6. `+5VS` accuracy (§2) if ratiometric sensors are used.
7. This is a schematic, not a layout. The usual switching-regulator rules
   apply: tight input-cap loops on `U2`/`U3`, `C4` right at the pin, ground
   pour under the ideal-diode block, CAN pair routed as a differential pair
   with the choke close to the connector.

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

### Regenerating

The schematic is generated. The netlist tables in `gen/generate_schematic.py`
are authoritative; symbol geometry and **all pin numbering** are pulled from the
installed KiCad libraries, so no pin number is ever hand-typed.

```sh
sudo apt-get install kicad kicad-symbols     # provides kicad-cli and the libraries
python3 gen/generate_schematic.py
python3 gen/validate.py
```

`validate.py` loads every sheet through KiCad, exports the hierarchy's netlist,
and asserts that KiCad's own extracted connectivity matches `netlist.txt`
node-for-node. It currently passes on 102 nets / 153 component instances.

Note that `kicad-cli` 7 has no ERC subcommand (added in 8), so the netlist
comparison above is the electrical check that was actually run. Open the project
in KiCad and run ERC before committing to a layout.

Editing the `.kicad_sch` files by hand is fine — they are ordinary KiCad files —
but the next generator run will overwrite them.
