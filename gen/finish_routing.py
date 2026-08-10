#!/usr/bin/env python3
"""
Close out whatever the autorouter left open, and report the rest.

By this point gen/stitch_planes.py has given every GND / +3V3 pad its via
and the autorouter has done the signals.  What is typically left is a
connector whose duplicated pins have to be tied together on copper: USB-C
carries D+ on both A6 and B6 and D- on both A7 and B7 so a cable works
either way up, and the board has to bridge each pair.  This escapes both
pads sideways to vias and links them underneath on B.Cu, which is empty
beneath an SMD connector.

Anything still open is printed with its net and pads, for hand-routing.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/finish_routing.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import wx
    wx.DisableAsserts()
except Exception:
    pass

import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I, PCB_VIA, PCB_TRACK, VIATYPE_THROUGH

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from stitch_planes import collect, fits, seg_fits          # noqa: E402
import netclasses                                          # noqa: E402

BOARD_PATH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")
SIZES = netclasses.Sizes()

PAD_RE = re.compile(r"^(?:PTH )?[Pp]ad (\S+) \[([^\]]*)\] of (\S+)")
NET_RE = r"\[([^\]]+)\]"
TIE_VIA, TIE_DRILL, TIE_W = 0.5, 0.25, 0.2      # floor; per-net sizes win


def mm(v):
    return FromMM(v)


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


def drc(board_path):
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "drc.json")
    subprocess.run([find_cli(), "pcb", "drc", "--output", out,
                    "--format", "json", "--severity-error", board_path],
                   capture_output=True, text=True)
    with open(out, encoding="utf-8-sig") as fh:
        rep = json.load(fh)
    shutil.rmtree(tmp, ignore_errors=True)
    return rep.get("violations", []), rep.get("unconnected_items", [])


def add_via(board, x, y, netinfo, dia=TIE_VIA, drill=TIE_DRILL):
    v = PCB_VIA(board)
    v.SetPosition(VECTOR2I(int(mm(x)), int(mm(y))))
    v.SetDrill(mm(drill))
    v.SetWidth(mm(dia))
    v.SetViaType(VIATYPE_THROUGH)
    v.SetNet(netinfo)
    try:
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    except Exception:
        pass
    board.Add(v)
    return v


def add_track(board, netinfo, p1, p2, layer, width=TIE_W):
    t = PCB_TRACK(board)
    t.SetStart(VECTOR2I(int(mm(p1[0])), int(mm(p1[1]))))
    t.SetEnd(VECTOR2I(int(mm(p2[0])), int(mm(p2[1]))))
    t.SetWidth(mm(width))
    t.SetLayer(layer)
    t.SetNet(netinfo)
    board.Add(t)
    return t


def open_pads(unconnected):
    """{(ref, net)} for every pad DRC still reports as open."""
    out = set()
    for entry in unconnected:
        for item in entry.get("items", []):
            m = PAD_RE.match(item.get("description", ""))
            if m:
                out.add((m.group(3), m.group(2)))
    return out


def tie_duplicates(board, unconnected):
    """Jumper a connector's duplicated pins (USB-C D+ on A6/B6 etc)."""
    rects, segs = collect(board)
    made = []
    for ref, net in sorted(open_pads(unconnected)):
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            continue
        spots = {}
        for pad in fp.Pads():
            if pad.GetNetname() != net:
                continue
            p = pad.GetPosition()
            spots.setdefault((ToMM(p.x), ToMM(p.y)), pad)
        if len(spots) < 2:
            continue                       # not a duplicated-pin case
        keys = sorted(spots)
        a = spots[keys[0]]
        netinfo = a.GetNet()
        tie_w = SIZES.track(net)
        tie_via, tie_drill = SIZES.via(net)
        (ax, ay), (bx, by) = keys[0], keys[-1]
        half = ToMM(a.GetBoundingBox().GetWidth()) / 2.0
        placed = False
        for step in range(14):
            off = 0.5 + 0.25 * step
            for sign in (1, -1):
                vx = ax + sign * (half + off)
                if not (fits(vx, ay, tie_via, net, rects, segs) and
                        fits(vx, by, tie_via, net, rects, segs)):
                    continue
                if not (seg_fits(ax, ay, vx, ay, tie_w, net, rects, segs) and
                        seg_fits(bx, by, vx, by, tie_w, net, rects, segs) and
                        seg_fits(vx, ay, vx, by, tie_w, net, rects, segs)):
                    continue
                add_via(board, vx, ay, netinfo, tie_via, tie_drill)
                add_via(board, vx, by, netinfo, tie_via, tie_drill)
                add_track(board, netinfo, (ax, ay), (vx, ay), pcbnew.F_Cu, tie_w)
                add_track(board, netinfo, (bx, by), (vx, by), pcbnew.F_Cu, tie_w)
                add_track(board, netinfo, (vx, ay), (vx, by), pcbnew.B_Cu, tie_w)
                for x, y in ((vx, ay), (vx, by)):
                    rects.append((x, y, tie_via, tie_via, net))
                segs.append((vx, ay, vx, by, tie_w, net))
                made.append("%s [%s]" % (ref, net))
                placed = True
                break
            if placed:
                break
        if not placed:
            made.append("%s [%s] NO ROOM" % (ref, net))
    return made



