#!/usr/bin/env python3
"""
Stencil check: will every joint get the right amount of solder paste?

  python gen/audit_paste.py [--thickness 0.12]

Two failure modes, opposite in sign, and a default 1:1 paste layer walks into
both of them.

**Too little paste — open joints.** Paste releases from a stencil aperture only
if the opening is large compared with the area of wall it has to let go of.
IPC-7525 calls that the *area ratio*: aperture area divided by aperture wall
area, which for a rectangle is `A / (P x t)` with `t` the stencil thickness.
Below about **0.66** the paste preferentially stays in the aperture and the
joint comes out starved. Fine-pitch parts are where this bites -- this board
has 18 USB-C pads under 1 mm2 at 0.5 mm pitch.

**Too much paste — floating and tilting.** A large thermal pad given a solid
1:1 aperture gets far more paste volume than the joint needs. On reflow the
part rides up on molten solder, and the fine pins around the edge lift with it
or tomb-stone. The fix is to *window* the aperture: several smaller openings
totalling 50-80 % of the pad rather than one big one. This board has three
pads that need it -- `U3` and `U4` (the LM5164 thermal pads) and `U5` (the
module).

Nothing else in `gen/` looks at the paste layer, and JLC's default stencil is
generated from it as-drawn. So the numbers below are what would actually be
cut unless the apertures are changed or a custom stencil is asked for.

Stencil thickness defaults to **0.12 mm**, JLCPCB's standard framework
stencil. Pass `--thickness` if ordering something else; the area ratio scales
inversely with it, so a thicker stencil makes the fine-pitch case worse.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
GERBERS = os.path.join(PROJ, "fab", "gerbers")
PCB = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

AREA_RATIO_MIN = 0.66        # IPC-7525
ASPECT_RATIO_MIN = 1.5       # narrowest opening dimension / stencil thickness
BIG_PAD_MM2 = 6.0            # above this, a solid aperture is too much paste
COVERAGE_MAX = 0.80          # windowed thermal pads want 50-80 % coverage


# ----------------------------------------------------------------- gerber ---
def parse_apertures(text):
    """D-code -> (label, area mm2, perimeter mm, min width mm)."""
    ap = {}
    for m in re.finditer(r"%ADD(\d+)([A-Za-z_][\w.]*),([^*]*)\*%", text):
        code, shape, params = int(m.group(1)), m.group(2), m.group(3)
        vals = [float(v) for v in params.split("X") if v.strip()]
        if shape == "C" and vals:
            d = vals[0]
            ap[code] = ("circle d=%.3f" % d, math.pi * d * d / 4, math.pi * d, d)
        elif shape == "R" and len(vals) >= 2:
            w, h = vals[0], vals[1]
            ap[code] = ("rect %.3fx%.3f" % (w, h), w * h, 2 * (w + h), min(w, h))
        elif shape == "O" and len(vals) >= 2:
            w, h = vals[0], vals[1]
            # obround = rectangle with semicircular ends on the short axis
            a, b = max(w, h), min(w, h)
            area = (a - b) * b + math.pi * b * b / 4
            per = 2 * (a - b) + math.pi * b
            ap[code] = ("obround %.3fx%.3f" % (w, h), area, per, b)
        elif shape == "RoundRect" and len(vals) >= 9:
            r = vals[0]
            pts = [(vals[i], vals[i + 1]) for i in range(1, 9, 2)]
            area = abs(sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1]
                           for i in range(4))) / 2
            per = sum(math.dist(pts[i], pts[(i + 1) % 4]) for i in range(4))
            # grow the polygon by the corner radius
            ap[code] = ("roundrect r=%.2f" % r,
                        area + per * r + math.pi * r * r,
                        per + 2 * math.pi * r,
                        min(abs(pts[0][0] - pts[2][0]), abs(pts[0][1] - pts[2][1])) + 2 * r)
        else:
            ap[code] = ("%s (unmodelled)" % shape, 0.0, 0.0, 0.0)
    return ap


def flashes(path):
    """Count D03 flashes per aperture in one gerber."""
    if not os.path.exists(path):
        return {}, {}
    text = open(path, encoding="utf-8", errors="replace").read()
    ap = parse_apertures(text)
    counts = collections.Counter()
    cur = None
    for line in text.splitlines():
        sel = re.fullmatch(r"D(\d+)\*", line.strip())
        if sel:
            cur = int(sel.group(1))
            continue
        if "D03*" in line and cur is not None:
            counts[cur] += 1
    return ap, counts


# -------------------------------------------------------------------- pcb ---
def _area(shape, w, h):
    if shape in ("circle", "oval") and abs(w - h) < 1e-9:
        return math.pi * w * h / 4
    if shape == "oval":
        a, b = max(w, h), min(w, h)
        return (a - b) * b + math.pi * b * b / 4
    return w * h            # rect / roundrect, close enough for a volume check


def pad_paste_areas():
    """(ref, pad, copper mm2, paste mm2, n_apertures) per *copper* pad.

    The subtlety that makes this worth doing properly: KiCad's library
    footprints for exposed-pad packages already carry the windowing, as extra
    pads that live on F.Paste only and sit on top of the copper pad. Comparing
    each paste pad to itself says 100 % every time and means nothing. What
    matters is the paste belonging to a copper pad, summed -- so apertures are
    matched to the copper pad whose outline contains their centre. Everything
    is in footprint-local coordinates, so footprint rotation cancels out.
    """
    out = []
    if not os.path.exists(PCB):
        return out
    text = open(PCB, encoding="utf-8").read()
    head_re = re.compile(r'\n\t\t\(pad "([^"]*)" (\w+) (\w+)')
    for blk in re.finditer(r'\(footprint "[^"]*"(.*?)\n\t\)', text, re.S):
        body = blk.group(1)
        rm = re.search(r'\(property "Reference" "([^"]+)"', body)
        ref = rm.group(1) if rm else "?"
        copper, paste = [], []
        # Split on pad headers rather than matching a fixed field order --
        # pads carry optional keys (locked, thermal_bridge_angle, ...) in
        # whatever order KiCad wrote them, and a strict pattern silently skips
        # exactly the interesting ones: every exposed pad on this board.
        heads = list(head_re.finditer(body))
        for i, pm in enumerate(heads):
            name, ptype, shape = pm.groups()
            chunk = body[pm.end():heads[i + 1].start() if i + 1 < len(heads) else len(body)]
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", chunk)
            sz = re.search(r"\(size ([\d.]+) ([\d.]+)\)", chunk)
            lay = re.search(r"\(layers([^)]*)\)", chunk)
            if not (at and sz and lay):
                continue
            x, y = float(at.group(1)), float(at.group(2))
            w, h = float(sz.group(1)), float(sz.group(2))
            layers = lay.group(1)
            if "Cu" in layers:
                copper.append([name, shape, x, y, w, h, 0.0, 0])
            if "Paste" in layers:
                paste.append((x, y, _area(shape, w, h)))
        # Assign each aperture to the LARGEST copper pad that contains it.
        # Exposed-pad footprints define the EP more than once (SOIC-8-1EP has
        # two pads both called "9", a 2.95x4.9 and a 1.8x4.4), and preferring
        # the smaller one sent all five windows to the wrong copy and left the
        # real EP looking like it had no paste at all -- so it never appeared.
        for px, py, pa in paste:
            best = None
            for c in copper:
                _n, _s, cx, cy, cw, ch, _acc, _k = c
                if abs(px - cx) <= cw / 2 + 1e-6 and abs(py - cy) <= ch / 2 + 1e-6:
                    if best is None or cw * ch > best[4] * best[5]:
                        best = c
            if best is not None:
                best[6] += pa
                best[7] += 1
        # One row per pad name; duplicated definitions collapse to the biggest.
        merged = {}
        for name, shape, x, y, w, h, acc, k in copper:
            a = _area(shape, w, h)
            cur = merged.get(name)
            if cur is None or a > cur[0]:
                merged[name] = [a, acc, k]
            else:
                cur[1] += acc
                cur[2] += k
        for name, (a, acc, k) in merged.items():
            out.append((ref, name, a, acc, k))
    return out


# ------------------------------------------------------------------- main ---
def main():
    ap_arg = argparse.ArgumentParser()
    ap_arg.add_argument("--thickness", type=float, default=0.12,
                        help="stencil foil thickness in mm (JLC standard 0.12)")
    args = ap_arg.parse_args()
    t = args.thickness
    failures, warnings = [], []

    print("Stencil thickness %.3f mm\n" % t)
    print("1. Area ratio per aperture (IPC-7525: >= %.2f releases cleanly)" % AREA_RATIO_MIN)
    print("   %-24s %6s %8s %9s %8s  %s" % ("aperture", "count", "area", "min width", "AR", ""))
    total = 0
    for side, fname in (("F", "esp32s3-can-sd-logger-F_Paste.gbr"),
                        ("B", "esp32s3-can-sd-logger-B_Paste.gbr")):
        aps, counts = flashes(os.path.join(GERBERS, fname))
        if not counts:
            continue
        rows = []
        for code, n in counts.items():
            label, area, per, minw = aps.get(code, ("?", 0, 0, 0))
            if per <= 0:
                warnings.append("%s side: aperture D%d (%s) not modelled" % (side, code, label))
                continue
            ar = area / (per * t)
            asp = minw / t
            rows.append((ar, label, n, area, minw, asp))
            total += n
        rows.sort()
        for ar, label, n, area, minw, asp in rows[:6]:
            flag = ""
            if ar < AREA_RATIO_MIN:
                flag = "FAIL area ratio"
                failures.append("%s: area ratio %.2f < %.2f (%d apertures)" % (label, ar, AREA_RATIO_MIN, n))
            elif asp < ASPECT_RATIO_MIN:
                flag = "warn aspect %.2f" % asp
                warnings.append("%s: aspect ratio %.2f < %.1f" % (label, asp, ASPECT_RATIO_MIN))
            print("   %s %-22s %6d %7.3f %9.3f %8.2f  %s"
                  % (side, label, n, area, minw, ar, flag))
        if len(rows) > 6:
            print("   %s ... %d more aperture types, all above %.2f"
                  % (side, len(rows) - 6, rows[6][0]))
    print("   %d flashes total" % total)

    print("\n2. Large pads: is the paste volume windowed down, or solid?")
    print("   A big terminal on a two-lead part is fine solid -- it self-aligns")
    print("   and has no fine pins to lift. The ones that matter are exposed pads")
    print("   under multi-pin packages, where excess paste floats the whole part.")
    print("   %-6s %-6s %9s %9s %6s %8s  %s"
          % ("ref", "pad", "copper", "paste", "aper", "coverage", ""))
    pads = pad_paste_areas()
    npins = collections.Counter(r for r, _n, _c, _p, _k in pads)
    shown = 0
    for ref, name, copper, paste, k in sorted(pads, key=lambda r: -r[2]):
        if copper < BIG_PAD_MM2 or paste <= 0:
            continue
        multipin = npins[ref] >= 5      # exposed pad on a real IC, not a 2-lead part
        cov = paste / copper
        shown += 1
        verdict = "ok"
        if multipin and cov > COVERAGE_MAX:
            verdict = "FAIL window this (want 50-80%)"
            failures.append("%s pad %s: %.1f mm2 of paste at %.0f%% coverage on a "
                            "%d-pad package -- window the aperture"
                            % (ref, name or "EP", paste, cov * 100, npins[ref]))
        elif multipin and cov < 0.50:
            # Not a defect -- this is KiCad's own library windowing, and less
            # paste is the safe direction for float. But these pads are the
            # heat path for the two converters, and the thermal resistance
            # goes with how much of the pad actually wets.
            verdict = "ok - windowed to %d apertures, but %.0f%% is below the 50%% guide" % (k, cov * 100)
            warnings.append("%s pad %s: %.0f%% paste coverage on a thermal pad -- "
                            "fine for assembly, slightly thin for heat transfer"
                            % (ref, name or "EP", cov * 100))
        elif multipin:
            verdict = "ok - windowed into %d apertures" % k
        else:
            verdict = "ok - few-terminal part, solid is correct"
        print("   %-6s %-6s %8.2f %9.2f %6d %7.0f%%  %s"
              % (ref, name or "EP", copper, paste, k, cov * 100, verdict))
    if not shown:
        print("   (no pads above %.1f mm2)" % BIG_PAD_MM2)

    print("\n%d failures, %d warnings" % (len(failures), len(warnings)))
    for f in failures:
        print("  FAIL  %s" % f)
    for w in warnings[:8]:
        print("  warn  %s" % w)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
