#!/usr/bin/env python3
"""
Route whatever connections are still open, with rip-up and retry.

gen/finish_routing.py closes gaps it can reach with a straight line or a
single corner.  Anything that needs to weave defeats it, and what an
autorouter leaves behind is never that easy: it routes the simple nets
first and fences the awkward ones in.  On this board SD_CLK ran straight
under the module's pads 20 and 21 and walled both of them off, and
USB_DP_CON did the same to D- at the USB-C connector.  No amount of
searching finds a path, because there is not one.

So this does what a person does.  An occupancy grid per copper layer at
0.1 mm, obstacles rasterised from real pad, track, via and rule-area
geometry, and Dijkstra over both layers with a cost for changing layer.
When that fails it works out which nets are pressed up against the dead
end, tears them out, routes the trapped net through the space they
leave, and puts them back on a later pass -- the router now has room
where it did not before.

Same-net copper is never an obstacle, so a trace may run along its own
pad to escape a tight row.  Vias are only placed where a via-sized hole
really fits, a stricter test than the track itself needs.  Ripped nets
are re-routed at their own netclass width, so a power net does not come
back as a signal trace.

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/maze_route.py

Nets that carry the plane stitching or the buck power loops are never
ripped -- that copper is placed deliberately and is not the router's to
rearrange.
"""

from __future__ import annotations

import glob
import heapq
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

try:
    import wx
    wx.DisableAsserts()
except Exception:
    pass

import numpy as np
import pcbnew
from pcbnew import FromMM, ToMM, VECTOR2I, PCB_VIA, PCB_TRACK, VIATYPE_THROUGH

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import netclasses                                          # noqa: E402

BOARD_PATH = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

RES = 0.1             # grid pitch, mm
CLEAR = 0.2           # net-to-net copper clearance the board is built to
EDGE_KEEP = 0.75      # keep generated copper this far inside the outline
WIDTH = 0.2           # default track width
VIA_DIA, VIA_DRILL = 0.5, 0.25      # only a floor; per-net sizes win
WINDOW = 13.0         # search margin around the two endpoints, mm

ORTH, DIAG, VIA_COST = 10, 14, 90    # Dijkstra step costs
MAX_RIP = 7           # how many blocking nets to tear out at once
RIP_LIMIT = 6         # times one net may be torn up before it is left alone
PASSES = 40           # one connection per pass, so allow plenty

F, B = 0, 1
LAYER_OF = {F: pcbnew.F_Cu, B: pcbnew.B_Cu}

# Copper that is placed on purpose and must not be rearranged: the plane
# stitching, and the buck loops from gen/route_bucks.py whose shape is the
# whole point of them.
KEEP = {"GND", "+3V3", "+5V", "+5VS", "+VBAT", "VBAT_FB", "VBUS", "VBUS_IN"}


def mm(v):
    return FromMM(v)


MAX_RIP_PADS = 4      # only tear up point-to-point nets


def rippable(net, pads=None):
    """Whether this net's copper may be torn up and re-routed.

    Nets with many pads are off limits even when they are in the way.  A
    two-pad signal comes back as one trace; a rail like SD_VDD that
    daisy-chains half a dozen decoupling caps comes back as half a dozen
    separate connections, each of which can fail on its own, and one
    awkward signal is not worth that risk.
    """
    if not net or net in KEEP or net.startswith("SW_"):
        return False
    return pads is None or pads.get(net, 0) <= MAX_RIP_PADS


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


# ------------------------------------------------------------- obstacles ----

