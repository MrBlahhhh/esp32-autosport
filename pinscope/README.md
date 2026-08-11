# Pinscope inputs

[Pinscope](https://github.com/Faradworks/Pinscope) is a datasheet-backed
schematic reviewer: it parses a netlist and BOM into a design graph, pulls pin
tables and limits out of the manufacturers' datasheet PDFs, and reviews each IC
against its datasheet *and* its context in this circuit. It is the one class of
check nothing else here does — KiCad's ERC knows the netlist is self-consistent,
but has no idea whether pin 5 of `U7` is allowed to sit at 3.3 V.

These two files are what it takes as input. Regenerate them whenever the
schematic changes:

```sh
kicad-cli sch export netlist --format pads \
    --output pinscope/esp32-autosport.asc esp32s3-can-sd-logger.kicad_sch
python gen/export_pinscope_bom.py
```

| File | What |
|---|---|
| `esp32-autosport.asc` | PADS-PCB netlist — 168 parts, 107 nets |
| `esp32-autosport-bom.csv` | BOM in Pinscope's column layout (`Reference`, `Manufacturer Part Number`, …) |

Both have been checked against Pinscope's own parsers
(`backend/pinscopex/parsers.py`): format detected as `pads`, 168 parts and 107
nets recovered, `validate_netlist()` returns **no errors**, and every reference
in the netlist resolves in the BOM apart from `TP1`–`TP7`, which are bare
copper pads and correctly absent.

## Running it

Pinscope needs an **Anthropic API key** — it calls the API to read the
datasheets, so a run costs real money, which is why this repo stops at
preparing the inputs.

It has no CLI. The documented route is the web UI on `localhost:3000`, but the
FastAPI backend exposes the whole flow, so it can also be driven with `curl`:

```
POST /projects                        create
POST /projects/{id}/upload/netlist    esp32-autosport.asc
POST /projects/{id}/upload/bom        esp32-autosport-bom.csv
POST /projects/{id}/upload/datasheets the PDFs
POST /pipeline/{id}/estimate          what the run will cost
POST /pipeline/{id}/start
GET  /pipeline/{id}/status
GET  /report/{id}                     findings, with page citations
```

Datasheets are needed for the eight ICs — `U1` LM74700-Q1, `U2`/`U3` LM5164,
`U4` ESP32-S3-WROOM-1, `U5` USBLC6-2SC6, `U6` SN74AHCT1G125, `U7` TJA1051T/3,
`U8` ADS1115 — plus the FETs if you want them covered. Pinscope can fetch them
itself given DigiKey API keys, or take uploaded PDFs.

## What preparing these already found

Nothing to do with Pinscope's analysis — just from making the BOM parse:

- `bom.csv` was written with **seven values against an eight-column header**,
  so the LCSC column was dropped entirely and every note shifted left into it.
  Fixed in `gen/generate_schematic.py`.
- With the LCSC numbers visible again, the sourcing table in the main README
  turned out to have the wrong part for the CAN transceiver: **C58988**, not
  C38695.
