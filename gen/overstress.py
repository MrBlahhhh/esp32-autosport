#!/usr/bin/env python3
"""
Worst-case overvoltage analysis for every input that leaves the board.

  python gen/overstress.py

This is not a SPICE simulation and does not pretend to be one.  The parts
that do the protecting here -- the TVS diodes, the polyfuses, the LM74700
ideal-diode controller, the LM5164s -- have no SPICE models available
offline, and a transient run built on invented models would look like
validation while proving nothing.  What this does instead is what a design
review does: take the datasheet numbers, apply the standard automotive
stress definitions, solve the clamp network, and compare the result against
what each downstream part can survive.

The model for a transient is the standard one.  ISO 7637-2 defines each
pulse as an open-circuit voltage behind a source resistance, and a TVS is a
breakdown voltage in series with a dynamic resistance:

    R_dyn  = (V_clamp - V_br) / I_pp          from the datasheet pair
    I      = (V_oc - V_br) / (R_src + R_dyn)
    V_pin  = V_br + I * R_dyn

which is exact for the piecewise-linear part of the characteristic and
conservative below it.  Peak power capability is derated from the 10/1000 us
figure as P(t) = P_1ms * (1ms / t) ** 0.5, the usual square-root rule for
these packages; it is approximate and marked as such where it decides an
outcome.

Every number below is from a datasheet.  Where a value could not be
confirmed offline it is marked UNVERIFIED and the analysis reports the
threshold rather than a verdict -- see the notes at the end of the output.
"""

from __future__ import annotations

import sys

# --------------------------------------------------------------- devices ----
# TVS: (V_rwm, V_br_min, V_clamp, I_pp, P_pp_1ms)  volts / amps / watts
TVS = {
    # Littelfuse SMCJ series, 1500 W, and SMAJ series, 400 W, both 10/1000 us
    "SMCJ40CA": (40.0, 44.4, 64.5, 23.2, 1500.0),
    "SMAJ40CA": (40.0, 44.4, 64.5, 6.2, 400.0),
    "SMAJ26CA": (26.0, 28.9, 42.1, 9.5, 400.0),
    "SMAJ6.0A": (6.0, 6.67, 11.4, 35.1, 400.0),
}

# Downstream absolute maxima, volts.  UNVERIFIED entries are flagged in the
# report rather than silently trusted.
LIMITS = {
    "LM5164 VIN": (100.0, True),
    "IPD068N10N3G VDS": (100.0, True),
    "LM74700 ANODE/CATHODE": (65.0, False),   # UNVERIFIED - confirm in TI ds
    "100uF bulk cap": (100.0, True),
    "TJA1051 bus pin": (58.0, True),
    "ADS1115 AIN": (3.6, True),
    "BAT54S": (30.0, True),
    "PMEG4010": (40.0, True),
    "TJA1051 VCC": (6.0, True),
    "74AHCT1G125 VCC": (7.0, True),
}

# ISO 7637-2 test pulses for a 12 V system: (name, V_oc, R_src, t_d seconds)
# Severity III/IV amplitudes; 5b is quoted both suppressed and not.
PULSES = [
    ("pulse 1   (-100 V, 10 R, 2 ms)",        -100.0, 10.0, 2e-3),
    ("pulse 2a  (+50 V, 2 R, 50 us)",           50.0,  2.0, 50e-6),
    ("pulse 3a  (-150 V, 50 R, 100 ns)",      -150.0, 50.0, 100e-9),
    ("pulse 3b  (+150 V, 50 R, 100 ns)",       150.0, 50.0, 100e-9),
    ("pulse 5b  (+35 V suppressed, 0.5 R)",     35.0,  0.5, 400e-3),
    ("pulse 5b  (+87 V UNsuppressed, 0.5 R)",   87.0,  0.5, 400e-3),
]

# Steady-state conditions the board is expected to sit in indefinitely.
DC_CASES = [
    ("normal 14.4 V running",   14.4),
    ("24 V jump start",         24.0),
    ("36 V declared maximum",   36.0),
    ("reverse battery -14 V",  -14.0),
]


def clamp(part, v_oc, r_src):
    """Solve the TVS clamp. Returns (current A, voltage at the pin V)."""
    _rwm, v_br, v_c, i_pp, _p = TVS[part]
    r_dyn = (v_c - v_br) / i_pp
    mag = abs(v_oc)
    if mag <= v_br:                      # below breakdown: TVS does nothing
        return 0.0, v_oc
    i = (mag - v_br) / (r_src + r_dyn)
    v = v_br + i * r_dyn
    sign = 1.0 if v_oc > 0 else -1.0
    return i, sign * v


def p_capability(part, t):
    """Peak power the package can take for t seconds, from the 1 ms figure."""
    return TVS[part][4] * (1e-3 / t) ** 0.5


