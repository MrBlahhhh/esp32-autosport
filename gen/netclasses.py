#!/usr/bin/env python3
"""
Per-net track and via sizes, read from the project file.

pcbnew's `net.GetNetClass()` hands back an undecorated SwigPyObject in
these scripts, so asking the API for a net's width raises AttributeError
and any caller with a try/except quietly falls back to a default. That is
exactly how this board came to be routed with 0.2 mm power rails while
the Power netclass sat in the project file saying 0.5 mm -- nothing
errored, because narrow track is legal track.

Reading the .kicad_pro directly avoids the whole problem. KiCad matches a
net to a class with glob patterns, first match winning, so fnmatch is a
faithful stand-in.
"""

from __future__ import annotations

import fnmatch
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
PRO = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_pro")

FALLBACK = (0.2, 0.5, 0.25)      # track, via diameter, via drill (mm)


def load(path=PRO):
    """-> (classes, patterns): {name: (track, via, drill)}, [(glob, name)]."""
    try:
        with open(path, encoding="utf-8") as fh:
            ns = json.load(fh).get("net_settings") or {}
    except Exception:
        return {}, []
    classes = {}
    for c in ns.get("classes") or []:
        name = c.get("name")
        if not name:
            continue
        classes[name] = (float(c.get("track_width") or FALLBACK[0]),
                         float(c.get("via_diameter") or FALLBACK[1]),
                         float(c.get("via_drill") or FALLBACK[2]))
    patterns = [(p.get("pattern"), p.get("netclass"))
                for p in (ns.get("netclass_patterns") or [])
                if p.get("pattern") and p.get("netclass")]
    return classes, patterns


def sizes_for(netname, classes=None, patterns=None):
    """(track, via diameter, via drill) in mm for one net."""
    if classes is None or patterns is None:
        classes, patterns = load()
    for glob, cls in patterns:
        if fnmatch.fnmatchcase(netname, glob) and cls in classes:
            return classes[cls]
    return classes.get("Default", FALLBACK)


class Sizes:
    """Cached lookup, so callers do not re-read the file per net."""

    def __init__(self, path=PRO):
        self.classes, self.patterns = load(path)
        self._cache = {}

    def __call__(self, netname):
        if netname not in self._cache:
            self._cache[netname] = sizes_for(netname, self.classes,
                                             self.patterns)
        return self._cache[netname]

    def track(self, netname):
        return self(netname)[0]

    def via(self, netname):
        return self(netname)[1:]

    def describe(self):
        if not self.classes:
            return "no netclasses found -- check the .kicad_pro"
        return "%d classes, %d patterns" % (len(self.classes),
                                            len(self.patterns))


if __name__ == "__main__":
    s = Sizes()
    print(s.describe())
    for net in ("+5V", "+5VS", "+VBAT", "SD_VDD", "CAN_H", "I2C_SDA",
                "SD_CMD", "USB_DM_CON"):
        t, v, d = s(net)
        print("  %-12s track %.2f  via %.2f/%.2f" % (net, t, v, d))
