#!/usr/bin/env python3
"""
Hand-drawn schematic blocks.

The generic problem -- lay out any netlist so it reads well -- is
schematic auto-layout, and it is hard. This side-steps it the same way
gen/generate_pcb.py side-steps auto-placement for the buck islands: the
recurring blocks are drawn once, by hand, as explicit coordinates and
wires, and the generator stamps them out. A person laying out this
schematic would not solve the general problem either; they would draw a
buck converter, and then draw the second one the same way.

Coordinates are millimetres relative to the block's anchor part, on the
1.27 mm grid KiCad connects on. Parts are identified the way the PCB
tables identify them -- by value and the exact set of nets they touch --
so the mapping survives reference renumbering.

Each block declares:
  anchor    (value, netset) of the part everything else is placed around
  parts     [(value, netset, dx, dy, rot)]
  wires     [[(x, y), ...]] polylines, block-relative
  junctions [(x, y)] where three or more wires meet
  labels    {net: (x, y)} one name per net, anchored on its wire
  rails     [(net, x, y)] power symbols, at the wire end they terminate
"""

from __future__ import annotations


def buck(anchor, rail, sw, en, ron, fb, bst, ramp, pg, ind, ron_r, fb_lo,
         ramp_r):
    """One LM5164 step-down, drawn the way the datasheet draws it.

    VIN and the enable divider on the left, RON below them, the bootstrap
    cap and the inductor on the right, feedback dividing back from the
    output, ramp injection under it. Both converters on this board are
    the same circuit, so both get this same drawing.
    """
    return {
        "sheet": "Power",
        "anchor": (anchor, None),
        "parts": [
            # value        nets                       dx      dy   rot
            ("100k",       {"+VBAT", en},          -22.86,  -2.54,  90),
            ("10nF",       {en, "GND"},            -19.05,   1.27,   0),
            (ron_r,        {ron, "GND"},           -15.24,  19.05,   0),
            ("2.2nF",      {bst, sw},               19.05,  -5.08,   0),
            (ind,          {sw, rail},              29.21,  -2.54,  90),
            ("100k",       {rail, fb},              45.72,   5.08,   0),
            (fb_lo,        {fb, "GND"},             45.72,  16.51,   0),
            (ramp_r,       {sw, ramp},              25.40,   3.81,   0),
            ("3.3nF",      {ramp, rail},            30.48,  11.43,   0),
            ("270pF",      {ramp, fb},              36.83,  11.43,   0),
            ("100k",       {pg, "+3V3"},            16.51,   8.89,   0),
        ],
        "wires": [
            [(-19.05, -2.54), (-12.70, -2.54)],                  # EN divider
            [(-12.70, 5.08), (-15.24, 5.08), (-15.24, 15.24)],   # RON leg
            [(12.70, -7.62), (19.05, -7.62), (19.05, -8.89)],    # BST cap
            [(12.70, -2.54), (25.40, -2.54)],                    # SW rail
            [(19.05, -2.54), (19.05, -1.27)],                    # BST cap foot
            [(25.40, -2.54), (25.40, 0.00)],                     # SW to ramp R
            [(33.02, -2.54), (45.72, -2.54), (45.72, 1.27)],     # out to divider
            [(12.70, 2.54), (41.91, 2.54), (41.91, 15.24)],      # FB sense
            [(41.91, 10.16), (45.72, 10.16)],                    # FB tap
            [(45.72, 8.89), (45.72, 12.70)],                     # divider mid
            [(36.83, 15.24), (41.91, 15.24)],                    # ramp cap to FB
            [(25.40, 7.62), (36.83, 7.62)],                      # ramp node
            [(12.70, 5.08), (16.51, 5.08)],                      # PGOOD
        ],
        # Only where a wire genuinely branches. A junction dot dropped on a
        # point that is merely a wire end -- even one two pins share -- does
        # not help and does break things: KiCad stopped carrying SW past the
        # inductor and FB past the divider tap until these came out.
        "junctions": [(19.05, -2.54), (30.48, 7.62),
                      (41.91, 10.16), (45.72, 10.16)],
        # Angles keep the name off the wire it names. A horizontal net label
        # printed on a short horizontal wire lands on the pin name behind it;
        # turned 90 degrees it stands clear.
        "labels": {
            sw: (13.97, -2.54, 0),
            en: (-19.05, -2.54, 0),
            fb: (20.32, 2.54, 0),
            ramp: (31.75, 7.62, 0),
            bst: (13.97, -7.62, 0),
            ron: (-15.24, 8.89, 0),
        },
    }


BLOCKS = [
    buck("LM5164 (5V)", "+5V", "SW_5V", "EN_5V", "RON_5V", "FB_5V", "BST_5V", "RAMP_5V",
         "PG_5V", "33uH 3A", "31.6k", "31.6k", "121k"),
    buck("LM5164 (3V3)", "+3V3", "SW_3V3", "EN_3V3", "RON_3V3", "FB_3V3", "BST_3V3",
         "RAMP_3V3", "PG_3V3", "22uH 3A", "20.5k", "57.6k", "95.3k"),
]
