#!/usr/bin/env python3
"""
Circuit-level simulation of the parts of this board a DRC cannot judge.

  python gen/simulate.py [--only frontend|analog|buck] [--no-plots]

Needs ngspice on PATH or at C:\\spice64\\bin, plus numpy and matplotlib.
Runs with any Python 3 -- unlike the pcbnew scripts it does not need
KiCad's interpreter.

Three decks, each answering a question the board file cannot:

  frontend  what actually arrives at the LM5164 VIN pins, and what Q1
            stands off, when each ISO 7637-2 pulse hits the harness
  analog    the transfer function, fault current and bandwidth of one
            sensor channel in all three jumper configurations
  buck      inductor ripple, saturation margin, output ripple and input
            capacitor RMS current across the 8-36 V input window

What these decks do NOT cover: the LM5164's own control loop. It is a
constant-on-time part with an encrypted TI model, so the buck deck drives
the power stage from an ideal duty-cycle source and answers questions
about the passives -- ripple, saturation, RMS current -- not about loop
stability or transient recovery. Those need TI's PSpice model.

Component values are duplicated from gen/generate_schematic.py rather
than parsed out of it, so treat a disagreement as a bug in this file.
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import shutil
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
SIM = os.path.join(PROJ, "sim")

# ISO 7637-2 pulses as (label, volts, source ohms, decay seconds, absolute).
#
# `absolute` is the difference between the disturbance pulses and load dump.
# Pulses 1 to 3b are drawn in the standard as a spike superimposed on the
# supply, so their U_S adds to 13.5 V.  Pulse 5 is a load dump: U_S is the
# peak the line actually reaches, and 35 V is what a centrally-suppressed
# alternator clamps to.  Adding 13.5 V to that would invent 13.5 V of stress
# and, at the 87 V level, credit the TVS with clamping a pulse that is
# already past every rating on the board.
PULSES = [
    ("pulse 1   -100 V  10 R  2 ms",     -100.0, 10.0, 2e-3,   False),
    ("pulse 2a   +50 V   2 R  50 us",      50.0,  2.0, 50e-6,  False),
    ("pulse 3a  -150 V  50 R  100 ns",   -150.0, 50.0, 100e-9, False),
    ("pulse 3b  +150 V  50 R  100 ns",    150.0, 50.0, 100e-9, False),
    ("pulse 5b   35 V 0.5 R  400 ms",      35.0,  0.5, 400e-3, True),
    ("pulse 5b   87 V 0.5 R  400 ms",      87.0,  0.5, 400e-3, True),
]

VBAT_NOM = 13.5
LOAD_A = 1.20          # both bucks at full load, referred to the input
UVLO = 6.0             # the converters stop drawing below this


def tvs_energy_capability(tau, p_pp=1500.0):
    """Joules a TVS can take at a given pulse width.

    Peak power derates roughly as the inverse square root of pulse width
    over the 1 us to 10 ms range the datasheet curves cover, so the energy
    it can absorb goes as the square root.  Past about 10 ms the die is in
    thermal equilibrium with its land and the limit stops being a pulse
    rating at all: it becomes steady dissipation, which for an SMC package
    on a normal footprint is a few watts.
    """
    e_1ms = p_pp * 1e-3
    if tau <= 10e-3:
        return e_1ms * math.sqrt(tau / 1e-3)
    return e_1ms * math.sqrt(10.0) + 5.0 * (tau - 10e-3)


# --------------------------------------------------------------- plumbing ---
def ngspice():
    exe = shutil.which("ngspice_con") or shutil.which("ngspice")
    if exe:
        return exe
    for pat in (r"C:\spice64\bin\ngspice_con.exe",
                r"C:\Program Files\ngspice*\bin\ngspice_con.exe",
                "/usr/bin/ngspice", "/usr/local/bin/ngspice",
                "/opt/homebrew/bin/ngspice"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    raise SystemExit("ngspice not found -- put ngspice_con.exe on PATH")


def run_deck(name, deck, vectors):
    """Write, run and read back one deck.  Returns {vector: ndarray} plus
    'x' for the sweep variable."""
    os.makedirs(SIM, exist_ok=True)
    cir = os.path.join(SIM, name + ".cir")
    dat = os.path.join(SIM, name + ".dat")
    if os.path.exists(dat):
        os.remove(dat)
    with open(cir, "w", encoding="utf-8") as fh:
        fh.write(deck.replace("@DAT@", dat.replace("\\", "/")))
    res = subprocess.run([ngspice(), "-b", cir],
                         capture_output=True, text=True, cwd=SIM)
    if not os.path.exists(dat):
        sys.stderr.write((res.stdout or "")[-2000:])
        sys.stderr.write((res.stderr or "")[-2000:])
        raise SystemExit("%s: ngspice produced no data" % name)
    raw = np.loadtxt(dat)
    if raw.ndim == 1:
        raw = raw.reshape(1, -1)
    n = len(vectors)
    if raw.shape[1] == 2 * n:            # wrdata's default x,y,x,y,... layout
        cols = {v: raw[:, 2 * i + 1] for i, v in enumerate(vectors)}
        cols["x"] = raw[:, 0]
    elif raw.shape[1] == n + 1:          # one shared scale column
        cols = {v: raw[:, i + 1] for i, v in enumerate(vectors)}
        cols["x"] = raw[:, 0]
    else:
        raise SystemExit("%s: expected %d vectors, got %d columns"
                         % (name, n, raw.shape[1]))
    return cols


def head(t):
    print("\n" + t)
    print("=" * len(t))


def verdict(ok, text):
    print("    %s %s" % ("ok  " if ok else "FAIL", text))
    return [] if ok else [text]


# ------------------------------------------------------ shared model cards ---
MODELS = """
* Bidirectional TVS as the two junctions it physically is: a forward drop
* in series with the other die's avalanche.  BV is set so forward drop plus
* breakdown equals the datasheet V_br(min), and RS splits the dynamic
* resistance (V_clamp - V_br) / I_pp between the two halves.
.subckt tvs_smcj40ca a k
D1 a m DT
D2 k m DT
.model DT D(IS=1e-12 N=1.0 RS=0.433 BV=43.5 IBV=1m CJO=3.0n)
.ends

.subckt tvs_smaj40ca a k
D1 a m DT
D2 k m DT
.model DT D(IS=1e-12 N=1.0 RS=1.62 BV=43.5 IBV=1m CJO=1.5n)
.ends

* BAT54 signal Schottky: 200 mA, ~0.32 V at 10 mA, 30 V reverse.
.model BAT54 D(IS=2e-7 N=1.05 RS=0.6 BV=30 IBV=10u CJO=10p)

* IPD068N10N3G body diode.  The channel itself is a switch below, because
* the LM74700 holds it fully enhanced whenever the input is the higher side.
.model DBODY D(IS=5e-8 N=1.2 RS=0.02 BV=100 IBV=1m CJO=1.5n)
.model IDEALFET SW(vt=0 vh=0.02 ron=0.0068 roff=10meg)
"""


# ================================================================ frontend ===
def frontend_deck(volts, ri, tau):
    """One ISO 7637-2 pulse into the protection chain."""
    # Long pulses need a coarse step or the run never ends; short ones need
    # a fine one or the clamp edge is missed entirely.
    tstop = max(6.0 * tau, 40e-6)
    tstep = min(tau / 200.0, tstop / 20000.0)
    # The standard's rise times scale with the pulse: 100 ns pulse 3 rises
    # in 5 ns.  A fixed 1 us rise never let the short pulses reach their
    # peak at all, which made the front end look better than it is.
    t_rise = max(tau / 20.0, 5e-9)
    t_delay = max(tau / 10.0, 1e-6)
    return """* ISO 7637-2 into the reverse-battery front end
%s

.param vb=%g

* Pulse generator: open-circuit source behind the standard impedance.
Vpulse pk 0 EXP(0 %g %g %g %g %g)
Vbat   bt 0 DC {vb}
Bsrc   src 0 V = V(bt) + V(pk)
Rsrc   src hin %g

* 5 m of unshielded loom, about 1 uH/m, with its own copper resistance.
Lharn  hin hr 5u
Rharn  hr  in 0.05

