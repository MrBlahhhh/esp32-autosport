#!/usr/bin/env python3
"""
Place LM5164 buck islands (stacked left of the SD socket) and route SW + VIN.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/route_bucks.py
"""

from __future__ import annotations

import os

import pcbnew
from pcbnew import (
    FromMM, ToMM, VECTOR2I, PCB_TRACK, PCB_VIA, VIATYPE_THROUGH, F_Cu, B_Cu,
)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
BOARD_PATH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

# Stacked vertically in x=34–60 so J7 (SD, right edge) stays clear.
# Bottom headers moved left under the frontend.
PLACE = {
    # ---- +5V (upper) ----------------------------------------------------
    "C4":  (34.5, 44.0, 0),
    "C3":  (34.5, 47.8, 0),
    "U2":  (41.0, 45.0, 0),
    "C5":  (41.0, 39.5, 0),      # above IC, off the SW→L path
    "L1":  (50.0, 43.0, 0),
    "C8":  (55.0, 48.5, 0),
    "C10": (45.5, 48.5, 0),
    "C9":  (55.0, 51.8, 0),
    "TP2": (45.5, 51.8, 0),
    "R3":  (34.5, 40.5, 0),
    "R7":  (45.5, 39.5, 0),
    "R4":  (38.5, 51.0, 0),
    "R6":  (42.5, 51.0, 0),
    "R5":  (46.5, 51.0, 0),
    "C6":  (50.5, 51.0, 0),
    "C7":  (46.5, 54.0, 0),
    "R8":  (42.5, 54.0, 0),

    # ---- +3V3 (lower) ---------------------------------------------------
    "U3":  (41.0, 59.0, 0),
    "C11": (41.0, 53.5, 0),
    "L2":  (50.0, 57.0, 0),
    "C14": (55.0, 62.5, 0),
    "C16": (45.5, 62.5, 0),
    "C15": (55.0, 65.8, 0),
    "TP3": (45.5, 65.8, 0),
    "R9":  (34.5, 55.5, 0),
    "R13": (45.5, 53.5, 0),
    "R10": (34.5, 65.5, 0),
    "R12": (38.5, 65.5, 0),
    "R11": (42.5, 65.5, 0),
    "C12": (46.5, 65.5, 0),
    "C13": (42.5, 68.5, 0),
    "R14": (38.5, 68.5, 0),

    # Headers — left bottom, clear of 3V3 row
    "value:UART0":          (12.0, 69.5, 90),
    "value:I2C / Qwiic":    (22.0, 69.5, 90),
    "value:Rail break-out": (32.0, 69.5, 90),

    "SW1": (78.0, 55.0, 0),
    "SW2": (78.0, 61.0, 0),
    "D6":  (78.0, 49.0, 0),
    "D7":  (78.0, 46.0, 0),

    "Q2":  (60.0, 28.0, 0),
    "Q3":  (64.0, 28.0, 0),
    "R25": (60.0, 24.5, 0),
    "R26": (64.0, 24.5, 0),
    "R24": (68.0, 24.5, 0),

    "F1":  (29.0, 51.0, 0),
    "FB1": (24.0, 51.0, 0),
    "Q1":  (26.0, 43.0, 0),
}

# Header value keys are applied via generate_pcb FIXED; here by ref after place.
HEADER_REFS = {
    "J3": (12.0, 69.5, 90),
    "J4": (22.0, 69.5, 90),
    "J6": (32.0, 69.5, 90),
}

POWER_NETS = {"SW_5V", "SW_3V3", "+VBAT", "+5V", "+3V3", "BST_5V", "BST_3V3"}
W_PWR, W_SW = 0.45, 0.50


def mm(v):
    return FromMM(v)


def pt(x, y):
    return VECTOR2I(mm(x), mm(y))


def pad_xy(fp, num):
    for pad in fp.Pads():
        if pad.GetNumber() == num:
            p = pad.GetPosition()
            return ToMM(p.x), ToMM(p.y)
    raise KeyError("%s.%s" % (fp.GetReference(), num))


def net(board, name):
    ni = board.FindNet(name)
    if ni is None:
        raise KeyError(name)
    return ni


