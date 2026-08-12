#!/usr/bin/env python3
"""
Shopping list for building the boards yourself.

  python gen/export_order.py [--boards 5] [--spares-passive 20] [--spares-ic 1]

`fab/bom.csv` is written for JLCPCB's assembly flow, where quantities are
implicit and the eight through-hole connectors and three unavailable
electrolytics are deliberately absent. None of that is right for buying the
parts loose and reflowing them at home, which needs the opposite: every part,
an explicit count, and spares for the ones that will get lost or tombstoned.

Writes three files to `fab/`:

  order-lcsc.csv       part number + quantity, ready to paste into LCSC's
                       bulk-order box
  order-elsewhere.csv  the parts LCSC/JLC cannot supply, with what to search
  order-summary.txt    a readable version of both, with the reasoning

Spares default to +20 pieces on passives (they cost fractions of a cent and
LCSC has minimum order quantities anyway, which will round most of them up
regardless) and +1 on anything with a package you would not want to hand-place
twice.
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
FAB = os.path.join(PROJ, "fab")

# Parts no assembly house here can supply, or that are through-hole and were
# deliberately kept out of fab/. Buy these separately.
#
# The three electrolytics are the interesting ones: JLCPCB's SMD aluminium
# electrolytic library stops at 47 uF once 100 V is needed, so there is no
# part to order from them at all. Nichicon UCD / Rubycon MV are the reference
# series. Check the can diameter before ordering -- the land patterns are
# 10 mm and 16 mm, and an 18 mm part will not fit.
ELSEWHERE = [
    ("C3",        1, "100uF 100V SMD electrolytic, 10 mm can",
     "Nichicon UCD2A101MNL1GS or equivalent. Land is CP_Elec_10x10.5"),
    ("C6, C7",    2, "330uF 100V SMD electrolytic, 16 mm can",
     "Land is CP_Elec_16x22 (same pads as 16x17.5). If 330 uF only exists at "
     "18 mm, fit 220 uF at 16 mm instead -- the bank drops to 540 uF and the "
     "ride-through to ~108 ms, which is what the board was simulated at "
     "originally"),
    ("J1",        1, "JST PH 4-pin vertical header, 2.00 mm", "B4B-PH-K-S"),
    ("J10",       1, "JST PH 8-pin vertical header, 2.00 mm", "B8B-PH-K-S"),
    ("J3, J4, J8", 3, "0.1\" pin header, 1x04 vertical", "any"),
    ("J5, J7",    2, "0.1\" pin header, 1x06 vertical", "any"),
    ("J6",        1, "0.1\" pin header, 1x03 vertical", "any"),
    ("harness",   1, "JST PH crimp housings + crimps for J1 (4) and J10 (8)",
     "buy with the headers or the harness cannot be made up"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boards", type=int, default=5)
    ap.add_argument("--spares-passive", type=int, default=20)
    ap.add_argument("--spares-ic", type=int, default=1)
    args = ap.parse_args()

    bom = os.path.join(FAB, "bom.csv")
    if not os.path.exists(bom):
        raise SystemExit("fab/bom.csv missing -- run gen/export_fab.py first")

    per_part = collections.defaultdict(lambda: {"n": 0, "refs": [], "cmt": "", "fp": ""})
    unspecified = []
    for row in csv.DictReader(open(bom, encoding="utf-8")):
        pn = row["JLCPCB Part #"].strip()
        refs = [r.strip() for r in row["Designator"].split(",") if r.strip()]
        if not pn:
            unspecified.append((row["Comment"], row["Designator"]))
            continue
        e = per_part[pn]
        e["n"] += len(refs)
        e["refs"] += refs
        e["cmt"] = row["Comment"]
        e["fp"] = row["Footprint"].split(":")[-1]

    def is_passive(fp):
        return bool(re.search(r"_(0402|0603|0805|1206|1210)_", fp))

    lines = []
    for pn, e in sorted(per_part.items(), key=lambda kv: -kv[1]["n"]):
        need = e["n"] * args.boards
        spare = args.spares_passive if is_passive(e["fp"]) else args.spares_ic
        lines.append((pn, need + spare, need, spare, e["cmt"], e["fp"],
                      ",".join(sorted(set(e["refs"])))))

    os.makedirs(FAB, exist_ok=True)
    out = os.path.join(FAB, "order-lcsc.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["LCSC Part Number", "Order Qty", "Needed", "Spares",
                    "Comment", "Footprint", "Designators"])
        for r in lines:
            w.writerow(r)

    out2 = os.path.join(FAB, "order-elsewhere.csv")
    with open(out2, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Designators", "Qty per board", "Order Qty", "What", "Note"])
        for refs, n, what, note in ELSEWHERE:
            w.writerow([refs, n, n * args.boards, what, note])

    txt = os.path.join(FAB, "order-summary.txt")
    with open(txt, "w", encoding="utf-8") as fh:
        fh.write("Parts order for %d boards\n" % args.boards)
        fh.write("=" * 40 + "\n\n")
        fh.write("1. From LCSC -- paste the first two columns of\n")
        fh.write("   fab/order-lcsc.csv into their bulk-order box.\n\n")
        fh.write("   %-12s %6s %7s  %-18s %s\n"
                 % ("part", "order", "needed", "comment", "designators"))
        for pn, order, need, spare, cmt, fp, refs in lines:
            fh.write("   %-12s %6d %7d  %-18s %s\n"
                     % (pn, order, need, cmt[:18], refs[:46]))
        fh.write("\n   %d distinct parts, %d pieces total\n"
                 % (len(lines), sum(l[1] for l in lines)))
        fh.write("\n2. Not available from LCSC/JLC -- source separately\n\n")
        for refs, n, what, note in ELSEWHERE:
            fh.write("   %-12s x%-3d %s\n" % (refs, n * args.boards, what))
            fh.write("   %s\n\n" % ("             " + note))

    print("wrote %s  (%d parts, %d pieces)"
          % (os.path.relpath(out, PROJ), len(lines), sum(l[1] for l in lines)))
    print("wrote %s  (%d items)" % (os.path.relpath(out2, PROJ), len(ELSEWHERE)))
    print("wrote %s" % os.path.relpath(txt, PROJ))
    if unspecified:
        print("\n%d BOM lines have no part number and are NOT in the order:"
              % len(unspecified))
        for cmt, refs in unspecified:
            print("   %-20s %s" % (cmt, refs))
        return 1
    print("\nEvery line in fab/bom.csv carries a part number.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