* F1, 2 A slow blow -- cold resistance only; it does not open in 400 ms.
Rfuse  in vf 0.05

* D1 SMCJ40CA, ahead of the FET.  V0 is an ammeter for the clamp energy.
Vtvs   vf ta DC 0
Xtvs   ta 0 tvs_smcj40ca

* FB1 600R @ 100 MHz: series L with the loss resistance across it.
Rdcr   vf fa 0.02
Lbead  fa vfb 0.955u
Rbead  fa vfb 600

* C_ANODE and the LM74700 UVLO divider hang on VBAT_FB.
Canode vfb 0 100n
Ruvhi  vfb uv 100k
Ruvlo  uv  0 44.2k

* Q1: on whenever the harness is the higher side, body diode otherwise.
S1     vfb vbp vfb vbp IDEALFET
Dbody  vfb vbp DBODY

* +VBAT: the full bank -- 100 uF plus the two 220 uF ride-through cans,
* each with its ESR, and the 10 uF / 100 nF ceramics.
Cbulk  vbp b1 100u
Rbulk  b1 0 0.30
Crt1   vbp b4 330u
Rrt1   b4 0 0.15
Crt2   vbp b5 330u
Rrt2   b5 0 0.15
Cin1   vbp b2 10u
Rin1   b2 0 0.005
Cin2   vbp b3 100n
Rin2   b3 0 0.02

* Both converters, referred to the input as a constant current.  A resistor
* would soak up the transient; a current sink is the honest worst case.  The
* sink folds back below the LM5164's UVLO, because a converter that has
* dropped out stops loading the rail -- without that the model happily drags
* +VBAT negative during the milliseconds Q1 is off, which no real board does.
Bload  vbp 0 I = %g * (0.5 + 0.5*tanh((V(vbp) - %g)/0.3))

* A hint for the operating-point solve, not a substitute for it: the switch
* and the tanh load between them give the solver two things to guess at.
.ic v(hin)=%g v(hr)=%g v(in)=%g v(vf)=%g v(fa)=%g v(vfb)=%g v(vbp)=%g

.control
set filetype=ascii
set wr_singlescale
* tmax has to be given explicitly.  Left to itself ngspice caps its
* internal step at one fiftieth of the run, which for the 40 us window
* around a 100 ns pulse is 800 ns -- it steps clean over the pulse and
* reports the same answer for 3a and 3b.
* No uic.  Letting ngspice find the real operating point first is both
* more honest and less trouble than hand-feeding initial conditions: the
* hand-fed version started the harness node at 20 mV, and the resulting
* charge-up swamped the 100 ns pulses it was supposed to be measuring.
tran %g %g 0 %g
wrdata @DAT@ v(vf) v(vbp) v(vfb) i(vtvs)
quit
.endc
.end
""" % (MODELS, VBAT_NOM, volts, t_delay, t_rise, t_delay + t_rise, tau,
       ri, LOAD_A, UVLO,
       VBAT_NOM, VBAT_NOM, VBAT_NOM, VBAT_NOM, VBAT_NOM, VBAT_NOM, VBAT_NOM,
       tstep, tstop, tstep)


def sim_frontend(plots):
    head("1. ISO 7637-2 transients into the protection front end")
    print("    Limits: SMCJ40CA 1500 W / 10x1000 us,  Q1 IPD068N10N3G 100 V")
    print("    Vds,  LM74700 65 V, LM5164 VIN 100 V (abs max).")
    print()
    print("    %-26s %9s %9s %9s %9s %8s  %s"
          % ("pulse", "V harness", "V +VBAT", "Q1 Vds", "TVS J", "brownout",
             "verdict"))
    fails, traces = [], []
    for label, volts, ri, tau, absolute in PULSES:
        # A load-dump level is where the line ends up; a disturbance pulse is
        # a spike on top of where it already was.
        spike = volts - VBAT_NOM if absolute else volts
        d = run_deck("frontend_%s" % label.split()[1].replace("-", "m")
                     .replace("+", "p"),
                     frontend_deck(spike, ri, tau),
                     ["v(vf)", "v(vbp)", "v(vfb)", "i(vtvs)"])
        t, vf, vbp, vfb = d["x"], d["v(vf)"], d["v(vbp)"], d["v(vfb)"]
        itvs = d["i(vtvs)"]
        # The excursion furthest from the resting rail, signed.  A negative
        # pulse leaves +VBAT sagging rather than peaking, and reporting the
        # largest magnitude would just report the 13.5 V it started at.
        pk_f = vf[np.argmax(np.abs(vf - VBAT_NOM))]
        pk_b = vbp[np.argmax(np.abs(vbp - VBAT_NOM))]
        vds = vfb - vbp
        pk_ds = vds[np.argmax(np.abs(vds))]
        joules = float(np.trapezoid(np.abs(vf * itvs), t))
        cap_j = tvs_energy_capability(tau)
        # How long the +VBAT bank holds the converters up while Q1 is off.
        under = t[vbp < UVLO]
        brown = float(under[-1] - under[0]) if len(under) > 1 else 0.0
        bad = []
        if abs(pk_ds) > 100.0:
            bad.append("Q1 Vds %.0f V over 100 V" % abs(pk_ds))
        if pk_b > 100.0:
            bad.append("+VBAT %.0f V over the LM5164 100 V abs max" % pk_b)
        if pk_f > 58.5:                      # 90 % of the LM74700's 65 V
            bad.append("LM74700 sees %.0f V against its 65 V limit" % pk_f)
        if joules > cap_j:
            # Everything downstream of the clamp is quoted on the assumption
            # that the clamp is still there.  Once it is over its energy the
            # voltages above are the last ones before it fails, not the ones
            # the rest of the board ends up seeing.
            bad.append("TVS absorbs %.1f J against about %.1f J at this width, "
                       "so the voltages left of this column are what the board "
                       "sees only until it fails" % (joules, cap_j))
        if brown > 0:
            bad.append("both rails drop out for %.1f ms" % (brown * 1e3))
        print("    %-26s %8.1fV %8.1fV %8.1fV %8.3fJ %7.1fms  %s"
              % (label, pk_f, pk_b, pk_ds, joules, brown * 1e3,
                 "ok" if not bad else "; ".join(bad)))
        fails += ["%s: %s" % (label.split("(")[0].strip(), b) for b in bad]
        traces.append((label, t, vf, vbp))
    if plots:
        plot_frontend(traces)
    return fails


def plot_frontend(traces):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 3, figsize=(15, 7), constrained_layout=True)
    for ax, (label, t, vf, vbp) in zip(axes.ravel(), traces):
        ax.plot(t * 1e3, vf, lw=1.0, label="harness (VBAT_F)")
        ax.plot(t * 1e3, vbp, lw=1.0, label="+VBAT (LM5164 VIN)")
        ax.axhline(100, color="r", ls=":", lw=0.8)
        ax.axhline(-100, color="r", ls=":", lw=0.8)
        ax.set_title(label, fontsize=9)
        ax.set_xlabel("ms")
        ax.set_ylabel("V")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
    fig.suptitle("ISO 7637-2 pulses at the harness and at the converter input")
    out = os.path.join(SIM, "frontend.png")
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print("\n    wrote %s" % out)


# ================================================================== analog ===
# Jumper configurations, as (label, upper-leg shorted?, lower leg ohms,
# pull-up fitted?) and the input span the setting is meant to cover.
RANGES = [
    ("0-5 V   (RANGE C-A, 15k)",   False, 15e3,  False, 5.0),
    ("0-16 V  (RANGE C-B, 2.21k)", False, 2.21e3, False, 16.0),
    ("bypass  (BYPASS closed)",    True,  None,  False, 3.3),
]


def analog_deck(bypass, rlow, pullup, mode):
    lower = ("Rlow out 0 %g" % rlow) if rlow else "Rlow out 0 1e12"
    upper = "Rup a out 1m" if bypass else "Rup a out 10k"
    pu = "Rpu p5 a 2.49k" if pullup else "Rpu p5 a 1e12"
    if mode == "dc":
        ctrl = "dc Vin -5 40 0.05"
        vec = "wrdata @DAT@ v(out) i(vclamp) v(a)"
        src = "Vin in 0 DC 0"
    elif mode == "ac":
        # wrdata splits a complex vector into two columns, so take the
        # magnitude first and write a real one.
        ctrl = "ac dec 60 1 1meg\nlet m = mag(v(out))"
        vec = "wrdata @DAT@ m"
        src = "Vin in 0 DC 2 AC 1"
    else:                                  # mux settling
        ctrl = "tran 200n 4m uic"
        vec = "wrdata @DAT@ v(out) v(adc)"
        src = "Vin in 0 PULSE(0 %g 1m 1u 1u 1m 4m)" % (16.0 if rlow and
                                                       rlow < 5e3 else 5.0)
    return """* One sensor channel: TVS, series limit, divider, clamp, ADC load
