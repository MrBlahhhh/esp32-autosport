#!/usr/bin/env python3
"""
Export the JLCPCB manufacturing package: Gerbers, drill files, and the
assembly BOM / component-position files in the column layout JLC expects.

  python gen/export_fab.py

Everything lands in fab/ :

  fab/gerbers/           Gerber + Excellon, zipped as fab/<project>-gerbers.zip
  fab/bom.csv            Comment, Designator, Footprint, LCSC  (JLC order)
  fab/positions.csv      Designator, Mid X, Mid Y, Layer, Rotation

Parts with no LCSC number are left out of the assembly BOM and listed on
stdout -- those are the ones to pick in JLC's catalogue at order time.
"""

from __future__ import annotations

import csv
import glob
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

PROJECT = "esp32s3-can-sd-logger"
BOARD = os.path.join(PROJ, PROJECT + ".kicad_pcb")
FAB = os.path.join(PROJ, "fab")

# JLC's 4-layer stackup order.
LAYERS = ("F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,"
          "F.Mask,B.Mask,Edge.Cuts")


def find_cli():
    found = shutil.which("kicad-cli")
    if found:
        return found
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/bin/kicad-cli"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    raise SystemExit("kicad-cli not found")


CLI = None


def run(*args):
    res = subprocess.run([CLI] + list(args), capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit("kicad-cli %s failed:\n%s" %
                         (args[0] + " " + args[1], res.stdout + res.stderr))
    return res.stdout


def gerbers():
    out = os.path.join(FAB, "gerbers")
    os.makedirs(out, exist_ok=True)
    for f in glob.glob(os.path.join(out, "*")):
        os.remove(f)
    run("pcb", "export", "gerbers", "--output", out, "--layers", LAYERS,
        "--no-protel-ext", "--subtract-soldermask", BOARD)
    run("pcb", "export", "drill", "--output", out, "--format", "excellon",
        "--excellon-separate-th", "--generate-map", BOARD)
    zip_path = os.path.join(FAB, PROJECT + "-gerbers.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(glob.glob(os.path.join(out, "*"))):
            z.write(f, os.path.basename(f))
    return zip_path, len(glob.glob(os.path.join(out, "*")))


def positions():
    """kicad-cli position file -> JLC's column names and rotation sign."""
    raw = os.path.join(FAB, "pos-raw.csv")
    run("pcb", "export", "pos", "--output", raw, "--format", "csv",
        "--units", "mm", "--side", "both", "--use-drill-file-origin", BOARD)
    out = os.path.join(FAB, "positions.csv")
    n = 0
    with open(raw, encoding="utf-8") as fh, \
            open(out, "w", newline="", encoding="utf-8") as wfh:
        r = csv.DictReader(fh)
        w = csv.writer(wfh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for row in r:
            ref = row.get("Ref") or row.get("Designator")
            side = (row.get("Side") or "").lower()
            w.writerow([ref, row.get("PosX"), row.get("PosY"),
                        "Top" if side.startswith("t") else "Bottom",
                        row.get("Rot")])
            n += 1
    os.remove(raw)
    return out, n


def bom():
    """Assembly BOM from the generator's own table, JLC column order."""
    import generate_schematic as sch
    sch.assign_refs()
    groups, skipped = {}, {}
    for sh in sch.SHEETS:
        for p in sh["parts"]:
            if p["prefix"].startswith("#"):
                continue
            if p["lib_id"] in ("Connector:TestPoint",):
                continue
            if p["footprint"].startswith("MountingHole"):
                continue
            key = (p["value"], p["footprint"], p["lcsc"])
            groups.setdefault(key, []).append(p["ref"])
    out = os.path.join(FAB, "bom.csv")
    placed = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC"])
        for (value, fp, lcsc), refs in sorted(groups.items(),
                                              key=lambda kv: kv[1][0]):
            if not lcsc:
                skipped[value] = sorted(refs)
                continue
            w.writerow([value, ",".join(sorted(refs)),
                        fp.split(":", 1)[-1], lcsc])
            placed += len(refs)
    return out, placed, skipped


def main():
    global CLI
    CLI = find_cli()
    os.makedirs(FAB, exist_ok=True)
    print("kicad-cli   : %s" % CLI)

    zip_path, n_files = gerbers()
    print("gerbers     : %s (%d files)" % (zip_path, n_files))

    pos_path, n_pos = positions()
    print("positions   : %s (%d parts)" % (pos_path, n_pos))

    bom_path, n_bom, skipped = bom()
    print("bom         : %s (%d parts with LCSC numbers)" % (bom_path, n_bom))
    if skipped:
        print("\nPick these in the JLC catalogue at order time:")
        for value, refs in sorted(skipped.items()):
            print("  %-22s %s" % (value, " ".join(refs)))


if __name__ == "__main__":
    main()