def collect(board):
    """Every obstacle as (kind, geometry, layers, net, movable, pad).

    layers is drawn from {F, B}; copper on an inner plane is not an
    obstacle here because nothing is ever routed there.  `movable` marks
    copper a rip could actually remove -- tracks and vias.  Pads and
    keepouts stay put no matter which net they belong to, so ripping a
    net must not turn its pads into free space.
    """
    obs = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            ls = pad.GetLayerSet()
            layers = set()
            if ls.Contains(pcbnew.F_Cu):
                layers.add(F)
            if ls.Contains(pcbnew.B_Cu):
                layers.add(B)
            if not layers:
                continue
            # Bounding box, not GetSize(): the latter is in the footprint's
            # own frame, so a rotated connector's pads come out transposed.
            bb = pad.GetBoundingBox()
            obs.append(("rect",
                        ((ToMM(bb.GetLeft()) + ToMM(bb.GetRight())) / 2.0,
                         (ToMM(bb.GetTop()) + ToMM(bb.GetBottom())) / 2.0,
                         ToMM(bb.GetWidth()), ToMM(bb.GetHeight())),
                        layers, pad.GetNetname(), False, pad))

    for t in board.GetTracks():
        if isinstance(t, PCB_VIA):
            p = t.GetPosition()
            d = ToMM(t.GetWidth())
            obs.append(("rect", (ToMM(p.x), ToMM(p.y), d, d),
                        {F, B}, t.GetNetname(), True, None))
        elif t.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu):
            s, e = t.GetStart(), t.GetEnd()
            obs.append(("seg", (ToMM(s.x), ToMM(s.y), ToMM(e.x), ToMM(e.y),
                                ToMM(t.GetWidth())),
                        {F if t.GetLayer() == pcbnew.F_Cu else B},
                        t.GetNetname(), True, None))

    for z in board.Zones():
        if not z.GetIsRuleArea():
            continue
        # The only rule areas are the four axis-aligned perimeter bands, so
        # a bounding box is the shape itself rather than an over-estimate.
        bb = z.GetBoundingBox()
        obs.append(("rect",
                    ((ToMM(bb.GetLeft()) + ToMM(bb.GetRight())) / 2.0,
                     (ToMM(bb.GetTop()) + ToMM(bb.GetBottom())) / 2.0,
                     ToMM(bb.GetWidth()), ToMM(bb.GetHeight())),
                    {F, B}, "~keepout", False, None))
    return obs


def dilate(mask, n):
    out = mask.copy()
    for _ in range(n):
        d = out.copy()
        d[1:, :] |= out[:-1, :]
        d[:-1, :] |= out[1:, :]
        d[:, 1:] |= out[:, :-1]
        d[:, :-1] |= out[:, 1:]
        out = d
    return out


