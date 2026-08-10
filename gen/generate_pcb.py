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

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import pcbnew  # noqa: E402  (needs KiCad's python)

import generate_schematic as sch  # noqa: E402

# ------------------------------------------------------------------ setup ----

BOARD_W = 84.0
BOARD_H = 72.0
FILLET = 3.0
OUT = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pcb")

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
    "JST B4B-PH-K-S(LF)(SN)":   (5.0, 50.0, 270),   # power/CAN, left edge
    "ESP32-S3-WROOM-1-N16R8":   (52.0, 6.8, 0),     # antenna overhangs top edge
    "HRO TYPE-C-31-M-12":       (78.5, 8.0, 90),    # right edge, opening out
    "Hirose DM3D-SF":           (75.0, 41.0, 270),  # right edge, card out
    "value:Spare IO":           (11.5, 3.0, 90),    # top edge, row along X
    "value:UART0":              (12.0, 69.5, 90),
    "value:I2C / Qwiic":        (22.0, 69.5, 90),   # bottom edge, left
    "value:Rail break-out":     (32.0, 69.5, 90),
    "value:SPI":                (42.0, 69.5, 90),
    "value:WS2812":             (78.0, 40.0, 0),    # right edge near LEDs
}

# Buck islands — kept in sync with gen/route_bucks.py PLACE. Keyed by ref
# after assign_refs() so regenerating the board does not scatter the loops.
REF_FIXED = {
    "C4":  (34.5, 44.0, 0),
    "C3":  (34.5, 47.8, 0),
    "U2":  (41.0, 45.0, 0),
    "C5":  (41.0, 39.5, 0),
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

HOLES = [(4.0, 4.0), (4.0, 68.0), (66.0, 4.0), (80.0, 26.0)]

# Auto-packed zones: (x, y, w, h) shelves filled left-to-right, top-to-bottom.
# Order matters: the first predicate that matches a part claims it.
ZONES = [
    # (name, rect, predicate)
    ("adc",       (43.0, 29.0,  8.0, 13.0), lambda p, n, s: p["mpn"] == "ADS1115IDGSR"),
    ("ch1",       ( 9.5,  7.0,  8.0, 24.0), lambda p, n, s: n & {"AIN1_A", "AIN1_PU", "AIN1_R1", "AIN1_R2", "AIN1_IN", "AIN1"}),
    ("ch2",       (18.0,  7.0,  8.0, 24.0), lambda p, n, s: n & {"AIN2_A", "AIN2_PU", "AIN2_R1", "AIN2_R2", "AIN2_IN", "AIN2"}),
    ("ch3",       (26.5,  7.0,  8.0, 24.0), lambda p, n, s: n & {"AIN3_A", "AIN3_PU", "AIN3_R1", "AIN3_R2", "AIN3_IN", "AIN3"}),
    ("ch4",       (34.6,  7.0,  7.6, 24.0), lambda p, n, s: n & {"AIN4_A", "AIN4_PU", "AIN4_R1", "AIN4_R2", "AIN4_IN", "AIN4"}),
    ("adc",       (43.0, 29.0,  7.5, 13.0), lambda p, n, s: s == "Analog Inputs"),
    ("usb",       (62.0, 13.5, 14.0,  9.5), lambda p, n, s: n & {"USB_DP_CON", "USB_DM_CON", "USB_CC1", "USB_CC2", "VBUS_IN", "VBUS", "USB_DP", "USB_DM"}),
    ("buttons",   (73.0, 49.0, 10.0, 18.0), lambda p, n, s: p["value"] in ("RESET", "BOOT") or n & {"LED1_A", "LED2_A", "LED1", "LED2"}),
    ("sdpwr",     (55.5, 44.0, 12.5,  6.2), lambda p, n, s: n & {"SD_PG", "SD_EN_G", "SD_PWR_EN"}),
    ("sd",        (50.5, 29.0, 12.0, 15.0), lambda p, n, s: s == "SD Card"),
    ("can",       ( 9.5, 29.0, 21.0, 15.0), lambda p, n, s: s == "CAN"),
    ("sens5v",    ( 9.5, 66.0, 25.0,  5.5), lambda p, n, s: n & {"VSENS_F", "+5VS"} and s == "Power"),
    ("rail5",     (38.5, 44.0, 17.0, 20.0), lambda p, n, s: s == "Power" and n & {"SW_5V", "BST_5V", "RON_5V", "FB_5V", "RAMP_5V", "EN_5V", "PG_5V", "+5V"}),
    ("rail3",     (55.5, 50.4, 17.0, 16.6), lambda p, n, s: s == "Power" and n & {"SW_3V3", "BST_3V3", "RON_3V3", "FB_3V3", "RAMP_3V3", "EN_3V3", "PG_3V3"}),
    ("frontend",  ( 9.5, 44.0, 29.0, 21.5), lambda p, n, s: s == "Power" and n & {"VBAT_IN", "VBAT_F", "VBAT_FB", "GATE_RB", "VCAP", "VBAT_UVLO", "+VBAT"}),
    ("mcu_misc",  (63.0, 23.3, 13.0,  9.7), lambda p, n, s: s == "MCU"),
    ("pwr_misc",  (31.5, 30.0, 10.5, 13.5), lambda p, n, s: True),
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
            board.Add(fp)

            if p["footprint"].startswith("MountingHole"):
                hole_parts.append(fp)
                continue
            if p["ref"] in REF_FIXED:
                x, y, rot = REF_FIXED[p["ref"]]
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

    # --- shelf-pack each zone ---------------------------------------------
    GAP = 0.4
    report = []
    for name, (zx, zy, zw, zh), _ in ZONES:
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
                x, y = zx, y + row_h + GAP
                row_h = 0.0
            bb = courtyard_box(fp)
            # position so the courtyard's top-left lands at (x, y)
            fp.SetPosition(pt(x + w / 2.0, y + h / 2.0))
            bb = courtyard_box(fp)
            fp.Move(pt(x, y) - pcbnew.VECTOR2I(bb.GetLeft(), bb.GetTop()))
            x += w + GAP
            row_h = max(row_h, h)
            if y + row_h > zy + zh:
                overflow = True
        used_h = (y + row_h) - zy
        report.append((name, len(parts), used_h, zh, overflow))

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
    pcbnew.SaveBoard(OUT, board)

    print("board       : %s" % OUT)
    print("size        : %.0f x %.0f mm, 4 layer" % (W, H))
    n_fp = len(list(board.GetFootprints()))
    print("footprints  : %d (%d fixed, %d holes)" % (n_fp, len(fixed_parts), len(hole_parts)))
    print("nets        : %d" % len(nets))
    for name, count, used, zh, overflow in report:
        flag = "  OVERFLOW" if overflow else ""
        print("  zone %-9s %2d parts, %5.1f/%4.1f mm used%s" % (name, count, used, zh, flag))


if __name__ == "__main__":
    main()
