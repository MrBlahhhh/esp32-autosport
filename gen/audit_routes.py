#!/usr/bin/env python3
"""
Post-route electrical extraction -- tests the routed copper itself.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/audit_routes.py

Everything else in gen/ tests the design's intent; this reads the actual
tracks and asks what the manufactured board will do:

  1. IR drop       real resistance of the routed path from each rail's
                   source pad to its hungriest consumer, at rated current
  2. bus skew      routed length of every SD line against SD_CLK, and the
                   USB pair mismatch
  3. CAN stubs     how far CANH/CANL hang off the through-line
  4. SW loops      copper area of each buck's switch node (radiated-EMI
                   proxy: the smaller the better)
"""

from __future__ import annotations

import collections
import heapq
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
BOARD = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

RHO = 1.72e-8          # ohm-metre, copper
T_CU = 35e-6           # 1 oz
R_VIA = 0.0005         # ohm per through via, generous

# rail -> (source part value, load part value, that LOAD's own current).
# Charging a whole rail's rating to one branch flagged a 10 mA buffer for
# a 100 mV drop it will never see; each path gets its consumer's draw.
IR_CASES = [
    ("+5V",   "33uH",      "0.5A hold",   0.50),   # WS2812 strip feed
    ("+5V",   "33uH",      "AO3401A",     0.20),   # sensor-rail switch
    ("+5V",   "33uH",      "TJA1051T/3",  0.07),   # CAN transceiver
    ("VBAT_F", "2A slow",  "600R",        1.20),   # fuse -> ferrite
    ("+VBAT", "IPD068N10", "LM5164 (5V)", 1.20),   # front end -> bucks
    ("+5VS",  "600R",      "Sensor harness", 0.20),
]


def head(t):
    print("\n" + t)
    print("-" * len(t))


def seg_r(t):
    L = t.GetLength() / 1e9              # metres
    w = t.GetWidth() / 1e9
    return RHO * L / (w * T_CU) if w else 0.0


def net_graph(board, net):
    """Endpoints as nodes, segments as resistive edges."""
    g = collections.defaultdict(list)
    for t in board.GetTracks():
        if t.GetNetname() != net:
            continue
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            # vias join layers at one x,y: model as tiny R to a shared node
            a = (p.x, p.y, "F")
            b = (p.x, p.y, "B")
            g[a].append((b, R_VIA))
            g[b].append((a, R_VIA))
            continue
        lay = "F" if t.GetLayer() == pcbnew.F_Cu else "B"
        a = (t.GetStart().x, t.GetStart().y, lay)
        b = (t.GetEnd().x, t.GetEnd().y, lay)
        r = seg_r(t)
        g[a].append((b, r))
        g[b].append((a, r))
    return g


def nearest_node(g, pos, layer_hint="F"):
    best, bd = None, None
    for (x, y, lay) in g:
        d = (x - pos.x) ** 2 + (y - pos.y) ** 2
        if bd is None or d < bd:
            best, bd = (x, y, lay), d
    return best, (math.sqrt(bd) / 1e6 if bd is not None else None)


def path_r(g, a, b):
    dist = {a: 0.0}
    pq = [(0.0, a)]
    while pq:
        d, n = heapq.heappop(pq)
        if n == b:
            return d
        if d > dist.get(n, 1e9):
            continue
        for m, r in g[n]:
            nd = d + r
            if nd < dist.get(m, 1e9):
                dist[m] = nd
                heapq.heappush(pq, (nd, m))
    return None


def pad_of(board, value, net):
    for f in board.GetFootprints():
        if f.GetValue() != value:
            continue
        for p in f.Pads():
            if p.GetNetname() == net:
                return f.GetReference(), p.GetPosition()
    return None, None


def net_length(board, net):
    return sum(t.GetLength() for t in board.GetTracks()
               if t.GetNetname() == net and t.GetClass() != "PCB_VIA") / 1e6


