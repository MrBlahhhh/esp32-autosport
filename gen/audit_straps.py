#!/usr/bin/env python3
"""
Boot-strap audit: what does every ESP32-S3 strapping pin see at reset?

  python gen/audit_straps.py     (any Python 3)

The classic way a new board fails to boot is a strapping pin biased the
wrong way by something that seemed unrelated -- an LED, a pull-up on a
shared line, a peripheral that drives its input on power-up. This walks
the schematic tables and lists everything attached to each strapping
net, then applies the S3's rules:

  GPIO0  (MCU_BOOT)  high = SPI boot (normal), low = download
  GPIO3  (IO3)       JTAG source strap; floating is invalid
  GPIO45 (IO45)      VDD_SPI voltage strap; low = 3.3 V flash (required)
  GPIO46 (IO46)      with GPIO0: ROM messages / download entry
  EN     (MCU_EN)    must rise cleanly after the 3V3 rail
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_schematic as sch  # noqa: E402

STRAPS = [
    ("MCU_BOOT", "high", "SPI boot; the button pulls it low only when held"),
    ("IO3",      "low",  "JTAG strap; the pull-down selects the default"),
    ("IO45",     "low",  "must be low for 3.3 V flash -- high would feed "
                         "the flash 1.8 V and brick boot"),
    ("IO46",     "low",  "download-entry qualifier with GPIO0"),
    ("MCU_EN",   "high", "reset; must lag 3V3 (RC) and idle high"),
]


def attachments(net):
    out = []
    for sh in sch.SHEETS:
        for p in sh["parts"]:
            if net in p["pins"].values():
                other = sorted(set(p["pins"].values()) - {net})
                out.append((p["prefix"], p["value"], other))
    return out


def main():
    fails = []
    print("Boot-strap audit (from the schematic tables)")
    for net, want, why in STRAPS:
        parts = attachments(net)
        pull_up = any("+3V3" in o or "+5V" in o for pre, v, o in parts
                      if pre == "R")
        pull_dn = any("GND" in o for pre, v, o in parts if pre == "R")
        cap = any(pre == "C" for pre, v, o in parts)
        drivers = [(pre, v) for pre, v, o in parts
                   if pre not in ("R", "C", "SW", "J", "U", "TP")]
        state = "high" if pull_up and not pull_dn else \
                "low" if pull_dn and not pull_up else \
                "high" if net == "MCU_EN" and pull_up else "FLOATING"
        ok = state == want
        print("\n  %-8s wants %-4s -> sits %-8s %s"
              % (net, want, state, "ok" if ok else "WRONG"))
        for pre, v, o in parts:
            print("      %-3s %-16s with %s" % (pre, v, ", ".join(o)))
        if not ok:
            fails.append("%s sits %s at reset, needs %s (%s)"
                         % (net, state, want, why))
        if drivers:
            fails.append("%s has an active part attached: %s"
                         % (net, drivers))

    print("\nSummary")
    if not fails:
        print("    Every strapping pin boots in its required state, and")
        print("    nothing but resistors, buttons and headers touches them.")
    for f in dict.fromkeys(fails):
        print("  - " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