def add_track(board, n, x1, y1, x2, y2, w, layer=F_Cu):
    if abs(x1 - x2) < 0.05 and abs(y1 - y2) < 0.05:
        return
    t = PCB_TRACK(board)
    t.SetStart(pt(x1, y1))
    t.SetEnd(pt(x2, y2))
    t.SetWidth(mm(w))
    t.SetLayer(layer)
    t.SetNet(n)
    board.Add(t)


def add_via(board, n, x, y):
    v = PCB_VIA(board)
    v.SetPosition(pt(x, y))
    v.SetDrill(mm(0.30))
    v.SetWidth(mm(0.60))
    v.SetViaType(VIATYPE_THROUGH)
    v.SetNet(n)
    try:
        v.SetLayerPair(F_Cu, B_Cu)
    except Exception:
        pass
    board.Add(v)


def clear_power(board):
    doomed = [t for t in board.GetTracks() if t.GetNetname() in POWER_NETS]
    for t in doomed:
        board.Delete(t)
    for z in board.Zones():
        z.UnFill()
    return len(doomed)


def place(board):
    for ref, (x, y, rot) in {**PLACE, **HEADER_REFS}.items():
        if ref.startswith("value:"):
            continue
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            print("  skip", ref)
            continue
        fp.SetOrientationDegrees(rot)
        fp.SetPosition(pt(x, y))


def route_safe(board):
    U2, L1 = board.FindFootprintByReference("U2"), board.FindFootprintByReference("L1")
    U3, L2 = board.FindFootprintByReference("U3"), board.FindFootprintByReference("L2")
    C4, C3 = board.FindFootprintByReference("C4"), board.FindFootprintByReference("C3")

    n_sw5 = net(board, "SW_5V")
    n_sw3 = net(board, "SW_3V3")
    n_vin = net(board, "+VBAT")

    add_track(board, n_sw5, *pad_xy(U2, "8"), *pad_xy(L1, "1"), W_SW)
    add_track(board, n_sw3, *pad_xy(U3, "8"), *pad_xy(L2, "1"), W_SW)

    # Approach C4 pad1 from the IC without crossing pad2: dogbone via pad1 X
    u_vin = pad_xy(U2, "2")
    c4 = pad_xy(C4, "1")
    add_track(board, n_vin, *u_vin, c4[0], u_vin[1], W_PWR)
    add_track(board, n_vin, c4[0], u_vin[1], *c4, W_PWR)
    c3 = pad_xy(C3, "1")
    add_track(board, n_vin, *c4, *c3, W_PWR)

    # Vertical +VBAT on B.Cu so it does not skim RON / FB discretes
    u3 = pad_xy(U3, "2")
    va, vb = (c3[0] - 2.0, c3[1]), (c3[0] - 2.0, u3[1])
    add_via(board, n_vin, *va)
    add_via(board, n_vin, *vb)
    add_track(board, n_vin, *c3, *va, W_PWR)
    add_track(board, n_vin, *va, *vb, W_PWR, B_Cu)
    add_track(board, n_vin, *vb, *u3, W_PWR)


def main():
    board = pcbnew.LoadBoard(BOARD_PATH)
    print("placing stacked buck islands…")
    place(board)
    print("clearing… (%d)" % clear_power(board))
    print("routing SW + VIN…")
    route_safe(board)
    board.Save(BOARD_PATH)

    power = {"U2", "U3", "L1", "L2", "C3", "C4", "C5", "C8", "C11", "C14"}
    boxes = {}
    for r in list(PLACE) + list(HEADER_REFS):
        if r.startswith("value:"):
            continue
        fp = board.FindFootprintByReference(r)
        if not fp:
            continue
        bb = fp.GetBoundingBox(False, False)
        boxes[r] = (ToMM(bb.GetLeft()), ToMM(bb.GetTop()),
                    ToMM(bb.GetRight()), ToMM(bb.GetBottom()))
    print("core power courtyard overlaps:")
    hits = 0
    keys = sorted(k for k in boxes if k in power)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            ax1, ay1, ax2, ay2 = boxes[a]
            bx1, by1, bx2, by2 = boxes[b]
            if ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1:
                print("  %s × %s" % (a, b))
                hits += 1
    if not hits:
        print("  none")
    n_tr = sum(1 for t in board.GetTracks() if isinstance(t, PCB_TRACK))
    print("tracks      : %d" % n_tr)
    print("saved       : %s" % BOARD_PATH)


if __name__ == "__main__":
    main()
