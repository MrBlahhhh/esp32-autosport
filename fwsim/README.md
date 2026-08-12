# fwsim — running the firmware, not just the circuit

`gen/simulate.py` answers what the **circuit** does. This answers what the
**firmware** does on top of it.

That is a different question, and on a board whose GPIO map is not the one the
code was written against it is a more dangerous one. A wrong pin number is not
a compile error. It is a board that boots, prints cheerful status lines once a
second, and measures nothing.

```sh
python gen/simulate_firmware.py                 # everything
python gen/simulate_firmware.py --only port     # one study
python gen/simulate_firmware.py --sketch r53    # just the original
```

## How it works

The sketch is compiled **unmodified** for the host against shims in `shim/`
that implement the Arduino, FastLED, NimBLE, Wire, SD_MMC and TWAI calls it
makes. Under the shims sits a model of the board. `runner.cpp` calls `setup()`
once and `loop()` until the scenario ends.

Two ideas carry the whole thing.

**Time is virtual.** `delay(20)` does not sleep, it advances a counter, and so
does every modelled cost: a WS2812 refresh, an I²C transaction, an ADC
conversion, a card flush. A minute of driving runs in milliseconds and runs
identically every time. It also means the numbers are only as good as those
costs — they are listed in `sim.cpp` and are the first thing to argue with if a
timing result looks wrong.

**The board is a model, not an assumption.** A GPIO is not a number, it is
whatever the schematic hung on it. Reading an ADC on a pin this board uses as
the microSD supply enable does not return a value, it returns a fault. Two
profiles are built in, both from README §2, §3 and §6:

| Profile | What it is |
|---|---|
| `s3zero` | Waveshare ESP32-S3-Zero, SN65HVD230, 10k/10k on the wideband — what the R53 sketch was written for |
| `autosport` | this board |

`s3zero` exists to be the control. Before the harness is allowed to say
anything about the new board, study 1 requires it to agree that the shipping
R53 firmware — known-good, running in a car right now — is clean on its own
hardware. A harness that fails that check is broken, whatever else it reports.

## Scenarios

A scenario is a line-based text file. Bare directives configure the run; lines
beginning `@<ms>` are events applied at that moment in simulated time.

```
board autosport
duration 3000
trace 2                 # trace sample period, ms

@0    vbat 13.8         # harness volts; drives PWR_FAIL and the ride-through
@0    sensorrail 1 5vs  # channel 1 is excited from the switched +5VS rail
@0    range 1 r5v       # jumper: bypass | r5v | r16v
@0    canid 0x316
@0    canrate 100       # frames/s from the generator, 0 stops it
@0    rpm 3000          # what the generator encodes
@500  ble connect
@600  ble subscribe 1
@1500 vbat 0.0          # ignition off
```

Other commands: `canframe <id> <dlc> <bytes...>` injects one raw frame,
`busoff` forces the bus-off state, `sensor <ch> <volts>` sets a source,
`ads 0|1` fits or removes the ADS1115, `ble hwmode <n>` / `ble disconnect` /
`ble initfail 1`, `sdflush <ms>` and `sdstall 0|1` set card latency.

Each run writes four files to `sim/fw/`: the scenario, a `.csv` trace, a `.log`
of the firmware's own serial output with virtual timestamps, and
`.faults.txt`. Every number in the report can be re-derived from them.

## Findings

Faults are recorded once per (code, message) pair, so a bug inside a 20 ms loop
produces one line and not three thousand. Severity is `ERROR` (this does not
work on this board), `WARN` (works, but is storing up trouble) or `note`.

The runner exits non-zero if any `ERROR` was recorded, so a scenario can be a
CI gate.

## What it does not cover

The shims model the **API contract**, not the implementation. A bug inside
NimBLE will not appear here, and neither will a stack overflow, a heap
fragmentation failure, an RMT peripheral conflict, FreeRTOS scheduling, flash
wear or anything about the radio. There is no Xtensa instruction set involved —
this is the sketch's logic compiled for x86.

It answers questions about the firmware's own logic and its fit to the
hardware. For anything below that line the answer is still a board on a bench.