def close_gaps(board, unconnected):
    """Route the short leftovers the autorouter did not quite finish.

    DRC gives both ends of every open connection.  Each is tried as a
    straight segment, then as an L on either side, on F.Cu first and B.Cu
    (with a via at each end) second -- all checked against real pad and
    track geometry.
    """
    rects, segs = collect(board)
    done, stuck = [], []
    for entry in unconnected:
        items = entry.get("items", [])
        if len(items) < 2:
            continue
        net = None
        for it in items:
            m = re.search(NET_RE, it.get("description", ""))
            if m:
                net = m.group(1)
                break
        pa, pb = items[0].get("pos"), items[1].get("pos")
        if net is None or not pa or not pb:
            continue
        netinfo = board.FindNet(net)
        if netinfo is None:
            continue
        ax, ay, bx, by = pa["x"], pa["y"], pb["x"], pb["y"]

        routed = False
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            paths = [[(ax, ay), (bx, by)],
                     [(ax, ay), (bx, ay), (bx, by)],
                     [(ax, ay), (ax, by), (bx, by)]]
            for path in paths:
                ok = True
                for p1, p2 in zip(path, path[1:]):
                    if not seg_fits(p1[0], p1[1], p2[0], p2[1], TIE_W,
                                    net, rects, segs):
                        ok = False
                        break
                if not ok:
                    continue
                if layer == pcbnew.B_Cu:
                    if not (fits(ax, ay, TIE_VIA, net, rects, segs) and
                            fits(bx, by, TIE_VIA, net, rects, segs)):
                        continue
                    add_via(board, ax, ay, netinfo)
                    add_via(board, bx, by, netinfo)
                    for x, y in ((ax, ay), (bx, by)):
                        rects.append((x, y, TIE_VIA, TIE_VIA, net))
                for p1, p2 in zip(path, path[1:]):
                    add_track(board, netinfo, p1, p2, layer)
                    segs.append((p1[0], p1[1], p2[0], p2[1], TIE_W, net))
                done.append(net)
                routed = True
                break
            if routed:
                break
        if not routed:
            stuck.append(net)
    return done, stuck


def main():
    print("asking DRC what is still open…")
    violations, unconnected = drc(BOARD_PATH)
    print("violations   : %d" % len(violations))
    print("unconnected  : %d" % len(unconnected))

    board = pcbnew.LoadBoard(BOARD_PATH)
    made = tie_duplicates(board, unconnected)
    if made:
        print("duplicate-pin ties: %s" % ", ".join(made))
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(BOARD_PATH)

    # close_gaps() is deliberately not called any more.  Straight-line and
    # single-corner guesses land half a step short of the copper often
    # enough to leave stranded fragments -- three of them on +5VS the last
    # time round -- and gen/maze_route.py now does the same job properly,
    # with real geometry and a check before anything becomes copper.  The
    # function is kept for reference.
    violations, unconnected = drc(BOARD_PATH)

    print("\nfinal: %d violations, %d unconnected" % (len(violations),
                                                      len(unconnected)))
    for x in violations[:12]:
        print("  ! %-22s %s" % (x["type"],
                                "; ".join(i.get("description", "")[:45]
                                          for i in x.get("items", []))))
    for entry in unconnected[:20]:
        print("  ~ %s" % "  <->  ".join(i.get("description", "")[:42]
                                        for i in entry.get("items", [])))


if __name__ == "__main__":
    main()