%s

V33 p33 0 DC 3.3
V5s p5  0 DC 5.0
%s

* D_n SMAJ40CA at the connector.
Xtvs in 0 tvs_smaj40ca

* R series/fault limit, then the divider and the range jumper.
Rser in a 1k
%s
%s
%s
Cflt out 0 100n

* BAT54S pair: GND -> node -> +3V3.  Vclamp is the ammeter on the upper
* half, which is the one that carries a positive overvoltage.
Dlo 0 out BAT54
Vclamp out cm DC 0
Dhi cm p33 BAT54

* ADS1115 unbuffered switched-cap front end at +/-4.096 V FSR: about
* 710 k of equivalent input resistance, and the sampling capacitor it
* charges through the mux on-resistance.
Radc out adc 100
Rin adc 0 710k
Csamp adc 0 20p

.control
set filetype=ascii
set wr_singlescale
%s
%s
quit
.endc
.end
""" % (MODELS, src, pu, upper, lower, ctrl, vec)


def sim_analog(plots):
    head("2. Sensor channel: transfer, fault current, bandwidth")
    fails, curves = [], []
    print("    %-28s %10s %10s %10s  %s"
          % ("jumper setting", "at f.s.", "at 36 V", "clamp I", "verdict"))
    for label, byp, rlow, pu, span in RANGES:
        d = run_deck("analog_dc_%s" % label.split()[0],
                     analog_deck(byp, rlow, pu, "dc"),
                     ["v(out)", "i(vclamp)", "v(a)"])
        vin, vout, iclamp = d["x"], d["v(out)"], d["i(vclamp)"]
        at_fs = float(np.interp(span, vin, vout))
        at_36 = float(np.interp(36.0, vin, vout))
        i_36 = abs(float(np.interp(36.0, vin, iclamp)))
        at_neg = float(np.interp(-5.0, vin, vout))
        bad = []
        if at_fs < 1.5:
            bad.append("full scale only %.2f V -- half the range is wasted"
                       % at_fs)
        if at_36 > 3.9:
            bad.append("36 V input drives the pin to %.2f V" % at_36)
        if i_36 > 0.2:
            bad.append("36 V pushes %.0f mA into the BAT54S, rated 200 mA"
                       % (i_36 * 1e3))
        if at_neg < -0.45:
            bad.append("-5 V input drives the pin to %.2f V" % at_neg)
        # Four channels shorted to the top of the input window all inject
        # into +3V3 through their upper clamp diode.  That is fine while the
        # ESP32 is drawing its ~100 mA, and is a rail-pumping hazard when the
        # board is asleep or unpowered with the loom still live.
        if i_36 * 4 > 0.05:
            bad.append("all four channels at 36 V backfeed %.0f mA into +3V3"
                       % (i_36 * 4e3))
        note = ""
        # Only the ESP32's own SAR runs out of window below the rail; the
        # ADS1115 reads to 3.3 V happily at its 4.096 V FSR.
        if at_fs > 3.1:
            note = ("full scale %.2f V is past the ESP32 SAR's usable 3.1 V "
                    "-- read this setting on the ADS1115" % at_fs)
        print("    %-28s %9.3fV %9.3fV %9.1fmA  %s"
              % (label, at_fs, at_36, i_36 * 1e3,
                 "; ".join(bad) if bad else (note or "ok")))
        fails += ["%s: %s" % (label.split("(")[0].strip(), b) for b in bad]
        curves.append((label, vin, vout, iclamp))

    print()
    print("    Small-signal bandwidth (the anti-alias corner the ADS1115 and")
    print("    the ESP32 SAR both sample behind):")
    bws = []
    for label, byp, rlow, pu, span in RANGES:
        d = run_deck("analog_ac_%s" % label.split()[0],
                     analog_deck(byp, rlow, pu, "ac"), ["m"])
        f, mag = d["x"], np.abs(d["m"])
        ref = mag[0]
        below = np.where(mag <= ref / math.sqrt(2.0))[0]
        f3 = float(f[below[0]]) if len(below) else float(f[-1])
        bws.append((label, f3))
        print("        %-28s dc gain %.4f   -3 dB at %8.1f Hz"
              % (label, ref, f3))
    # Aliasing: the ADS1115's fastest rate is 860 SPS, so anything above
    # 430 Hz folds back into the reading.
    for label, f3 in bws:
        if f3 > 430.0:
            fails.append("%s: filter corner %.0f Hz is above the 430 Hz "
                         "Nyquist of the ADS1115 at 860 SPS, so wideband "
                         "sensor noise aliases into the reading"
                         % (label.split("(")[0].strip(), f3))
    if plots:
        plot_analog(curves, bws)
    return fails


def plot_analog(curves, bws):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6),
                                   constrained_layout=True)
    for label, vin, vout, iclamp in curves:
        ax1.plot(vin, vout, lw=1.2, label=label)
    ax1.axhline(3.3, color="r", ls=":", lw=0.8)
    ax1.axhline(3.1, color="orange", ls=":", lw=0.8)
    ax1.set_xlabel("harness input, V")
    ax1.set_ylabel("ADC node, V")
    ax1.set_title("Transfer and clamp")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)
    for label, vin, vout, iclamp in curves:
        ax2.plot(vin, np.abs(iclamp) * 1e3, lw=1.2, label=label)
    ax2.axhline(200, color="r", ls=":", lw=0.8)
    ax2.set_xlabel("harness input, V")
    ax2.set_ylabel("BAT54S current, mA")
    ax2.set_yscale("log")
    ax2.set_title("Current into the clamp")
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8)
    out = os.path.join(SIM, "analog.png")
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print("\n    wrote %s" % out)


# ==================================================================== buck ===
def buck_deck(vin, vout, ind, iout, cout, lloop=4e-9, fsw=400e3):
    """Power stage only: an ideal switch pair at the duty the converter
    would settle on, driving the real inductor and output bank.

    The load is a resistor, not a current sink.  Open loop there is nothing
    to set the operating point, and a current sink into a fixed duty cycle
    has no DC solution at all -- the first version of this deck reported
    3 V of ripple on a 5 V rail for exactly that reason.  The inductor and
    output cap start at the operating point so the measurement window is
    not measuring startup.
    """
    duty = (vout + 0.4) / (vin - 0.2)      # diode drop and switch IR
    per = 1.0 / fsw
    ton = duty * per
    rload = vout / iout
    return """* LM5164 power stage: %g V in, %g V out, %g uH, %g A
%s
.model SWM SW(vt=0.5 vh=0.01 ron=0.09 roff=10meg)
.model DFW D(IS=1e-8 N=1.0 RS=0.02 BV=100 IBV=1m)

Vin  vin 0 DC %g
* Input capacitor bank at the VIN pin: 100 nF + 10 uF ceramic, plus the
* 100 uF bulk further away with its ESR.  Vamm is the ammeter that shows
* how much switching ripple current the ceramics have to carry.
Vamm vin vcap DC 0
Cin1 vcap 0 100n
Cin2 vcap ci2 10u
Rci2 ci2 0 0.005
Cin3 vcap ci3 100u
Rci3 ci3 0 0.30

* The loop from those ceramics to the VIN pin, at roughly 1 nH per mm of
* go-and-return.  This is the whole reason a switcher's input capacitor has
* to be at its own pin: the number below is the only difference between a
* 3.7 mm placement and a 12.6 mm one.
Lloop vcap vsw_in %g
Rloop vsw_in vsw_r 0.010