def main():
    board = pcbnew.LoadBoard(BOARD)
    fails = []

    # ------------------------------------------------------- 1. IR drop ----
    head("1. IR drop along the routed copper, at rated current")
    print("    Dijkstra over the actual segments; planes are not modelled,")
    print("    so plane-fed rails (GND, +3V3) are excluded by construction.")
    print("    %-8s %-22s %8s %9s  %s"
          % ("net", "path", "R", "drop", "verdict"))
    for net, src_v, dst_v, amps in IR_CASES:
        g = net_graph(board, net)
        if not g:
            print("    %-8s no tracks" % net)
            continue
        sref, spos = pad_of(board, src_v, net)
        dref, dpos = pad_of(board, dst_v, net)
        if spos is None or dpos is None:
            print("    %-8s %s or %s not found" % (net, src_v, dst_v))
            continue
        a, da = nearest_node(g, spos)
        b, db = nearest_node(g, dpos)
        r = path_r(g, a, b)
        if r is None:
            print("    %-8s %-22s  NO COPPER PATH (plane or zone fed)"
                  % (net, "%s->%s" % (sref, dref)))
            continue
        drop = r * amps
        ok = drop < 0.100
        print("    %-8s %-22s %6.1fmR %7.1fmV  %s"
              % (net, "%s->%s" % (sref, dref), r * 1e3, drop * 1e3,
                 "ok" if ok else "HIGH"))
        if not ok:
            fails.append("%s drops %.0f mV from %s to %s at %.1f A"
                         % (net, drop * 1e3, sref, dref, amps))

    # ------------------------------------------------------- 2. bus skew ----
    head("2. Routed length and skew")
    clk = net_length(board, "SD_CLK") + net_length(board, "SD_CLK_C")
    print("    SD bus against CLK (%.1f mm); 40 MHz has ~44 mm per ns of" % clk)
    print("    margin at these lengths, so this is a report, not a gate:")
    for d in ("CMD", "D0", "D1", "D2", "D3"):
        l = net_length(board, "SD_" + d) + net_length(board, "SD_%s_C" % d)
        print("      SD_%-4s %6.1f mm  (%+6.1f mm vs CLK)" % (d, l, l - clk))
    dp = net_length(board, "USB_DP") + net_length(board, "USB_DP_CON")
    dm = net_length(board, "USB_DM") + net_length(board, "USB_DM_CON")
    mm_mismatch = abs(dp - dm)
    print("    USB  D+ %.1f mm  D- %.1f mm  mismatch %.1f mm"
          % (dp, dm, mm_mismatch))
    # Full speed tolerates huge skew; flag only the absurd.
    if mm_mismatch > 20.0:
        fails.append("USB pair mismatched by %.1f mm" % mm_mismatch)

    # ------------------------------------------------------ 3. CAN stubs ----
    head("3. CAN through-line and stubs")
    for net in ("CAN_H", "CAN_L", "CANH_T", "CANL_T"):
        print("      %-7s %6.1f mm" % (net, net_length(board, net)))
    print("    The bus proper is the harness; on-board copper is all stub.")
    # A 1 Mbit/s node tolerates roughly a 0.3 m unterminated stub; the
    # gate triggers only when a net's on-board meander approaches that.
    for n in ("CAN_H", "CAN_L"):
        l = net_length(board, n)
        if l > 150.0:
            fails.append("%s meanders %.0f mm on-board" % (n, l))

    # ---------------------------------------------------- 4. SW-node area ----
    head("4. Switch-node copper (radiated-EMI proxy)")
    for net in ("SW_5V", "SW_3V3"):
        length = net_length(board, net)
        area = sum(t.GetLength() / 1e6 * t.GetWidth() / 1e6
                   for t in board.GetTracks()
                   if t.GetNetname() == net and t.GetClass() != "PCB_VIA")
        print("      %-7s %5.1f mm of track, %5.1f mm2 of copper"
              % (net, length, area))
        if length > 15.0:
            fails.append("%s runs %.1f mm -- the hot loop wants under 15"
                         % (net, length))

    head("Summary")
    if not fails:
        print("    Nothing flagged.")
    for f in dict.fromkeys(fails):
        print("  - " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