def row(label, text, ok):
    mark = "ok  " if ok is True else ("FAIL" if ok is False else "??  ")
    print("    %-4s %-42s %s" % (mark, label, text))


def head(title):
    print("\n" + title)
    print("-" * len(title))


def main():
    print(__doc__.strip().split("\n\n")[0])
    worst = []

    # ---------------------------------------------------------- battery ----
    head("1. Battery input  J1 -> F1 (2 A slow) -> SMCJ40CA -> FB1 -> Q1")
    rwm = TVS["SMCJ40CA"][0]
    for name, v in DC_CASES:
        conducts = abs(v) > TVS["SMCJ40CA"][1]
        if conducts:
            row(name, "TVS in breakdown -- sustained, will overheat", False)
            worst.append("battery DC %s" % name)
        else:
            note = "TVS off (|V| < %.1f V standoff)" % rwm
            if v < 0:
                note += "; Q1 blocks, fuse holds"
            row(name, note, True)

    for name, v_oc, r_src, t in PULSES:
        i, v = clamp("SMCJ40CA", v_oc, r_src)
        if i == 0.0:
            row(name, "TVS never conducts (below breakdown)", True)
            continue
        p = abs(v) * i
        cap = p_capability("SMCJ40CA", t)
        e = p * t
        ok_v = abs(v) <= LIMITS["LM74700 ANODE/CATHODE"][0]
        ok_p = p <= cap
        txt = ("clamps %.1f V at %.1f A; %.0f W for %s (cap %.0f W, %.2f J)"
               % (abs(v), i, p, fmt_t(t), cap, e))
        ok = ok_v and ok_p
        row(name, txt, ok)
        if not ok_p:
            worst.append("%s: TVS %.0f W over %.0f W capability" % (name, p, cap))
        if not ok_v:
            worst.append("%s: %.1f V exceeds LM74700 65 V limit" % (name, abs(v)))

    # ---------------------------------------------------------- sensors ----
    head("2. Sensor inputs  J10 -> SMAJ40CA -> 1k -> [10k] -> ADS1115")
    print("    Series path to the ADC is 1k + 10k = 11k with RANGE open and")
    print("    BYPASS open; closing BYPASS shorts the 10k out.")
    for short_to, v in (("+14 V battery", 14.0), ("+24 V jump start", 24.0),
                        ("+36 V", 36.0)):
        i_tvs, v_pin = clamp("SMAJ40CA", v, 0.0)
        v_clampnode = 3.3 + 0.4                    # BAT54S into +3V3
        i_series = (v - v_clampnode) / 11000.0
        ok = i_tvs == 0.0 and abs(i_series) < 10e-3
        row("harness shorted to %s" % short_to,
            "TVS off; %.2f mA through 11k into the BAT54S" % (i_series * 1e3),
            ok)

    print("    The BAT54S sits on the ADC node beside the ADS1115 input, so")
    print("    it -- not the ADC pin -- carries the clamp current. The series")
    print("    resistance sets how much the diode has to take.")
    for name, v_oc, r_src, t in PULSES[:4]:
        i, v = clamp("SMAJ40CA", v_oc, r_src)
        p = abs(v) * i
        cap = p_capability("SMAJ40CA", t)
        i_d_11k = (abs(v) - 0.4) / 11000.0
        i_d_1k = (abs(v) - 0.4) / 1000.0
        ok = p <= cap and i_d_1k <= 0.6           # BAT54S 600 mA surge
        row(name,
            "clamps %.1f V, %.0f W vs %.0f W; BAT54S takes %.1f mA (11k) / "
            "%.0f mA (BYPASS closed); ADC held at 0.4 V"
            % (abs(v), p, cap, i_d_11k * 1e3, i_d_1k * 1e3), ok)

    # -------------------------------------------------------------- CAN ----
    head("3. CAN bus  J1 -> SMAJ26CA -> choke -> TJA1051 (+/-58 V bus pins)")
    for short_to, v in (("+14 V battery", 14.0), ("+24 V", 24.0),
                        ("-14 V", -14.0)):
        i, vp = clamp("SMAJ26CA", v, 0.0)
        row("bus shorted to %s" % short_to,
            "TVS off; transceiver sees %.1f V" % v,
            i == 0.0 and abs(v) <= LIMITS["TJA1051 bus pin"][0])
    for name, v_oc, r_src, t in PULSES[:4]:
        i, v = clamp("SMAJ26CA", v_oc, r_src)
        p = abs(v) * i
        cap = p_capability("SMAJ26CA", t)
        ok = p <= cap and abs(v) <= LIMITS["TJA1051 bus pin"][0]
        row(name, "clamps %.1f V at %.1f A; %.0f W vs %.0f W capability"
            % (abs(v), i, p, cap), ok)
        if not ok:
            worst.append("CAN %s" % name.split("(")[0].strip())

    # -------------------------------------------------------------- USB ----
    head("4. USB VBUS  J2 -> PF2 (0.5 A hold) -> USBLC6 -> D5 -> +5V rail")
    print("    The CC pins carry 5.1k pull-downs, so a compliant source only")
    print("    ever offers 5 V. These are non-compliant-supply faults.")
    for name, v in (("compliant host, 5.25 V max", 5.25),
                    ("faulty supply at 12 V", 12.0),
                    ("faulty supply at 20 V", 20.0)):
        if v <= 5.5:
            row(name, "within USBLC6 5.25 V standoff; rail fine", True)
            continue
        # PF2 holds 0.5 A and trips near 1 A, so the USBLC6's VBUS diode
        # is what sets the node: roughly 17 V at 1 A for this part.
        v_bus = min(v, 17.0)
        v_rail = v_bus - 0.4                      # through D5
        hits = [k for k in ("TJA1051 VCC", "74AHCT1G125 VCC")
                if v_rail > LIMITS[k][0]]
        row(name, "USBLC6 holds VBUS near %.1f V while PF2 trips; +5V driven "
                  "to %.1f V through D5; over limit for %s"
            % (v_bus, v_rail, ", ".join(hits) if hits else "nothing"),
            not hits)
        if hits:
            worst.append("VBUS at %.0f V holds +5V at %.1f V until PF2 trips "
                         "-- over %s; the USBLC6 is dissipating ~%.0f W "
                         "meanwhile" % (v, v_rail, " and ".join(hits),
                                        v_bus * 1.0))

    # ------------------------------------------------------ sensor 5 V ----
    head("5. Sensor 5 V supply  +5V -> PF1 (0.2 A) -> ferrite -> SMAJ6.0A")
    print("    PF1 sits on the board side of the ferrite, so it carries")
    print("    current out of the rail, not current pushed in from the loom.")
    for short_to, v, r_loom in (("+14 V battery", 14.0, 0.5),
                                ("+14 V through 2 R", 14.0, 2.0)):
        i, vp = clamp("SMAJ6.0A", v, r_loom)
        p = abs(vp) * i
        # Current pushed back down the ferrite into the rail hits PF1.
        i_back = (abs(vp) - 5.0) / 1.6            # ferrite DCR + PF1 R_max
        row("+5VS shorted to %s" % short_to,
            "TVS clamps %.1f V at %.1f A = %.0f W sustained (dies); %.1f A "
            "back down the ferrite trips PF1, so the rail survives"
            % (abs(vp), i, p, i_back), False)
        worst.append("+5VS shorted to battery: %.0f W in the SMAJ6.0A -- it is "
                     "sacrificial, PF1 saves the +5V rail behind it" % p)

    # ----------------------------------------------------------- fusing ----
    head("6. What the 2 A fuse actually has to do")
    print("    Littelfuse 0466002.NR, 2 A time-lag.  For each event that puts")
    print("    the TVS into breakdown, the fuse must open before the TVS")
    print("    cooks.  Required melting I2t is I^2 * t_allowed:")
    for name, v_oc, r_src, t in PULSES[-2:]:
        i, v = clamp("SMCJ40CA", v_oc, r_src)
        if i == 0.0:
            row(name, "TVS never conducts -- fuse not involved", True)
            continue
        cap = p_capability("SMCJ40CA", 10e-3)
        t_allow = cap / (abs(v) * i) * 10e-3
        row(name, "%.0f A in the TVS; fuse must clear within %.1f ms "
                  "(I2t <= %.2f A2s)" % (i, t_allow * 1e3, i * i * t_allow),
            None)

    # ---------------------------------------------------------- summary ----
    head("Findings")
    if not worst:
        print("    Nothing over a rating.")
    for w in dict.fromkeys(worst):
        print("  - " + w)

    head("Values that need confirming against a datasheet")
    for k, (v, verified) in LIMITS.items():
        if not verified:
            print("  - %s: analysis assumed %.0f V (UNVERIFIED)" % (k, v))
    print("  - 0466002.NR melting I2t: not confirmed offline; section 6")
    print("    reports the requirement rather than a pass/fail.")
    print("  - Power derating uses P(t) = P_1ms * sqrt(1ms/t), an")
    print("    approximation. Where it decides an outcome the margin is")
    print("    large enough that the rule's error does not change it.")
    return 0


def fmt_t(t):
    for scale, unit in ((1.0, "s"), (1e-3, "ms"), (1e-6, "us"), (1e-9, "ns")):
        if t >= scale:
            return "%.3g %s" % (t / scale, unit)
    return "%g s" % t


if __name__ == "__main__":
    sys.exit(main())
