# ESP32-S3 CAN + microSD Automotive Logger — Rev B

## TL;DR

A data logger you wire straight into a race car: 12 V and CAN come in on one
4-pin plug, sensor readings and CAN traffic get written to a microSD card.

- **You cannot kill it with the wiring.** Battery backwards? Blocks it,
  forever, no blown fuse. Alternator load dump, jump start, ignition spikes?
  Clamped. A sensor wire shorted to 12 V? That channel's fuse trips, the
  board keeps logging.
- **Ignition-off doesn't corrupt the card.** The board sees the power cut
  ~150 ms before it actually dies and uses that time (banked in two big
  capacitors) to finish the write and close the file.
- **4 sensor inputs, jumper-set for 0–3.3 V / 0–5 V / 0–16 V**, readable
  fast-and-rough on the ESP32's own ADC or slow-and-precise on a 16-bit
  ADS1115. Plus battery voltage monitoring, USB-C, a WS2812 shift-light
  header, and spare I/O.
- **The Python in `gen/` is the real source.** It generates, places, routes,
  audits, and circuit-simulates the whole board; the KiCad files are build
  outputs. `python gen/build_board.py` rebuilds the PCB from nothing, and
  [`gen/README.md`](gen/README.md) documents the whole pipeline — including
  the schematic-drawing conventions — so the next board can reuse it.
- **The firmware runs before the board exists.** `gen/simulate_firmware.py`
  compiles the sketch for the PC against a model of this board and feeds it
  CAN frames, sensor voltages and an ignition cut. Porting the proven MINI R53
  shift-light firmware to this hardware turned up five defects that no ERC,
  DRC or SPICE run can see — including a wideband that reads 17 % high and a
  sensor rail that never powers up.
- **State: routed clean, simulated, firmware ported, never manufactured.**
  Order a small prototype run first.

---

A single-CAN, single-microSD ESP32-S3 board with a motorsport-grade front end:
reverse-battery protection that survives being hooked up backwards indefinitely,
load-dump clamping, and four analog sensor inputs whose dividers are selected by
solder jumpers.

Feature set is deliberately close to the Autosport Labs ESP32-CAN-X2 and the
ESP32 Dual CAN-FD dev board — same ESP32-S3-WROOM-1 module, same 4-pin JST-PH
harness convention (12 V / GND / CAN_H / CAN_L), same jumper-selectable 120 Ω
termination — but trades the second CAN channel for an onboard microSD socket
and conditioned analog inputs. Termination ships **off** here rather than on,
which is the one place this board deliberately breaks with that convention:
the common case is plugging into a vehicle bus that is already terminated.

**Status: Rev B, fully routed.** Schematic is ERC-clean on KiCad 9.0. The PCB
is **84 x 100 mm**, 4-layer, **fully placed and routed with zero DRC errors and
nothing unconnected** — 1731 tracks, 323 vias, 206 footprints, 115 nets. Rev B
carries the fixes from an external datasheet review (§7): the LM74700 enable
divider, the ANODE capacitor, two TVS standoff corrections, transient clamps
on the analog harness inputs, and a dozen smaller items. `fab/` holds the
Gerbers, drill, BOM and pick-and-place in JLCPCB's format, BOM and CPL verified
to list the same 170 designators. See §9 for how the board is built, §10 for
what simulation and the physical audit say about it, and §11 for
the ordering steps. **Nothing has ever been fabricated — the first order should
be a small prototype run.**

---

## 1. Specification

| | |
|---|---|
| MCU | ESP32-S3-WROOM-1-N16R8 (16 MB flash, 8 MB octal PSRAM) |
| Supply input | 6–36 V continuous, reverse-protected to −65 V, transients clamped at 64.5 V, 154 ms power-cut ride-through (51 ms worst-case on a flat battery) |
| CAN | 1× CAN 2.0B, TJA1051T/3, ESP32-S3 TWAI controller, split termination, **off by default** |
| Storage | microSD, 4-bit SDMMC, switchable card supply |
| Analog in | 4 channels, solder-jumper divider, **0–5 V by default** (0–3.3 V / 0–16 V by jumper) + optional pull-up bias, shared by the ESP32 ADC and a 16-bit ADS1115 |
| Extras | Battery voltage monitor, USB-C (native USB), I²C/Qwiic, UART0, SPI breakout, WS2812 5 V DIN header, 6-pin spare-IO header |
| Rails | +5 V @ 1 A, +3V3 @ 1 A, +5 V sensor excitation (separately fused) |
| Parts | 195 component instances, 97 distinct BOM lines, all surface-mount except 8 through-hole connectors |

---

## 2. Power chain

