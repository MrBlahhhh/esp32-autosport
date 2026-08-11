#!/usr/bin/env python3
"""
Route the LM5164 buck power paths: SW node to inductor, and the +VBAT feed
through the input capacitors to both VIN pins.

Placement is owned entirely by gen/generate_pcb.py (BUCK_FIXED there);
this script only draws tracks and vias, finding each part on the saved
board by value and pad-net signature, so it survives reference
renumbering and regeneration.  Run it after every generate_pcb.py run:

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

POWER_NETS = {"SW_5V", "SW_3V3", "+VBAT", "+5V", "+3V3", "BST_5V", "BST_3V3"}
W_PWR, W_SW = 0.45, 0.50


def mm(v):
    return FromMM(v)


def pt(x, y):
    return VECTOR2I(mm(x), mm(y))


def pad_nets(fp):
    return frozenset(p.GetNetname() for p in fp.Pads() if p.GetNetname())


def find(board, value=None, nets=None):
    """The footprint matching a value and/or exact pad-net signature."""
    hits = []
    for fp in board.GetFootprints():
        if value is not None and fp.GetValue() != value:
            continue
        if nets is not None and pad_nets(fp) != frozenset(nets):
            continue
        hits.append(fp)
    if len(hits) != 1:
        raise SystemExit("expected exactly one match for value=%r nets=%r, "
                         "got %d" % (value, nets, len(hits)))
    return hits[0]


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


def route(board):
    u2 = find(board, value="LM5164 (5V)")
    u3 = find(board, value="LM5164 (3V3)")
    l1 = find(board, nets={"SW_5V", "+5V"})
    l2 = find(board, nets={"SW_3V3", "+3V3"})
    c_hf = find(board, value="100nF", nets={"+VBAT", "GND"})  # input HF bypass
    c_in = find(board, value="10uF", nets={"+VBAT", "GND"})   # input bulk bypass

    n_sw5 = net(board, "SW_5V")
    n_sw3 = net(board, "SW_3V3")
    n_vin = net(board, "+VBAT")

    add_track(board, n_sw5, *pad_xy(u2, "8"), *pad_xy(l1, "1"), W_SW)
    add_track(board, n_sw3, *pad_xy(u3, "8"), *pad_xy(l2, "1"), W_SW)

    # VIN pin -> HF cap -> bulk cap, approaching the cap pads on their own X
    # so the trace does not cross the caps' GND pads.
    u_vin = pad_xy(u2, "2")
    hf = pad_xy(c_hf, "1")
    add_track(board, n_vin, *u_vin, hf[0], u_vin[1], W_PWR)
    add_track(board, n_vin, hf[0], u_vin[1], *hf, W_PWR)
    blk = pad_xy(c_in, "1")
    add_track(board, n_vin, *hf, *blk, W_PWR)

    # Vertical +VBAT drop to the 3V3 buck on B.Cu, leaving the bulk input
    # cap downward between the RON/FB resistor rows so nothing is skimmed.
    u3_vin = pad_xy(u3, "2")
    wa = (blk[0], blk[1] + 1.65)
    wb = (blk[0], u3_vin[1] - 0.8)
    add_track(board, n_vin, *blk, *wa, W_PWR)
    add_via(board, n_vin, *wa)
    add_track(board, n_vin, *wa, *wb, W_PWR, B_Cu)
    add_via(board, n_vin, *wb)
    add_track(board, n_vin, *wb, *u3_vin, W_PWR)


def main():
    board = pcbnew.LoadBoard(BOARD_PATH)
    print("clearing old power tracks: %d" % clear_power(board))
    route(board)
    board.Save(BOARD_PATH)
    n_tr = sum(1 for t in board.GetTracks() if isinstance(t, PCB_TRACK))
    print("tracks      : %d" % n_tr)
    print("saved       : %s" % BOARD_PATH)


if __name__ == "__main__":
    main()