class Grid:
    """Occupancy over a window, one plane per copper layer."""

    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0 = x0, y0
        self.nx = int((x1 - x0) / RES) + 1
        self.ny = int((y1 - y0) / RES) + 1
        xs = x0 + RES * np.arange(self.nx)
        ys = y0 + RES * np.arange(self.ny)
        self.gx, self.gy = np.meshgrid(xs, ys)          # [ny, nx]
        self.blocked = {L: np.zeros((self.ny, self.nx), bool) for L in (F, B)}
        self.viabad = {L: np.zeros((self.ny, self.nx), bool) for L in (F, B)}

    def to_idx(self, x, y):
        return (int(round((y - self.y0) / RES)), int(round((x - self.x0) / RES)))

    def to_mm(self, iy, ix):
        return (self.x0 + ix * RES, self.y0 + iy * RES)

    def _sub(self, xlo, ylo, xhi, yhi):
        """Index window covering an mm box, clipped; None if off-grid."""
        i0 = max(0, int((ylo - self.y0) / RES))
        i1 = min(self.ny - 1, int((yhi - self.y0) / RES) + 1)
        j0 = max(0, int((xlo - self.x0) / RES))
        j1 = min(self.nx - 1, int((xhi - self.x0) / RES) + 1)
        if i0 > i1 or j0 > j1:
            return None
        return i0, i1 + 1, j0, j1 + 1

    def _footprint(self, kind, geom, margin):
        """(slice, distance-array) for one obstacle, or None if off-grid."""
        if kind == "rect":
            cx, cy, w, h = geom
            reach = max(w, h) / 2.0 + margin
            sl = self._sub(cx - reach, cy - reach, cx + reach, cy + reach)
            if sl is None:
                return None
            i0, i1, j0, j1 = sl
            dx = np.maximum(np.abs(self.gx[i0:i1, j0:j1] - cx) - w / 2.0, 0.0)
            dy = np.maximum(np.abs(self.gy[i0:i1, j0:j1] - cy) - h / 2.0, 0.0)
            return sl, np.hypot(dx, dy)

        x1, y1, x2, y2, w = geom
        reach = w / 2.0 + margin
        sl = self._sub(min(x1, x2) - reach, min(y1, y2) - reach,
                       max(x1, x2) + reach, max(y1, y2) + reach)
        if sl is None:
            return None
        i0, i1, j0, j1 = sl
        px, py = self.gx[i0:i1, j0:j1], self.gy[i0:i1, j0:j1]
        vx, vy = x2 - x1, y2 - y1
        L2 = vx * vx + vy * vy
        if L2 == 0:
            d = np.hypot(px - x1, py - y1)
        else:
            t = np.clip(((px - x1) * vx + (py - y1) * vy) / L2, 0.0, 1.0)
            d = np.hypot(px - (x1 + t * vx), py - (y1 + t * vy))
        return sl, d - w / 2.0

    def add(self, obs, net, width, via_dia=VIA_DIA):
        """Rasterise every obstacle that is not on `net`."""
        track_m = CLEAR + width / 2.0
        via_m = CLEAR + via_dia / 2.0
        big = max(track_m, via_m)
        for kind, geom, layers, onet, _mv, _pad in obs:
            if onet == net:
                continue
            got = self._footprint(kind, geom, big)
            if got is None:
                continue
            (i0, i1, j0, j1), d = got
            hit_t, hit_v = d < track_m, d < via_m
            for L in layers:
                self.blocked[L][i0:i1, j0:j1] |= hit_t
                self.viabad[L][i0:i1, j0:j1] |= hit_v

    def mask_for(self, obs, net, width, via_dia=VIA_DIA):
        """Cells this net's *rippable* copper blocks, on either layer."""
        out = np.zeros((self.ny, self.nx), bool)
        margin = max(CLEAR + width / 2.0, CLEAR + via_dia / 2.0)
        for kind, geom, _layers, onet, mv, _pad in obs:
            if onet != net or not mv:
                continue
            got = self._footprint(kind, geom, margin)
            if got is None:
                continue
            (i0, i1, j0, j1), d = got
            out[i0:i1, j0:j1] |= d < margin
        return out


def island(grid, obs, net, seed_xy):
    """The net's existing copper, as {layer: mask}, around a seed point.

    A connection is to a *net*, not to one pad.  Where a pad is already
    tied to its twin -- USB-C carries D- on both A7 and B7 -- reaching
    either end satisfies it, and insisting on the pad DRC happened to
    name can be the difference between an impossible route and an easy
    one: A7 escapes only down a 16 um corridor, finer than any grid, and
    B7 opens onto the whole board.

    Travel inside this copper is free, so the router starts from its
    edge and stops the moment it touches the far side's copper.
    """
    own = {L: np.zeros((grid.ny, grid.nx), bool) for L in (F, B)}
    xfer = np.zeros((grid.ny, grid.nx), bool)
    for kind, geom, layers, onet, _mv, pad in obs:
        if onet != net:
            continue
        got = grid._footprint(kind, geom, RES)
        if got is None:
            continue
        (i0, i1, j0, j1), d = got
        # Strictly inside the copper.  Allowing cells merely *near* it
        # lets a route end half a grid step short of the metal, which
        # looks connected and is not: the track becomes its own island
        # and the same gap gets re-routed for ever.
        inside = d <= 0.0
        if pad is not None:
            # A round through-hole pad does not fill its bounding box --
            # the corners are 30% of the radius outside the copper. Ending
            # a trace there connects nothing, which is exactly how every
            # header on J3/J4/J5/J7 stayed open. Ask the pad itself.
            inside = np.zeros_like(inside)
            for iy in range(i0, i1):
                for ix in range(j0, j1):
                    x, y = grid.to_mm(iy, ix)
                    if pad.HitTest(VECTOR2I(int(mm(x)), int(mm(y)))):
                        inside[iy - i0, ix - j0] = True
        for L in layers:
            own[L][i0:i1, j0:j1] |= inside
        if len(layers) > 1:                  # a via, or a through-hole pad
            xfer[i0:i1, j0:j1] |= inside

    # Keep only what hangs together with the seed.
    sy, sx = grid.to_idx(*seed_xy)
    seeds = [(L, sy, sx) for L in (F, B) if own[L][sy, sx]]
    if not seeds:                            # rounding put us just outside
        for L in (F, B):
            ys, xs = np.nonzero(own[L])
            if len(ys):
                k = int(np.argmin((ys - sy) ** 2 + (xs - sx) ** 2))
                seeds.append((L, int(ys[k]), int(xs[k])))
    out = {L: np.zeros((grid.ny, grid.nx), bool) for L in (F, B)}
    stack = []
    for L, iy, ix in seeds:
        out[L][iy, ix] = True
        stack.append((L, iy, ix))
    while stack:
        L, iy, ix = stack.pop()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            jy, jx = iy + dy, ix + dx
            if not (0 <= jy < grid.ny and 0 <= jx < grid.nx):
                continue
            if own[L][jy, jx] and not out[L][jy, jx]:
                out[L][jy, jx] = True
                stack.append((L, jy, jx))
        other = B if L == F else F
        if xfer[iy, ix] and own[other][iy, ix] and not out[other][iy, ix]:
            out[other][iy, ix] = True
            stack.append((other, iy, ix))
    return out


