#!/usr/bin/env python3
"""
Generate the KiCad board for the ESP32-S3 CAN + microSD automotive logger.

Run with KiCad's bundled Python so the pcbnew API is available:

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/generate_pcb.py

The part and net tables in gen/generate_schematic.py are the single source
of truth: this script imports them, loads each part's real footprint from the
installed KiCad libraries (or the project library), assigns every pad its
net, and places parts into functional zones:

  - left edge: sensor harness (J8) and power/CAN harness (J1)
  - top: analog front end + ADS1115, ESP32 module with the antenna over a
    copper keepout at the top edge
  - right edge: USB-C and microSD for bench access, buttons and LEDs
  - bottom strip: battery front end, then the 5V and 3V3 bucks
  - inner layers: solid GND plane (In1) and 3V3 plane (In2)

Output is placed-but-unrouted: run DRC (gen/validate_pcb.py) to confirm the
placement is legal, then route interactively.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import pcbnew  # noqa: E402  (needs KiCad's python)

import generate_schematic as sch  # noqa: E402

# ------------------------------------------------------------------ setup ----

BOARD_W = 84.0
BOARD_H = 74.0
FILLET = 3.0
OUT = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")
PRO = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pro")


# Netclasses live in the .kicad_pro, and gen/generate_schematic.py is what
# writes them.  This module builds its board with CreateEmptyBoard(), which
# knows only the Default class, and saving that board rewrites the project
# file from the board's own settings -- silently throwing Power and CAN
# away.  Everything downstream then routes at the default 0.2 mm, including
# the 1 A rails, and nothing complains: the widths are legal, just wrong.
#
# So the classes are lifted out before the save and put back after it.

def read_net_settings():
    try:
        with open(PRO, encoding="utf-8") as fh:
            return json.load(fh).get("net_settings")
    except Exception:
        return None


def restore_net_settings(keep):
    if not keep:
        return "none to restore -- run gen/generate_schematic.py first"
    with open(PRO, encoding="utf-8") as fh:
        pro = json.load(fh)
    if pro.get("net_settings") == keep:
        return "unchanged"
    pro["net_settings"] = keep
    with open(PRO, "w", encoding="utf-8") as fh:
        json.dump(pro, fh, indent=2)
    names = [c.get("name") for c in keep.get("classes", [])]
    return "restored %s (%d patterns)" % (
        ", ".join(n for n in names if n), len(keep.get("netclass_patterns", [])))

KICAD_FP = None
for pat in (r"C:\Program Files\KiCad\9.0\share\kicad\footprints",
            "/usr/share/kicad/footprints",
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"):
    if os.path.isdir(pat):
        KICAD_FP = pat
        break

PROJECT_FP = os.path.join(PROJ, "footprints")


def mm(v):
    return pcbnew.FromMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def lib_path(lib):
    if os.path.isdir(os.path.join(PROJECT_FP, lib + ".pretty")):
        return os.path.join(PROJECT_FP, lib + ".pretty")
    return os.path.join(KICAD_FP, lib + ".pretty")


# ------------------------------------------------------- part -> zone map ----

# Fixed placements for the structural parts: identified by MPN or value so the
# mapping survives reference renumbering.  (x, y, rotation_degrees)
FIXED = {
    "JST B8B-PH-K-S(LF)(SN)":   (5.0, 18.0, 270),   # sensor harness, left edge
    "JST B4B-PH-K-S(LF)(SN)":   (5.0, 50.0, 270),   # power/CAN harness
    "ESP32-S3-WROOM-1-N16R8":   (52.0, 6.8, 0),     # antenna overhangs top edge
    # 2.5 mm inboard of flush.  The A/B duplicate pins (D+ on A6/B6, D- on
    # A7/B7) have to be tied together on copper, and that needs two columns
    # of vias in the channel between the 0.5 mm-pitch pad row and the edge
    # keepout -- about 2.4 mm.  The mouth ends ~2 mm inside the outline; the
    # case opening sets plug access anyway.
    "HRO TYPE-C-31-M-12":       (76.0, 8.0, 270),   # right edge, opening out
    "Hirose DM3D-SF":           (75.0, 41.0, 270),  # right edge, card out
    "value:Spare IO":           (10.0, 3.0, 90),    # top edge
    "value:SPI":                (21.7, 3.0, 90),    # top edge
    "value:UART0":              (26.0, 71.5, 90),   # bottom edge
    "value:I2C / Qwiic":        (41.0, 71.5, 90),
    "value:Rail break-out":     (56.0, 71.5, 90),
    "value:WS2812":             (70.5, 71.5, 90),   # shift-light strip
    "value:RESET":              (78.0, 56.5, 0),    # right edge, case access
    "value:BOOT":               (78.0, 63.5, 0),
    "value:amber":              (75.5, 50.8, 0),    # status LEDs by the edge
    "value:blue":               (72.0, 50.8, 0),
}

# The two LM5164 buck islands, stacked mid-board left of the SD socket, with
# tight SW loops and input caps at the VIN pins (geometry from
# gen/route_bucks.py's original layout).  Parts here are generic values, so
# they are identified by (sheet, value, exact net set) -- stable no matter how
# references renumber.  Entries are consumed in schematic order for twins.
BUCK_FIXED = [
    # sheet,   value,        nets,                          x,    y,   rot
    # ---- 5V island: EN/BST/RA row, IC + inductor, input caps at VIN,
    # ---- RON/FB row, ripple/PG row, output caps at the inductor
    ("Power", "100k",       {"+VBAT", "EN_5V"},          (38.3, 37.6, 0)),
    ("Power", "2.2nF 50V",  {"BST_5V", "SW_5V"},         (42.0, 37.6, 0)),
    ("Power", "121k",       {"SW_5V", "RAMP_5V"},        (45.7, 37.6, 0)),
    ("Power", "LM5164 (5V)", None,                       (42.5, 42.0, 0)),
    ("Power", "33uH 3A",    {"SW_5V", "+5V"},            (53.0, 42.0, 0)),
    ("Power", "100nF 100V", {"+VBAT", "GND"},            (36.3, 42.2, 180)),
    ("Power", "10uF 100V",  {"+VBAT", "GND"},            (36.2, 45.1, 180)),
    ("Power", "31.6k",      {"RON_5V", "GND"},           (38.5, 48.2, 0)),
    ("Power", "31.6k",      {"FB_5V", "GND"},            (42.5, 48.2, 0)),
    ("Power", "100k",       {"+5V", "FB_5V"},            (46.5, 48.2, 0)),
    ("Power", "22uF 16V",   {"+5V", "GND"},              (51.5, 47.8, 0)),
    ("Power", "22uF 16V",   {"+5V", "GND"},              (56.5, 47.8, 0)),
    ("Power", "100nF 16V",  {"+5V", "GND"},              (56.5, 50.4, 0)),
    ("Power", "3.3nF 50V",  {"RAMP_5V", "+5V"},          (51.0, 50.8, 0)),
    ("Power", "270pF 50V",  {"RAMP_5V", "FB_5V"},        (40.5, 51.0, 0)),
    ("Power", "100k",       {"PG_5V", "+3V3"},           (44.5, 51.0, 0)),
    ("Power", "+5V",        {"+5V"},                     (33.8, 37.4, 0)),

    # ---- 3V3 island: same pattern, one pitch down
    ("Power", "100k",       {"+VBAT", "EN_3V3"},         (38.3, 53.4, 0)),
    ("Power", "2.2nF 50V",  {"BST_3V3", "SW_3V3"},       (42.0, 53.4, 0)),
    ("Power", "95.3k",      {"SW_3V3", "RAMP_3V3"},      (45.7, 53.4, 0)),
    ("Power", "LM5164 (3V3)", None,                      (42.5, 57.8, 0)),
    ("Power", "22uH 3A",    {"SW_3V3", "+3V3"},          (53.0, 57.8, 0)),
    ("Power", "20.5k",      {"RON_3V3", "GND"},          (38.5, 64.0, 0)),
    ("Power", "57.6k",      {"FB_3V3", "GND"},           (42.5, 64.0, 0)),
    ("Power", "100k",       {"+3V3", "FB_3V3"},          (46.5, 64.0, 0)),
    ("Power", "22uF 6.3V",  {"+3V3", "GND"},             (51.5, 63.6, 0)),
    ("Power", "22uF 6.3V",  {"+3V3", "GND"},             (56.5, 63.6, 0)),
    ("Power", "100nF 16V",  {"+3V3", "GND"},             (56.5, 66.2, 0)),
    ("Power", "3.3nF 50V",  {"RAMP_3V3", "+3V3"},        (51.0, 66.6, 0)),
    ("Power", "270pF 50V",  {"RAMP_3V3", "FB_3V3"},      (40.5, 66.8, 0)),
    ("Power", "100k",       {"PG_3V3", "+3V3"},          (44.5, 66.8, 0)),
    ("Power", "+3V3",       {"+3V3"},                    (33.5, 68.2, 0)),
]


HOLES = [(4.0, 4.0), (4.0, 70.0), (66.0, 4.0), (80.0, 26.0)]

# Auto-packed zones: (x, y, w, h) shelves filled left-to-right, top-to-bottom.
# Order matters: the first predicate that matches a part claims it.
ZONES = [
    # (name, rect, predicate)
    ("adc",       (43.0, 29.0,  7.5,  7.0), lambda p, n, s: p["mpn"] == "ADS1115IDGSR" or (s == "Analog Inputs" and n <= {"+3V3", "GND"})),
    ("ch1",       ( 9.5,  7.0,  8.0, 24.0), lambda p, n, s: n & {"AIN1_A", "AIN1_PU", "AIN1_R1", "AIN1_R2", "AIN1_IN", "AIN1"}),
    ("ch2",       (18.0,  7.0,  8.0, 24.0), lambda p, n, s: n & {"AIN2_A", "AIN2_PU", "AIN2_R1", "AIN2_R2", "AIN2_IN", "AIN2"}),
    ("ch3",       (26.5,  7.0,  8.0, 24.0), lambda p, n, s: n & {"AIN3_A", "AIN3_PU", "AIN3_R1", "AIN3_R2", "AIN3_IN", "AIN3"}),
    ("ch4",       (34.6,  7.0,  7.6, 24.0), lambda p, n, s: n & {"AIN4_A", "AIN4_PU", "AIN4_R1", "AIN4_R2", "AIN4_IN", "AIN4"}),
    ("ws2812",    (59.0, 58.3, 14.2,  3.8), lambda p, n, s: n & {"LED_DIN_MCU", "LED_DIN_A", "LED_DIN", "LED_5V"}),
    ("ledr",      (78.5, 48.7,  5.0,  4.6), lambda p, n, s: n & {"LED1_A", "LED2_A"}),
    ("usb",       (62.0, 14.2, 13.0,  8.8), lambda p, n, s: n & {"USB_DP_CON", "USB_DM_CON", "USB_CC1", "USB_CC2", "VBUS_IN", "VBUS", "USB_DP", "USB_DM"}),
    ("sdpwr",     (59.0, 49.3, 11.0,  8.5), lambda p, n, s: n & {"SD_PG", "SD_EN_G", "SD_PWR_EN"}),
    ("sd",        (49.8, 21.4, 13.2, 15.0), lambda p, n, s: s == "SD Card"),
    ("can",       ( 9.5, 28.2, 21.0, 15.0), lambda p, n, s: s == "CAN"),
    ("sens5v",    (59.0, 62.5, 14.5,  6.9), lambda p, n, s: n & {"VSENS_F", "+5VS"} and s == "Power"),
    ("frontend",  ( 9.2, 43.3, 24.4, 25.0), lambda p, n, s: s == "Power" and n & {"VBAT_IN", "VBAT_F", "VBAT_FB", "GATE_RB", "VCAP", "VBAT_UVLO", "+VBAT"}),
    ("mcu_misc",  (63.0, 23.3, 13.0,  9.7), lambda p, n, s: s == "MCU"),
    ("pwr_misc",  (30.7, 23.9, 10.4, 12.0), lambda p, n, s: True),
]


def zone_for(part, sheet_name):
    nets = set(part["pins"].values())
    for name, rect, pred in ZONES:
        if pred(part, nets, sheet_name):
            return name
    return "pwr_misc"


# ------------------------------------------------------------------ build ----

def load_footprint(fpid):
    lib, name = fpid.split(":", 1)
    fp = pcbnew.FootprintLoad(lib_path(lib), name)
    if fp is None:
        raise SystemExit("footprint not found: " + fpid)
    return fp


def courtyard_box(fp):
    """Courtyard bbox if present, else the footprint bbox."""
    try:
        poly = fp.GetCourtyard(pcbnew.F_CrtYd)
        if poly.OutlineCount():
            return poly.BBox()
    except Exception:
        pass
    return fp.GetBoundingBox()


def main():
    sch.assign_refs()

    board = pcbnew.CreateEmptyBoard()
    board.SetCopperLayerCount(4)
    bds = board.GetDesignSettings()
    # JLCPCB 4-layer (JLC04161H-7628) allows 0.2mm via drills; the LM5164
    # thermal-via footprints use exactly that.
    bds.m_MinThroughDrill = mm(0.2)

    # --- nets -------------------------------------------------------------
    netnames = set()
    for sh in sch.SHEETS:
        for p in sh["parts"]:
            netnames.update(p["pins"].values())
    nets = {}
    for name in sorted(netnames):
        item = pcbnew.NETINFO_ITEM(board, name)
        board.Add(item)
        nets[name] = item

    # --- board outline (rounded rectangle) --------------------------------
    def edge_line(x1, y1, x2, y2):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(x1, y1))
        s.SetEnd(pt(x2, y2))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)

    def edge_arc(cx, cy, sx, sy, angle):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        s.SetCenter(pt(cx, cy))
        s.SetStart(pt(sx, sy))
        s.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(angle, pcbnew.DEGREES_T), False)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        board.Add(s)

    f, W, H = FILLET, BOARD_W, BOARD_H
    edge_line(f, 0, W - f, 0)
    edge_line(W, f, W, H - f)
    edge_line(W - f, H, f, H)
    edge_line(0, H - f, 0, f)
    edge_arc(f, f, 0, f, 90)
    edge_arc(W - f, f, W - f, 0, 90)
    edge_arc(W - f, H - f, W, H - f, 90)
    edge_arc(f, H - f, f, H, 90)

    # --- load, net, and bucket every part ---------------------------------
    buckets = {name: [] for name, _, _ in ZONES}
    fixed_parts, hole_parts = [], []
    buck_index = {}
    for sheet_name, value, netset, pos in BUCK_FIXED:
        key = (sheet_name, value,
               frozenset(netset) if netset is not None else None)
        buck_index.setdefault(key, []).append(pos)

    for sh in sch.SHEETS:
        for p in sh["parts"]:
            if p["prefix"].startswith("#"):
                continue
            fp = load_footprint(p["footprint"])
            fp.SetReference(p["ref"])
            fp.SetValue(p["value"])
            for pad in fp.Pads():
                net = p["pins"].get(pad.GetNumber())
                if net is not None:
                    pad.SetNet(nets[net])
            # Solid plane connections rather than thermal spokes for the
            # LM5164 exposed pads (they are the part's heatsink) and for
            # through-hole pads on a plane net, whose spokes get starved by
            # the surrounding via field.  The board is machine-assembled, so
            # the easier-to-hand-solder thermal relief buys nothing.
            full = getattr(pcbnew, "ZONE_CONNECTION_FULL", None)
            if full is not None:
                for pad in fp.Pads():
                    thru = pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
                    ep = p["value"].startswith("LM5164") and pad.GetNumber() == "9"
                    if ep or (thru and p["pins"].get(pad.GetNumber()) in
                              ("GND", "+3V3")):
                        pad.SetLocalZoneConnection(full)

            board.Add(fp)

            if p["footprint"].startswith("MountingHole"):
                hole_parts.append(fp)
                continue
            netset = frozenset(p["pins"].values())
            hit = None
            for key in ((sh["name"], p["value"], netset),
                        (sh["name"], p["value"], None)):
                lst = buck_index.get(key)
                if lst:
                    hit = lst.pop(0)
                    break
            if hit is not None:
                x, y, rot = hit
                fp.SetOrientationDegrees(rot)
                fp.SetPosition(pt(x, y))
                fixed_parts.append(fp)
                continue
            key_m, key_v = p["mpn"], "value:" + p["value"]
            if key_m in FIXED or key_v in FIXED:
                x, y, rot = FIXED[key_m if key_m in FIXED else key_v]
                fp.SetOrientationDegrees(rot)
                fp.SetPosition(pt(x, y))
                fixed_parts.append(fp)
                continue
            buckets[zone_for(p, sh["name"])].append(fp)

    for fp, (x, y) in zip(hole_parts, HOLES):
        fp.SetPosition(pt(x, y))

    unused = sum(len(v) for v in buck_index.values())
    if unused:
        print("WARNING: %d BUCK_FIXED entries matched no part" % unused)

    # Report courtyard overlaps between fixed parts so BUCK_FIXED can be
    # tuned against real numbers instead of guesses.
    boxes = []
    for fp in fixed_parts:
        bb = courtyard_box(fp)
        boxes.append((fp.GetReference(), fp.GetValue()[:14],
                      pcbnew.ToMM(bb.GetLeft()), pcbnew.ToMM(bb.GetTop()),
                      pcbnew.ToMM(bb.GetRight()), pcbnew.ToMM(bb.GetBottom())))
    clashes = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            r1, v1, ax1, ay1, ax2, ay2 = boxes[i]
            r2, v2, bx1, by1, bx2, by2 = boxes[j]
            ox = min(ax2, bx2) - max(ax1, bx1)
            oy = min(ay2, by2) - max(ay1, by1)
            if ox > 0.01 and oy > 0.01:
                print("  FIXED CLASH %s(%s) x %s(%s): dx=%.2f dy=%.2f" %
                      (r1, v1, r2, v2, ox, oy))
                clashes += 1
    if clashes:
        print("  %d fixed-part clashes" % clashes)

    # --- shelf-pack each zone ---------------------------------------------
    GAP = 0.4
    # The microSD block is nothing but series resistors and pull-ups that all
    # have to be routed through; 0.4 mm leaves no channel for a 0.2 mm track
    # with clearance either side, so that one zone gets more elbow room.
    ZONE_GAP = {"sd": 0.85}
    report = []
    for name, (zx, zy, zw, zh), _ in ZONES:
        gap = ZONE_GAP.get(name, GAP)
        parts = buckets[name]
        # tallest first packs shelves tightly
        sized = []
        for fp in parts:
            bb = courtyard_box(fp)
            sized.append((pcbnew.ToMM(bb.GetHeight()), pcbnew.ToMM(bb.GetWidth()), fp))
        sized.sort(key=lambda t: (-t[0], -t[1]))
        x, y, row_h = zx, zy, 0.0
        overflow = False
        for h, w, fp in sized:
            if x + w > zx + zw and x > zx:
                x, y = zx, y + row_h + gap
                row_h = 0.0
            bb = courtyard_box(fp)
            # position so the courtyard's top-left lands at (x, y)
            fp.SetPosition(pt(x + w / 2.0, y + h / 2.0))
            bb = courtyard_box(fp)
            fp.Move(pt(x, y) - pcbnew.VECTOR2I(bb.GetLeft(), bb.GetTop()))
            x += w + gap
            row_h = max(row_h, h)
            if y + row_h > zy + zh:
                overflow = True
        used_h = (y + row_h) - zy
        report.append((name, len(parts), used_h, zh, overflow))

    # --- perimeter keepout -------------------------------------------------
    # Copper-to-edge clearance is 0.5 mm; an autorouter reading the Specctra
    # export does not know that rule, so the band is made an explicit rule
    # area.  It also pulls the plane pours back from the routed edge.
    band = 0.6
    for x1, y1, x2, y2 in ((0, 0, W, band), (0, H - band, W, H),
                           (0, 0, band, H), (W - band, 0, W, H)):
        ka = pcbnew.ZONE(board)
        ka.SetIsRuleArea(True)
        ka.SetDoNotAllowCopperPour(True)
        ka.SetDoNotAllowTracks(True)
        ka.SetDoNotAllowVias(True)
        ka.SetLayerSet(pcbnew.LSET.AllCuMask(4))
        olk = ka.Outline()
        olk.NewOutline()
        for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)):
            olk.Append(mm(x), mm(y))
        board.Add(ka)

    # --- inner-layer planes ------------------------------------------------
    def plane(layer, netname):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(nets[netname])
        z.SetLocalClearance(mm(0.3))
        z.SetMinThickness(mm(0.25))
        ol2 = z.Outline()
        ol2.NewOutline()
        for x, y in ((0, 0), (W, 0), (W, H), (0, H)):
            ol2.Append(mm(x), mm(y))
        board.Add(z)
        return z

    plane(pcbnew.In1_Cu, "GND")
    plane(pcbnew.In2_Cu, "+3V3")

    # ZONE_FILLER segfaults in headless KiCad 9.0.5; the zones are saved
    # unfilled and KiCad regenerates the fill on demand (press B).

    board.SetFileName(OUT)
    keep = read_net_settings()
    pcbnew.SaveBoard(OUT, board)
    restored = restore_net_settings(keep)

    print("board       : %s" % OUT)
    print("netclasses  : %s" % restored)
    print("size        : %.0f x %.0f mm, 4 layer" % (W, H))
    n_fp = len(list(board.GetFootprints()))
    print("footprints  : %d (%d fixed, %d holes)" % (n_fp, len(fixed_parts), len(hole_parts)))
    print("nets        : %d" % len(nets))
    for name, count, used, zh, overflow in report:
        flag = "  OVERFLOW" if overflow else ""
        print("  zone %-9s %2d parts, %5.1f/%4.1f mm used%s" % (name, count, used, zh, flag))


if __name__ == "__main__":
    main()
