#!/usr/bin/env python3
"""
Import an autorouter's Specctra SES session back onto the board and
refill the plane zones.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/import_ses.py [in.ses]

Defaults to fab/board.ses.  Run gen/finish_routing.py afterwards to tie
off whatever the router left open.
"""

from __future__ import annotations

import os
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


def main():
    ses = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "fab",
                                                             "board.ses")
    if not os.path.exists(ses):
        raise SystemExit("no session file: " + ses)

    board = pcbnew.LoadBoard(BOARD_PATH)
    before = sum(1 for _ in board.GetTracks())
    if not pcbnew.ImportSpecctraSES(board, ses):
        raise SystemExit("Specctra session import failed")

    tracks = list(board.GetTracks())
    vias = sum(1 for t in tracks if isinstance(t, pcbnew.PCB_VIA))

    # Routed copper changes what the planes can reach, so refill.
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(BOARD_PATH)

    print("session     : %s" % ses)
    print("tracks      : %d -> %d (%d vias)" % (before, len(tracks), vias))
    print("zones       : refilled")
    print("saved       : %s" % BOARD_PATH)


if __name__ == "__main__":
    main()