def seeds_of(grid, side):
    """Free cells on the edge of a copper island, to flood outward from."""
    out = []
    for L in (F, B):
        ys, xs = np.nonzero(side[L] & ~grid.blocked[L])
        out.extend((L, int(y), int(x)) for y, x in zip(ys, xs))
    return out


def reachable(grid, seeds):
    """Every cell the router can get to from `seeds`, as {layer: mask}."""
    ny, nx = grid.ny, grid.nx
    seen = {L: np.zeros((ny, nx), bool) for L in (F, B)}
    stack = []
    for L0, y0, x0 in seeds:
        if not seen[L0][y0, x0]:
            seen[L0][y0, x0] = True
            stack.append((L0, y0, x0))
    while stack:
        L, iy, ix = stack.pop()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            jy, jx = iy + dy, ix + dx
            if not (0 <= jy < ny and 0 <= jx < nx):
                continue
            if grid.blocked[L][jy, jx] or seen[L][jy, jx]:
                continue
            seen[L][jy, jx] = True
            stack.append((L, jy, jx))
        other = B if L == F else F
        if not (grid.viabad[F][iy, ix] or grid.viabad[B][iy, ix]) \
                and not seen[other][iy, ix]:
            seen[other][iy, ix] = True
            stack.append((other, iy, ix))
    return seen


def blockers(grid, obs, regions, net, width, pads=None):
    """Nets pressed against the dead end, worst first.

    `regions` are the reachable sets from both ends of the connection.
    Flooding from one end only is not enough: if that end has the run of
    the board and the other is the boxed-in one, every candidate comes
    back from the wrong side of the wall.

    Only the tightest pocket is scored, though.  An end that can reach
    half the board has a frontier thousands of cells long, and the nets
    along it are mostly innocent bystanders that swamp the handful
    actually doing the trapping.  Where both ends are boxed in about
    equally, both count.
    """
    sizes = [sum(int(seen[L].sum()) for L in (F, B)) for seen in regions]
    tightest = min(sizes) if sizes else 0
    frontier = np.zeros((grid.ny, grid.nx), bool)
    for seen, size in zip(regions, sizes):
        if size > 4 * max(tightest, 1):
            continue
        for L in (F, B):
            frontier |= dilate(seen[L], 6) & grid.blocked[L]
    if not frontier.any():
        return []
    cands = {o[3] for o in obs
             if o[4] and o[3] != net and rippable(o[3], pads)}
    score = Counter()
    for cand in cands:
        n = int((grid.mask_for(obs, cand, width) & frontier).sum())
        if n:
            score[cand] = n
    return [n for n, _ in score.most_common()]