```
J1.1 ──[F1 2A]──┬──[FB1 ferrite]── LM74700-Q1 + Q1 (ideal diode) ──┬── +VBAT
                │                                                  ├── C3 100µF bulk
                └── D1 SMCJ40CA clamp                              ├── U3 LM5164 ─→ +5V ─[PF1]─→ +5VS
                    (ahead of the FET)                             └── U4 LM5164 ─→ +3V3
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

**Order matters here.** `D1`, the transient clamp, sits *ahead* of the blocking
FET. Clamping downstream of it leaves `Q1` and the LM74700 exposed to whatever
arrives on the harness: on a negative transient `Q1` turns off and stands the
whole pulse across its drain-source, and pulses 1 (−100 V) and 3a (−150 V) both
exceed its 100 V rating. In front of the FET the clamp catches those first —
simulation puts `Q1` at 55 V during pulse 1, against 100 V (§10).

That placement is only safe because `D1` is **bidirectional**. A unidirectional
part in this position would forward-conduct on a sustained reverse connection
and blow `F1`, which is exactly the outcome the ideal diode exists to avoid. At
40 V standoff either way, a −14 V reverse battery is still `Q1`'s job to block
and only real transients are clamped.

`R1`/`R2` set the LM74700 UVLO so the board enables at roughly 5.9 V and drops
out cleanly during cranking rather than browning out in an undefined state.

### Transient rating and its assumption

`D1` is an SMCJ40CA: 40 V standoff either polarity, 64.5 V clamping at 1500 W
(10/1000 µs). 40 V rather than 33 V so the part stands off the declared 36 V
top of the input window instead of sitting in conduction there.

That covers **ISO 7637-2 pulse 5b with centralised load-dump suppression**
(test level ~35 V), which is what any alternator with an internal avalanche
clamp produces — i.e. everything built in the last several decades. Simulation
(§10) shows the harness reaching 31 V in that case with the TVS never
conducting at all. Pulses 1, 2a, 3a and 3b are likewise covered: the worst is
pulse 1, where the clamp takes 0.07 J against roughly 2 J of capability at that
pulse width. A 24 V jump-start sits below the standoff voltage so it does not
conduct.

It does **not** cover unsuppressed load dump. At the pulse 5b level IV of 87 V
the clamp is asked to absorb 219 J against about 6.7 J of capability and is
destroyed (§10). That needs a higher-energy clamp or a series pre-regulator —
**say so if the target vehicle has an external-regulator alternator.**

Both bucks are LM5164s rated to **100 V**, so at the 64.5 V clamp level there
is still headroom on the switcher inputs; the clamp fires before anything
downstream is stressed. The tighter limit is the LM74700's own 65 V, which the
clamp level sits directly on top of — another reason the unsuppressed case is
out of scope rather than marginal.

### Why two bucks instead of a buck plus an LDO

`U3` (+5 V) and `U4` (+3V3) both run directly off `+VBAT`. It costs one extra
inductor versus 12 V→5 V→3.3 V cascading, and buys two things:

1. **Fault isolation.** A shorted 5 V sensor wire in the harness loads `U3`
   and trips `PF1`, but `U4` and the MCU never see it. A cascaded 3.3 V rail
   would collapse with the 5 V rail.
2. **No heat.** A 5 V→3.3 V linear at 600 mA burns ~1 W, which in an engine
   bay at 85 °C ambient is a thermal problem in a small package. A switcher
   burns almost nothing.

Both parts are the same LM5164, so it is one line on the BOM in quantity 20
rather than two different regulators.

### Power-fail detection and ride-through

The ignition cuts this board's power with a log file open on the SD card,
every single drive. The chain that makes that survivable:

1. **Detect** — a TLV431 watches `VBAT_F` *ahead* of Q1 through a 100k/12.7k
   divider and asserts `PWR_FAIL` (GPIO15, rising edge) when the harness
   drops below **11.0 V**. Sensing ahead of the ideal diode is the point:
   when the ignition opens, the harness collapses within microseconds while
   `+VBAT` coasts, so the warning arrives before any stored energy has been
   spent. A 1 M feedback resistor adds ~0.3 V of hysteresis so cranking sag
   cannot chatter the interrupt.
2. **Coast** — `+VBAT` holds 760 µF (100 µF + 2 × 330 µF/100 V). Energy is
   ½CV², so the bank lives on the input rail where a volt is worth most.
3. **Shed** — `SENS_EN` (GPIO16) drives a 2N7002 + AO3401 high-side switch on
   the sensor rail. Sensors are external loads firmware cannot otherwise
   turn off, and at four × 20 mA they double the drain. The switch is off at
   reset — the rail comes up only when firmware asks.

Simulated end to end (§10, `sim/ridethru.png`): detection in under 1 ms, and
from `PWR_FAIL` to the converters dropping out is **154 ms with the sensor
rail shed, 75 ms without** — both measured on the 760 µF bank.

That is the *healthy-battery* case: the ignition opens at 13.5 V and `+VBAT`
starts its coast from there, because `Q1` isolates it from the harness. Open
the ignition on a battery already down at the trip point and the bank starts
at 10.7 V instead — energy goes as V², so that alone is 1.9× less charge. The
Monte Carlo puts that flat-battery corner at **78 ms median, 51 ms at 0.1 %**
with worst-case tolerances (§10). Both are real; they bound opposite ends of
the same event, and firmware should be sized against the 51 ms.

**Firmware contract:** on `PWR_FAIL` rising — drop `SENS_EN`, stop sampling,
flush and close the file, then idle. Do not start a new write while
`PWR_FAIL` is high. The window is guaranteed by hardware; spending it is
firmware's job.

The firmware that spends it now exists and has been run against that window
(§10, *Firmware in the loop*). Measured: the file is closed **31 ms** after
`PWR_FAIL`, leaving 121 ms of the bank unspent, and the card may take up to a
**~130 ms** flush before the close no longer fits — though that, too, is
against the nominal window; on the flat-battery corner the budget is 51 ms and
only a healthy card fits. That is roughly seven times a
healthy card's latency but it is a limit, not unlimited protection — a card
that stalls for longer than that still loses the file.

### Reversed 5 V on the exposed 5 V pins

Asked and answered per connector: **USB-C** cannot be reversed through the
connector (VBUS/GND positions are fixed; the plug is rotation-symmetric).
**J10 sensor +5VS** survives a reversed 5 V by design — D3 forward-conducts
at −0.7 V and PF1 trips, resettable. A **backwards WS2812 strip** trips PF3;
the strip is on its own. **J8 rail break-out is unprotected** — it is a bare
bench header, and a reversed supply clipped onto it lands directly on the
+5 V rail's loads. Treat J8 like any bare rail.

### 5 V sensor excitation

`+5VS` is the +5 V rail behind the `SENS_EN` load switch, `PF1` (200 mA hold
/ 400 mA trip polyfuse), a ferrite, and `D3` (SMAJ6.0A). A sensor wire
shorted to chassis or to battery trips the polyfuse and clamps the transient
without taking the board down — and a tripped rail can now also be cycled
from firmware instead of waiting for the polyfuse to cool.

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

| RANGE | BYPASS | Input range | Ratio | At the ADC | Clips at | Typical sensor |
|---|---|---|---|---|---|---|
| **A** | open | **0–5.0 V — ships like this** | 0.5769 | 2.885 V at 5.0 V in | 5.37 V in | MAP, TPS, wideband AFR, most 5 V sensors |
| open | **closed** | 0–3.3 V | 1.0000 | 3.300 V at 3.3 V in | 3.10 V in | 3.3 V-native sensor, ratiometric output |
| **B** | open | 0–16 V | 0.1673 | 2.677 V at 16.0 V in | 18.53 V in | Battery-referenced signals, 12 V switch inputs |

**All four channels ship in the 0–5 V position** — `RANGE` bridged 1–2,
`BYPASS` open — because that is what almost every automotive sensor is, and
because it means the firmware's `DIVIDER_GAIN` matches the board as delivered
rather than assuming a jumper somebody has to remember to fit. Cut the 1–2
bridge to move a channel to either of the other two rows.

The upper leg is the 1 k series resistor plus the 10 k, so 11 k against 15 k
gives 15/26 = 0.5769 on the 5 V range and 2.21/13.21 = 0.1673 on the 16 V one.

**These deliberately do not divide to 3.3 V.** The ESP32-S3 ADC tops out near
3.10 V at 12 dB attenuation, and a divider scaled to land exactly there has no
margin: a 5 V sensor rail is rarely exactly 5.000 V, and anything that
overshoots clips silently at full scale — which reads as a plausible-but-wrong
value rather than an obvious fault. At 0.5769 a 5 V channel does not clip until
5.37 V in. The 1:1 mode is the exception and has no headroom by design; it
assumes a sensor that genuinely cannot exceed 3.3 V. The BAT54S clamps protect
the pin either way, but clamping is protection, not measurement.

Source impedance at the ADC node is 11 k ∥ 15 k = **6.35 kΩ** on the 5 V range
(1.84 kΩ on the 16 V range). With the 100 nF filter cap that puts the −3 dB
point at about 250 Hz — ideal for AFR, temperature and pressure, and the limit
if you ever want something fast on a channel.

`PULLUP` is independent of the above: closing it puts 2.49 kΩ to `+5VS` on the
input node, turning the channel into a bias network for **2-wire NTC sensors**
(coolant, oil, air temp) or open-collector/switch-to-ground inputs. Leave it
open for anything that drives its own output.

Every channel lands on **ADC1** (`GPIO1`, `GPIO2`, `GPIO4`, `GPIO5`), which is
the half of the ESP32-S3 ADC that keeps working while WiFi is active. ADC2 is
unusable with WiFi up — that constraint drove the pin assignment.

The same four conditioned nodes also feed `U13`, an **ADS1115** (16-bit
delta-sigma, on the existing I²C bus at 0x48). The ESP32-S3's internal ADC is
only good for ±1–2 % even after calibration — ±0.2 AFR on a 0–5 V wideband
output — so firmware picks per channel: fast-and-rough on the internal ADC, or
slow-and-accurate (up to 860 SPS) on the ADS1115. The divider resistors are
0.1 % thin-film so the front end does not throw away what the converter buys,
and the note in the schematic records that the 1 k series resistor is part of
the divider chain — the exact scale factor is a firmware calibration constant.

`R77` (100 k) and `R64` (10 k) divide `+VBAT` by 11 onto `GPIO6` for
battery-voltage logging: 14.0 V reads 1.27 V, and the 36 V top of the input
range reads 3.27 V.

---

## 4. CAN

`U12` is a TJA1051T/3 — 5 V bus drive with a separate `VIO` pin tied to +3V3, so
the ESP32 sees 3.3 V logic with no level shifter. `S` is pulled low by `R54`
(10 k) so the transceiver comes up in normal mode with the MCU still in reset;
`GPIO21` can raise it for silent (listen-only) sniffing. `CAN_TX` has no
external pull — the TJA1051 pulls TXD high internally, so a floating pin at
boot is recessive and a bare board cannot jam the bus.

Bus side, in order from the transceiver out:

- `L3` common-mode choke — TDK ACT45B-510-2P-TL003, 51 µH, AEC-Q200. The
  footprint is project-local (`footprints/esp32autosport.pretty`) and its pads
  are renumbered so the symbol's winding 1–2 maps to the package.
- Split termination: `R55`/`R56`, two 60.4 Ω in series with `C28` 4.7 nF to
  ground at the midpoint. Split termination beats a single 120 Ω resistor
  because it gives the common-mode noise somewhere to go instead of
  reflecting it.
- `JP1` in series with the top half — **open by default: the board ships
  unterminated**. Bridge the pads only when this board is an end node on a bus
  of its own. Every vehicle bus, OBD-II diagnostics included, is already
  terminated at both ends, and a third 120 Ω across the pair takes it to about
  40 Ω and can stop it working. This deliberately departs from the Autosport
  Labs convention of shipping terminated, because the common case here is
  plugging into a car that is already correct.
- `D6`/`D7` SMAJ26CA bidirectional clamps to ground on each line.

CAN_H/CAN_L run to `J1` pins 3 and 4, so one 4-pin JST-PH carries power and bus
in a single harness.

### OBD-II and "K-DCAN"

A BMW/MINI K+DCAN cable carries **two unrelated buses**, and this board has
hardware for exactly one of them.

**D-CAN — yes, already.** Diagnostic CAN is ISO 15765-4: plain 500 kbit/s CAN
on OBD-II pins 6 and 14, which is what `U12` and the TWAI controller do. `J1`
maps onto an OBD-II plug with nothing in between — pin 16 to `J1.1`, pins 4/5
to `J1.2`, pin 6 to `J1.3`, pin 14 to `J1.4` — so one cable both powers the
board and connects it to the bus. What is left is software: ISO-TP
(ISO 15765-2) segmentation and whichever of UDS or KWP2000 the target speaks.

> **Termination is already off.** `JP1` ships open (§4), which is what a
> vehicle bus wants — it is already terminated at both ends, and a third
> 120 Ω across the pair drops it to ~40 Ω and can stop it working entirely.
> So there is nothing to cut before plugging into a car; bridge `JP1` only
> for a bench bus where this board is an end node. Note also
> that `CAN_S` (GPIO21) lets firmware pick listen-only for sniffing, so the
> board need never ACK a bus it is only observing.

**K-line — no, and it cannot be bodged.** ISO 9141-2 / KWP2000 is a single
bidirectional wire on OBD-II pin 7 idling at battery voltage and pulled to
ground by an open-collector driver. That needs a transceiver — an L9637D or
equivalent — and there is none on this board. Feeding it into an analog input
does not work either: on the 0–16 V setting the anti-alias filter rolls off at
891 Hz (§10), which destroys even 9600 baud, and it gives no way to transmit.
Routing it to `J7` is worse, since those are strapping pins and a K-line
idling high at boot would hold `IO46` in the wrong boot mode.

This matters for the **MINI R53** specifically: its instrument CAN carries
`0x316` at 500 kbit/s, which this board already reads and logs — but its
*diagnostics and flashing* are K-line at 9600 8E1, negotiating to 62500 baud
for flash reads. The shift light and telemetry work on this hardware. A
tuning or flashing tool does not.

Adding it needs an external L9637D module on the `J5` SPI header — the S3's
UARTs matrix onto any GPIO, so two of `GPIO40`–`42`/`47` become K-line TX/RX,
and `J5` supplies +3V3 and ground. The one wire `J5` cannot supply is the
transceiver's battery feed: `J8` breaks out +5 V, +3V3 and ground only, so
12 V has to come from `TP3` or upstream of `J1`. **For a rev C, an on-board
L9637D is the highest-value addition** — SO-8, about a dollar, two GPIOs and a
`+VBAT` tap — and it would make one board cover both halves of a K+DCAN cable.

---

## 5. microSD

Wired for **4-bit SDMMC** (`GPIO9`–`GPIO14`), not SPI — roughly 4× the write
throughput, which matters when logging a busy bus.

- 33 Ω series damping on CLK, CMD and D0–D3.
- 10 kΩ pull-ups on CMD and D0–D3, the value Espressif's SD documentation
  asks for. **These pull to `SD_VDD`, the switched rail, not to +3V3** —
  pull-ups to a permanent rail would back-feed a powered-down card through
  its ESD structures. The pull-up on D3 is also what selects SD mode over
  SPI mode when the card powers up. Card detect keeps a weaker 47 kΩ: it is
  a mechanical contact, not a bus line, so there is no reason to burn the
  standing current.
- `Q6`/`Q7` form a high-side switch on the card supply, driven by `GPIO7`, so
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
| 10, 11 | IO17, IO18 | `CAN_TX`, `CAN_RX` | TWAI |
| 23 | IO21 | `CAN_S` | Transceiver silent mode (low = normal) |
| 13, 14 | IO19, IO20 | `USB_DM`, `USB_DP` | Native USB |
| 31, 32 | IO38, IO39 | `I2C_SDA`, `I2C_SCL` | Qwiic header |
| 36, 37 | IO44, IO43 | `UART_RX`, `UART_TX` | UART0 header |
| 33–35, 24 | IO40–42, IO47 | `SPI_SCK`, `SPI_MISO`, `SPI_MOSI`, `SPI_CS` | SPI breakout |
| 25 | IO48 | `LED_DIN_MCU` | WS2812 data (via 5 V AHCT buffer → header) |
| 27 | IO0 | `MCU_BOOT` | BOOT button |
| 3 | EN | `MCU_EN` | RESET button |
| 15, 16, 26 | IO3, IO46, IO45 | — | Spare-IO header `J7`, each with a 10 k pull-down |
| 8 | IO15 | `PWR_FAIL` | Power-fail interrupt (high = harness below 11 V); also J7 pin 4 as a probe point |
| 9 | IO16 | `SENS_EN` | Sensor-rail (+5VS) enable, active high, off at reset; also J7 pin 5 |
| 28, 29, 30 | IO35, IO36, IO37 | — | **Unusable** — octal PSRAM |

`IO3`, `IO45` and `IO46` are strapping pins, and each carries a 10 kΩ
pull-down (`R36`–`R38`) chosen for its strapping role: `IO45` low sets
VDD_SPI to 3.3 V, `IO46` low selects normal boot, `IO3` low fixes the JTAG
source. The board therefore boots correctly with `J7` empty. What the
pull-downs cannot survive is something plugged into `J7` that actively
*drives* one of them high at boot — 10 kΩ loses to a driver — so do not hang
anything on `J7` that asserts a level before the MCU is out of reset.

### WS2812 shift-light header

`J6` (silk **WS2812**): `LED_5V` / `LED_DIN` / `GND`. `LED_DIN` is driven by a
`74AHCT1G125` powered from `+5V`, so the strip sees a real 5 V logic level.
Firmware bit-bangs / RMT on **GPIO48**. `LED_5V` is behind a 0.5 A polyfuse off
the board 5 V rail — enough for an 8-LED stick, not a full belly-band.

### SPI breakout

`+3V3` / `GND` / `SCK` / `MISO` / `MOSI` / `CS` on GPIO40/41/42/47. Intended for
an external MCP2515 (second CAN), CC1101 (433 MHz TPMS), or MAX6675, etc.

---

## 7. Review before layout

Resolved in the rev A review (2026-08, against datasheet SNVSAU4D and TI
drawing 4214849/B):

1. ~~RON placeholders~~ — computed from Eq. 12: **31.6 kΩ → 396 kHz** on the
   5 V rail, **20.5 kΩ → 402 kHz** on the 3.3 V rail. Minimum on-time at the
   64.5 V clamp is 196 ns / 128 ns, both far above the 50 ns floor.
2. ~~Footprint~~ — the original `EP2.41x3.3mm` exposed pad was *smaller* than
   the DDA0008B pad itself (max 2.71 × 3.4 mm). Now on
   `EP2.95x4.9mm_Mask2.71x3.4mm_ThermalVias`, which matches TI's example land
   pattern exactly (2.95 × 4.9 copper, 2.71 × 3.4 mask-defined opening).
3. ~~Load-dump assumption~~ — confirmed: the target vehicle's alternator never
   exceeds 20 V, so the SMCJ40CA's 40 V standoff has comfortable margin and the
   centralised-suppression assumption in §2 holds. Simulation later put a
   number on what happens if it does not: see §10.
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

5. ~~ESP32-S3 ADC linearity~~ — resolved for the AFR use case: `U13`, an
   onboard ADS1115 (16-bit, I²C), now shares the four conditioned input
   nodes, and the divider resistors are 0.1 % thin-film. See §3.

6. ~~Layout rules~~ — the board is laid out now (§9). The switching-regulator
   rules this item called for are all in `gen/generate_pcb.py` and
   `gen/route_bucks.py` as fixed placements: tight input-cap loops on
   `U3`/`U4` with `C4` at the VIN pin, a solid GND plane under the whole
   ideal-diode block, and the CAN choke beside `J1`.

Still open for a second pair of eyes:

7. `+5VS` accuracy (§2) if ratiometric sensors are used. (AFR is absolute, not
   ratiometric, so this does not affect the wideband channel.)
8. **Nothing has been fabricated.** Every check here is a software check —
   ERC, DRC, datasheet review, sourcing. No one has powered one on.

Found later, during the pin audit (§6), and worth knowing rather than fixing:

9. The WS2812 buffer input floats at boot. `GPIO48` reaches `U9` pin 2 through
   a 33 Ω series resistor with no pull-down, so that CMOS input is undefined
   from power-on until firmware drives it — a brief random flicker on the
   strip and a milliamp or so of shoot-through in `U9`. Harmless, and cheaper
   to fix in firmware (drive `GPIO48` low first) than to respin for.
10. `J7` (Spare IO) has three signals and no ground pin. Use `J8`'s ground two
    headers along. Also note all three of its pins are strapping pins — see
    §6.

### Sourcing (JLCPCB assembly)

**Every part number in `fab/bom.csv` was re-checked against the live LCSC and
JLCPCB catalogues on 2026-08-12** — all 23, one at a time, confirming the MPN,
the package and that the part exists. Two were wrong:

| Was | Resolved to | Should be |
|---|---|---|
| `C8544` for `Q5` MMBT3906 | **S9018, an NPN** from JSCJ | `C84105` — ST(Semtech) MMBT3906, PNP, SOT-23 |
| `C7975` for `U9` SN74AHCT1G125DBVR | **LMV324IPWRG4**, a quad op-amp in TSSOP-14, out of stock | `C7484` — TI SN74AHCT1G125DBVR, SOT-23-5 |

The `Q5` one was live in the BOM and would have assembled an NPN where the USB
over-voltage cutoff needs a PNP — protection that silently does not work. The
`U9` one was only ever in this table; the generator already carried a comment
saying `C7975` was wrong and left the field blank, and that fix never reached
the prose. Both are corrected, and `L2`'s 22 µH inductor picked up a verified
number (`C340243`) on the way past.

The critical semiconductors, all confirmed present and in stock:

| Part | MPN | JLC # | Notes |
|---|---|---|---|
| ESP32 module | ESP32-S3-WROOM-1-N16R8 | C2913202 | 13 087 in stock |
| Buck ×2 | LM5164DDAR | C477928 | Non-automotive variant — the Q1 is backordered at DigiKey and nearly dry at Mouser; same silicon, minus AEC-Q100 |
| Ideal-diode ctrl | LM74700QDBVRQ1 | C2941042 | SOT-23-6, 3 987 in stock |
| Reverse-batt FET | IPD068N10N3G | C88066 | Replaces PSMN4R3-100BSE, **which does not exist** (nearest real Nexperia part is a D2PAK); TO-252, drops into the same footprint, 6.8 mΩ costs ~3 mV at load |
| CAN transceiver | TJA1051T/3 | C58988 | NXP original, 11 085 in stock; the BOM carries the `,118` reel suffix |
| Precision ADC | ADS1115IDGSR | C37593 | VSSOP-10, 24 565 in stock. Non-automotive variant — the Q1 needs a manufacturer quote at DigiKey |
| WS2812 buffer | SN74AHCT1G125DBVR | **C7484** | TI, SOT-23-5. **Not C7975** |
| USB OVP transistor | MMBT3906 | **C84105** | ST(Semtech), PNP. **Not C8544** |
| Buck inductors | ASWPA8050S330MT / S220MT | C340244 / C340243 | Sunlord automotive series, 8×8 mm, 3 A Isat. JLCPCB-catalogue parts — the LCSC product page 404s, which is normal for JLC-only stock |

Nine lines still have no part number and are picked by hand at order time: the
`330 µF`/`100 µF` 100 V electrolytics, the green LED, `PMEG4010`, both
polyfuses, `DMG2301L`, the `TL3342` buttons, `TLV431ASN1T1G`. The 0.1 %
thin-film divider resistors are commodity and auto-match on value + package.


---

## 8. Files

| Path | What |
|---|---|
| `esp32s3-can-sd-logger.kicad_pro` | KiCad project. **Owns the netclasses** — Power 0.5 mm / CAN 0.25 mm — and the 0.2 mm min-drill rule |
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
| `gen/build_board.py` | One-command board build: places, stitches, routes, verifies |
| `gen/generate_pcb.py` | Board generator — run with KiCad's bundled Python |
| `gen/stitch_planes.py` | Via from every GND / +3V3 pad to its plane |
| `gen/export_dsn.py` | Specctra export with the inner layers locked as planes |
| `gen/import_ses.py` | Imports the router's session and refills the pours |
| `gen/finish_routing.py` | Ties duplicated connector pins; reports leftovers |
| `gen/maze_route.py` | Rip-up-and-retry router for what the autorouter fences in |
| `gen/tidy_silk.py` | Shrinks and re-places reference designators; no copper |
| `gen/netclasses.py` | Per-net track and via sizes, read from the project file |
| `gen/export_fab.py` | Gerbers, drill, JLC assembly BOM and position files |
| `gen/export_plots.py` | Rebuilds everything under `plots/` — run it after any change |
| `gen/route_bucks.py` | Places buck islands + routes SW/VIN critical copper |
| `esp32s3-can-sd-logger.kicad_pcb` | Generated 4-layer board, fully placed and routed |
| `footprints/esp32autosport.pretty` | Project footprints (TDK ACT45B CAN choke) |
| `plots/board-routed.png` | Render of the finished board, top |
| `plots/board-back.png` | Render of the finished board, bottom — routing only, no parts |
| `fab/` | Gerbers, drill, JLC BOM and pick-and-place (generated) |
| `docs/PREORDER.md` | Pre-order checklist: what is checked, what is not, what to do at upload |
| `docs/BRINGUP.md` | Staged first-power checklist, tied to the study values |
| `firmware/esp32_shiftlight_wideband/` | Firmware for this board: shift light, wideband BLE bridge, microSD logger |
| `firmware/vendor/` | Verbatim copy of the R53 sketch — the control build, do not edit |
| `gen/simulate_firmware.py` | Firmware-in-the-loop studies: runs the sketch and feeds it inputs |
| `fwsim/` | The host shims and board model those studies run on |
| `sim/fw/` | Per-run scenario, trace, serial log and findings (generated) |

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
node-for-node, and runs ERC where available. It currently passes on **107 nets /
168 component instances**.

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

## 9. Board layout and routing

The board is generated the same way the schematic is. One command does the
whole chain:

```sh
python gen/build_board.py --freerouting freerouting.jar
```

| Stage | Script | What it does |
|---|---|---|
| 1 | `generate_pcb.py` | Places every part, draws the outline, planes, keepouts |
| 2 | `route_bucks.py` | Hand-shaped copper for the two buck power loops |
| 3 | `stitch_planes.py` | A via from every GND / +3V3 pad down to its plane |
| 4 | `export_dsn.py` | Specctra export, **In1/In2 marked as power planes** |
| 5 | freerouting | Routes the remaining signals on F.Cu / B.Cu |
| 6 | `import_ses.py` | Brings the routes back, refills the pours |
| 7 | `finish_routing.py` | Ties duplicated connector pins, reports leftovers |
| 8 | `maze_route.py` | Rips up and re-routes whatever is still open |
| 9 | `tidy_silk.py` | Shrinks and shuffles the reference designators |

84 x 100 mm, 4 layers: **F.Cu / GND / +3V3 / B.Cu**, JLCPCB JLC04161H-7628
stackup, 0.2 mm minimum drill (the LM5164 thermal vias), 0.5 mm copper-to-edge
enforced by a rule-area band around the perimeter.

Five things in that pipeline are worth knowing, because every one of them
was learned the hard way:

**The inner layers must be declared `power` in the DSN.** KiCad exports every
copper layer as `signal`, so an autorouter cheerfully runs traces straight
through what are meant to be solid pours -- the first attempt here had `IO45`
and `SPI_MOSI` crossing the middle of the +3V3 plane, which fragmented it and
orphaned everything that depended on it. `export_dsn.py` rewrites those two
layer declarations, and routing then stays on the outer layers where it
belongs.

**Plane pads are stitched before routing, not after.** A pad on GND or +3V3
does not want a routed track, it wants a via next to it; doing all 113 of them
up front means the router treats them as obstacles and has only signals left to
solve. 110 get a via; the three that do not are the exposed thermal pads of
`U3`, `U4` and the module, which the pour bonds to directly. `stitch_planes.py` places each via with real point-to-rectangle and
point-to-segment distance checks -- bounding boxes are far too pessimistic in a
dense field and refuse room that is really there.

**The last few connections need rip-up, not a better search.** An autorouter
takes the easy nets first and fences the awkward ones in, so what it leaves
behind has no path at all -- `SD_CLK` ran straight under the ESP32 module's
pads 20 and 21 and walled both of them off, and at 1.27 mm pitch nothing slips
past sideways. `maze_route.py` grids each copper layer at 0.1 mm, searches both
layers at once with Dijkstra, and when that fails works out which nets are
pressed against the dead end, tears them out, routes the trapped net through
the space they leave, and re-routes them afterwards. Three details make it
work rather than thrash:

- **A rip removes tracks, never pads.** Ignoring a ripped net's pads too lets
  the router lay copper straight across them.
- **Blockers are ranked from the tighter pocket.** Flood from both ends and
  score the smaller one -- an end that can reach half the board has a frontier
  thousands of cells long, and the innocent nets along it drown out the few
  actually doing the trapping.
- **Connections are to a net, not a pad.** USB-C carries D- on both A7 and B7;
  A7 escapes only down a 16 um corridor, finer than any grid, while B7 opens
  onto the whole board. Aiming at the net's existing copper turns an
  impossible route into an easy one.

Nets with more than four pads are never ripped: a two-pad signal comes back as
one trace, but a rail that daisy-chains six decoupling caps comes back as six
separate connections, each able to fail on its own.

**The netclasses live in the `.kicad_pro`, and two things used to lose them.**
This one is worth spelling out because it fails silently -- narrow track is
legal track, so nothing errors, nothing warns, and the board looks finished.

`generate_pcb.py` builds its board with `CreateEmptyBoard()`, which knows only
the Default class; saving that board rewrote the project file from the board's
own settings and threw Power and CAN away. Everything downstream then routed
at the default width, **including the 1 A rails** -- 0.2 mm copper, which is
1.01 A at a 20 °C rise and 147 mV of drop over a 60 mm run. On `+5VS`, the
sensor excitation rail, that droop lands directly on every ratiometric reading
the 0.1 % dividers exist to protect. The generator now lifts `net_settings` out
before the save and puts it back after, and reports what it restored.

The second loss was `net.GetNetClass()`, which returns an undecorated
SwigPyObject in these scripts and raises `AttributeError` on any method. A
`try/except ... return DEFAULT` around it turns straight into 0.2 mm power
rails. `gen/netclasses.py` reads the classes and their glob patterns out of the
project file instead, and both routers size every track and via from it.

**Pads are not their bounding boxes.** The router's occupancy grid may use
bounding boxes -- being too cautious only costs it room -- but deciding *where
a trace may end* cannot. A 1.70 mm round header pad has a 1.70 mm square
bounding box whose corners are ~0.35 mm off the copper, so 23 % of the apparent
landing area is bare laminate. Ending there produces a connection that is not
one, which is how every header on J3/J4/J5/J7 stayed open while the router
insisted it had routed them. The island builder asks `pad.HitTest()` instead,
which is exact for round, oval and rounded-rectangle pads alike.

### Placement

- **Left edge:** sensor harness (`J10`) and power/CAN harness (`J1`) -- one
  wiring direction toward the car.
- **Top:** four identical analog channel columns with their solder jumpers
  facing up for probing; ESP32 module top-centre with the **antenna
  overhanging the board edge**, so the module's own RF keepout falls entirely
  off-board; Spare-IO and SPI headers to its left.
- **Right edge:** USB-C and microSD for bench access, then RESET/BOOT and the
  status LEDs.
- **Middle:** the two stacked buck islands, each with its input caps at the
  VIN pin and its RON/FB/ramp network alongside.
- **Bottom edge:** battery front end beside `J1`, and the UART0 / I2C / rail /
  WS2812 headers along the edge.

The USB-C receptacle sits ~2.5 mm inboard of flush rather than overhanging.
Its A/B duplicate pins (D+ on A6 and B6, D- on A7 and B7) have to be tied
together on copper for a reversible cable, and at 0.5 mm pitch that needs a
via channel between the pad row and the edge keepout. The case opening sets
plug access anyway.

## 10. Simulation and physical audit

Two scripts check what ERC and DRC structurally cannot. ERC asks whether the
netlist is self-consistent and DRC asks whether the copper is manufacturable;
neither asks whether the circuit works, or whether the board survives a car.

```
python gen/simulate.py                          # ngspice: the circuits
"…/KiCad/9.0/bin/python.exe" gen/audit_pcb.py   # pcbnew: the layout's physics
"…/KiCad/9.0/bin/python.exe" gen/overstress.py  # closed-form worst case
```

`simulate.py` needs ngspice, numpy and matplotlib and runs on any Python 3. It
writes its decks, data and plots to `sim/`, so every number below can be
re-derived rather than taken on trust.

**What it confirms.** The divider maths in §3 is right to three digits: 5.0 V
in gives 2.859 V at the ADC on the 0–5 V setting and 16.0 V gives 2.670 V on
the 0–16 V setting, against the 2.88 and 2.67 the schematic notes predict.
Both converters run well inside their inductors — 0.27 A of ripple on a 2.13 A
peak against a 3.0 A saturation rating for +5 V, 0.35 A on 1.18 A against
3.4 A for +3V3 — with 26 mV and 44 mV of output ripple at 13.5 V in, computed
against the output MLCCs' biased capacitance rather than their printed value.
ISO 7637-2 pulses 2a, 3a and 3b never reach the clamp at all: the 100 µF bulk
and the ferrite swallow them and the TVS absorbs a measured 0 J.

**What it flags.**

1. **Pulse 1 still browns the board out, now for ~5.6 ms.** A -100 V, 2 ms
   transient turns Q1 off, and the board runs on the 760 uF bank at full
   load until the harness recovers. The ride-through caps shrank the outage
   from 8.2 ms but cannot absorb it: the pulse actively holds the harness
   at -47 V for 2 ms, and the bank spends most of its charge riding that
   out. Nothing is damaged -- Q1 stands off 59 V against its 100 V rating --
   and the power-fail detector fires on the way down, so firmware sees it
   as an ordinary power cut and closes the file. A reset mid-drive on a
   full ISO pulse 1 is the accepted outcome. **This is a decision, not a
   defect.**

2. **An unsuppressed load dump destroys the front end.** At the pulse 5b
   level IV of 87 V the SMCJ40CA absorbs 219 J against roughly 6.7 J of
   capability at that pulse width. The suppressed 35 V case is a non-event:
   31 V at the harness, the TVS never conducts. So D1's note claiming it
   "absorbs ISO 7637-2 pulse 5b load dump" holds only for a vehicle with a
   centrally suppressed alternator — every modern one, but an assumption the
   board depends on rather than a property it has.

3. **The anti-alias corner moves with the range jumper**: 261 Hz on the 0–5 V
   setting, 891 Hz on 0–16 V, 1.65 kHz bypassed. The 100 nF sees a different
   source impedance in each configuration, so the filter is whatever the
   divider leaves it. Two of the three sit above the 430 Hz Nyquist of the
   ADS1115 at its fastest rate.

4. **A 36 V input with BYPASS closed backfeeds +3V3.** Each channel pushes
   32 mA through its BAT54S into the rail, 129 mA across four. Harmless while
   the ESP32 is drawing its share; a rail-pumping hazard on a sleeping or
   unpowered board with a live loom.

5. **A non-compliant USB brick used to backfeed the 5 V rail — now cut
   off.** VBUS reaches +5V through PF2 and D5, and the buck can source but
   not sink, so a brick that negotiated 9 V put ~8.4 V on the rail — over
   the TJA1051's 6 V absolute maximum. Fixed with a TLV431 + AO3401 series
   cutoff tripping at 5.77 V (the same recipe as the sensor-rail switch):
   re-simulated, a compliant 5 V source passes at 4.99 V, the switch opens
   at 5.95 V of brick, and the rail never exceeds 5.2 V from anything up
   to 14 V. Off-the-shelf OVP parts were rejected first — the TPS25200
   class cuts off near 7 V, which is no protection for a 6 V limit.

**Second-round studies** (also in `gen/simulate.py`):

- **Battery-connect inrush**: the 760 µF bank charges through Q1's body diode
  before the LM74700 wakes — **44.0 A peak, 0.274 A²s** of surge and 11.7 mJ
  in the diode over 0.80 ms. The fuse's I²t burden is linear in C, so this is
  the price of the bigger bank: it was 0.20 A²s at 540 µF. A 2 A nano fuse is specified around 1 A²s; verify the exact
  figure on the datasheet at order, but the margin is ~3.6×.
- **Engine crank (ISO 16750-2 profile)**: warm (6.0 V dip) and cold (4.5 V
  dip, 15 ms) both ride through — the bank carries the bottom of the dip and
  the 6.5 V starter plateau sits above the converters' dropout. `PWR_FAIL`
  asserts exactly once per crank; the 1 M hysteresis prevents chatter, so
  firmware sees one clean "close the file" event per start.
- **Monte Carlo tolerances** (20 000 samples): power-fail trip lands in
  10.72–11.29 V at 99.8 %; the 0.1 % analog divider stack holds gain error
  to ±0.08 %; +5 V spans 4.85–5.15 V and +3V3 spans 3.19–3.38 V worst-case;
  the battery monitor's 1 % divider is ±1.9 % — calibrate it in firmware.
  Ride-through on a **flat battery** (bank starting at the 10.7 V trip point
  rather than 13.5 V) is 78 ms median and **51 ms at the 0.1 % corner** with
  caps −20 % and load +40 %. That is the number firmware has to fit inside,
  not the 154 ms of §2 — which assumes a healthy battery.

**What it does not cover.** The LM5164's control loop. It is a constant-on-time
part with an encrypted TI model, so the buck deck drives the power stage at an
ideal duty cycle: it answers what the passives have to survive, not whether the
loop is stable or how it recovers from a load step. That needs TI's PSpice
model.

`audit_pcb.py` reads the routed board and checks rail current capacity against
IPC-2221, copper inside the antenna keepout, how far each bypass capacitor sits
from the pin it bypasses, dissipation against the copper and thermal vias under
each converter, and drilled holes that overlap. That last check exists because
DRC only compares holes on *different* nets: two vias of the same net can sit
on one point and pass, and the fab then drills it twice.

The decoupling check is what found the two placement defects fixed here — the
+3V3 converter had no input capacitor of its own and was reaching 12.6 mm
across to the +5 V island's pair, and the CAN transceiver's +5 V bypass sat
8.8 mm from its supply pin. Both now sit at their pins, pinned by `PIN_FIXED`
in `gen/generate_pcb.py` so the zone packer cannot drift them again.

### Firmware in the loop

A third script runs the **firmware itself** and feeds it inputs:

```
python gen/simulate_firmware.py
```

The sketch is compiled unmodified for the PC against shims for Arduino,
FastLED, NimBLE, Wire, SD_MMC and TWAI, on a virtual clock, over a model of
this board — which GPIO is really wired to what, the divider ratios, the
switched sensor rail, the ADC ceiling, the power-fail detector and the
ride-through budget. Scenarios inject CAN frames, sensor voltages, BLE
connect/subscribe events and harness collapse. `fwsim/README.md` documents it,
including what it cannot see.

It exists because ERC, DRC and ngspice all pass on a board whose firmware
reads the wrong pin. A wrong GPIO number is not a compile error; it is a board
that boots, prints happy status lines, and measures nothing.

**The control.** `firmware/vendor/` holds a verbatim copy of the shift-light +
wideband sketch running in the MINI today. Study 1 requires the harness to
agree that it is clean on its own hardware before any claim it makes about
this board is worth reading.

**What it found.** Run unmodified on this board, that known-good firmware has
five defects, none of them bugs in it — every one is a pin that means
something different here:

| | |
|---|---|
| WS2812 data | drives `GPIO4`, which is `AIN3` here. The strip is on `GPIO48`; the shift light is dark for the entire run |
| TWAI TX/RX | `GPIO5`/`GPIO6` are `AIN4` and `VBAT_SNS` here. No frame ever arrives, and the CAN controller drives two analog inputs |
| I²C (ADS1115) | `GPIO7`/`GPIO8` are `SD_PWR_EN` and `SD_CD` here. Selecting the 16-bit converter from the phone toggles the microSD supply |
| `DIVIDER_GAIN` | 2.0 is the reciprocal of that board's 10k/10k. This front end is 0.5769, so every wideband reading is **17 % high**, and the sketch's own 5.5 V clamp then silently truncates the top of the range |
| `+5VS` | switched and off at reset. Nothing on the sensor harness reads anything until `SENS_EN` goes high, which that firmware never does |

Plus the power-fail contract above, which that board has no signal for.

**The port.** `firmware/esp32_shiftlight_wideband/` is the same firmware with
those fixed, plus SD logging and the `PWR_FAIL` path. It passes all 31 checks.
Study 11 is what sized the shutdown: it swept card latency and caught a
redundant `flush()` before `close()` in the shutdown path — two full card
writes instead of one — which had cut the tolerable card latency roughly in
half. `sim/firmware.png` plots the shift light tracking CAN and the
ignition-off sequence.

### External DFT review

An automated design-for-test review (tomachie, 85/100) was run on the design.
Three findings became hardware: **test points on SD_CLK/SD_CMD** (the SD bus
is the most likely bring-up debug target and was otherwise unprobeable),
**a second USBLC6 on the USB-C CC pins** (first contacts to mate on every
plug insertion, previously bare), and **two SRV05-4 arrays on the card-slot
contacts** (swapped by hand constantly; only the ESP32's ~2 kV pin diodes
stood behind them).

The rest was triaged and declined deliberately: its "unconnected J9 pin 4"
is a pin-type classification of the FET-switched SD_VDD (the `PWR_FLAG`
case, verified connected on copper); PWR_FAIL/SENS_EN/I2C/SPI/UART "missing
test points" are already on 0.1" headers, which beat test points; IPC-7351B
footprint renaming, ATE isolation resistors on the buttons, bed-of-nails
coverage scores and boundary scan are production-line concerns that a
prototype run priced for flying-probe does not buy anything from.

### Bring-up

[`docs/PREORDER.md`](docs/PREORDER.md) is the checklist to work through
**before** uploading `fab/` — what has been checked, what has not, and the
handful of things only JLC's own DFM report can tell you.

[`docs/BRINGUP.md`](docs/BRINGUP.md) is the staged first-power checklist —
every step carries the expected value and points back at the simulation
study that derived it. `fab/board.step` is the full 3D model for designing
the dash enclosure around.

### Ordering note: BOM part-number coverage

`fab/bom.csv` is in JLCPCB's format (`Comment,Designator,Footprint,JLCPCB
Part #`), and BOM/CPL designators are machine-verified to agree. 23 lines
carry LCSC numbers **checked against the live catalogue on 2026-08-12** (see
below); 40 blank lines are plain 0805/1206 R/C that JLC's order flow
auto-matches from value + package. **Nine extended lines must be matched by
hand in the order UI** (search the Comment, pick the stocked equivalent): the
330 uF/100 V and 100 uF/100 V electrolytics, the green LED, PMEG4010, the
0466 2 A fuse, both polyfuses, DMG2301L, the TL3342 buttons and
TLV431ASN1T1G. Never guess a
C-number into the file -- a wrong part number assembles the wrong part.