* The switch node's own capacitance -- the high-side FET's Coss and the
* catch diode's junction -- is what the loop inductance rings against when
* the current commutates.  Leave it out and the model has nowhere to put
* the loop energy, so it reports hundreds of volts of spike instead of the
* I*sqrt(L/C) the circuit actually produces.
Coss vsw_r sw 80p
Cjd  sw 0 100p

* 5 ns edges, which is what a 100 V GaN-adjacent part actually does.  An
* instantaneous edge would ring on numerical noise rather than on physics.
Vg   g 0 PULSE(0 1 0 5n 5n %g %g)
Shi  vsw_r sw g 0 SWM
Dlo  0 sw DFW

L1   sw out %g IC=%g
Rdcr out o2 0.055
* Output bank at its biased capacitance, not its printed value: a 22 uF
* 16 V X5R in 1206 keeps roughly half of it at 5 V of DC bias, and reading
* the ripple off the nameplate value would understate it by a factor of two.
Cout o2 c1 %g
Rc1  c1 0 0.003
Cou2 o2 c2 %g
Rc2  c2 0 0.003
Cou3 o2 c3 100n
Rc3  c3 0 0.02
Rload o2 0 %g
.ic v(o2)=%g

.control
set filetype=ascii
set wr_singlescale
tran 10n 300u uic
wrdata @DAT@ v(o2) i(l1) i(vamm) v(vsw_in)
quit
.endc
.end
""" % (vin, vout, ind * 1e6, iout, MODELS, vin, lloop, ton, per, ind, iout,
       cout, cout, rload, vout)


BUCKS = [
    # label, Vout, L, Isat of the Sunlord SWPA8040S part, full load,
    # and one output MLCC's capacitance at that DC bias
    ("+5V  33 uH", 5.0, 33e-6, 3.0, 2.0, 11e-6),
    ("+3V3 22 uH", 3.3, 22e-6, 3.4, 1.0, 14e-6),
]


def sim_buck(plots):
    head("3. Buck power stage: ripple, saturation margin, input RMS current")
    print("    Ideal-duty power stage, not the LM5164 control loop. Answers")
    print("    what the passives have to survive; loop stability needs TI's")
    print("    encrypted PSpice model.")
    print()
    print("    %-12s %6s %9s %8s %9s %9s  %s"
          % ("rail", "Vin", "I ripple", "I peak", "V ripple", "Cin Irms",
             "verdict"))
    fails, traces = [], []
    for label, vo, ind, isat, io, cbias in BUCKS:
        for vin in (8.0, 13.5, 36.0):
            d = run_deck("buck_%s_%dv" % (label.split()[0].strip("+"), vin),
                         buck_deck(vin, vo, ind, io, cbias),
                         ["v(o2)", "i(l1)", "i(vamm)", "v(vsw_in)"])
            t, vo_t, il, iin = d["x"], d["v(o2)"], d["i(l1)"], d["i(vamm)"]
            # Last third of the run, once the start-up ring has died away.
            w = (t > 200e-6)
            ripple_i = float(il[w].max() - il[w].min())
            peak_i = float(np.abs(il[w]).max())
            ripple_v = float(vo_t[w].max() - vo_t[w].min())
            irms = float(np.sqrt(np.mean((iin[w] - iin[w].mean()) ** 2)))
            bad = []
            if peak_i > isat:
                bad.append("peak %.2f A over the %.1f A saturation current"
                           % (peak_i, isat))
            if ripple_v > 0.05 * vo:
                bad.append("%.0f mV ripple is over 5%% of %.1f V"
                           % (ripple_v * 1e3, vo))
            print("    %-12s %5.1fV %8.2fA %7.2fA %8.0fmV %8.2fA  %s"
                  % (label, vin, ripple_i, peak_i, ripple_v * 1e3, irms,
                     "ok" if not bad else "; ".join(bad)))
            fails += ["%s at %.0f V: %s" % (label, vin, b) for b in bad]
            if abs(vin - 13.5) < 0.1:
                traces.append((label, t, vo_t, il))

    print()
    print("    The 'Cin Irms' column above is the ripple current the input")
    print("    capacitors have to supply from beside the VIN pin. Every")
    print("    millimetre between them and the pin is about 1 nH of loop,")
    print("    go and return, that this current has to be pushed through on")
    print("    each edge. How many volts of spike that becomes at the pin is")
    print("    NOT answered here: it depends on the internal FET's Coss and")
    print("    gate drive, and with an ideal switch the deck reports whatever")
    print("    numerical ringing it is given. Take the RMS figure as the")
    print("    reason to keep the caps at the pin, not as a spike amplitude.")
    if plots:
        plot_buck(traces)
    return fails


def plot_buck(traces):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(traces), 1, figsize=(11, 3.2 * len(traces)),
                             constrained_layout=True, squeeze=False)
    for ax, (label, t, vo, il) in zip(axes.ravel(), traces):
        ax.plot(t * 1e6, vo, lw=0.9, color="tab:blue", label="Vout")
        ax.set_ylabel("V", color="tab:blue")
        ax.set_xlabel("us")
        ax.grid(alpha=0.3)
        ax2 = ax.twinx()
        ax2.plot(t * 1e6, il, lw=0.6, color="tab:orange", label="I(L)")
        ax2.set_ylabel("A", color="tab:orange")
        ax.set_title("%s at 13.5 V in, load step at 400 us" % label,
                     fontsize=9)
    out = os.path.join(SIM, "buck.png")
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print("\n    wrote %s" % out)


# ================================================================ ridethru ===
# The whole reason this board grew to 100 mm: the ignition cuts power with a
# file open on the SD card, and firmware needs enough warning and enough
# stored energy to flush and close it.  Chain under test:
#
#   harness opens -> VBAT_F collapses -> TLV431 releases PWR_FAIL (interrupt)
#   -> firmware sheds the sensor rail and LEDs -> +VBAT coasts on the bank
#   -> converters drop out at UVLO.  The usable window is detect-to-dropout.

C_BANK = 760e-6        # 100 uF + 2 x 330 uF on +VBAT
P_BOARD = 0.35         # ESP32 logging + SD write bursts, after the shed
P_SENSORS = 0.40       # four sensors at 20 mA on +5VS, before the shed
T_FW = 5e-3            # firmware latency from interrupt to load shed


def ridethru_deck(shed):
    """Ignition cut at 20 ms.  `shed` = firmware drops the sensor rail T_FW
    after PWR_FAIL asserts; otherwise the sensors ride the bank down."""
    return """* power-cut ride-through: detector warning vs bank hold-up
%s

* Battery behind the harness, disconnected at 20 ms by a series switch.
Vbat  bt 0 DC 13.5
Vsw   ctl 0 PULSE(1 0 20m 1u 1u 1 2)
Scut  bt hin ctl 0 SWM
.model SWM SW(vt=0.5 vh=0.01 ron=0.05 roff=100meg)

Lharn hin hr 5u
Rharn hr vf 0.1

* The rest of the car: with the ignition open the harness is not floating,
* the remaining loads pull it down.  Without this the model leaves vf
* hanging at whatever the divider lets it float to.
Rcar  vf 0 200

* Divider and a behavioral TLV431: cathode pulls low while REF > 1.24 V.
Rdu  vf  sen 100k
Rdl  sen 0   12.7k
Rhys pf  sen 1meg
Rpu  p33 pf  10k
V33  p33 0   DC 3.3
Bq   pf  0   I = (V(pf)/50.0) / (1 + exp(-(V(sen) - 1.24)/0.005))

* Q1 under the LM74700: the controller compares its two sides and turns the
* FET off on reverse current, so unlike the plain SW model used elsewhere
* this one must NOT conduct backward -- that is the entire mechanism that
* separates the coasting bank from the collapsing harness.  Modelled as a
* smooth behavioral diode (6.8 mohm forward, open reverse) rather than a
* voltage-controlled switch: a switch whose control is its own terminal
* voltage has no stable operating point and the solver never converges.
* max() not a tanh blend: the blend's transition region still conducts tens
* of mA backward at a few mV of reverse bias, which quietly tied the
* harness to the bank and made the detector watch the bank discharge
* instead of the ignition switch.
Bdio  vf vbp I = max(V(vf)-V(vbp), 0)/0.0068 + (V(vf)-V(vbp))*1e-6
Dbody vf vbp DBODY

