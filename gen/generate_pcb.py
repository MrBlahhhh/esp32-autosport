#!/usr/bin/env python3
"""
Generate the KiCad board for the ESP32-S3 CAN + microSD automotive logger.

Run with KiCad's bundled Python so the pcbnew API is available:

  "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" gen/generate_pcb.py

The part and net tables in gen/generate_schematic.py are the single source
of truth: this script imports them, loads each part's real footprint from the
installed KiCad libraries (or the project library), assigns every pad its
net, and places parts into functional zones:

  - left edge: sensor harness (J10) and power/CAN harness (J1)
  - top: analog front end + ADS1115, ESP32 module with the antenna over a
    copper keepout at the top edge
  - right edge: USB-C and microSD for bench access, buttons and LEDs
  - middle: the two stacked buck islands
  - bottom edge: battery front end, and the UART0 / I2C / rail / WS2812
    headers
  - inner layers: solid GND plane (In1) and 3V3 plane (In2)

Output is placed but unrouted; gen/build_board.py runs this as stage 1 and
carries on through routing. The netclasses live in the .kicad_pro and are
preserved across this script's save -- see read_net_settings() below.
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
BOARD_H = 100.0  # 74 -> 80 for the Rev B parts, 80 -> 84 to put the
                 # sensor-5V block on the same side as what it feeds,
                 # 84 -> 100 for the power-fail ride-through: two 16 mm
                 # electrolytic cans plus the detector and the sensor-rail
                 # switch had no 11 x 11 mm of free board between them.
                 # 100 mm is still inside JLC's cheap 100 x 100 tier.
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


def full_value(p):
    """'100nF' + '100V' -> '100nF 100V'.

    The schematic tables split ratings out of VALUE so review tools can
    parse them, but the placement tables below are keyed on the rating as
    written, which reads better and disambiguates (there are 16 V and
    100 V 100nF parts). Reassemble for lookup only; the board's own VALUE
    field stays bare, matching the schematic.
    """
    return " ".join(x for x in (p["value"], p.get("voltage", ""),
                                p.get("tolerance", "")) if x)


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
    # The fab outline runs y -3.65..+3.65 and the signal pads sit at y -4.04,
    # outside it: the contacts leave the REAR of the shell, so at 0 degrees
    # the mouth faces +y, and the pad row faces the other way.  At 90 the
    # mouth points out of the right edge and the pads face inboard, which is
    # where the D+/D- via columns and the USBLC6 already are.
    #
    # The shell face sits 0.5 mm inside the outline.  It was 2 mm, which is
    # enough laminate in front of the mouth to stop a plug's overmould
    # seating; 0.5 mm is as close as the body can go and still be inside the
    # board for assembly.
    "HRO TYPE-C-31-M-12":       (79.85, 16.0, 90),  # right edge, opening out
    "Hirose DM3D-SF":           (75.0, 41.0, 270),  # right edge, card out
    "value:Spare IO":           (10.0, 3.0, 90),    # top edge, clear of H1
    "value:SPI":                (27.5, 3.0, 90),    # top edge
    "value:UART0":              (26.0, 97.5, 90),   # bottom edge
    "value:I2C / Qwiic":        (41.0, 97.5, 90),
    "value:Rail break-out":     (56.0, 97.5, 90),
    # 1.5 mm left of centre in its slot, so H4's keepout ring clears the
    # connector body by 0.65 mm.
    "value:WS2812":             (69.0, 97.5, 90),   # shift-light strip
    "value:RESET":              (78.0, 56.5, 0),    # right edge, case access
    "value:BOOT":               (78.0, 63.5, 0),
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
    ("Power", "10nF 100V",  {"EN_5V", "GND"},            (34.5, 39.9, 0)),
    ("Power", "+5V",        {"+5V"},                     (33.8, 37.4, 0)),

    # ---- 3V3 island: same pattern, one pitch down
    # This converter had no input capacitor of its own and reached
    # across to the 5 V island's pair, 12.6 mm away. Same offsets
    # from the IC as that island uses.
    ("Power", "100nF 100V", {"+VBAT", "GND"},            (36.3, 58.0, 180)),
    ("Power", "10uF 100V",  {"+VBAT", "GND"},            (36.2, 60.9, 180)),
    ("Power", "100k",       {"+VBAT", "EN_3V3"},         (38.3, 53.4, 0)),
    ("Power", "2.2nF 50V",  {"BST_3V3", "SW_3V3"},       (42.0, 53.4, 0)),
    ("Power", "95.3k",      {"SW_3V3", "RAMP_3V3"},      (45.7, 53.4, 0)),
    ("Power", "LM5164 (3V3)", None,                      (42.5, 57.8, 0)),
    ("Power", "22uH 3A",    {"SW_3V3", "+3V3"},          (53.0, 57.8, 0)),
    ("Power", "20.5k",      {"RON_3V3", "GND"},          (38.5, 64.0, 0)),
    ("Power", "57.6k",      {"FB_3V3", "GND"},           (42.5, 64.0, 0)),
    ("Power", "100k",       {"+3V3", "FB_3V3"},          (46.5, 64.0, 0)),
    ("Power", "22uF 16V",   {"+3V3", "GND"},             (51.5, 63.6, 0)),
    ("Power", "22uF 16V",   {"+3V3", "GND"},             (56.5, 63.6, 0)),
    ("Power", "100nF 16V",  {"+3V3", "GND"},             (56.5, 66.2, 0)),
    ("Power", "3.3nF 50V",  {"RAMP_3V3", "+3V3"},        (51.0, 66.6, 0)),
    ("Power", "270pF 50V",  {"RAMP_3V3", "FB_3V3"},      (40.5, 66.8, 0)),
    ("Power", "100k",       {"PG_3V3", "+3V3"},          (44.5, 66.8, 0)),
    ("Power", "10nF 100V",  {"EN_3V3", "GND"},           (34.5, 55.7, 0)),
    ("Power", "+3V3",       {"+3V3"},                    (33.5, 68.2, 0)),
]

# Bypass capacitors that have to sit at the pin they bypass rather than
# wherever the zone packer has room.  These share BUCK_FIXED's machinery --
# same (sheet, value, net signature) key, same position table -- but they
# are not part of a buck, so they are listed separately.
#
# gen/audit_pcb.py found both of these 8 mm from their supply pin, at the
# far end of a zone.  A 100 nF that far away is not a bypass: the loop it
# closes is longer than the one it is supposed to shorten, and for the CAN
# transceiver that loop is the return path for a 1 Mbit/s driver.
PIN_FIXED = [
    # sheet, value,        nets,              x,    y,   rot     serves
    ("MCU", "100nF 16V", {"+5V", "GND"}, (50.8, 84.5, 180)),  # U6 pin 5
    ("CAN", "100nF 16V", {"+5V", "GND"}, ( 7.6, 37.4, 180)),  # U7 pin 3
    # The ride-through bank.  Fixed rather than zone-packed because a part
    # with a 20.8 mm courtyard steamrolls a shelf packer, and split one can
    # per side because two of them do not fit abreast anywhere: the left one
    # under the front end that feeds it, the right one under the buttons.
    ("Power", "220uF 100V", {"+VBAT", "GND"}, (20.5, 83.5, 0)),
    ("Power", "220uF 100V", {"+VBAT", "GND"}, (72.0, 77.0, 0)),
]


# Mounting holes: one per corner, 4 mm in from each, so the four of them
# form a square.  They used to sit inboard and at three different insets --
# a fixing beside the module, another halfway down the right edge, and the
# bottom pair 6 mm out of line with the top -- which is neither symmetric
# nor useful.  4 mm is set by H1: any further in and its keepout ring eats
# into J7, and the top header row has no slack to give.
HOLES = [(4.0, 4.0), (4.0, 96.0), (80.0, 4.0), (80.0, 96.0)]

# How much of a zone's spare height may go between its rows.  Silkscreen
# reference text is 0.8 mm, so a millimetre on top of the 0.4 mm packing gap
# is the difference between text that fits and text that collides.
ROW_SLACK = 1.0

# Auto-packed zones: (x, y, w, h) shelves filled left-to-right, top-to-bottom.
# Order matters: the first predicate that matches a part claims it.
ZONES = [
    # (name, rect, predicate)
    ("adc",       (43.0, 29.0,  7.5,  7.0), lambda p, n, s: p["mpn"] == "ADS1115IDGSR" or (s == "Analog Inputs" and n <= {"+3V3", "GND"})),
    ("ch1",       ( 9.5,  7.0,  8.0, 26.0), lambda p, n, s: n & {"AIN1_A", "AIN1_PU", "AIN1_R1", "AIN1_R2", "AIN1_IN", "AIN1"}),
    ("ch2",       (18.0,  7.0,  8.0, 26.0), lambda p, n, s: n & {"AIN2_A", "AIN2_PU", "AIN2_R1", "AIN2_R2", "AIN2_IN", "AIN2"}),
    ("ch3",       (26.5,  7.0,  8.0, 26.0), lambda p, n, s: n & {"AIN3_A", "AIN3_PU", "AIN3_R1", "AIN3_R2", "AIN3_IN", "AIN3"}),
    ("ch4",       (34.6,  7.0,  7.6, 26.0), lambda p, n, s: n & {"AIN4_A", "AIN4_PU", "AIN4_R1", "AIN4_R2", "AIN4_IN", "AIN4"}),
    ("ws2812",    (48.5, 85.8, 16.0,  9.6), lambda p, n, s: n & {"LED_DIN_MCU", "LED_DIN_A", "LED_DIN", "LED_5V"} or (s == "MCU" and n == {"+5V", "GND"})),
    # Ride-through support: the power-fail detector and the sensor-rail
    # switch, in the band the bottom-edge furniture vacated when the board
    # grew.  Must be listed before `frontend` and `sens5v`, both of whose
    # predicates would otherwise claim the divider and the switch.
    ("ridethru",  (34.5, 69.0, 14.5, 11.0), lambda p, n, s: s == "Power" and bool(n & {"PFD_SENSE", "PWR_FAIL", "SENS_G", "SENS_EN_G"})),
    ("usb",       (62.0,  8.0, 13.0, 14.0), lambda p, n, s: n & {"USB_DP_CON", "USB_DM_CON", "USB_CC1", "USB_CC2", "VBUS_IN", "VBUS", "USB_DP", "USB_DM"}),
    ("sdpwr",     (59.0, 49.3, 11.0,  8.5), lambda p, n, s: n & {"SD_PG", "SD_EN_G", "SD_PWR_EN"}),
    ("sd",        (49.8, 21.4, 13.2, 15.0), lambda p, n, s: s == "SD Card"),
    ("can",       ( 9.5, 34.0, 21.0, 15.2), lambda p, n, s: s == "CAN"),
    ("sens5v",    (34.5, 81.0, 13.5,  8.0), lambda p, n, s: n & {"VSENS_F", "VSENS_SW", "+5VS"} and s == "Power"),
    ("frontend",  ( 9.2, 49.7, 24.4, 25.0), lambda p, n, s: s == "Power" and n & {"VBAT_IN", "VBAT_F", "VBAT_FB", "GATE_RB", "VCAP", "VBAT_UVLO", "+VBAT"}),
    # IO3/IO45/IO46 run from the module's south pad row to J7 at the top
    # left. Pull-downs parked in the bottom band left R29 needing a run
    # the length of the board; put them on the path instead.
    ("strap",     (42.6, 20.8,  6.6,  7.6), lambda p, n, s: n & {"IO3", "IO45", "IO46"}),
    ("mcu_misc",  (63.0, 23.0, 19.0, 10.4), lambda p, n, s: s == "MCU"),
    ("pwr_misc",  (47.5, 69.5, 13.0, 13.0), lambda p, n, s: True),
]


def zone_for(part, sheet_name):
    nets = set(part["pins"].values())
    for name, rect, pred in ZONES:
        if pred(part, nets, sheet_name):
            return name
    return "pwr_misc"


# ------------------------------------------------------------------ build ----

# Footprints whose own 3D model KiCad does not ship, and a part of the same
# class to stand in.  This is cosmetic only -- no 3D model reaches fab -- but
# without one the part is simply absent from a render, and a USB-C port you
# cannot see in the board plot is a USB-C port nobody checks the direction of.
MODEL_SUBS = {
    "USB_C_Receptacle_HRO_TYPE-C-31-M-12":
        ("Connector_USB.3dshapes/"
         "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal.step"),
    # The buck names an exposed pad KiCad has no model for; the 2.41x3.81
    # variant is the same SOIC-8 body.
    "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.95x4.9mm_Mask2.71x3.4mm_ThermalVias":
        ("Package_SO.3dshapes/"
         "SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.41x3.81mm.step"),
    # The CAN choke is a project footprint with no model at all. Its body is
    # 4.5 x 3.2 mm, which is an 1812.
    "L_CommonMode_TDK_ACT45B":
        "Inductor_SMD.3dshapes/L_1812_4532Metric.step",
}


def substitute_model(fp, name):
    """Point a modelless footprint at a stand-in of the same class."""
    path = MODEL_SUBS.get(name)
    if path is None:
        return
    models = fp.Models()
    while len(models):
        models.pop()
    m = pcbnew.FP_3DMODEL()
    m.m_Filename = "${KICAD9_3DMODEL_DIR}/" + path
    m.m_Show = True
    models.push_back(m)


def load_footprint(fpid):
    lib, name = fpid.split(":", 1)
    fp = pcbnew.FootprintLoad(lib_path(lib), name)
    if fp is None:
        raise SystemExit("footprint not found: " + fpid)
    substitute_model(fp, name)
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
    for sheet_name, value, netset, pos in BUCK_FIXED + PIN_FIXED:
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
            fv = full_value(p)
            for key in ((sh["name"], fv, netset),
                        (sh["name"], fv, None)):
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
        # Break into shelves first, place second.  A zone that does not need
        # its full height shares what is left between its rows: the analogue
        # channels were packing 20.7 mm of parts into a 28 mm shelf and then
        # sitting them 0.4 mm apart, which leaves the silkscreen nowhere to
        # go and is why the reference designators ran into each other.
        rows, row, x, row_h = [], [], zx, 0.0
        for h, w, fp in sized:
            if x + w > zx + zw and x > zx:
                rows.append((row, row_h))
                row, x, row_h = [], zx, 0.0
            row.append((h, w, fp))
            x += w + gap
            row_h = max(row_h, h)
        if row:
            rows.append((row, row_h))
        packed = sum(rh for _r, rh in rows) + gap * max(len(rows) - 1, 0)
        slack = 0.0
        if len(rows) > 1:
            slack = min(max(zh - packed, 0.0) / (len(rows) - 1), ROW_SLACK)
        y, bottom = zy, zy
        for members, rh in rows:
            x = zx
            for h, w, fp in members:
                # position so the courtyard's top-left lands at (x, y)
                fp.SetPosition(pt(x + w / 2.0, y + h / 2.0))
                bb = courtyard_box(fp)
                fp.Move(pt(x, y) - pcbnew.VECTOR2I(bb.GetLeft(), bb.GetTop()))
                x += w + gap
            bottom = y + rh
            y += rh + gap + slack
        used_h = bottom - zy
        report.append((name, len(parts), used_h, zh, used_h > zh))

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

    # --- footprint keepouts, promoted to board level ------------------------
    # The microSD footprint carries keepouts for the card slot and the eject
    # mechanism, and the module carries its RF area. Those live inside the
    # footprint, where the Specctra export cannot see them -- so the
    # autorouter happily ran a track through the middle of J9's card slot.
    # Board-level rule areas do get exported, and gen/maze_route.py reads
    # them too, so copying them out makes both routers aware.
    promoted = 0
    for fp in board.GetFootprints():
        for z in list(fp.Zones()):
            if not z.GetIsRuleArea():
                continue
            ka = pcbnew.ZONE(board)
            ka.SetIsRuleArea(True)
            ka.SetDoNotAllowCopperPour(True)
            ka.SetDoNotAllowTracks(True)
            ka.SetDoNotAllowVias(True)
            ka.SetLayerSet(z.GetLayerSet())
            src, dst = z.Outline(), ka.Outline()
            for i in range(src.OutlineCount()):
                dst.NewOutline()
                oc = src.Outline(i)
                for j in range(oc.PointCount()):
                    pt_ = oc.CPoint(j)
                    dst.Append(pt_.x, pt_.y)
            board.Add(ka)
            promoted += 1

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
    print("keepouts    : %d promoted from footprints" % promoted)
    for name, count, used, zh, overflow in report:
        flag = "  OVERFLOW" if overflow else ""
        print("  zone %-9s %2d parts, %5.1f/%4.1f mm used%s" % (name, count, used, zh, flag))


if __name__ == "__main__":
    main()