## 11. Handoff — remaining work

**Schematic is done.** Do not redesign power/CAN/analog unless a datasheet
conflict appears. Edit `gen/generate_schematic.py` only; then
`python gen/generate_schematic.py && python gen/validate.py`.

**The board is finished.** 341 of 341 connections routed, **zero DRC errors,
zero unconnected**: 1432 tracks, 249 vias, solid GND and +3V3 pours, every net
at its netclass width. Rebuild it any time with `python gen/build_board.py --freerouting freerouting.jar` —
placement, plane vias and the buck loops are deterministic, so the only thing
that varies between runs is the autorouter's solution for the signals, and
stage 8 closes out whatever it leaves.

The 105 remaining DRC findings are all `warning`-severity silkscreen overlap
on a board with 206 parts in 84 x 100 mm. Reference designators are at 0.8 mm,
the floor for both the board's own text rule and JLC's silkscreen, and values
are hidden. Nothing here affects fabrication or assembly.

### Ordering it from JLCPCB, step by step

Everything you upload is already in `fab/`. Regenerate it any time with
`python gen/export_fab.py`.

**1 — Upload the board.** At [jlcpcb.com](https://jlcpcb.com), *Order now*,
then *Add gerber file* and give it `fab/esp32s3-can-sd-logger-gerbers.zip`.
It will read 84 x 100 mm and 4 layers off the files.

**2 — Board options.** Everything not listed here can stay at its default.

| Option | Set it to | Why |
|---|---|---|
| Layers | 4 | detected automatically |
| PCB Qty | 5 | the cheapest quantity, and this is a first run |
| Thickness | 1.6 mm | what the stackup assumes |
| Impedance control | **No** | nothing here is fast enough to need it — see below. Leaving it off also keeps you on JLC's default 4-layer 1.6 mm stackup, JLC04161H-7628, which is what the board is built to anyway |
| Surface finish | **ENIG** | worth the few extra dollars — HASL leaves an uneven surface, and the USB-C and the module are 0.5 mm pitch |
| Outer copper | 1 oz | |
| Remove order number | "Specify a location" or Yes | otherwise they print their job number wherever they like |

**Why no impedance control.** It is the option people reach for on a
4-layer board, and this one does not need it. The ESP32-S3's USB is
**full-speed only** (12 Mbps, no high-speed PHY), and at those edge rates a
trace has to run past roughly 100 mm before it behaves like a transmission
line — `USB_DM` is 30 mm and `USB_DP` 27 mm. CAN at 1 Mbps has edges measured
in tens of nanoseconds against a 49 mm run, and the SD bus is 40 MHz over
10 mm with 33 Ω series damping already fitted. There is no RF on the board;
the antenna is inside the module. Turning impedance control on would cost more
and pin the stackup you were going to get regardless.

**3 — Turn on assembly.** Switch *PCB Assembly* on. Assembly side **Top**,
quantity 5, tooling holes *Added by JLCPCB*. Every part is on the top face.

**4 — Upload the parts files.** BOM is `fab/bom.csv`, CPL (they may call it
"pick and place") is `fab/positions.csv`. Both list the same **170
designators** — they have to, because JLC pairs them up by designator and
anything present in one and missing from the other simply does not get
assembled. The eight through-hole connectors are deliberately in neither
file; you solder those yourself, so there is nothing to mark "do not place".

**The BOM's part-number column must be headed `JLCPCB Part #`.** That is the
only name JLC reads. Head it `LCSC` and the column is silently ignored, every
line falls through to the fuzzy text matcher, and you get offered a mechanical
limit switch for a Schottky diode named "40V 1A" and a real WS2812 LED for a
header named WS2812. Both happened.

**5 — Match the parts.** **23** lines arrive with a part number and match
themselves; the other **49** you pick on this screen. Most carry a specific
manufacturer part number and should match on it — the rest are generic
passives where any equivalent will do.
Take *Basic* parts over *Extended* where the value and package match; Extended
parts add a setup fee each. Two things to hold to: the 0.1 % divider resistors
(`R43`–`R62`) must stay **0.1 %**, that tolerance is the whole point of the
analog front end, and `C3`/`C6`/`C7` (the bulk and ride-through electrolytics) must stay **100 V** rated.

**6 — Check the placement preview, carefully.** This is the step that bites
people. JLC's library orients some packages differently from KiCad, so
polarised parts can come back rotated 180°. Look hard at every diode (`D1`–
`D14`), the electrolytics `C3`/`C6`/`C7`, the LEDs, and the ICs, and correct any that
face the wrong way in their previewer. Everything else is symmetrical and
cannot go on backwards.

**7 — Order.** Roughly two weeks including assembly.

### The eight parts you solder yourself

JLC places all 170 surface-mount parts. These eight are through-hole, and
through-hole assembly is a separate, pricier service — it is easier to buy
them and solder them yourself. They are large, widely spaced, and a good
first soldering job.

| Ref | Part |
|---|---|
| `J1` | 4-pin JST-PH — power and CAN harness |
| `J10` | 8-pin JST-PH — sensor harness |
| `J3` `J4` `J5` `J6` `J7` `J8` | 0.1" pin headers — UART0, I²C/Qwiic, SPI, WS2812, spare IO, rail break-out |

The mounting holes, the 13 solder jumpers and the 7 test points are bare
copper, not parts — nothing to fit.

### After the boards arrive

Before connecting anything to a car: put a bench supply on `J1` at **12 V with
the current limit set to 100 mA**, and check `TP3` (+VBAT), `TP4` (+5 V),
`TP5` (+3V3) and `TP6` (+5VS) with a meter, against ground on `TP7`.
(`TP1` and `TP2` are the converters' power-good outputs, not rails — both
should read high once the rails are up. The full map is `TP1` PG_5V, `TP2`
PG_3V3, `TP3` +VBAT, `TP4` +5V, `TP5` +3V3, `TP6` +5VS, `TP7` GND, `TP8`
SD_CLK, `TP9` SD_CMD, `TP10` CAN_H, `TP11` CAN_L.) If a rail is wrong or the supply
hits its limit, nothing is damaged. Then set the jumpers for your sensors
per the table in §3 if you need something other than the default. The `RANGE`
jumpers ship **bridged 1-2, the 0–5 V range**, which is what almost every
automotive sensor wants and what the firmware's `DIVIDER_GAIN` assumes.

### This board has never been fabricated

Rev B has been checked as hard as software can check it — ERC, DRC, a
datasheet review that caught five real defects (§7), and part-by-part JLC
sourcing — but no one has yet held one. Treat the first order as a
prototype run: build a few, not fifty.

**Firmware exists now** — `firmware/esp32_shiftlight_wideband/`, ported from
the R53 shift-light sketch and passing all 31 firmware-in-the-loop checks
(§10). It covers TWAI, the WS2812 header on GPIO48, the wideband on the
internal ADC and the ADS1115, microSD logging and the `PWR_FAIL` shutdown
path. The GPIO map in §6 is still the contract, and `fwsim/` now enforces it.

**Still out of scope:** the SPI client breakout, the three spare-IO pins, and
anything the simulator explicitly cannot see — the radio, USB, FreeRTOS
scheduling and flash wear (`fwsim/README.md`). None of it has run on real
silicon; the firmware has never been flashed to a board, because no board
exists yet.
