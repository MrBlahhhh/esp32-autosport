#!/usr/bin/env python3
"""
Rewrite bom.csv into the column layout Pinscope's parser expects.

Pinscope reads a BOM with `Reference` and `Manufacturer Part Number` columns
and comma-separated reference groups (backend/pinscopex/parsers.py). This
project's own bom.csv uses `References` with spaces and carries two quantity
columns, so it needs translating rather than renaming.

  python gen/export_pinscope_bom.py

Pair it with the netlist:

  kicad-cli sch export netlist --format pads \\
      --output pinscope/esp32-autosport.asc esp32s3-can-sd-logger.kicad_sch
"""

from __future__ import annotations

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(PROJ, "bom.csv")
OUT = os.path.join(PROJ, "pinscope", "esp32-autosport-bom.csv")

COLUMNS = ["Reference", "Qty", "Value", "Footprint", "Datasheet", "LCSC",
           "Manufacturer Part Number"]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(SRC, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for r in rows:
            refs = [x for x in r["References"].split() if x]
            w.writerow([",".join(refs), r["Qty (1 board)"], r["Value"],
                        r["Footprint"], "", r.get("LCSC", ""),
                        r.get("Manufacturer part number", "")])

    with_lcsc = sum(1 for r in rows if r.get("LCSC"))
    print("bom        : %s" % OUT)
    print("lines      : %d (%d with an LCSC number)" % (len(rows), with_lcsc))


if __name__ == "__main__":
    main()