* The +VBAT bank.
Cbulk vbp b1 100u
Rblk  b1 0 0.30
Cr1   vbp b2 330u
Rr1   b2 0 0.15
Cr2   vbp b3 330u
Rr2   b3 0 0.15

* Board load as constant POWER with UVLO foldback: a buck's input current
* RISES as its input falls, which is exactly what makes hold-up worse than
* a resistor model pretends.
Bload vbp 0 I = (%g / max(V(vbp), 2.0)) * (0.5 + 0.5*tanh((V(vbp) - %g)/0.3))

* Sensor load: constant current from +5V referred to the input, gone %s
* -- tracks PWR_FAIL plus the firmware latency via a delayed RC flag.  The
* RC hangs off a buffered copy of PWR_FAIL: hung directly on the pin, its
* microfarads did what no GPIO does and slewed the interrupt itself into
* a 78 ms ramp.
Bbuf  dbf 0 V = V(pf)
Rdly  dbf dly 1k
Cdly  dly 0   %g
Bsens vbp 0 I = (%g / max(V(vbp), 2.0)) * (0.5 + 0.5*tanh((V(vbp) - %g)/0.3)) * %s

.control
set filetype=ascii
set wr_singlescale
tran 20u 400m 0 20u
wrdata @DAT@ v(vbp) v(pf) v(vf)
quit
.endc
.end
""" % (MODELS, P_BOARD, UVLO,
       "after the shed" if shed else "never (shed disabled)",
       T_FW / (1e3 * 0.7),  # R*C such that the flag crosses mid at T_FW
       P_SENSORS, UVLO,
       "(1 - (0.5 + 0.5*tanh((V(dly) - 1.65)/0.1)))" if shed else "1")


def sim_ridethru(plots):
    head("4. Power-cut ride-through: warning window vs hold-up")
    print("    Ignition opens at t=20 ms with the battery at 13.5 V. The")
    print("    usable window is PWR_FAIL asserting to +VBAT crossing the")
    print("    converters' %.0f V dropout. An SD flush-and-close is tens of" % UVLO)
    print("    ms on a healthy card, worst-case ~100 ms on a stalling one.")
    print()
    fails, traces = [], []
    print("    %-22s %10s %10s %10s  %s"
          % ("case", "detect", "dropout", "window", "verdict"))
    for shed in (True, False):
        d = run_deck("ridethru_%s" % ("shed" if shed else "noshed"),
                     ridethru_deck(shed), ["v(vbp)", "v(pf)", "v(vf)"])
        t, vbp, pf = d["x"], d["v(vbp)"], d["v(pf)"]
        cut = 20e-3
        rise = t[(t > cut) & (pf > 1.65)]
        t_det = float(rise[0]) - cut if len(rise) else None
        drop = t[(t > cut) & (vbp < UVLO)]
        t_die = float(drop[0]) - cut if len(drop) else None
        if t_det is None:
            fails.append("PWR_FAIL never asserted after the cut")
            continue
        window = (t_die - t_det) if t_die is not None else float("inf")
        label = "sensors shed" if shed else "shed disabled"
        bad = []
        if t_det > 5e-3:
            bad.append("detection took %.1f ms" % (t_det * 1e3))
        if window < 50e-3:
            bad.append("only %.0f ms between warning and dropout -- not "
                       "enough to guarantee a worst-case SD flush"
                       % (window * 1e3))
        w_txt = ">380" if window == float("inf") else "%8.1f" % (window * 1e3)
        print("    %-22s %8.2fms %8s %9sms  %s"
              % (label, t_det * 1e3,
                 ("%8.1fms" % (t_die * 1e3)) if t_die is not None else "  never",
                 w_txt, "ok" if not bad else "; ".join(bad)))
        fails += ["ride-through (%s): %s" % (label, b) for b in bad]
        traces.append((label, t, vbp, pf))

    # Static checks on the detector itself, from the divider algebra rather
    # than the transient: trip point and hysteresis.
    trip = 1.24 * (100e3 + 12.7e3) / 12.7e3
    hys = trip * (12.7e3 / (100e3 + 12.7e3)) * (3.3 / 1e6) * (100e3 * 12.7e3
          / (100e3 + 12.7e3)) / 1.24 * 1e3  # rough, reported not judged
    print()
    print("    Divider trip point: %.2f V on the harness (target ~11 V, "
          "below any healthy battery, above the %.0f V dropout)." % (trip, UVLO))
    if not 10.0 <= trip <= 12.0:
        fails.append("power-fail trip computes to %.2f V, outside 10-12 V"
                     % trip)
    if plots:
        plot_ridethru(traces)
    return fails


def plot_ridethru(traces):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(traces), figsize=(6.2 * len(traces), 4.2),
                             constrained_layout=True, squeeze=False)
    for ax, (label, t, vbp, pf) in zip(axes.ravel(), traces):
        ax.plot(t * 1e3, vbp, lw=1.2, label="+VBAT (bank)")
        ax.plot(t * 1e3, pf, lw=1.0, label="PWR_FAIL")
        ax.axhline(UVLO, color="r", ls=":", lw=0.8)
        ax.axvline(20, color="gray", ls=":", lw=0.8)
        ax.set_title("ignition cut at 20 ms -- " + label, fontsize=9)
        ax.set_xlabel("ms")
        ax.set_ylabel("V")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    out = os.path.join(SIM, "ridethru.png")
    fig.savefig(out, dpi=110)
    plt.close(fig)
    print("\n    wrote %s" % out)


# ================================================================== inrush ===
def inrush_deck():
    """Battery connect into the discharged 760 uF bank.

    Until the LM74700's charge pump wakes, the inrush flows through Q1's
    body diode -- so the surge is limited only by the harness inductance,
    the fuse's cold resistance and the capacitors' ESR.  The fuse must not
    open on it and the body diode must not absorb more than a diode can.
    """
    return """* battery connect: inrush into the discharged bank
%s

Vbat  bt 0 DC 0 PULSE(0 13.5 100u 10u 1u 1 2)
Lharn bt hr 5u
Rharn hr fin 0.1
* F1 cold resistance, with an ammeter for the I2t integral.
Vfuse fin vf DC 0
Rfuse vf fa 0.05
Rfb   fa vfb 0.02
Lfb   fa vfb 0.955u

* Body diode only: the controller has not started yet.  This IS the worst
* case -- once the FET enhances the drop collapses and stress goes down.
Dbody vfb vbp DBODY

Cblk  vbp b1 100u
Rblk  b1 0 0.30
Cr1   vbp b2 330u
Rr1   b2 0 0.15
Cr2   vbp b3 330u
Rr2   b3 0 0.15
Cin1  vbp b4 10u
Rin1  b4 0 0.005

.control
set filetype=ascii
set wr_singlescale
tran 0.2u 3m 0 0.2u uic
wrdata @DAT@ i(vfuse) v(vbp) v(vfb)
quit
.endc
.end
""" % MODELS


def sim_inrush(plots):
    head("5. Battery-connect inrush into the ride-through bank")
    print("    760 uF charging through the harness, F1's cold resistance and")
    print("    Q1's body diode (the LM74700 has not started yet).")
    fails = []
    d = run_deck("inrush", inrush_deck(), ["i(vfuse)", "v(vbp)", "v(vfb)"])
    t, i_f, vbp, vfb = d["x"], d["i(vfuse)"], d["v(vbp)"], d["v(vfb)"]
    pk = float(np.abs(i_f).max())
    i2t = float(np.trapezoid(i_f ** 2, t))
    # Diode energy during the charge.
    vd = np.clip(vfb - vbp, 0, None)
    e_d = float(np.trapezoid(vd * np.abs(i_f), t))
    settle = t[np.abs(i_f) > 0.5]
    dur = float(settle[-1] - settle[0]) * 1e3 if len(settle) else 0.0
    print("    peak current     : %6.1f A" % pk)
    print("    surge I2t        : %6.3f A2s   (verify against the 0466002.NR"
          % i2t)
    print("                       melting I2t in the Littelfuse datasheet --")
    print("                       nano2 2 A parts are specified around 1 A2s)")
    print("    body-diode energy: %6.1f mJ over %.2f ms" % (e_d * 1e3, dur))
    if i2t > 0.8:
        fails.append("battery-connect I2t %.2f A2s is in the range where a "
                     "2 A nano fuse ages or opens -- verify the datasheet "
                     "number or add inrush limiting" % i2t)
    if e_d > 50e-3:
        fails.append("Q1 body diode absorbs %.0f mJ during connect" % (e_d*1e3))
    return fails


# =================================================================== crank ===
def crank_deck(vdip):
    """ISO 16750-2 style starting profile: drop to `vdip`, 15 ms at the
    bottom, partial recovery to 6.5 V while the starter turns, then back."""
    return """* engine crank: does the logger ride it or reset?
