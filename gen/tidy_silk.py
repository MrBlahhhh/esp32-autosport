#!/usr/bin/env python3
"""
Make the silkscreen legible on a dense board.

Straight out of the placer every footprint carries two pieces of text at
the library's default size, sitting wherever the library put them.  At
168 parts on 84 x 74 mm that is 128 labels overlapping each other and 96
sitting on pads -- none of it wrong electrically, all of it useless when
you are looking for R29 with a meter.

Three things fix most of it:

  * Hide the value field.  "100nF" is schematic information; the board
    already says C24, and the BOM says what C24 is.
  * Shrink references to 0.8 mm.  That is the floor -- both the
    board's own minimum text rule and the smallest silkscreen JLC
    prints legibly -- but it is well under the library default and
    frees up a lot of room.
  * Move each reference to whichever spot around its own part is least
    crowded, testing against pads, the board edge and the labels already
    placed.

Anything that genuinely has nowhere to go is left where it was and
reported, rather than being dropped somewhere worse.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/tidy_silk.py

Copper is not touched, so this is safe to run on a finished board.
"""

from __future__ import annotations

import os

try:
    import wx
    wx.DisableAsserts()
except Exception:
    pass

import numpy as np
import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
BOARD_PATH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

TEXT_H = 0.8          # reference height, mm -- the board's minimum,
TEXT_W = 0.8          # and the smallest JLC prints reliably
TEXT_PEN = 0.15       # stroke width, mm -- JLC's minimum silk line
RES = 0.2             # occupancy grid pitch, mm
GAP = 0.25            # clearance from the part's own body
CHAR_W = 0.75         # glyph advance as a fraction of text height


def mm(v):
    return FromMM(v)


def text_box(ref, x, y):
    """Bounding box (x0, y0, x1, y1) of a reference drawn centred at x, y."""
    w = max(len(ref), 1) * TEXT_W * CHAR_W + TEXT_PEN
    h = TEXT_H + TEXT_PEN
    return (x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0)


class Occupancy:
    """What the silkscreen has to keep off, at RES resolution."""

    def __init__(self, board):
        box = board.GetBoardEdgesBoundingBox()
        self.x0, self.y0 = ToMM(box.GetLeft()), ToMM(box.GetTop())
        self.nx = int(ToMM(box.GetWidth()) / RES) + 2
        self.ny = int(ToMM(box.GetHeight()) / RES) + 2
        self.grid = np.zeros((self.ny, self.nx), np.int32)

    def _slice(self, x0, y0, x1, y1):
        j0 = max(0, int((x0 - self.x0) / RES))
        j1 = min(self.nx, int((x1 - self.x0) / RES) + 1)
        i0 = max(0, int((y0 - self.y0) / RES))
        i1 = min(self.ny, int((y1 - self.y0) / RES) + 1)
        return i0, i1, j0, j1

    def block(self, x0, y0, x1, y1):
        i0, i1, j0, j1 = self._slice(x0, y0, x1, y1)
        if i0 < i1 and j0 < j1:
            self.grid[i0:i1, j0:j1] += 1

    def cost(self, x0, y0, x1, y1):
        """How crowded a box is; off-board counts as heavily crowded."""
        i0, i1, j0, j1 = self._slice(x0, y0, x1, y1)
        if i0 >= i1 or j0 >= j1:
            return 10 ** 6
        want = ((y1 - y0) / RES) * ((x1 - x0) / RES)
        got = (i1 - i0) * (j1 - j0)
        penalty = 40 * max(0.0, want - got)      # clipped by the board edge
        return int(self.grid[i0:i1, j0:j1].sum()) + int(penalty)


def main():
    board = pcbnew.LoadBoard(BOARD_PATH)
    occ = Occupancy(board)

    # Pads and part bodies are what a label must not land on.
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            occ.block(ToMM(bb.GetLeft()), ToMM(bb.GetTop()),
                      ToMM(bb.GetRight()), ToMM(bb.GetBottom()))

    hidden = moved = stuck = 0
    for fp in board.GetFootprints():
        val = fp.Value()
        if val.IsVisible():
            val.SetVisible(False)
            hidden += 1

        ref = fp.Reference()
        ref.SetTextSize(VECTOR2I(mm(TEXT_W), mm(TEXT_H)))
        ref.SetTextThickness(mm(TEXT_PEN))
        ref.SetTextAngle(pcbnew.EDA_ANGLE(0, pcbnew.DEGREES_T))
        ref.SetLayer(pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS)

        text = fp.GetReference()
        body = fp.GetBoundingBox(False, False)      # pads and outline, no text
        bx0, by0 = ToMM(body.GetLeft()), ToMM(body.GetTop())
        bx1, by1 = ToMM(body.GetRight()), ToMM(body.GetBottom())
        cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
        w = max(len(text), 1) * TEXT_W * CHAR_W + TEXT_PEN
        h = TEXT_H + TEXT_PEN

        here = ToMM(ref.GetPosition().x), ToMM(ref.GetPosition().y)
        spots = [here,
                 (cx, by0 - GAP - h / 2.0),         # above
                 (cx, by1 + GAP + h / 2.0),         # below
                 (bx0 - GAP - w / 2.0, cy),         # left
                 (bx1 + GAP + w / 2.0, cy),         # right
                 (cx, cy)]                          # on the part itself
        best, best_cost = None, None
        for k, (x, y) in enumerate(spots):
            c = occ.cost(*text_box(text, x, y))
            if k == 0:
                c -= 1                              # prefer not moving on a tie
            if best_cost is None or c < best_cost:
                best, best_cost = (x, y), c

        if best != here:
            ref.SetPosition(VECTOR2I(mm(best[0]), mm(best[1])))
            moved += 1
        if best_cost and best_cost > 0:
            stuck += 1
        occ.block(*text_box(text, best[0], best[1]))

    board.Save(BOARD_PATH)
    print("values hidden      : %d" % hidden)
    print("references resized : %d at %.1f mm" % (len(board.GetFootprints()),
                                                  TEXT_H))
    print("references moved   : %d" % moved)
    print("still crowded      : %d" % stuck)
    print("saved              : %s" % BOARD_PATH)


if __name__ == "__main__":
    main()
