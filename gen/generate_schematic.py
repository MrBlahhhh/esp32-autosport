#!/usr/bin/env python3
"""
Generate the KiCad schematic for the ESP32-S3 CAN + microSD automotive logger.

The netlist below is the single source of truth: every component lists its
pins and the net each pin lands on.  Symbol geometry and -- critically --
pin numbering come from the official KiCad symbol libraries, so pin numbers
are never hand-typed here.

Output: <project>/*.kicad_sch (hierarchical, one sheet per functional block),
        <project>/bom.csv, <project>/netlist.txt

Usage:  python3 gen/generate_schematic.py [--symbol-dir /usr/share/kicad/symbols]
"""

import argparse
import csv
import math
import os
import re
import uuid

# --------------------------------------------------------------------------
# S-expression reading (only what we need: pin geometry + raw symbol text)
# --------------------------------------------------------------------------

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse_sexp(text):
    toks = TOKEN.findall(text)
    pos = 0

    def read():
        nonlocal pos
        tok = toks[pos]
        pos += 1
        if tok == "(":
            out = []
            while toks[pos] != ")":
                out.append(read())
            pos += 1
            return out
        return tok

    out = []
    while pos < len(toks):
        out.append(read())
    return out


def unquote(s):
    if s.startswith('"'):
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return s