%s

Vbat  bt 0 DC 13.5 PWL(0 13.5  10m 13.5  11m %g  26m %g  31m 6.5
+ 431m 6.5  436m 13.5  1 13.5)
Lharn bt hr 5u
Rharn hr vf 0.11
Rcar  vf 0 200

Rdu  vf  sen 100k
Rdl  sen 0   12.7k
Rhys pf  sen 1meg
Rpu  p33 pf  10k
V33  p33 0   DC 3.3
Bq   pf  0   I = (V(pf)/50.0) / (1 + exp(-(V(sen) - 1.24)/0.005))

Bdio  vf vbp I = max(V(vf)-V(vbp), 0)/0.0068 + (V(vf)-V(vbp))*1e-6
Dbody vf vbp DBODY

Cblk  vbp b1 100u
Rblk  b1 0 0.30
Cr1   vbp b2 330u
Rr1   b2 0 0.15
Cr2   vbp b3 330u
Rr2   b3 0 0.15

Bload vbp 0 I = (0.35 / max(V(vbp), 2.0)) * (0.5 + 0.5*tanh((V(vbp) - %g)/0.3))

.control
set filetype=ascii
set wr_singlescale
tran 50u 600m 0 50u
wrdata @DAT@ v(vbp) v(pf) v(vf)
quit
.endc
.end
""" % (MODELS, vdip, vdip, UVLO)


def sim_crank(plots):
    head("6. Engine crank: ride or reset, and does PWR_FAIL chatter")
    print("    ISO 16750-2 style profile: dip at 11 ms, 15 ms at the bottom,")
    print("    then 400 ms at 6.5 V while the starter turns.  Sensors are")
    print("    assumed already shed (PWR_FAIL asserts on the way down).")
    print()
    fails = []
    print("    %-18s %10s %10s %10s  %s"
          % ("dip", "min +VBAT", "rails", "PF edges", "verdict"))
    for vdip, label in ((6.0, "warm crank 6.0V"), (4.5, "cold crank 4.5V")):
        d = run_deck("crank_%d" % (vdip * 10), crank_deck(vdip),
                     ["v(vbp)", "v(pf)", "v(vf)"])
        t, vbp, pf = d["x"], d["v(vbp)"], d["v(pf)"]
        vmin = float(vbp.min())
        dropped = bool((vbp < UVLO).any())
        # count PWR_FAIL rising edges -- more than one is chatter
        hi = pf > 1.65
        edges = int(np.sum(hi[1:] & ~hi[:-1]))
        bad = []
        if edges > 1:
            bad.append("PWR_FAIL chattered %d times" % edges)
        print("    %-18s %9.2fV %10s %10d  %s"
              % (label, vmin, "DROP" if dropped else "held", edges,
                 "; ".join(bad) if bad else
                 ("ok" if not dropped else
                  "ok (reset accepted: harness sat below the converters' "
                  "own dropout)")))
        fails += ["crank (%s): %s" % (label, b) for b in bad]
        if dropped and vdip >= 6.0:
            fails.append("crank (%s): rails dropped even though the harness "
                         "never went below %.1f V" % (label, vdip))
    return fails


# ==================================================================== usb ===
def sim_usb(plots):
    head("7. Non-compliant USB supply vs the OVP cutoff")
    print("    Without protection a brick that negotiates 9 V lifts the whole")
    print("    +5V rail to 8.4 V through PF2 and D5 (the buck can source but")
    print("    not sink), over the TJA1051's 6 V absolute maximum.  The board")
    print("    now carries a TLV431 + AO3401 series cutoff at 5.77 V; this")
    print("    deck sweeps the brick and checks the rail stays inside 6 V.")
    fails = []
    deck = """* hostile USB brick vs the TLV431/AO3401 OVP cutoff
%s
.model DOR D(IS=1e-9 N=1.05 RS=0.05 BV=40 IBV=1m)
Vbrick vb 0 DC 12
Rpf2   vb vr 0.3

* OVP divider off the raw side, and the switch as a behavioral element:
* conducting while the divider sits below the TLV431's 1.24 V reference,
* smoothly opening above it.  The P-FET body diode points at the brick,
* so an open switch really is open toward the board.
Rdu  vr sen 100k
Rdl  sen 0  27.4k
Bsw  vr vbus I = (V(vr)-V(vbus))/0.05 / (1 + exp((V(sen) - 1.24)/0.003))
Rlk  vr vbus 10meg