def route(grid, home, target):
    """Dijkstra from one copper island to the other.

    Both ends are {layer: mask} of existing copper.  Every free cell of
    `home` starts at zero cost, and the search ends on first contact
    with `target`; the path between is the copper that has to be added.
    Returns [(layer, iy, ix)] or None.
    """
    ny, nx = grid.ny, grid.nx
    blocked, viabad = grid.blocked, grid.viabad
    steps = ((-1, 0, ORTH), (1, 0, ORTH), (0, -1, ORTH), (0, 1, ORTH),
             (-1, -1, DIAG), (-1, 1, DIAG), (1, -1, DIAG), (1, 1, DIAG))

    dist, prev, heap = {}, {}, []
    for L in (F, B):
        ys, xs = np.nonzero(home[L] & ~blocked[L])
        for iy, ix in zip(ys.tolist(), xs.tolist()):
            node = (L, iy, ix)
            dist[node] = 0
            heap.append((0, node))
    if not heap:
        return None
    heapq.heapify(heap)

    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, 1 << 30):
            continue
        L, iy, ix = node
        if target[L][iy, ix]:
            path = [node]
            while node in prev:
                node = prev[node]
                path.append(node)
            return path[::-1]
        for dy, dx, cost in steps:
            jy, jx = iy + dy, ix + dx
            if not (0 <= jy < ny and 0 <= jx < nx):
                continue
            # Landing on the far side's own copper is always allowed: the
            # metal is already there, whatever else crowds the cell.
            if blocked[L][jy, jx] and not target[L][jy, jx]:
                continue
            if cost == DIAG and blocked[L][iy, jx] and blocked[L][jy, ix]:
                continue                      # do not squeeze between corners
            nxt = (L, jy, jx)
            nd = d + cost
            if nd < dist.get(nxt, 1 << 30):
                dist[nxt] = nd
                prev[nxt] = node
                heapq.heappush(heap, (nd, nxt))
        other = B if L == F else F
        if not (viabad[F][iy, ix] or viabad[B][iy, ix]):
            nxt = (other, iy, ix)
            nd = d + VIA_COST
            if nd < dist.get(nxt, 1 << 30):
                dist[nxt] = nd
                prev[nxt] = node
                heapq.heappush(heap, (nd, nxt))
    return None


def simplify(path):
    """Collapse runs in the same direction into corner points."""
    pts = [path[0]]
    for i in range(1, len(path) - 1):
        pl, py, px = path[i - 1]
        cl, cy, cx = path[i]
        nl, ny, nx = path[i + 1]
        if pl != cl or cl != nl:
            pts.append(path[i])
        elif (cy - py, cx - px) != (ny - cy, nx - cx):
            pts.append(path[i])
    pts.append(path[-1])
    return pts


def verify(grid, path, home, target):
    """Re-check a finished path against the grid before it becomes copper.

    Cheap insurance against the search and the board disagreeing -- if a
    route was planned around obstacles that are still there, this is what
    notices, instead of DRC after the fact.
    """
    for L, iy, ix in path:
        if grid.blocked[L][iy, ix] and not (home[L][iy, ix] or
                                            target[L][iy, ix]):
            return False, "track at %.3f,%.3f" % grid.to_mm(iy, ix)
    for a, b in zip(path, path[1:]):
        if a[0] != b[0] and (grid.viabad[F][a[1], a[2]] or
                             grid.viabad[B][a[1], a[2]]):
            return False, "via at %.3f,%.3f" % grid.to_mm(a[1], a[2])
    return True, ""


def emit(board, grid, path, netinfo, width):
    """Turn a grid path into tracks and vias.  Returns (tracks, vias)."""
    pts = simplify(path)
    via_dia, via_drill = SIZES.via(netinfo.GetNetname())
    ntrack = nvia = 0
    for a, b in zip(pts, pts[1:]):
        la, ay, ax = a
        lb, by, bx = b
        if a == b:
            continue                     # nothing to draw between a point
        if la != lb:
            v = PCB_VIA(board)
            x, y = grid.to_mm(ay, ax)
            v.SetPosition(VECTOR2I(int(mm(x)), int(mm(y))))
            v.SetDrill(mm(via_drill))
            v.SetWidth(mm(via_dia))
            v.SetViaType(VIATYPE_THROUGH)
            v.SetNet(netinfo)
            try:
                v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            except Exception:
                pass
            board.Add(v)
            nvia += 1
            continue
        x1, y1 = grid.to_mm(ay, ax)
        x2, y2 = grid.to_mm(by, bx)
        t = PCB_TRACK(board)
        t.SetStart(VECTOR2I(int(mm(x1)), int(mm(y1))))
        t.SetEnd(VECTOR2I(int(mm(x2)), int(mm(y2))))
        t.SetWidth(mm(width))
        t.SetLayer(LAYER_OF[la])
        t.SetNet(netinfo)
        board.Add(t)
        ntrack += 1
    return ntrack, nvia


