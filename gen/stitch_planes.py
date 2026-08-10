#!/usr/bin/env python3
"""
Give every GND and +3V3 pad a via down to its plane.

On a 4-layer board with solid GND (In1) and +3V3 (In2) pours, a surface
pad on either net is connected by a via next to it, not by a routed
track.  Doing this before autorouting means the router sees the vias as
obstacles and keeps out of the way, and only has signals left to solve.

Placement uses real geometry -- point-to-rectangle and point-to-segment
distances against pads, tracks, keepouts and the board edge -- rather
than bounding boxes, which are far too pessimistic in a dense field and
refuse room that is really there.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/stitch_planes.py
"""

from __future__ import annotations

import math
import os

try:
    import wx
    wx.DisableAsserts()
except Exception:
    pass

import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I, PCB_VIA, PCB_TRACK, VIATYPE_THROUGH

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
BOARD_PATH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

PLANES = {"GND": pcbnew.In1_Cu, "+3V3": pcbnew.In2_Cu}

CLEARANCE = 0.2       # net-to-net copper clearance the board is built to
EDGE_KEEP = 0.75      # keep generated copper this far inside the outline
SIZES = ((0.6, 0.3), (0.5, 0.25))    # via diameter / drill, largest first
                                     # 0.5/0.25 is the board minimum and
                                     # a JLC standard drill


def mm(v):
    return FromMM(v)


def dist_point_rect(px, py, cx, cy, w, h):
    dx = max(abs(px - cx) - w / 2.0, 0.0)
    dy = max(abs(py - cy) - h / 2.0, 0.0)
    return math.hypot(dx, dy)


def dist_point_seg(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    if L2 == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2))
    return math.hypot(px - (x1 + t * vx), py - (y1 + t * vy))


def collect(board):
    """Obstacles as geometry, in mm, with the net they belong to."""
    rects, segs = [], []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            # Bounding box, not GetSize(): the latter is in the footprint's
            # own frame, so a rotated connector's pads come out transposed.
            bb = pad.GetBoundingBox()
            rects.append(((ToMM(bb.GetLeft()) + ToMM(bb.GetRight())) / 2.0,
                          (ToMM(bb.GetTop()) + ToMM(bb.GetBottom())) / 2.0,
                          ToMM(bb.GetWidth()), ToMM(bb.GetHeight()),
                          pad.GetNetname()))
    for t in board.GetTracks():
        if isinstance(t, PCB_VIA):
            p = t.GetPosition()
            d = ToMM(t.GetWidth())
            rects.append((ToMM(p.x), ToMM(p.y), d, d, t.GetNetname()))
        else:
            s, e = t.GetStart(), t.GetEnd()
            segs.append((ToMM(s.x), ToMM(s.y), ToMM(e.x), ToMM(e.y),
                         ToMM(t.GetWidth()), t.GetNetname()))
    for z in board.Zones():
        if not z.GetIsRuleArea():
            continue
        bb = z.GetBoundingBox()
        rects.append(((ToMM(bb.GetLeft()) + ToMM(bb.GetRight())) / 2.0,
                      (ToMM(bb.GetTop()) + ToMM(bb.GetBottom())) / 2.0,
                      ToMM(bb.GetWidth()), ToMM(bb.GetHeight()), "~keepout"))
    return rects, segs


def fits(x, y, dia, net, rects, segs):
    need = dia / 2.0 + CLEARANCE
    for cx, cy, w, h, rnet in rects:
        if rnet == net:
            continue
        if dist_point_rect(x, y, cx, cy, w, h) < need:
            return False
    for x1, y1, x2, y2, w, snet in segs:
        if snet == net:
            continue
        if dist_point_seg(x, y, x1, y1, x2, y2) < need + w / 2.0:
            return False
    return True


def seg_fits(x1, y1, x2, y2, width, net, rects, segs):
    """The escape track from pad to via has to be clear as well."""
    need = width / 2.0 + CLEARANCE
    for cx, cy, w, h, rnet in rects:
        if rnet == net:
            continue
        # sample the segment against the rectangle
        steps = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.1))
        for i in range(steps + 1):
            t = i / float(steps)
            if dist_point_rect(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                               cx, cy, w, h) < need:
                return False
    for sx1, sy1, sx2, sy2, w, snet in segs:
        if snet == net:
            continue
        steps = max(2, int(math.hypot(x2 - x1, y2 - y1) / 0.1))
        for i in range(steps + 1):
            t = i / float(steps)
            if dist_point_seg(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                              sx1, sy1, sx2, sy2) < need + w / 2.0:
                return False
    return True