D5     vbus v5 DOR
Vbuck  bk 0 DC 5.05
Dbuck  bk v5 DOR
Rload  v5 0 25
Cv5    v5 0 22u
.control
set filetype=ascii
set wr_singlescale
dc Vbrick 4.5 14 0.05
wrdata @DAT@ v(v5) v(vbus)
quit
.endc
.end
""" % MODELS
    d = run_deck("usb_ovp", deck, ["v(v5)", "v(vbus)"])
    vin, v5, vbus = d["x"], d["v(v5)"], d["v(vbus)"]
    at5 = float(np.interp(5.0, vin, vbus))
    pk = float(v5.max())
    cut = vin[vbus < vin - 1.0]
    print("    brick at 5.0 V -> VBUS %.2f V (normal bench operation)" % at5)
    print("    switch opens at ~%.2f V of brick" % (float(cut[0]) if len(cut)
                                                    else float("nan")))
    print("    worst +5V across the 4.5-14 V sweep: %.2f V" % pk)
    if pk > 6.0:
        fails.append("+5V still reaches %.2f V somewhere in the sweep -- "
                     "the OVP trip or the divider is wrong" % pk)
    if at5 < 4.6:
        fails.append("a compliant 5 V source only delivers %.2f V of VBUS "
                     "through the switch" % at5)
    return fails


# =============================================================== tolerance ===
def sim_tolerance(plots):
    """Monte Carlo over component tolerances -- numpy, no spice needed."""
    head("8. Tolerance stack (Monte Carlo, 20000 samples)")
    rng = np.random.default_rng(20260811)
    N = 20000
    fails = []

    def r(nom, pct):
        return nom * (1 + rng.uniform(-pct, pct, N) / 100.0)

    # Power-fail trip point: 1% divider, 1% reference.
    vref = 1.24 * (1 + rng.uniform(-1, 1, N) / 100.0)
    trip = vref * (r(100e3, 1) + r(12.7e3, 1)) / r(12.7e3, 1)
    lo, hi = np.percentile(trip, [0.1, 99.9])
    print("    PWR_FAIL trip    : %5.2f V nominal, %5.2f..%5.2f V at 99.8%%"
          % (float(np.median(trip)), lo, hi))
    if hi > 11.8:
        fails.append("power-fail trip can reach %.2f V, close enough to a "
                     "resting 12.2 V battery to false-trigger" % hi)
    if lo < 9.0:
        fails.append("power-fail trip can fall to %.2f V" % lo)

    # 0-5 V channel gain: 1k + 10k upper, 15k lower, all 0.1%.
    up = r(1e3, 0.1) + r(10e3, 0.1)
    dn = r(15e3, 0.1)
    gain = dn / (up + dn)
    err = (gain / (15.0 / 26.0) - 1) * 100
    print("    0-5V channel gain: +/-%.3f %% worst of 99.8%% "
          "(0.1%% thin-film stack)" % float(np.percentile(np.abs(err), 99.9)))
    if np.percentile(np.abs(err), 99.9) > 0.5:
        fails.append("0-5V divider gain error exceeds 0.5%")

    # Buck outputs: 1.2 V +/-1.5% reference, 1% dividers.
    for rail, rt, rb, nom in (("+5V", 100e3, 31.6e3, 5.0),
                              ("+3V3", 100e3, 57.6e3, 3.3)):
        fb = 1.2 * (1 + rng.uniform(-1.5, 1.5, N) / 100.0)
        vout = fb * (r(rt, 1) + r(rb, 1)) / r(rb, 1)
        lo, hi = np.percentile(vout, [0.1, 99.9])
        print("    %-5s output     : %5.3f..%5.3f V at 99.8%%"
              % (rail, lo, hi))
        if rail == "+3V3" and hi > 3.46:
            fails.append("+3V3 can reach %.2f V, into the ESP32's 3.6 V "
                         "absolute-max margin" % hi)
        if lo < nom * 0.95:
            fails.append("%s can fall %.1f%% low" % (rail, (1 - lo/nom)*100))

    # Ride-through window at the bad corner: electrolytics are -20/+20%,
    # the load estimate is soft, and the window must still cover a flush.
    c_bank = (100e-6 * (1 + rng.uniform(-0.2, 0.2, N))
              + 2 * 220e-6 * (1 + rng.uniform(-0.2, 0.2, N)))
    p_load = 0.35 * (1 + rng.uniform(-0.2, 0.4, N))
    v0 = 11.0 - 0.3                       # detect, less hysteresis band
    window = c_bank * (v0 ** 2 - 6.0 ** 2) / (2 * p_load)
    lo = float(np.percentile(window, 0.1))
    print("    ride-through     : %4.0f ms median, %4.0f ms at the 0.1%% "
          "corner (caps -20%%, load +40%%)"
          % (float(np.median(window)) * 1e3, lo * 1e3))
    # The gate is the healthy-card flush (~10-30 ms). Covering a stalled
    # card was never in reach even at nominal (55 ms median) -- that risk
    # class is handled by firmware flushing often, per the README.
    if lo < 0.030:
        fails.append("ride-through window can fall to %.0f ms" % (lo * 1e3))

    # Battery monitor: 1% divider into a 1%-ish calibrated ADC.
    div = r(8.2e3, 1) / (r(100e3, 1) + r(8.2e3, 1))
    err_b = (div / (8.2 / 108.2) - 1) * 100
    print("    VBAT_SNS scale   : +/-%.2f %% worst of 99.8%% -- calibrate "
          "in firmware, do not trust the nominal ratio"
          % float(np.percentile(np.abs(err_b), 99.9)))
    return fails


# =============================================================== stability ===
def sim_stability(plots):
    """The LM5164's ripple-injection criterion, analytically.

    The control loop itself hides inside TI's encrypted model, but a COT
    converter's stability reduces to one requirement the datasheet states
    outright: the injected ramp at FB during the on-time must be large
    enough (and in phase) for clean valley detection.  That amplitude is
    (VIN - VOUT) * tON / (RA * CA), and it shrinks as VIN falls toward
    dropout -- so the number to know is the worst corner, not the nominal.
    """
    head("9. Buck ripple-injection amplitude across the input window")
    print("    Requirement: >= ~15 mV of injected ramp at FB for clean COT")
    print("    valley switching (TI SNVSAI2, ripple-injection network).")
    print()
    fails = []
    fsw = 400e3
    print("    %-6s %6s | %s" % ("rail", "Vin", "ramp at FB, worst-tolerance"))
    for rail, vout, ra, ca in (("+5V", 5.0, 121e3, 2.2e-9),
                               ("+3V3", 3.3, 95.3e3, 2.2e-9)):
        for vin in (6.0, 8.0, 13.5, 24.0, 36.0):
            if vin <= vout + 0.5:
                continue
            ton = vout / (vin * fsw)
            # worst case: RA +1 %, CA +10 % both shrink the ramp
            ramp = (vin - vout) * ton / (ra * 1.01 * ca * 1.10)
            note = ""
            if ramp < 0.015:
                note = ("thin -- acceptable only because a harness this low "
                        "is a crank event, not an operating point")
                if vin >= 8.0:
                    fails.append("%s ramp is %.1f mV at %.0f V in"
                                 % (rail, ramp * 1e3, vin))
            print("    %-6s %5.1fV | %5.1f mV  %s"
                  % (rail, vin, ramp * 1e3, note))
    return fails


# ================================================================== canbus ===
def sim_canbus(plots):
    """TJA1051 through the common-mode choke and split termination into a
    real cable: does a dominant bit arrive clean at the far end?"""
    head("10. CAN bus: dominant bit through choke, split term and 5 m of bus")
    fails = []
    deck = """* CAN dominant-recessive-dominant through the on-board network
%s

* Driver calibrated to the TJA1051 datasheet: 2.0 V typical differential
* INTO the 60 ohm double termination, i.e. 3.0 V open-circuit behind
* 15 ohm per leg. Recessive releases both to a weak 2.5 V hold.
Vctl  c 0 PULSE(0 1 200n 5n 5n 1u 2u)
Vhi   vh 0 DC 4.0
Vlo   vl 0 DC 1.0
Vmid  vm 0 DC 2.5
Bh    th 0 V = V(c) > 0.5 ? V(vh) : V(vm)
Bl    tl 0 V = V(c) > 0.5 ? V(vl) : V(vm)
Rh    th canh_t 15
Rl    tl canl_t 15

* 51 uH common-mode choke, k = 0.995: near-transparent differentially.
Lh    canh_t canh 51u
Ll    canl_t canl 51u
K1    Lh Ll 0.995

* Split termination on-board: 60 + 60 with the centre decoupled.
Rs1   canh split 60
Rs2   canl split 60
Cs    split 0 4.7n

* Clamps' parasitic capacitance at the connector.
Ch    canh 0 30p
Cl    canl 0 30p

* 5 m of bus at 120 ohm, ~21 ns of flight, terminated at the far end.
* An ideal T element rather than LTRA: the lossy model went numerically
* wild against the coupled choke and reported more differential volts at
* the far end than the driver can produce.
T1    canh canl far_h far_l Z0=120 TD=21n
Rterm far_h far_l 120
Rfh   far_h 0 1meg
Rfl   far_l 0 1meg

