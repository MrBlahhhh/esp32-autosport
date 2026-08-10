#!/usr/bin/env python3
"""
Export the board as Specctra DSN for an external autorouter.

Two things this does that a plain export does not:

  * In1.Cu and In2.Cu are re-declared as `power` layers.  KiCad exports
    every copper layer as `signal`, so an autorouter happily runs traces
    straight through what are meant to be solid GND and +3V3 planes --
    which then fragments the pour and orphans everything that relied on
    it.  Marking them `power` keeps routing on F.Cu and B.Cu.

  * KiCad's debug asserts are silenced, so the export does not stop on a
    modal dialog when run headless.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/export_dsn.py [out.dsn]
"""

from __future__ import annotations

import os
import re
import sys

try:
    import wx
    wx.DisableAsserts()
except Exception:
    pass

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
BOARD_PATH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

PLANE_LAYERS = ("In1.Cu", "In2.Cu")


def patch_layer_types(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    n = 0
    for layer in PLANE_LAYERS:
        pat = re.compile(r"(\(layer\s+" + re.escape(layer) +
                         r"\s*\(type\s+)signal(\s*\))")
        text, k = pat.subn(r"\1power\2", text)
        if k == 0:                      # multi-line form
            pat2 = re.compile(r"(\(layer\s+" + re.escape(layer) +
                              r"\s*\n\s*\(type\s+)signal(\s*\))")
            text, k = pat2.subn(r"\1power\2", text)
        n += k
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return n


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "fab",
                                                             "board.dsn")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    board = pcbnew.LoadBoard(BOARD_PATH)
    if not pcbnew.ExportSpecctraDSN(board, out):
        raise SystemExit("Specctra export failed")
    n = patch_layer_types(out)
    print("dsn         : %s" % out)
    print("plane layers: %d marked as power (routing stays on F.Cu/B.Cu)" % n)
    if n != len(PLANE_LAYERS):
        print("WARNING: expected %d, got %d -- check the layer syntax"
              % (len(PLANE_LAYERS), n))


if __name__ == "__main__":
    main()
