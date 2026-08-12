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
W_PWR, W_SW = 0.55, 0.60   # IPC-2221 wants 0.516 mm for 2 A at a 20 C
                           # rise on an outer layer; 0.55 clears it with
                           # margin and 0.60 keeps the SW loop the widest
                           # copper in the island


def mm(v):
    return FromMM(v)


def pt(x, y):
    return VECTOR2I(mm(x), mm(y))


def pad_nets(fp):
    return frozenset(p.GetNetname() for p in fp.Pads() if p.GetNetname())


def find(board, value=None, nets=None, near=None):
    """The footprint matching a value and/or exact pad-net signature.

    Both bucks carry an identical input pair -- same value, same two nets --
    so value plus net signature no longer names one part.  `near` breaks the
    tie by picking the closest to a point, which is how the two islands
    differ: each pair sits beside its own VIN pin.  Without `near` the match
    still has to be unique, so a genuine ambiguity is an error rather than
    an arbitrary pick.
    """
    hits = []
    for fp in board.GetFootprints():
        if value is not None and fp.GetValue() != value:
            continue
        if nets is not None and pad_nets(fp) != frozenset(nets):
            continue
        hits.append(fp)
    if not hits:
        raise SystemExit("no match for value=%r nets=%r" % (value, nets))
    if near is not None:
        return min(hits, key=lambda fp: (ToMM(fp.GetPosition().x) - near[0]) ** 2
                   + (ToMM(fp.GetPosition().y) - near[1]) ** 2)
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


def pad_on(fp, netname):
    """Where `fp` presents `netname`.  Pad numbering on a two-pad part
    depends on the footprint's rotation, so ask for the net, not for pad 1."""
    for pad in fp.Pads():
        if pad.GetNetname() == netname:
            p = pad.GetPosition()
            return ToMM(p.x), ToMM(p.y)
    raise KeyError("%s has no %s pad" % (fp.GetReference(), netname))


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

    n_sw5 = net(board, "SW_5V")
    n_sw3 = net(board, "SW_3V3")
    n_vin = net(board, "+VBAT")

    add_track(board, n_sw5, *pad_xy(u2, "8"), *pad_xy(l1, "1"), W_SW)
    add_track(board, n_sw3, *pad_xy(u3, "8"), *pad_xy(l2, "1"), W_SW)

    # Each converter owns an input pair beside its own VIN pin.  Which pair
    # is whose is decided by distance to that pin, not by reference, so the
    # two islands stay independent through a renumbering.
    v5, v3 = pad_xy(u2, "2"), pad_xy(u3, "2")
    hf5 = find(board, value="100nF", nets={"+VBAT", "GND"}, near=v5)
    bk5 = find(board, value="10uF", nets={"+VBAT", "GND"}, near=v5)
    hf3 = find(board, value="100nF", nets={"+VBAT", "GND"}, near=v3)
    bk3 = find(board, value="10uF", nets={"+VBAT", "GND"}, near=v3)
    if hf5 is hf3 or bk5 is bk3:
        raise SystemExit("both bucks claimed the same input cap -- BUCK_FIXED "
                         "in generate_pcb.py is missing a pair")

    # VIN pin -> HF cap -> bulk cap, approaching the cap pads on their own X
    # so the trace does not cross the caps' GND pads.
    for u, hf, bk in ((u2, hf5, bk5), (u3, hf3, bk3)):
        u_vin = pad_xy(u, "2")
        h = pad_on(hf, "+VBAT")
        add_track(board, n_vin, *u_vin, h[0], u_vin[1], W_PWR)
        add_track(board, n_vin, h[0], u_vin[1], *h, W_PWR)
        add_track(board, n_vin, *h, *pad_on(bk, "+VBAT"), W_PWR)

    # The two islands are tied by a +VBAT drop on B.Cu, leaving the 5 V
    # bulk cap downward and arriving above the 3V3 HF cap, so the run stays
    # clear of the RON/FB resistor rows between them.
    b5 = pad_on(bk5, "+VBAT")
    h3 = pad_on(hf3, "+VBAT")
    wa = (b5[0], b5[1] + 1.65)
    wb = (h3[0], h3[1] - 1.65)
    add_track(board, n_vin, *b5, *wa, W_PWR)
    add_via(board, n_vin, *wa)
    add_track(board, n_vin, *wa, wa[0], wb[1], W_PWR, B_Cu)
    add_track(board, n_vin, wa[0], wb[1], *wb, W_PWR, B_Cu)
    add_via(board, n_vin, *wb)
    add_track(board, n_vin, *wb, *h3, W_PWR)


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