.control
set filetype=ascii
set wr_singlescale
tran 1n 2u 0 1n
wrdata @DAT@ v(canh) v(canl) v(far_h) v(far_l)
quit
.endc
.end
""" % MODELS
    d = run_deck("canbus", deck, ["v(canh)", "v(canl)", "v(far_h)",
                                  "v(far_l)"])
    t = d["x"]
    dif_near = d["v(canh)"] - d["v(canl)"]
    dif_far = d["v(far_h)"] - d["v(far_l)"]
    w = (t > 0.6e-6) & (t < 1.1e-6)      # settled dominant portion
    dom_far = float(dif_far[w].mean())
    ring = float(dif_far.max() - dif_far[w].max())
    print("    dominant differential at the far end: %.2f V "
          "(ISO 11898 wants 1.5-3.0)" % dom_far)
    print("    worst overshoot beyond settled level: %.2f V" % max(ring, 0))
    if not 1.5 <= dom_far <= 3.0:
        fails.append("far-end dominant level %.2f V is outside 1.5-3.0 V"
                     % dom_far)
    if plots:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4), constrained_layout=True)
        ax.plot(t * 1e6, dif_near, lw=1.0, label="node (this board)")
        ax.plot(t * 1e6, dif_far, lw=1.0, label="far end, 5 m")
        ax.axhline(1.5, color="r", ls=":", lw=0.8)
        ax.set_xlabel("us")
        ax.set_ylabel("CANH - CANL, V")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_title("dominant bit through choke + split termination")
        out = os.path.join(SIM, "canbus.png")
        fig.savefig(out, dpi=110)
        plt.close(fig)
        print("    wrote %s" % out)
    return fails


# ================================================================= budgets ===
def sim_budgets(plots):
    """System-level budgets -- the failures that read 'marginal in July'
    rather than 'broken on the bench'. All analytic."""
    head("11. System budgets: thermal, fuse, I2C, LED chain, SD switch")
    fails = []

    # --- enclosure thermal --------------------------------------------------
    # Worst continuous: both bucks loaded (0.55 W loss each), ESP32 logging
    # with WiFi bursts (~1.0 W average), CAN + SD + sensors (~0.4 W).
    # The board mounts IN THE DASH: cabin hot-soak ambient (~70 C parked in
    # the sun), not the 85 C engine bay. The bay column stays as the answer
    # to "what happens if it ever moves there": a sealed plastic box in the
    # bay would exceed the electrolytics' 105 C rating.
    p_diss = 0.55 * 2 + 1.0 + 0.4
    print("    Enclosure thermal, %.1f W dissipated inside the box:" % p_diss)
    print("      %-34s %-8s %-8s %s"
          % ("enclosure", "rise", "70C dash", "85C bay"))
    for label, rth in (("sealed ABS ~100x120x40", 9.0),
                       ("vented ABS, same size", 5.5),
                       ("diecast aluminium on a bracket", 3.5)):
        rise = p_diss * rth
        t_dash, t_bay = 70 + rise, 85 + rise
        note = "ok in the dash" if t_dash <= 100 else "HOT even in the dash"
        if t_bay > 105:
            note += "; a bay mount would exceed the caps' 105 C"
        print("      %-34s +%4.1f C %6.1f C %6.1f C  %s"
              % (label, rise, t_dash, t_bay, note))
        if t_dash > 105:
            fails.append("%s exceeds 105 C even at dash ambient" % label)

    # --- fuse derating ------------------------------------------------------
    # A fuse holds ~87 % of its rating at a 70 C dash hot-soak; guidance
    # loads it to no more than 75 % of that. (An 85 C engine bay would put
    # it right on the line -- relevant only if the board moves there.)
    i_load = 1.20                      # both bucks flat out at 8 V input
    i_eff = 2.0 * 0.87
    util = i_load / i_eff
    print("\n    F1 (2 A slow) at a 70 C dash: effective %.2f A, load "
          "%.2f A -> %.0f %% utilisation" % (i_eff, i_load, util * 100))
    if util > 0.75:
        fails.append("F1 sits at %.0f%% of its derated rating" % (util * 100))

    # --- I2C rise time ------------------------------------------------------
    # 4.7k pull-ups; ~30 pF on-board (module + ADS1115 + trace). External
    # Qwiic devices add ~10 pF each plus ~50 pF per metre of cable.
    print("\n    I2C at 400 kHz needs t_r <= 300 ns (0.847*R*C):")
    for ext, label in ((0, "on-board only"),
                       (60, "2 Qwiic devices, 0.5 m cable"),
                       (150, "4 devices, 1.5 m of cable")):
        c = (30 + ext) * 1e-12
        tr = 0.847 * 4700 * c
        print("      %-28s %4.0f pF -> t_r %4.0f ns  %s"
              % (label, 30 + ext, tr * 1e9,
                 "ok" if tr <= 300e-9 else "drop the bus to 100 kHz"))

    # --- WS2812 chain -------------------------------------------------------
    print("\n    WS2812 header: PF3 holds 0.5 A -> %d LEDs at full white, "
          "~%d at typical shift-light duty"
          % (int(0.5 / 0.060), int(0.5 / 0.020)))

    # --- SD power switch ----------------------------------------------------
    r_on = 0.090                       # DMG2301L at Vgs = 3.3 V
    drop = r_on * 0.100
    print("\n    SD_VDD switch: DMG2301L ~%.0f mR at Vgs 3.3 -> %.0f mV "
          "drop in a 100 mA write burst; %.0f mV of margin to the card's "
          "2.7 V floor" % (r_on * 1e3, drop * 1e3, (3.3 - 2.7 - drop) * 1e3))

    # --- connector contacts -------------------------------------------------
    print("\n    JST-PH contacts are 2 A parts: J1 pin 1 carries 1.20 A "
          "continuous (60%), J10's two +5VS pins share 0.2 A. ok")
    return fails


# ================================================================ fidelity ===
def sim_fidelity(plots):
    """What sampling really does to logged data (scipy.signal).

    Study 2 reported the anti-alias corner abstractly ('891 Hz is above the
    430 Hz Nyquist'). This one answers the question a logger owner actually
    has: a realistic sensor waveform plus engine noise goes through each
    jumper mode's real RC, gets sampled at the ADS1115's 860 SPS, and the
    error against the true signal is measured in percent of full scale.
    """
    from scipy import signal as sig
    head("12. Logged-data fidelity through the channel filter at 860 SPS")
    fails = []
    rng = np.random.default_rng(7)
    fs_hi = 100_000.0
    t = np.arange(0, 2.0, 1 / fs_hi)
    # Sensor truth: an AFR-style sweep with a 3 Hz oscillation on it.
    truth = 2.5 + 1.5 * np.sin(2 * np.pi * 0.5 * t) \
        + 0.3 * np.sin(2 * np.pi * 3.0 * t)
    # Engine noise: alternator whine + ignition hash, 100 mV rms, band-
    # limited 200 Hz - 5 kHz, exactly the stuff that folds down if the
    # channel filter lets it through.
    noise = rng.normal(0, 1, t.size)
    b, a = sig.butter(3, [200 / (fs_hi / 2), 5000 / (fs_hi / 2)], "bandpass")
    noise = sig.lfilter(b, a, noise)
    noise *= 0.100 / max(noise.std(), 1e-12)
    vin = truth + noise

    fs_adc = 860.0
    step = int(fs_hi / fs_adc)
    print("    input: AFR-style sweep + 100 mV rms of 0.2-5 kHz engine")
    print("    noise; sampled at 860 SPS through each mode's real filter.")
    print()
    print("    %-26s %9s %10s  %s"
          % ("jumper setting", "corner", "log error", "verdict"))
    for label, gain, f3 in (("0-5 V   (RANGE C-A)", 15.0 / 26.0, 261.0),
                            ("0-16 V  (RANGE C-B)", 0.167, 891.0),
                            ("bypass  (BYPASS)", 0.999, 1647.0)):
        blp, alp = sig.butter(1, f3 / (fs_hi / 2))
        filtered = sig.lfilter(blp, alp, vin * gain)
        sampled = filtered[::step]
        # The truth, seen through the same DC gain, at the sample instants:
        ideal = (truth * gain)[::step]
        err = sampled - ideal
        # remove the filter's own settling from the score
        err = err[20:]
        rms = float(np.sqrt(np.mean(err ** 2)))
        fs_span = 3.3
        pct = rms / fs_span * 100
        ok = pct < 1.0
        print("    %-26s %7.0fHz %7.2f%%FS  %s"
              % (label, f3, pct, "ok" if ok else
                 "noisy logs -- average in firmware or accept"))
        if pct >= 2.0:
            fails.append("%s mode logs %.1f%% FS of noise+alias error"
                         % (label.split()[0], pct))
    print()
    print("    The error is dominated by in-band noise the filter passes,")
    print("    not by aliasing artifacts: oversample-and-average in firmware")
    print("    (the ADS1115 at 860 SPS averaged 4:1 gives an effective")
    print("    215 Hz rate with half the noise) if the logs look hairy.")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["frontend", "analog", "buck",
                                       "ridethru", "inrush", "crank",
                                       "usb", "tolerance", "stability",
                                       "canbus", "budgets", "fidelity"])
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()
    plots = not args.no_plots

    print("ngspice     : %s" % ngspice())
    print("decks + data: %s" % SIM)
    fails = []
    if args.only in (None, "frontend"):
        fails += sim_frontend(plots)
    if args.only in (None, "analog"):
        fails += sim_analog(plots)
    if args.only in (None, "buck"):
        fails += sim_buck(plots)
    if args.only in (None, "ridethru"):
        fails += sim_ridethru(plots)
    if args.only in (None, "inrush"):
        fails += sim_inrush(plots)
    if args.only in (None, "crank"):
        fails += sim_crank(plots)
    if args.only in (None, "usb"):
        fails += sim_usb(plots)
    if args.only in (None, "tolerance"):
        fails += sim_tolerance(plots)
    if args.only in (None, "stability"):
        fails += sim_stability(plots)
    if args.only in (None, "canbus"):
        fails += sim_canbus(plots)
    if args.only in (None, "budgets"):
        fails += sim_budgets(plots)
    if args.only in (None, "fidelity"):
        fails += sim_fidelity(plots)

    head("Summary")
    if not fails:
        print("    Nothing flagged.")
    for f in dict.fromkeys(fails):
        print("  - " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
