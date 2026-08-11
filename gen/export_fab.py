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


def through_hole_refs():
    """References JLC should not be asked to assemble.

    The JST harness connectors and the 0.1" headers are through-hole, and
    through-hole assembly is a separate and pricier service, so they are
    hand-soldered. Leaving them in the BOM and the position file means
    eight lines to mark "do not place" by hand in JLC's UI, and an
    auto-matcher that cheerfully offers real parts for them -- it matched
    an actual WS2812 LED to the header named WS2812. Cleaner to drop them
    from both files, consistently, so the two still agree.
    """
    try:
        import pcbnew
    except ImportError:
        return set()
    board = pcbnew.LoadBoard(BOARD)
    return {fp.GetReference() for fp in board.GetFootprints()
            if fp.GetAttributes() & pcbnew.FP_THROUGH_HOLE}


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
        skip = through_hole_refs()
        for row in r:
            ref = row.get("Ref") or row.get("Designator")
            if ref in skip:
                continue
            side = (row.get("Side") or "").lower()
            w.writerow([ref, row.get("PosX"), row.get("PosY"),
                        "Top" if side.startswith("t") else "Bottom",
                        row.get("Rot")])
            n += 1
    os.remove(raw)
    return out, n


def bom():
    """Assembly BOM from the generator's own table, JLC column order.

    Every part that gets placed goes in the file, whether or not it
    already has an LCSC number -- the ones without simply have an empty
    LCSC cell, which is what JLC's parts-matching screen expects you to
    fill in.

    Leaving them out instead is the obvious-looking shortcut and it is
    wrong: JLC matches BOM to placement by designator, so a designator
    that appears in positions.csv but in no BOM line is not "to be
    chosen later", it is *not assembled*. That silently drops most of
    the board.

    Excluded here are the things that are not parts at all -- mounting
    holes, test points and solder jumpers -- which is the same set
    kicad-cli leaves out of the position file, so the two agree.
    """
    import generate_schematic as sch
    sch.assign_refs()
    hand = through_hole_refs()
    groups, unpicked = {}, {}
    for sh in sch.SHEETS:
        for p in sh["parts"]:
            if p["prefix"].startswith("#"):
                continue
            if p["ref"] in hand:
                continue
            if p["lib_id"] in ("Connector:TestPoint",):
                continue
            if p["footprint"].startswith(("MountingHole", "Jumper:")):
                continue
            # JLC fuzzy-matches on Comment, so give it the manufacturer
            # part number when there is one. Matching on a bare value is
            # how "40V 1A" became a mechanical limit switch and "WS2812"
            # became an actual WS2812 LED.
            comment = p["mpn"] or p["value"]
            key = (comment, p["footprint"], p["lcsc"])
            groups.setdefault(key, []).append(p["ref"])
    out = os.path.join(FAB, "bom.csv")
    placed = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "JLCPCB Part #"])
        for (value, fp, lcsc), refs in sorted(groups.items(),
                                              key=lambda kv: kv[1][0]):
            w.writerow([value, ",".join(sorted(refs)),
                        fp.split(":", 1)[-1], lcsc])
            placed += len(refs)
            if not lcsc:
                unpicked[value] = sorted(refs)
    return out, placed, unpicked


def main():
    global CLI
    CLI = find_cli()
    os.makedirs(FAB, exist_ok=True)
    print("kicad-cli   : %s" % CLI)

    zip_path, n_files = gerbers()
    print("gerbers     : %s (%d files)" % (zip_path, n_files))

    pos_path, n_pos = positions()
    print("positions   : %s (%d parts)" % (pos_path, n_pos))

    bom_path, n_bom, unpicked = bom()
    n_unpicked = sum(len(r) for r in unpicked.values())
    print("bom         : %s (%d parts, %d already have an LCSC number)"
          % (bom_path, n_bom, n_bom - n_unpicked))

    if n_bom != n_pos:
        print("WARNING: %d parts in the BOM but %d in the position file -- "
              "JLC matches the two by designator, so any designator that is "
              "in one and not the other will not be assembled" % (n_bom, n_pos))

    if unpicked:
        print("\nThese %d are in the BOM with an empty LCSC cell; pick them on "
              "JLC's parts-matching screen:" % n_unpicked)
        for value, refs in sorted(unpicked.items()):
            print("  %-22s %s" % (value, " ".join(refs)))


if __name__ == "__main__":
    main()