def main():
    board = pcbnew.LoadBoard(BOARD_PATH)
    box = board.GetBoardEdgesBoundingBox()
    x_lo, x_hi = ToMM(box.GetLeft()) + EDGE_KEEP, ToMM(box.GetRight()) - EDGE_KEEP
    y_lo, y_hi = ToMM(box.GetTop()) + EDGE_KEEP, ToMM(box.GetBottom()) - EDGE_KEEP

    rects, segs = collect(board)

    # Pads that already sit on a plane layer, or already have a via touching
    # them, need nothing.
    have_via = []
    for t in board.GetTracks():
        if isinstance(t, PCB_VIA):
            p = t.GetPosition()
            have_via.append((ToMM(p.x), ToMM(p.y), t.GetNetname()))

    todo = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            net = pad.GetNetname()
            if net not in PLANES:
                continue
            ls = pad.GetLayerSet()
            if ls.Contains(PLANES[net]):
                continue                       # through-hole: already in it
            p = pad.GetPosition()
            bb = pad.GetBoundingBox()
            px, py = ToMM(p.x), ToMM(p.y)
            near = any(vnet == net and math.hypot(vx - px, vy - py) < 1.2
                       for vx, vy, vnet in have_via)
            if near:
                continue
            todo.append((pad, px, py, ToMM(bb.GetWidth()),
                         ToMM(bb.GetHeight()), net))

    print("pads needing a plane via : %d" % len(todo))

    placed, failed, skipped = 0, [], 0
    for pad, px, py, w, h, net in todo:
        if any(vnet == net and math.hypot(vx - px, vy - py) < 1.2
               for vx, vy, vnet in have_via):
            skipped += 1           # a stacked duplicate pad already served
            continue
        netinfo = pad.GetNet()
        layer = (pad.GetLayer() if pad.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu)
                 else pcbnew.F_Cu)
        done = False
        for dia, drill in SIZES:
            base = dia / 2.0 + CLEARANCE
            for step in range(8):
                r = base + 0.18 * step
                for ang in range(0, 360, 30):
                    a = math.radians(ang)
                    cx = px + (w / 2.0 + r) * math.cos(a)
                    cy = py + (h / 2.0 + r) * math.sin(a)
                    if not (x_lo < cx < x_hi and y_lo < cy < y_hi):
                        continue
                    if not fits(cx, cy, dia, net, rects, segs):
                        continue
                    if not seg_fits(px, py, cx, cy, 0.3, net, rects, segs):
                        continue
                    v = PCB_VIA(board)
                    v.SetPosition(VECTOR2I(int(mm(cx)), int(mm(cy))))
                    v.SetDrill(mm(drill))
                    v.SetWidth(mm(dia))
                    v.SetViaType(VIATYPE_THROUGH)
                    v.SetNet(netinfo)
                    try:
                        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
                    except Exception:
                        pass
                    board.Add(v)

                    t = PCB_TRACK(board)
                    t.SetStart(VECTOR2I(int(mm(px)), int(mm(py))))
                    t.SetEnd(VECTOR2I(int(mm(cx)), int(mm(cy))))
                    t.SetWidth(mm(0.3))
                    t.SetLayer(layer)
                    t.SetNet(netinfo)
                    board.Add(t)

                    rects.append((cx, cy, dia, dia, net))
                    segs.append((px, py, cx, cy, 0.3, net))
                    have_via.append((cx, cy, net))
                    placed += 1
                    done = True
                    break
                if done:
                    break
            if done:
                break
        if not done:
            failed.append("%s.%s [%s]" %
                          (pad.GetParentFootprint().GetReference(),
                           pad.GetNumber(), net))

    print("plane vias placed        : %d" % placed)
    if skipped:
        print("already served by a twin  : %d" % skipped)
    if failed:
        print("no room for (%d)         : %s" % (len(failed),
                                                 ", ".join(failed)))

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(BOARD_PATH)
    print("saved                    : %s" % BOARD_PATH)


if __name__ == "__main__":
    main()