def children(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def _property_spans(text):
    """[(start, end)] of each top-level (property ...) block in a symbol."""
    spans = []
    depth, i, in_str = 0, 0, False
    start = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            if depth == 1 and start is None and text.startswith("(property ", i):
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 1 and start is not None:
                spans.append((start, i + 1))
                start = None
        i += 1
    return spans


# The schematic format this generator emits, and the symbol-library format its
# embedded definitions were validated against.  KiCad opens older schematics and
# upgrades them on load, so emitting the 7.0 format is safe on 7, 8 and 9 alike.
SCH_FORMAT_VERSION = "20230121"          # KiCad 7.0
VALIDATED_SYMBOL_VERSION = "20241209"    # KiCad 9.0 symbol libraries


def find_symbol_dir():
    """Locate the stock KiCad symbol libraries on Windows, macOS or Linux."""
    import glob as _glob

    for var in ("KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR", "KICAD7_SYMBOL_DIR",
                "KICAD6_SYMBOL_DIR"):
        path = os.environ.get(var)
        if path and os.path.isdir(path):
            return path

    patterns = [
        r"C:\Program Files\KiCad\*\share\kicad\symbols",
        r"C:\Program Files (x86)\KiCad\*\share\kicad\symbols",
        "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
        "/usr/share/kicad/symbols",
        "/usr/local/share/kicad/symbols",
        os.path.expanduser("~/.local/share/kicad/*/symbols"),
    ]
    found = []
    for pat in patterns:
        found.extend(p for p in _glob.glob(pat) if os.path.isdir(p))
    if found:
        # Highest KiCad version wins when several are installed.
        return sorted(found)[-1]
    return None


class SymbolLibs:
    """Reads the installed KiCad symbol libraries."""

    def __init__(self, symbol_dir):
        self.symbol_dir = symbol_dir
        self._parsed = {}
        self._raw = {}
        self.lib_version = None

    def _load(self, lib):
        if lib in self._parsed:
            return
        path = os.path.join(self.symbol_dir, lib + ".kicad_sym")
        if not os.path.exists(path):
            raise SystemExit("symbol library not found: " + path)
        text = open(path, encoding="utf-8").read().replace("\r\n", "\n")
        root = parse_sexp(text)[0]
        ver = children(root, "version")
        if ver and self.lib_version is None:
            self.lib_version = unquote(ver[0][1])
        self._parsed[lib] = {unquote(s[1]): s for s in children(root, "symbol")}

        # Byte-exact source text for each top-level symbol, so the definitions
        # we embed in the schematic are identical to the library's.
        # One indent level is two spaces (7.0 libraries) or one tab (9.0).
        raw = {}
        for m in re.finditer(r'^(?:  |\t)\(symbol "([^"]+)"', text, re.M):
            start = m.start()
            depth, i, in_str = 0, start, False
            while i < len(text):
                ch = text[i]
                if in_str:
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            raw[m.group(1)] = text[start:i]
        self._raw[lib] = raw

    def symbol(self, lib_id):
        lib, name = lib_id.split(":", 1)
        self._load(lib)
        if name not in self._parsed[lib]:
            raise SystemExit("symbol not found: " + lib_id)
        return self._parsed[lib][name]

    def has(self, lib_id):
        lib, name = lib_id.split(":", 1)
        if not os.path.exists(os.path.join(self.symbol_dir, lib + ".kicad_sym")):
            return False
        self._load(lib)
        return name in self._parsed[lib]

    def extends(self, lib_id):
        ext = children(self.symbol(lib_id), "extends")
        return unquote(ext[0][1]) if ext else None

    def properties(self, lib_id):
        out = {}
        for prop in children(self.symbol(lib_id), "property"):
            out[unquote(prop[1])] = unquote(prop[2])
        return out

    def raw(self, lib_id):
        """Library source for the symbol, renamed to its full lib_id.

        A schematic's lib_symbols cache cannot express `extends`: KiCad stores
        derived symbols flattened.  KiCad's own flattening keeps the parent's
        geometry but the child's property fields verbatim (positions and text
        effects included), so the splice must swap whole property blocks or
        ERC flags the cached copy as differing from the library's.
        """
        lib, name = lib_id.split(":", 1)
        self._load(lib)
        parent = self.extends(lib_id)
        if parent is None:
            text = self._raw[lib][name]
            return text.replace('(symbol "%s"' % name,
                                '(symbol "%s:%s"' % (lib, name), 1)

        text = self._raw[lib][parent]
        child = self._raw[lib][name]
        pspans = _property_spans(text)
        child_props = [child[a:b] for a, b in _property_spans(child)]
        if pspans and child_props:
            first, last = pspans[0][0], pspans[-1][1]
            indent = text[:first].rsplit("\n", 1)[-1]
            text = text[:first] + ("\n" + indent).join(child_props) + text[last:]
        # Sub-symbol names must follow the derived symbol, e.g. FOO_1_1.
        text = text.replace('(symbol "%s_' % parent, '(symbol "%s_' % name)
        text = text.replace('(symbol "%s"' % parent,
                            '(symbol "%s:%s"' % (lib, name), 1)
        return text

    def pins(self, lib_id):
        """[(number, name, local_x, local_y, angle, hidden)] resolving `extends`.

        Hidden pins matter: the ESP32 module symbol carries its GND pin 40 and
        exposed pad 41 as hidden pins that KiCad ties to GND by name.  We record
        them so the netlist documents the connection, but never draw a stub to
        an invisible pin.
        """
        parent = self.extends(lib_id)
        if parent:
            lib = lib_id.split(":", 1)[0]
            return self.pins(lib + ":" + parent)
        out = []
        for unit in children(self.symbol(lib_id), "symbol"):
            for pin in children(unit, "pin"):
                at = children(pin, "at")[0]
                num = unquote(children(pin, "number")[0][1])
                nam = unquote(children(pin, "name")[0][1])
                out.append((num, nam, float(at[1]), float(at[2]),
                            int(float(at[3])), "hide" in pin))
        return out


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def rotate(theta, vx, vy):
    """Rotate a sheet-space vector by the symbol placement angle (CCW, Y down)."""
    theta %= 360
    if theta == 0:
        return (vx, vy)
    if theta == 90:
        return (vy, -vx)
    if theta == 180:
        return (-vx, -vy)
    if theta == 270:
        return (-vy, vx)
    raise ValueError("unsupported rotation %r" % theta)


def pin_geometry(local_x, local_y, angle, theta):
    """Return (offset_from_origin, outward_unit_vector) in sheet space."""
    # Symbol space has Y up; the sheet has Y down.
    off = rotate(theta, local_x, -local_y)
    # A pin's `angle` points from its connection end *into* the body, so the
    # free (wire) direction is the opposite.
    out_ang = math.radians(angle + 180)
    d = rotate(theta, round(math.cos(out_ang)), -round(math.sin(out_ang)))
    return off, d


def label_rotation(direction):
    return {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[direction]


def mm(v):
    v = round(v, 4)
    return ("%f" % v).rstrip("0").rstrip(".") or "0"


NS = uuid.UUID("6f1d7d1e-6d3e-5a2b-9c44-2b6f0f0a1c77")


def det_uuid(key):
    return str(uuid.uuid5(NS, key))


# --------------------------------------------------------------------------
# Board description
# --------------------------------------------------------------------------

PROJECT = "esp32s3-can-sd-logger"
TITLE = "ESP32-S3 CAN + microSD Automotive Logger"
REV = "A"
COMPANY = "geekopolis"

R0805 = "Resistor_SMD:R_0805_2012Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
C1206 = "Capacitor_SMD:C_1206_3216Metric"
SOT23 = "Package_TO_SOT_SMD:SOT-23"
SOT236 = "Package_TO_SOT_SMD:SOT-23-6"
# NOTE: confirm the exposed-pad size against TI's DDA package drawing before layout.
# TI DDA0008B (SO PowerPAD-8): pad max 2.71x3.4mm, TI land 2.95x4.9mm copper
# with a 2.71x3.4mm solder-mask-defined opening -- this footprint matches it.
SO8EP = ("Package_SO:SOIC-8-1EP_3.9x4.9mm_P1.27mm_"
         "EP2.95x4.9mm_Mask2.71x3.4mm_ThermalVias")
SOIC8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
SMC = "Diode_SMD:D_SMC"
SMA = "Diode_SMD:D_SMA"
SOD123 = "Diode_SMD:D_SOD-123"
LED0805 = "LED_SMD:LED_0805_2012Metric"
JST4 = "Connector_JST:JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical"
JST8 = "Connector_JST:JST_PH_B8B-PH-K_1x08_P2.00mm_Vertical"
HDR3 = "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical"
HDR4 = "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"
HDR6 = "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical"
HDR8 = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
SOT235 = "Package_TO_SOT_SMD:SOT-23-5"
SJ2 = "Jumper:SolderJumper-2_P1.3mm_Open_Pad1.0x1.5mm"
SJ2B = "Jumper:SolderJumper-2_P1.3mm_Bridged_Pad1.0x1.5mm"
SJ3 = "Jumper:SolderJumper-3_P1.3mm_Open_Pad1.0x1.5mm"
TP = "TestPoint:TestPoint_Pad_D1.5mm"
MH = "MountingHole:MountingHole_3.2mm_M3"

# Each entry: (lib_id, value, footprint, {pin: net}, mpn, note)
SHEETS = []


def sheet(name, filename, description):
    s = {"name": name, "file": filename, "desc": description, "parts": []}
    SHEETS.append(s)
    return s


def part(sh, prefix, lib_id, value, footprint, pins, mpn="", note="", nc=(),
         lcsc=""):
    sh["parts"].append(
        {
            "prefix": prefix,
            "lib_id": lib_id,
            "value": value,
            "footprint": footprint,
            "pins": pins,
            "mpn": mpn,
            "note": note,
            "nc": set(nc),
            "lcsc": lcsc,
        }
    )


def R(sh, value, a, b, mpn="", note="", fp=R0805):
    part(sh, "R", "Device:R", value, fp, {"1": a, "2": b}, mpn, note)


def C(sh, value, a, b, fp=C0805, mpn="", note=""):
    part(sh, "C", "Device:C", value, fp, {"1": a, "2": b}, mpn, note)


def flag(sh, net):
    part(sh, "#FLG", "power:PWR_FLAG", "PWR_FLAG", "", {"1": net}, note="ERC power-source flag")


# ---------------------------------------------------------------- power ----
pw = sheet(
    "Power",
    "power.kicad_sch",
    "Reverse-battery protection, transient clamping, +5V / +3V3 / +5V sensor rails",
)

part(pw, "J", "Connector_Generic:Conn_01x04", "PWR + CAN harness", JST4,
     {"1": "VBAT_IN", "2": "GND", "3": "CAN_H", "4": "CAN_L"},
     "JST B4B-PH-K-S(LF)(SN)", "Pin 1 +12V, 2 GND, 3 CAN_H, 4 CAN_L "
     "(Autosport Labs harness order)", lcsc="C131334")
part(pw, "F", "Device:Fuse", "2A slow", "Fuse:Fuse_1206_3216Metric",
     {"1": "VBAT_IN", "2": "VBAT_F"}, "Littelfuse 0466002.NR",
     "Sacrificial: only opens on a hard fault, not on reverse polarity")
part(pw, "FB", "Device:L", "600R @ 100MHz 3A", "Inductor_SMD:L_1206_3216Metric",
     {"1": "VBAT_F", "2": "VBAT_FB"}, "Wurth 742792625", "Conducted-emissions bead")

# Ideal-diode reverse-battery block
part(pw, "U", "Power_Management:LM74700", "LM74700-Q1", SOT236,
     {"1": "VCAP", "2": "GND", "3": "VBAT_UVLO", "4": "+VBAT", "5": "GATE_RB", "6": "VBAT_FB"},
     "LM74700QDBVRQ1", "Ideal-diode controller; blocks reverse battery via Q1",
     lcsc="C2941042")
part(pw, "Q", "Device:Q_NMOS_GSD", "100V 6.8mOhm N-ch", "Package_TO_SOT_SMD:TO-252-3_TabPin2",
     {"1": "GATE_RB", "2": "VBAT_FB", "3": "+VBAT"}, "Infineon IPD068N10N3G",
     "Source to battery, drain to load: body diode blocks reverse polarity. "
     "The previously specified PSMN4R3-100BSE does not exist", lcsc="C88066")
C(pw, "1uF 50V", "VCAP", "VBAT_FB", mpn="", note="LM74700 charge-pump reservoir")
R(pw, "100k", "VBAT_FB", "VBAT_UVLO", note="UVLO upper leg")
R(pw, "25.5k", "VBAT_UVLO", "GND", note="UVLO lower leg -> board enables at ~5.9V")

part(pw, "D", "Device:D_TVS", "SMCJ33A", SMC, {"1": "+VBAT", "2": "GND"},
     "Littelfuse SMCJ33A",
     "33V standoff / 53.3V clamp @ 1500W: absorbs ISO 7637-2 pulse 5b load dump")
C(pw, "100uF 100V", "+VBAT", "GND", fp="Capacitor_SMD:CP_Elec_10x10.5",
  mpn="Nichicon UCD2A101MNL1GS", note="Bulk hold-up; any 100uF >=80V SMD "
  "electrolytic on a 10x10.5 land works -- match in the JLC catalog at order")
C(pw, "10uF 100V", "+VBAT", "GND", fp=C1206, note="Switcher input bypass")
C(pw, "100nF 100V", "+VBAT", "GND", note="HF bypass")

# +5V rail
part(pw, "U", "Regulator_Switching:LM5164DDA", "LM5164 (5V)", SO8EP,
     {"1": "GND", "2": "+VBAT", "3": "EN_5V", "4": "RON_5V", "5": "FB_5V",
      "6": "PG_5V", "7": "BST_5V", "8": "SW_5V", "9": "GND"},
     "LM5164DDAR", "100V synchronous buck, ultra-low Iq. Non-automotive "
     "variant; the Q1 is scarce", lcsc="C477928")
R(pw, "100k", "+VBAT", "EN_5V", note="Enable tied to VIN (LM74700 already gates on UVLO)")
R(pw, "31.6k", "RON_5V", "GND",
  note="RON = 5.0V x 2500 / 400kHz (Eq 12) -> 396kHz; tON = 237ns at the "
       "53.3V clamp, comfortably above the 50ns minimum")
C(pw, "2.2nF 50V", "BST_5V", "SW_5V",
  note="Bootstrap: datasheet mandates exactly 2.2nF X7R -- a larger value "
       "overstresses the internal VCC regulator and damages the device")
part(pw, "L", "Device:L", "33uH 3A", "Inductor_SMD:L_Sunlord_SWPA8040S",
     {"1": "SW_5V", "2": "+5V"}, "Sunlord ASWPA8050S330MT",
     "Shielded molded, Isat 3A vs the 1.75A max peak limit", lcsc="C340244")
R(pw, "100k", "+5V", "FB_5V", note="FB upper: 1.2V ref -> 5.00V")
R(pw, "31.6k", "FB_5V", "GND", note="FB lower")
# Type-3 ripple injection (datasheet Table 6-1): the all-ceramic output has no
# ESR ripple for the COT comparator, so a SW-node RC ramp is AC-coupled into
# FB. Sized for ~20mV at FB with VIN = 14V nominal, per TI's design example.
R(pw, "121k", "SW_5V", "RAMP_5V", note="Ripple-injection ramp resistor RA")
C(pw, "3.3nF 50V", "RAMP_5V", "+5V", note="Ramp capacitor CA")
C(pw, "270pF 50V", "RAMP_5V", "FB_5V", note="Ramp coupling capacitor CB")
C(pw, "22uF 16V", "+5V", "GND", fp=C1206)
C(pw, "22uF 16V", "+5V", "GND", fp=C1206)
C(pw, "100nF 16V", "+5V", "GND")
R(pw, "100k", "PG_5V", "+3V3", note="Power-good pull-up")

# +3V3 rail
part(pw, "U", "Regulator_Switching:LM5164DDA", "LM5164 (3V3)", SO8EP,
     {"1": "GND", "2": "+VBAT", "3": "EN_3V3", "4": "RON_3V3", "5": "FB_3V3",
      "6": "PG_3V3", "7": "BST_3V3", "8": "SW_3V3", "9": "GND"},
     "LM5164DDAR", "Second buck straight off the battery: a shorted 5V "
     "sensor harness cannot brown out the MCU", lcsc="C477928")
R(pw, "100k", "+VBAT", "EN_3V3")
R(pw, "20.5k", "RON_3V3", "GND",
  note="RON = 3.3V x 2500 / 400kHz (Eq 12) -> 402kHz; tON = 154ns at the "
       "53.3V clamp, above the 50ns minimum")
C(pw, "2.2nF 50V", "BST_3V3", "SW_3V3",
  note="Bootstrap: datasheet-mandated 2.2nF X7R, do not increase")
part(pw, "L", "Device:L", "22uH 3A", "Inductor_SMD:L_Sunlord_SWPA8040S",
     {"1": "SW_3V3", "2": "+3V3"}, "Sunlord ASWPA8050S220MT",
     "Same automotive series as L1")
R(pw, "100k", "+3V3", "FB_3V3", note="FB upper: 1.2V ref -> 3.28V")
R(pw, "57.6k", "FB_3V3", "GND", note="FB lower")
R(pw, "95.3k", "SW_3V3", "RAMP_3V3", note="Ripple-injection ramp resistor RA")
C(pw, "3.3nF 50V", "RAMP_3V3", "+3V3", note="Ramp capacitor CA")
C(pw, "270pF 50V", "RAMP_3V3", "FB_3V3", note="Ramp coupling capacitor CB")
C(pw, "22uF 6.3V", "+3V3", "GND", fp=C1206)
C(pw, "22uF 6.3V", "+3V3", "GND", fp=C1206)
C(pw, "100nF 16V", "+3V3", "GND")
R(pw, "100k", "PG_3V3", "+3V3")
part(pw, "D", "Device:D_Zener", "3.6V 300mW", "Diode_SMD:D_SOD-323",
     {"1": "+3V3", "2": "GND"}, "onsemi MM3Z3V6T1G",
     "Rail clamp: an analog input shorted to battery back-feeds ~1.6mA "
     "through its BAT54S into +3V3; with the MCU asleep the rail would "
     "otherwise float above the ESP32's 3.6V absolute maximum")

# 5V sensor excitation, fused separately from the board 5V
part(pw, "PF", "Device:Polyfuse", "0.2A hold / 0.4A trip", "Resistor_SMD:R_1206_3216Metric",
     {"1": "+5V", "2": "VSENS_F"}, "Bourns MF-MSMF020",
     "Resettable: a shorted sensor wire trips this, not the board")
part(pw, "FB", "Device:L", "600R @ 100MHz 2A", "Inductor_SMD:L_0805_2012Metric",
     {"1": "VSENS_F", "2": "+5VS"}, "Wurth 742792022")
C(pw, "10uF 16V", "+5VS", "GND", fp=C1206)
part(pw, "D", "Device:D_TVS", "SMAJ5.0A", SMA, {"1": "+5VS", "2": "GND"},
     "Littelfuse SMAJ5.0A", "Clamps harness-injected transients on the sensor 5V")

part(pw, "D", "Device:LED", "green", LED0805, {"1": "PWR_LED_K", "2": "+3V3"}, note="Power indicator")
R(pw, "1k", "PWR_LED_K", "GND")

for net in ["+VBAT", "+5V", "+3V3", "+5VS", "GND"]:
    part(pw, "TP", "Connector:TestPoint", net, TP, {"1": net})
for net in ["GND", "+VBAT", "VBAT_FB", "+5V", "+3V3", "+5VS", "VBUS", "SD_VDD"]:
    flag(pw, net)
for _ in range(4):
    part(pw, "H", "Mechanical:MountingHole", "M3", MH, {})

# ------------------------------------------------------------------ MCU ----
mc = sheet("MCU", "mcu.kicad_sch", "ESP32-S3-WROOM-1 module, USB-C, boot/reset, break-out")

part(mc, "U", "RF_Module:ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1-N16R8",
     "RF_Module:ESP32-S3-WROOM-1",
     {
         "1": "GND", "2": "+3V3", "3": "MCU_EN",
         "4": "AIN3", "5": "AIN4", "6": "VBAT_SNS", "7": "SD_PWR_EN",
         "8": "LED1", "9": "LED2", "10": "CAN_TX", "11": "CAN_RX",
         "12": "SD_CD", "13": "USB_DM", "14": "USB_DP",
         "15": "IO3", "16": "IO46",
         "17": "SD_D3", "18": "SD_D2", "19": "SD_D1", "20": "SD_D0",
         "21": "SD_CMD", "22": "SD_CLK", "23": "CAN_S",
         "24": "SPI_CS", "25": "LED_DIN_MCU", "26": "IO45", "27": "MCU_BOOT",
         "31": "I2C_SDA", "32": "I2C_SCL",
         "33": "SPI_SCK", "34": "SPI_MISO", "35": "SPI_MOSI",
         "36": "UART_RX", "37": "UART_TX", "38": "AIN2", "39": "AIN1",
         # Hidden in the symbol; KiCad bonds them to GND by name.
         "40": "GND", "41": "GND",
     },
     "ESP32-S3-WROOM-1-N16R8", lcsc="C2913202",
     note="16MB flash / 8MB octal PSRAM. Pins 28-30 (IO35/36/37) are consumed by "
     "the octal PSRAM and must stay unconnected. IO3/IO45/IO46 on the spare "
     "header are strapping pins -- leave floating at boot.",
     nc=("28", "29", "30"))

R(mc, "10k", "+3V3", "MCU_EN")
C(mc, "1uF 16V", "MCU_EN", "GND", note="EN reset delay")
part(mc, "SW", "Switch:SW_Push", "RESET", "Button_Switch_SMD:SW_SPST_TL3342",
     {"1": "MCU_EN", "2": "GND"}, "TL3342F160QG",
     "Genuine E-Switch is scarce at LCSC; any 5.2mm gull-wing tact fits")
R(mc, "10k", "+3V3", "MCU_BOOT")
part(mc, "SW", "Switch:SW_Push", "BOOT", "Button_Switch_SMD:SW_SPST_TL3342",
     {"1": "MCU_BOOT", "2": "GND"}, "TL3342F160QG")
C(mc, "10uF 16V", "+3V3", "GND", fp=C1206)
C(mc, "100nF 16V", "+3V3", "GND")
C(mc, "100nF 16V", "+3V3", "GND")

part(mc, "J", "Connector:USB_C_Receptacle_USB2.0_16P", "USB-C",
     "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12",
     {"A1": "GND", "B1": "GND", "A12": "GND", "B12": "GND", "S1": "GND",
      "A4": "VBUS_IN", "B4": "VBUS_IN", "A9": "VBUS_IN", "B9": "VBUS_IN",
      "A5": "USB_CC1", "B5": "USB_CC2",
      "A6": "USB_DP_CON", "B6": "USB_DP_CON",
      "A7": "USB_DM_CON", "B7": "USB_DM_CON"},
     mpn="HRO TYPE-C-31-M-12", lcsc="C165948",
     note="Programming / CDC console only -- not a power path in the car",
     nc=("A8", "B8"))
R(mc, "5.1k", "USB_CC1", "GND")
R(mc, "5.1k", "USB_CC2", "GND")
part(mc, "PF", "Device:Polyfuse", "0.5A hold", "Resistor_SMD:R_1206_3216Metric",
     {"1": "VBUS_IN", "2": "VBUS"}, "Bourns MF-MSMF050")
part(mc, "D", "Device:D_Schottky", "40V 1A", SOD123, {"1": "+5V", "2": "VBUS"},
     "PMEG4010", "OR-ing: bench USB can power the board, but the 5V buck "
     "(5.00V) reverse-biases it whenever the car is connected")
C(mc, "10uF 16V", "VBUS", "GND", fp=C1206)
part(mc, "U", "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", SOT236,
     {"1": "USB_DP_CON", "2": "GND", "3": "USB_DM_CON",
      "4": "USB_DM", "5": "VBUS", "6": "USB_DP"},
     "USBLC6-2SC6", "USB ESD clamp", lcsc="C7519")

part(mc, "J", "Connector_Generic:Conn_01x04", "UART0", HDR4,
     {"1": "+3V3", "2": "UART_TX", "3": "UART_RX", "4": "GND"})
part(mc, "J", "Connector_Generic:Conn_01x04", "I2C / Qwiic", HDR4,
     {"1": "GND", "2": "+3V3", "3": "I2C_SDA", "4": "I2C_SCL"})
R(mc, "4.7k", "+3V3", "I2C_SDA")
R(mc, "4.7k", "+3V3", "I2C_SCL")

R(mc, "1k", "LED1", "LED1_A")
part(mc, "D", "Device:LED", "amber", LED0805, {"1": "GND", "2": "LED1_A"})
R(mc, "1k", "LED2", "LED2_A")
part(mc, "D", "Device:LED", "blue", LED0805, {"1": "GND", "2": "LED2_A"})

# SPI breakout for MCP2515 / CC1101 / MAX6675 / etc.
part(mc, "J", "Connector_Generic:Conn_01x06", "SPI", HDR6,
     {"1": "+3V3", "2": "GND", "3": "SPI_SCK", "4": "SPI_MISO",
      "5": "SPI_MOSI", "6": "SPI_CS"},
     note="IO40 SCK / IO41 MISO / IO42 MOSI / IO47 CS -- GPIO-matrix SPI")

# WS2812 shift-light header: true 5 V data via AHCT buffer (3.3 V TTL-friendly
# input, 5 V rail). IO48 is RMT-capable and not a strapping pin.
R(mc, "33", "LED_DIN_MCU", "LED_DIN_A",
  note="Edge-rate limit into the level shifter")
part(mc, "U", "74xGxx:74AHCT1G125", "74AHCT1G125", SOT235,
     {"1": "GND", "2": "LED_DIN_A", "3": "GND", "4": "LED_DIN", "5": "+5V"},
     "SN74AHCT1G125DBVR", lcsc="C7975",
     note="5 V buffer so WS2812 DIN is a real 5 V rail, not 3.3 V hoping")
C(mc, "100nF 16V", "+5V", "GND", note="AHCT decoupling")
part(mc, "PF", "Device:Polyfuse", "0.5A hold", "Resistor_SMD:R_1206_3216Metric",
     {"1": "+5V", "2": "LED_5V"}, "Bourns MF-MSMF050",
     "Fused tap for the shift-light strip (8x WS2812 ~0.5 A worst case)")
part(mc, "J", "Connector_Generic:Conn_01x03", "WS2812", HDR3,
     {"1": "LED_5V", "2": "LED_DIN", "3": "GND"},
     note="Shift-light header: +5V / 5V-logic DIN / GND")

# Remaining free GPIOs after SPI + WS2812. All three are strapping pins.
part(mc, "J", "Connector_Generic:Conn_01x03", "Spare IO", HDR3,
     {"1": "IO3", "2": "IO45", "3": "IO46"},
     note="IO3/IO45/IO46 are strapping pins -- leave floating at boot")
part(mc, "J", "Connector_Generic:Conn_01x04", "Rail break-out", HDR4,
     {"1": "+5V", "2": "+3V3", "3": "GND", "4": "GND"})

# ------------------------------------------------------------- SD card ----
sd = sheet("SD Card", "sdcard.kicad_sch",
           "microSD in 4-bit SDMMC mode with a switchable card supply")

part(sd, "J", "Connector:Micro_SD_Card_Det1", "microSD push-pull",
     "Connector_Card:microSD_HC_Hirose_DM3D-SF",
     {"1": "SD_D2_C", "2": "SD_D3_C", "3": "SD_CMD_C", "4": "SD_VDD",
      "5": "SD_CLK_C", "6": "GND", "7": "SD_D0_C", "8": "SD_D1_C",
      "9": "SD_CD", "10": "GND"},
     "Hirose DM3D-SF", "Push-pull socket; DET is the card-present switch",
     lcsc="C719027")

part(sd, "Q", "Device:Q_PMOS_GSD", "-20V 2.3A P-ch", SOT23,
     {"1": "SD_PG", "2": "+3V3", "3": "SD_VDD"}, "DMG2301L",
     "High-side switch so firmware can power-cycle a wedged card")
R(sd, "100k", "+3V3", "SD_PG", note="Default off")
part(sd, "Q", "Device:Q_NMOS_GSD", "60V 300mA N-ch", SOT23,
     {"1": "SD_EN_G", "2": "GND", "3": "SD_PG"}, "2N7002", "Level shift for the P-ch gate")
R(sd, "10k", "SD_PWR_EN", "SD_EN_G")
R(sd, "100k", "SD_EN_G", "GND")
C(sd, "10uF 6.3V", "SD_VDD", "GND", fp=C1206)
C(sd, "100nF 16V", "SD_VDD", "GND")

for sig in ["CLK", "CMD", "D0", "D1", "D2", "D3"]:
    R(sd, "33", "SD_" + sig, "SD_%s_C" % sig, note="Series damping")
for sig in ["CMD", "D0", "D1", "D2", "D3"]:
    R(sd, "10k", "SD_VDD", "SD_%s_C" % sig,
      note="Espressif's recommended value; pulled to the switched rail so "
           "nothing back-feeds a powered-down card")
# Card detect is a slow mechanical contact, not a bus line, so it keeps the
# weaker pull-up -- less standing current with a card inserted.
R(sd, "47k", "+3V3", "SD_CD", note="Card-detect pull-up")

# ----------------------------------------------------------------- CAN ----
cn = sheet("CAN", "can.kicad_sch", "Isolated-ground CAN 2.0B node with selectable termination")

part(cn, "U", "Interface_CAN_LIN:TJA1051T-3", "TJA1051T/3", SOIC8,
     {"1": "CAN_TX", "2": "GND", "3": "+5V", "4": "CAN_RX", "5": "+3V3",
      "6": "CANL_T", "7": "CANH_T", "8": "CAN_S"},
     "TJA1051T/3,118", lcsc="C58988",
     note="5V bus drive with a 3.3V VIO pin, so no level shifting to the ESP32")
C(cn, "100nF 16V", "+5V", "GND")
C(cn, "100nF 16V", "+3V3", "GND")
R(cn, "10k", "CAN_S", "GND", note="Default to normal (non-silent) mode")

part(cn, "L", "Device:L_Coupled", "51uH CMC", "esp32autosport:L_CommonMode_TDK_ACT45B",
     {"1": "CANH_T", "2": "CAN_H", "3": "CANL_T", "4": "CAN_L"},
     "TDK ACT45B-510-2P-TL003",
     "AEC-Q200 CAN choke; footprint pads renumbered so symbol winding 1-2 is "
     "the package's top (1-4) winding", lcsc="C76584")
part(cn, "JP", "Jumper:SolderJumper_2_Bridged", "TERM (default ON)", SJ2B,
     {"1": "CAN_H", "2": "TERM_A"},
     note="Cut the trace to remove the 120 ohm termination on a mid-bus node")
R(cn, "60.4", "TERM_A", "CAN_SPLIT", note="Split termination upper half")
R(cn, "60.4", "CAN_SPLIT", "CAN_L", note="Split termination lower half")
C(cn, "4.7nF 50V", "CAN_SPLIT", "GND", note="Split-termination common-mode stabiliser")
part(cn, "D", "Device:D_TVS", "SMAJ26CA", SMA, {"1": "CAN_H", "2": "GND"},
     "Littelfuse SMAJ26CA", "Bidirectional bus clamp")
part(cn, "D", "Device:D_TVS", "SMAJ26CA", SMA, {"1": "CAN_L", "2": "GND"},
     "Littelfuse SMAJ26CA")
part(cn, "TP", "Connector:TestPoint", "CAN_H", TP, {"1": "CAN_H"})
part(cn, "TP", "Connector:TestPoint", "CAN_L", TP, {"1": "CAN_L"})

# -------------------------------------------------------------- analog ----
an = sheet("Analog Inputs", "analog.kicad_sch",
           "4 sensor channels with jumper-selected dividers and pull-up bias")

part(an, "J", "Connector_Generic:Conn_01x08", "Sensor harness", JST8,
     {"1": "+5VS", "2": "AIN1_IN", "3": "AIN2_IN", "4": "AIN3_IN",
      "5": "AIN4_IN", "6": "GND", "7": "GND", "8": "+5VS"},
     "JST B8B-PH-K-S(LF)(SN)", "Two 5V and two ground pins so sensors can "
     "be paired up", lcsc="C157974")

for n in range(1, 5):
    inp, node, out = "AIN%d_IN" % n, "AIN%d_A" % n, "AIN%d" % n
    R(an, "1k 0.1%", inp, node,
      note="Ch%d series/fault-current limit; 0.1%% thin film -- it is inside "
           "the divider chain, so its tolerance is a gain error" % n)
    part(an, "JP", "Jumper:SolderJumper_2_Open", "PULLUP%d" % n, SJ2,
         {"1": "+5VS", "2": "AIN%d_PU" % n},
         note="Close for 2-wire NTC / open-collector sensors")
    R(an, "2.49k", "AIN%d_PU" % n, node, note="Ch%d bias resistor" % n)
    R(an, "10k 0.1%", node, out,
      note="Ch%d divider upper leg, 0.1%% thin film for AFR-grade accuracy" % n)
    part(an, "JP", "Jumper:SolderJumper_2_Open", "BYPASS%d" % n, SJ2,
         {"1": node, "2": out},
         note="Close for a raw 0-3.3V sensor (shorts the upper leg)")
    part(an, "JP", "Jumper:SolderJumper_3_Open", "RANGE%d" % n, SJ3,
         {"2": out, "1": "AIN%d_R1" % n, "3": "AIN%d_R2" % n},
         note="C-A = 0-5V range, C-B = 0-16V range, both open = no divider")
    R(an, "15k 0.1%", "AIN%d_R1" % n, "GND",
      note="0-5V range: 5.0V in -> ~2.88V at the ADC (1k series included); "
           "exact scale is a firmware calibration constant")
    R(an, "2.21k 0.1%", "AIN%d_R2" % n, "GND",
      note="0-16V range: 16.0V in -> ~2.67V at the ADC (1k series included)")
    C(an, "100nF 16V", out, "GND", note="Ch%d anti-alias / ADC charge reservoir" % n)
    # One SOT-23 series pair: GND -> signal -> +3V3, so the node is clamped a
    # Schottky drop either side of the rails.
    part(an, "D", "Device:D_Schottky_Dual_Series_AKC", "BAT54S", SOT23,
         {"1": "GND", "3": out, "2": "+3V3"}, "BAT54S",
         "Ch%d rail clamp (both polarities)" % n)

# Precision path for the four channels: the ESP32-S3 ADC is only good for
# ±1-2% even calibrated, which is ±0.2 AFR on a 0-5V wideband output. The
# ADS1115 (16-bit delta-sigma, on the existing I2C bus at 0x48) shares the
# conditioned AINx nodes, so firmware chooses per channel: fast-and-rough on
# the internal ADC, or slow-and-accurate here. Inputs are clamped to +3V3 by
# the BAT54S pairs, within the ADS1115's VDD+0.3V absolute maximum.
part(an, "U", "Analog_ADC:ADS1115IDGS", "ADS1115",
     "Package_SO:VSSOP-10_3x3mm_P0.5mm",
     {"1": "GND", "3": "GND", "4": "AIN1", "5": "AIN2", "6": "AIN3",
      "7": "AIN4", "8": "+3V3", "9": "I2C_SDA", "10": "I2C_SCL"},
     "ADS1115IDGSR", lcsc="C37593",
     note="16-bit 4-ch I2C ADC for AFR-grade accuracy; ADDR to GND = 0x48",
     nc=("2",))
C(an, "100nF 16V", "+3V3", "GND", note="ADS1115 decoupling")

R(an, "100k", "+VBAT", "VBAT_SNS", note="Battery monitor: divide by 11")
R(an, "10k", "VBAT_SNS", "GND")
C(an, "100nF 16V", "VBAT_SNS", "GND")
part(an, "D", "Device:D_Schottky_Dual_Series_AKC", "BAT54S", SOT23,
     {"1": "GND", "3": "VBAT_SNS", "2": "+3V3"}, "BAT54S", "Battery-monitor clamp")


# --------------------------------------------------------------------------
# Reference designators
# --------------------------------------------------------------------------

def assign_refs():
    counters = {}
    for sh in SHEETS:
        for p in sh["parts"]:
            pre = p["prefix"]
            counters[pre] = counters.get(pre, 0) + 1
            p["ref"] = "%s%d" % (pre, counters[pre])


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = 420.0, 297.0
MARGIN_X, MARGIN_TOP, MARGIN_BOT = 12.0, 18.0, 22.0
STUB = 5.08
LABEL_ALLOWANCE = 30.0
GRID = 1.27


def snap(v):
    return round(v / GRID) * GRID


def symbol_extent(libs, lib_id, theta):
    xs, ys = [0.0], [0.0]
    for _, _, lx, ly, ang, hidden in libs.pins(lib_id):
        if hidden:
            continue
        off, d = pin_geometry(lx, ly, ang, theta)
        xs += [off[0], off[0] + d[0] * STUB]
        ys += [off[1], off[1] + d[1] * STUB]
    return min(xs), max(xs), min(ys), max(ys)


def place(libs):
    """Lay parts out in columns, sized to the widest symbol in each column."""
    for sh in SHEETS:
        for p in sh["parts"]:
            # Two-pin parts read best lying horizontally, but KiCad draws some
            # of them vertically and others horizontally, so ask the symbol
            # which way its pins point rather than hard-coding a list.
            visible = [pin for pin in libs.pins(p["lib_id"]) if not pin[5]]
            p["theta"] = 0
            if len(visible) == 2:
                dirs = {pin_geometry(x, y, a, 0)[1] for _, _, x, y, a, _ in visible}
                if dirs <= {(0, 1), (0, -1)}:
                    p["theta"] = 90
            p["ext"] = symbol_extent(libs, p["lib_id"], p["theta"])

        columns, col, y = [], [], MARGIN_TOP
        usable = PAGE_H - MARGIN_TOP - MARGIN_BOT
        for p in sh["parts"]:
            x0, x1, y0, y1 = p["ext"]
            # Leave room for the reference and value text above and below the
            # body, otherwise adjacent rows of passives print on top of each
            # other.
            h = max((y1 - y0) + 7.62, 15.24)
            if col and y + h > MARGIN_TOP + usable:
                columns.append(col)
                col, y = [], MARGIN_TOP
            p["_h"], p["_y"] = h, y
            col.append(p)
            y += h
        if col:
            columns.append(col)

        x = MARGIN_X
        for col in columns:
            left = min(p["ext"][0] for p in col)
            right = max(p["ext"][1] for p in col)
            cx = x - left + LABEL_ALLOWANCE
            for p in col:
                p["x"] = snap(cx)
                p["y"] = snap(p["_y"] - p["ext"][2])
            x = cx + right + LABEL_ALLOWANCE + 6.35
        sh["width_used"] = x


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

FONT = "(effects (font (size 1.27 1.27)) %s)"


def emit_symbol(libs, sh, p, sheet_uuid):
    lines = []
    su = det_uuid("sym:%s:%s" % (sh["file"], p["ref"]))
    x0, x1, y0, y1 = p["ext"]
    lines.append(
        '  (symbol (lib_id "%s") (at %s %s %d) (unit 1)\n'
        "    (in_bom %s) (on_board yes) (dnp no)\n"
        '    (uuid %s)'
        % (p["lib_id"], mm(p["x"]), mm(p["y"]), p["theta"],
           "no" if p["prefix"].startswith("#") else "yes", su)
    )
    ref_y = p["y"] + y0 - 2.0
    val_y = p["y"] + y1 + 2.0
    props = [("Reference", p["ref"], ref_y, False),
             ("Value", p["value"], val_y, False),
             ("Footprint", p["footprint"], val_y, True),
             ("Datasheet", "~", val_y, True)]
    if p["mpn"]:
        props.append(("MPN", p["mpn"], val_y, True))
    if p["note"]:
        props.append(("Note", p["note"], val_y, True))
    # A property's angle is applied *on top of* the symbol's rotation, so a
    # rotated part needs its text counter-rotated to stay horizontal.
    text_angle = (360 - p["theta"]) % 360
    for name, value, py, hide in props:
        lines.append(
            '    (property "%s" "%s" (at %s %s %d)\n      %s\n    )'
            % (name, value.replace('"', "'"), mm(p["x"]), mm(py), text_angle,
               FONT % ("hide" if hide else ""))
        )
    for num, _, _, _, _, _ in libs.pins(p["lib_id"]):
        lines.append('    (pin "%s" (uuid %s))'
                     % (num, det_uuid("pin:%s:%s:%s" % (sh["file"], p["ref"], num))))
    lines.append(
        '    (instances\n      (project "%s"\n        (path "/%s/%s" (reference "%s") (unit 1))\n      )\n    )'
        % (PROJECT, ROOT_UUID, sheet_uuid, p["ref"])
    )
    lines.append("  )")
    return "\n".join(lines)


def emit_sheet(libs, sh, sheet_uuid, page):
    used = []
    for p in sh["parts"]:
        if p["lib_id"] not in used:
            used.append(p["lib_id"])
    lib_block = [libs.raw(lib_id) for lib_id in used]

    out = [
        "(kicad_sch (version %s) (generator eeschema)" % SCH_FORMAT_VERSION,
        "",
        "  (uuid %s)" % sheet_uuid,
        "",
        '  (paper "A3")',
        "",
        "  (title_block",
        '    (title "%s")' % TITLE,
        '    (date "")',
        '    (rev "%s")' % REV,
        '    (company "%s")' % COMPANY,
        '    (comment 1 "%s -- %s")' % (sh["name"], sh["desc"]),
        "  )",
        "",
        "  (lib_symbols",
        "\n".join(lib_block),
        "  )",
        "",
    ]

    wires, labels, syms, ncs = [], [], [], []
    for p in sh["parts"]:
        syms.append(emit_symbol(libs, sh, p, sheet_uuid))
        for num, _, lx, ly, ang, hidden in libs.pins(p["lib_id"]):
            if hidden:
                continue  # KiCad bonds hidden power pins by name
            off, d = pin_geometry(lx, ly, ang, p["theta"])
            px, py = p["x"] + off[0], p["y"] + off[1]
            if num in p["nc"]:
                ncs.append("  (no_connect (at %s %s) (uuid %s))"
                           % (mm(px), mm(py), det_uuid("nc:%s:%s:%s" % (sh["file"], p["ref"], num))))
                continue
            net = p["pins"].get(num)
            if net is None:
                continue
            ex, ey = px + d[0] * STUB, py + d[1] * STUB
            wires.append(
                "  (wire (pts (xy %s %s) (xy %s %s))\n"
                "    (stroke (width 0) (type default))\n"
                "    (uuid %s)\n  )"
                % (mm(px), mm(py), mm(ex), mm(ey),
                   det_uuid("wire:%s:%s:%s" % (sh["file"], p["ref"], num)))
            )
            labels.append(
                '  (global_label "%s" (shape input) (at %s %s %d) (fields_autoplaced)\n'
                "    (effects (font (size 1.27 1.27)) (justify left))\n"
                "    (uuid %s)\n"
                '    (property "Intersheet References" "${INTERSHEET_REFS}" (at %s %s 0)\n'
                "      (effects (font (size 1.27 1.27)) (justify left) hide)\n"
                "    )\n  )"
                % (net, mm(ex), mm(ey), label_rotation(d),
                   det_uuid("lbl:%s:%s:%s" % (sh["file"], p["ref"], num)), mm(ex), mm(ey))
            )

    out += ncs + wires + labels + syms
    out.append("")
    out.append('  (sheet_instances\n    (path "/" (page "%d"))\n  )' % page)
    out.append(")")
    return "\n".join(out) + "\n"


def emit_root(sheet_uuids):
    out = [
        "(kicad_sch (version %s) (generator eeschema)" % SCH_FORMAT_VERSION,
        "",
        "  (uuid %s)" % ROOT_UUID,
        "",
        '  (paper "A3")',
        "",
        "  (title_block",
        '    (title "%s")' % TITLE,
        '    (date "")',
        '    (rev "%s")' % REV,
        '    (company "%s")' % COMPANY,
        '    (comment 1 "Root sheet -- see the block sheets below")',
        "  )",
        "",
        "  (lib_symbols\n  )",
        "",
    ]
    y = 30.0
    for i, sh in enumerate(SHEETS):
        su = sheet_uuids[sh["file"]]
        out.append(
            "  (sheet (at 40 %s) (size 120 25) (fields_autoplaced)\n"
            "    (stroke (width 0.1524) (type solid))\n"
            "    (fill (color 0 0 0 0.0000))\n"
            "    (uuid %s)\n"
            '    (property "Sheetname" "%s" (at 40 %s 0)\n'
            "      (effects (font (size 1.27 1.27)) (justify left bottom))\n    )\n"
            '    (property "Sheetfile" "%s" (at 40 %s 0)\n'
            "      (effects (font (size 1.27 1.27)) (justify left top))\n    )\n"
            "    (instances\n"
            '      (project "%s"\n'
            '        (path "/%s" (page "%d"))\n'
            "      )\n    )\n  )"
            % (mm(y), su, sh["name"], mm(y - 0.6), sh["file"], mm(y + 25.6),
               PROJECT, ROOT_UUID, i + 2)
        )
        y += 45.0
    out.append("")
    out.append('  (sheet_instances\n    (path "/" (page "1"))\n  )')
    out.append(")")
    return "\n".join(out) + "\n"


PRO_TEMPLATE = """{
  "board": {"design_settings": {"rules": {"min_through_hole_diameter": 0.2}}},
  "boards": [],
  "cvpcb": {"equivalence_files": []},
  "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
  "meta": {"filename": "%s.kicad_pro", "version": 1},
  "net_settings": {
    "classes": [
      {"bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
       "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
       "name": "Default", "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
       "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6},
      {"bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
       "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
       "name": "Power", "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
       "track_width": 0.5, "via_diameter": 0.8, "via_drill": 0.4, "wire_width": 6},
      {"bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.2, "diff_pair_via_gap": 0.25,
       "diff_pair_width": 0.25, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
       "name": "CAN", "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
       "track_width": 0.25, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6}
    ],
    "meta": {"version": 3},
    "net_colors": null,
    "netclass_assignments": null,
    "netclass_patterns": [
      {"netclass": "Power", "pattern": "+VBAT"},
      {"netclass": "Power", "pattern": "+5V"},
      {"netclass": "Power", "pattern": "+3V3"},
      {"netclass": "Power", "pattern": "GND"},
      {"netclass": "Power", "pattern": "VBAT_*"},
      {"netclass": "Power", "pattern": "+5VS"},
      {"netclass": "Power", "pattern": "VBUS*"},
      {"netclass": "Power", "pattern": "SW_*"},
      {"netclass": "Power", "pattern": "SD_VDD"},
      {"netclass": "Power", "pattern": "LED_5V"},
      {"netclass": "Power", "pattern": "VSENS_F"},
      {"netclass": "CAN", "pattern": "CAN_H"},
      {"netclass": "CAN", "pattern": "CAN_L"},
      {"netclass": "CAN", "pattern": "CANH_T"},
      {"netclass": "CAN", "pattern": "CANL_T"}
    ]
  },
  "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
  "schematic": {
    "annotate_start_num": 0,
    "legacy_lib_dir": "",
    "legacy_lib_list": [],
    "meta": {"version": 1},
    "page_layout_descr_file": "",
    "spice_current_sheet_as_root": false,
    "spice_external_command": "spice \\"%%I\\"",
    "spice_model_current_sheet_as_root": true,
    "spice_save_all_currents": false,
    "spice_save_all_voltages": false,
    "subpart_first_id": 65,
    "subpart_id_separator": 0
  },
  "sheets": [],
  "text_variables": {}
}
"""


def write_bom(path):
    rows = []
    for sh in SHEETS:
        for p in sh["parts"]:
            if p["prefix"].startswith("#") or p["lib_id"] == "Connector:TestPoint":
                continue
            rows.append(p)
    groups = {}
    for p in rows:
        key = (p["value"], p["footprint"], p["mpn"], p["lcsc"])
        groups.setdefault(key, []).append(p["ref"])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Qty (1 board)", "Qty (10 boards)", "References", "Value",
                    "Footprint", "Manufacturer part number", "LCSC", "Notes"])
        for (value, fp, mpn, lcsc), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
            note = next((p["note"] for p in rows if p["ref"] == refs[0] and p["note"]), "")
            w.writerow([len(refs), len(refs) * 10, " ".join(sorted(refs)),
                        value, fp, mpn, note])
    return len(rows), len(groups)


