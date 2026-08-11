#!/usr/bin/env python3
"""
Regenerate everything under plots/ from the current design.

  python gen/export_plots.py

Three outputs, all committed so a reader of the repository sees the design
without installing KiCad:

  plots/schematic.pdf     every sheet, in hierarchy order
  plots/board-routed.png  raytraced render of the assembled board, top
  plots/board-back.png    the same board from below

These went stale once -- the committed render still showed the USB-C facing
the wrong way two commits after it had been turned round -- so this exists to
make regenerating them one command rather than a remembered incantation.

Needs kicad-cli only; run it with any Python 3.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
PLOTS = os.path.join(PROJ, "plots")
SCH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_sch")
PCB = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")


def find_cli():
    found = shutil.which("kicad-cli")
    if found:
        return found
    pats = (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
            r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
            "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
            "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli")
    hits = [h for p in pats for h in glob.glob(p)]
    if not hits:
        raise SystemExit("kicad-cli not found; add KiCad's bin directory to PATH")
    return sorted(hits)[-1]


def run(cli, args, label):
    print("  %-16s ..." % label, end="", flush=True)
    res = subprocess.run([cli] + args, capture_output=True, text=True)
    if res.returncode != 0:
        print(" FAILED")
        print((res.stdout + res.stderr).strip()[-1500:])
        raise SystemExit(1)
    print(" ok")


def main():
    cli = find_cli()
    os.makedirs(PLOTS, exist_ok=True)
    print("kicad-cli   : %s" % cli)

    run(cli, ["sch", "export", "pdf",
              "--output", os.path.join(PLOTS, "schematic.pdf"), SCH],
        "schematic.pdf")

    # --perspective reads as a photograph of the board rather than a
    # drawing of it, which is what makes a wrongly-turned connector
    # obvious at a glance.
    common = ["--width", "1800", "--height", "1800", "--quality", "high",
              "--perspective", "--zoom", "0.9"]
    run(cli, ["pcb", "render", "--side", "top",
              "--output", os.path.join(PLOTS, "board-routed.png")]
        + common + [PCB], "board-routed.png")
    run(cli, ["pcb", "render", "--side", "bottom",
              "--output", os.path.join(PLOTS, "board-back.png")]
        + common + [PCB], "board-back.png")

    for name in ("schematic.pdf", "board-routed.png", "board-back.png"):
        path = os.path.join(PLOTS, name)
        print("  %-18s %8.1f kB" % (name, os.path.getsize(path) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