def rip(board, nets):
    """Delete every track and via on these nets.  Returns how many."""
    doomed = [t for t in board.GetTracks() if t.GetNetname() in nets]
    for t in doomed:
        board.Remove(t)
    return len(doomed)


SIZES = netclasses.Sizes()


def net_width(board, netinfo):
    return SIZES.track(netinfo.GetNetname())


def endpoints(entry):
    """(net, (x, y, layer), (x, y, layer)) from a DRC unconnected entry."""
    items = entry.get("items", [])
    if len(items) < 2:
        return None
    ends, net = [], None
    for it in items[:2]:
        desc = it.get("description", "")
        if "[" in desc and "]" in desc:
            net = desc[desc.index("[") + 1:desc.index("]")]
        pos = it.get("pos")
        if not pos:
            return None
        ends.append((pos["x"], pos["y"], B if "B.Cu" in desc else F))
    return None if net is None else (net, ends[0], ends[1])


def try_route(board, obs, net, a, b, bounds, width, skip=(), via_dia=None):
    """Build a grid ignoring `skip` nets and search.  -> (grid, path)."""
    (ax, ay, al), (bx, by, bl) = a, b
    bx0, by0, bx1, by1 = bounds
    x0 = max(bx0, min(ax, bx) - WINDOW)
    x1 = min(bx1, max(ax, bx) + WINDOW)
    y0 = max(by0, min(ay, by) - WINDOW)
    y1 = min(by1, max(ay, by) + WINDOW)
    # Only rippable copper disappears: a ripped net keeps its pads.
    live = [o for o in obs if not (o[4] and o[3] in skip)]
    grid = Grid(x0, y0, x1, y1)
    grid.add(live, net, width,
             SIZES.via(net)[0] if via_dia is None else via_dia)
    home = island(grid, live, net, (ax, ay))
    target = island(grid, live, net, (bx, by))
    return grid, route(grid, home, target), home, target


RIP_LOG = os.path.join(PROJ, "logs", "maze_rips.json")

# Exit codes for --once, read by the driver loop.
DID_ONE, ALL_DONE, NO_PROGRESS = 0, 3, 4


def load_torn():
    try:
        with open(RIP_LOG, encoding="utf-8") as fh:
            return Counter(json.load(fh))
    except Exception:
        return Counter()


def save_torn(torn):
    os.makedirs(os.path.dirname(RIP_LOG), exist_ok=True)
    with open(RIP_LOG, "w", encoding="utf-8") as fh:
        json.dump(dict(torn), fh)