def write_netlist(path, libs):
    nets = {}
    for sh in SHEETS:
        for p in sh["parts"]:
            for num, net in p["pins"].items():
                nets.setdefault(net, []).append("%s.%s" % (p["ref"], num))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Net connectivity for %s rev %s\n" % (TITLE, REV))
        fh.write("(%d nets)\n\n" % len(nets))
        for net in sorted(nets):
            fh.write("%-14s %s\n" % (net, " ".join(sorted(nets[net]))))
    return nets


ROOT_UUID = det_uuid("root:" + PROJECT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol-dir", default=None,
                    help="KiCad symbol library directory "
                         "(auto-detected if omitted)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), ".."))
    args = ap.parse_args()

    if args.symbol_dir is None:
        args.symbol_dir = find_symbol_dir()
        if args.symbol_dir is None:
            raise SystemExit(
                "Could not find the KiCad symbol libraries.\n"
                "Pass --symbol-dir explicitly, e.g.\n"
                '  Windows: --symbol-dir "C:\\Program Files\\KiCad\\9.0'
                '\\share\\kicad\\symbols"\n'
                "  macOS:   --symbol-dir /Applications/KiCad/KiCad.app"
                "/Contents/SharedSupport/symbols\n"
                "  Linux:   --symbol-dir /usr/share/kicad/symbols")
        print("symbol libs : %s" % args.symbol_dir)

    libs = SymbolLibs(args.symbol_dir)
    libs.symbol("Device:R")  # force one library load so the format is known
    if libs.lib_version != VALIDATED_SYMBOL_VERSION:
        print(
            "WARNING: symbol libraries in %s are format %s, but the embedded\n"
            "         definitions were last validated against %s (KiCad 9.0).\n"
            "         A different library generation may use syntax the emitted\n"
            "         schematic format (%s) does not accept. Run gen/validate.py\n"
            "         before trusting the output."
            % (args.symbol_dir, libs.lib_version, VALIDATED_SYMBOL_VERSION,
               SCH_FORMAT_VERSION))

    # The KiCad 9 libraries moved the generic MOSFET symbols out of Device.
    # Resolve each part against whichever library the local install has; the
    # symbols are pin-identical, so this only changes the recorded lib_id.
    alternates = {
        "Device:Q_NMOS_GSD": ("Transistor_FET:Q_NMOS_GSD",),
        "Device:Q_PMOS_GSD": ("Transistor_FET:Q_PMOS_GSD",),
    }
    resolved = {}
    for sh in SHEETS:
        for p in sh["parts"]:
            lid = p["lib_id"]
            if lid not in resolved:
                resolved[lid] = lid
                if not libs.has(lid):
                    for alt in alternates.get(lid, ()):
                        if libs.has(alt):
                            print("symbol %s not in these libraries; using %s"
                                  % (lid, alt))
                            resolved[lid] = alt
                            break
            p["lib_id"] = resolved[lid]

    assign_refs()
    place(libs)

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    sheet_uuids = {sh["file"]: det_uuid("sheet:" + sh["file"]) for sh in SHEETS}
    open(os.path.join(out, PROJECT + ".kicad_sch"), "w", encoding="utf-8").write(
        emit_root(sheet_uuids))
    for i, sh in enumerate(SHEETS):
        open(os.path.join(out, sh["file"]), "w", encoding="utf-8").write(
            emit_sheet(libs, sh, sheet_uuids[sh["file"]], i + 2))
    open(os.path.join(out, PROJECT + ".kicad_pro"), "w", encoding="utf-8").write(
        PRO_TEMPLATE % PROJECT)

    n_parts, n_lines = write_bom(os.path.join(out, "bom.csv"))
    nets = write_netlist(os.path.join(out, "netlist.txt"), libs)

    print("sheets      : %d" % (len(SHEETS) + 1))
    print("components  : %d (%d distinct BOM lines)" % (n_parts, n_lines))
    print("nets        : %d" % len(nets))
    singles = [n for n, v in nets.items() if len(v) < 2]
    if singles:
        print("single-node nets (check these): %s" % ", ".join(sorted(singles)))


if __name__ == "__main__":
    main()
