#!/usr/bin/env python3
"""
Check that the prose still describes the board the generator builds.

  python gen/audit_docs.py

`gen/validate.py` proves the schematic matches `netlist.txt`. Nothing proved
that **the documentation** matches either, and it drifted badly: rev B inserted
parts (the two USBLC6 arrays, the USB OVP, the card-slot SRV05s), the generator
assigns designators sequentially as parts are added, and every reference-by-
designator in README.md written before that silently came to mean a different
component. `U7` went from the CAN transceiver to an ESD array; `U8` from the
ADS1115 to another one; the two bucks slid from U2/U3 to U3/U4.

None of it is caught by ERC, DRC or a netlist compare, because the netlist is
right — only the prose is wrong. It surfaces when somebody follows the docs at a
bench: probing `U7` for CAN traffic, or checking `C2` for the bulk electrolytic.

Two checks:

  1. Every designator the docs name in backticks exists, as a designator or a
     net name, in `netlist.txt`.
  2. Designators the docs identify by part type still carry that part in
     `bom.csv`. That table is curated -- prose is not machine-readable, so each
     claim is pinned here by hand once and then held.

Exit status is non-zero if anything fails, so this can gate a commit.
"""

from __future__ import annotations

import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))

DOCS = ["README.md", os.path.join("docs", "BRINGUP.md"), os.path.join("fwsim", "README.md")]

# "the docs say this designator is this part" -- substring match against the
# BOM Value. Verified by hand against netlist.txt pin-for-pin on 2026-08-12.
CLAIMS = {
    "U1": "LM74700",          # ideal-diode controller
    "U2": "TLV431",           # power-fail detector (NOT a buck)
    "U3": "LM5164",           # +5 V buck
    "U4": "LM5164",           # +3V3 buck
    "U5": "ESP32-S3-WROOM-1", # the module
    "U6": "TLV431",           # USB over-voltage cutoff
    "U7": "USBLC6",           # ESD on the USB data pair
    "U8": "USBLC6",           # ESD on the USB-C CC pins
    "U9": "74AHCT1G125",      # WS2812 5 V buffer
    "U10": "SRV05",           # card-slot ESD
    "U11": "SRV05",           # card-slot ESD
    "U12": "TJA1051",         # CAN transceiver
    "U13": "ADS1115",         # 16-bit ADC
    "Q1": "IPD068N10",        # reverse-battery FET
    "D1": "SMCJ40CA",         # harness transient clamp
    "D3": "SMAJ6.0A",         # sensor rail clamp
    "D6": "SMAJ26CA",         # CAN_H clamp
    "D7": "SMAJ26CA",         # CAN_L clamp
    "C3": "100uF",            # bulk hold-up
    "C6": "330uF",            # ride-through
    "C7": "330uF",            # ride-through
    "R55": "60.4",            # split termination
    "R56": "60.4",            # split termination
    "R64": "10k",             # battery monitor, low leg
    "R77": "100k",            # battery monitor, high leg
    "F1": "2A",               # input fuse
}


def load_bom():
    values = {}
    with open(os.path.join(PROJ, "bom.csv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            for ref in row["References"].split():
                values[ref] = row["Value"]
    return values


def load_netlist():
    designators, nets = set(), set()
    with open(os.path.join(PROJ, "netlist.txt"), encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 2:
                continue
            nets.add(parts[0])
            for ref in parts[1:]:
                m = re.fullmatch(r"([A-Za-z#]{1,4}\d{1,3})\.\w+", ref)
                if m:
                    designators.add(m.group(1))
    return designators, nets


def main():
    values = load_bom()
    designators, nets = load_netlist()
    failures = []

    print("Designator claims (docs -> bom.csv)")
    for ref, want in sorted(CLAIMS.items(), key=lambda kv: (kv[0][0], int(re.sub(r"\D", "", kv[0])))):
        got = values.get(ref)
        ok = got is not None and want.lower() in got.lower()
        if not ok:
            failures.append("%s: docs say %s, bom.csv says %s" % (ref, want, got or "(absent)"))
        print("  %-5s %-6s %-22s %s" % ("ok" if ok else "FAIL", ref, want, got or "(absent)"))

    print("\nDangling references in the docs")
    dangling = 0
    for doc in DOCS:
        path = os.path.join(PROJ, doc)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        for tok in sorted(set(re.findall(r"`([A-Z]{1,3}\d{1,3})`", text))):
            if tok in designators or tok in nets:
                continue
            dangling += 1
            failures.append("%s: `%s` is neither a designator nor a net" % (doc, tok))
            print("  FAIL  %s names `%s`, which is in neither netlist column" % (doc, tok))
    if not dangling:
        print("  ok    every designator named in the docs exists")

    print("\n%d checks failed" % len(failures))
    for f in failures:
        print("  %s" % f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