def one_pass():
    """Route a single open connection, ripping blockers if it takes it.

    Exactly one, then the process ends.  pcbnew hands back undecorated
    SwigPyObjects once a board has been loaded twice or a track removed,
    so a fresh interpreter per edit is the only reliable way to keep
    going; the driver below re-invokes this.
    """
    violations, unconnected = drc(BOARD_PATH)
    if not unconnected:
        print("   nothing open")
        return ALL_DONE
    print("   %d open, %d violations" % (len(unconnected), len(violations)))

    torn = load_torn()
    board = pcbnew.LoadBoard(BOARD_PATH)
    box = board.GetBoardEdgesBoundingBox()
    bounds = (ToMM(box.GetLeft()) + EDGE_KEEP,
              ToMM(box.GetTop()) + EDGE_KEEP,
              ToMM(box.GetRight()) - EDGE_KEEP,
              ToMM(box.GetBottom()) - EDGE_KEEP)
    obs = collect(board)
    pads = Counter(pad.GetNetname()
                   for fp in board.GetFootprints() for pad in fp.Pads())

    for entry in unconnected:
        ep = endpoints(entry)
        if ep is None:
            continue
        net, a, b = ep
        netinfo = board.FindNet(net)
        if netinfo is None:
            continue
        width = net_width(board, netinfo)

        grid, path, home, target = try_route(board, obs, net, a, b, bounds,
                                             width)
        skip = ()
        if path is None:
            regions = [reachable(grid, seeds_of(grid, side))
                       for side in (home, target)]
            cands = [c for c in blockers(grid, obs, regions, net, width, pads)
                     if torn[c] < RIP_LIMIT]
            for k in range(1, min(MAX_RIP, len(cands)) + 1):
                skip = tuple(cands[:k])
                grid, path, home, target = try_route(board, obs, net, a, b,
                                                     bounds, width, skip=skip)
                if path is not None:
                    break
            if path is None:
                print("   %-12s no path (tried ripping %s)"
                      % (net, ", ".join(cands[:MAX_RIP]) or "nothing"))
                continue

        if len(simplify(path)) < 2 or len(set(path)) < 2:
            # The two islands already touch as far as the grid can tell,
            # yet DRC says otherwise.  Emitting here would lay down a
            # zero-length track that connects nothing and leaves the same
            # gap to be found again next pass.
            print("   %-12s degenerate path, leaving it" % net)
            continue

        ok, why = verify(grid, path, home, target)
        if not ok:
            print("   %-12s path fails its own check (%s), leaving it"
                  % (net, why))
            continue

        # The route was planned through space these nets currently
        # occupy, so they have to come out before it goes in.
        if skip:
            n = rip(board, set(skip))
            for s in skip:
                torn[s] += 1
            save_torn(torn)
            print("   %-12s ripped %s (%d segments)"
                  % (net, ", ".join(skip), n))

        nt, nv = emit(board, grid, path, netinfo, width)
        print("   %-12s routed: %d segments, %d via(s), %.2f mm wide"
              % (net, nt, nv, width))
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        board.Save(BOARD_PATH)
        return DID_ONE

    print("   nothing else routable")
    return NO_PROGRESS


def main():
    import sys
    if "--once" in sys.argv:
        raise SystemExit(one_pass())
    if "--reset" in sys.argv and os.path.exists(RIP_LOG):
        os.remove(RIP_LOG)

    violations, unconnected = drc(BOARD_PATH)
    print("start: %d violations, %d unconnected"
          % (len(violations), len(unconnected)))

    prev, stalled = len(unconnected), 0
    for rnd in range(PASSES):
        print("\n-- pass %d" % (rnd + 1))
        res = subprocess.run([sys.executable, os.path.abspath(__file__),
                              "--once"], capture_output=True, text=True)
        for line in (res.stdout or "").splitlines():
            if "memory leak" in line or "image handler" in line:
                continue
            print(line)
        if res.returncode == ALL_DONE:
            break
        if res.returncode != DID_ONE:
            if res.returncode != NO_PROGRESS:
                print((res.stderr or "").strip()[-1200:])
            print("   stopping")
            break
        # Ripping trades one open connection for several, so the count
        # climbs before it falls and "worse than the best so far" is not
        # a stall.  Chasing its own tail looks different: pass after pass
        # routes something and the count never comes down at all.
        _, unconnected = drc(BOARD_PATH)
        now = len(unconnected)
        stalled = 0 if now < prev else stalled + 1
        prev = now
        if stalled >= 5:
            print("   no headway in %d passes -- stopping" % stalled)
            break

    violations, unconnected = drc(BOARD_PATH)
    print("\nfinal: %d violations, %d unconnected"
          % (len(violations), len(unconnected)))
    for x in violations[:12]:
        print("  ! %-22s %s" % (x["type"],
                                "; ".join(i.get("description", "")[:45]
                                          for i in x.get("items", []))))
    for entry in unconnected[:12]:
        print("  ~ %s" % "  <->  ".join(i.get("description", "")[:42]
                                        for i in entry.get("items", [])))


if __name__ == "__main__":
    main()
