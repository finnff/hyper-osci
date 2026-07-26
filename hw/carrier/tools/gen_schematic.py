#!/usr/bin/env python3
"""Emit carrier.kicad_sch from design.py — as a properly-drawn wiring diagram.

Style (v2, 2026-07-26, replaces the box-and-label netlist dump): real symbol
graphics (IEC resistors, Schottky diodes, drawn P-FET with body diode, PNP,
TL431, pot, switches), per-connector symbols with named pins, hand-placed
functional blocks, and the load-sharing power path drawn wire-by-wire.
Connectivity STILL lives entirely in design.py — every wire and label here is
checked against it at generation time (a wire whose endpoints disagree with
design.py raises), and the external gates re-verify what KiCad actually parsed:

  python3 tools/gen_schematic.py
  kicad-cli sch export netlist -o /tmp/carrier.net carrier.kicad_sch
  python3 tools/check_netlist.py /tmp/carrier.net
  kicad-cli sch erc carrier.kicad_sch     # footprint-lib warnings are expected
  kicad-cli pcb drc --schematic-parity carrier.kicad_pcb   # after gen_board

Layout conventions:
  - everything sits on the 1.27 mm grid; coordinates below are in GRID UNITS
    (u = 1.27 mm), sheet Y grows downward.
  - global labels carry every net name (local labels would net-name as "/NAME"
    on the root sheet, which check_netlist.py would reject). Nets that live
    inside one drawn cluster get a single label — the matching ERC warning
    (single_global_label) is expected and waived in carrier.kicad_pro.
  - GND / 3V3 appear as power symbols in discrete clusters, and as global
    labels on connector pin rows; both name the same net.
  - dashed boxes mark socketed modules and functional blocks (cosmetic only).
"""
import os, sys, uuid, math
from collections import Counter
sys.path.insert(0, os.path.dirname(__file__))
from design import COMPONENTS, norm, footprint_of

NS = uuid.UUID("bfa2dcb2-90d5-4c42-9f4d-6f2ac2f0b001")
ROOT = str(uuid.uuid5(NS, "root-sheet"))

def uid(*key):
    return str(uuid.uuid5(NS, ":".join(str(k) for k in key)))

G = 1.27
def g(n):                       # grid units -> mm
    return round(n * G, 3)

def fmt(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"

# ---------------------------------------------------------------------------
# symbol-space graphics helpers (mm, Y up)
# ---------------------------------------------------------------------------
def SL(pts, w=0.254, fill="none", typ="default"):
    p = " ".join(f"(xy {fmt(x)} {fmt(y)})" for x, y in pts)
    return (f'(polyline (pts {p}) (stroke (width {w}) (type {typ}))'
            f' (fill (type {fill})))')

def SC(cx, cy, r, w=0.254, fill="none"):
    return (f'(circle (center {fmt(cx)} {fmt(cy)}) (radius {fmt(r)})'
            f' (stroke (width {w}) (type default)) (fill (type {fill})))')

def SR(x1, y1, x2, y2, w=0.254, fill="background"):
    return (f'(rectangle (start {fmt(x1)} {fmt(y1)}) (end {fmt(x2)} {fmt(y2)})'
            f' (stroke (width {w}) (type default)) (fill (type {fill})))')

def STXT(txt, x, y, size=0.8):
    return (f'(text "{txt}" (at {fmt(x)} {fmt(y)} 0)'
            f' (effects (font (size {size} {size}))))')

def arrowhead(p_from, p_to, frac, half_w=0.45, length=1.0):
    """Filled triangle on segment p_from->p_to, tip pointing toward p_to."""
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    tip = (p_from[0] + dx * frac, p_from[1] + dy * frac)
    base = (tip[0] - ux * length, tip[1] - uy * length)
    px, py = -uy * half_w, ux * half_w
    return SL([tip, (base[0] + px, base[1] + py), (base[0] - px, base[1] - py),
               tip], w=0.127, fill="outline")

# ---------------------------------------------------------------------------
# symbol library
#   pins: (number, name, x_u, y_u, angle, length_u [, etype [, hide]])
#   pin "at" is the connection point; the pin extends toward the body
# ---------------------------------------------------------------------------
SYMS = {}

def defsym(name, pins, gfx, hide_nums=True, hide_names=True,
           name_size=0.9, num_size=0.9, power=False):
    SYMS[name] = dict(pins=pins, gfx=gfx, hide_nums=hide_nums,
                      hide_names=hide_names, name_size=name_size,
                      num_size=num_size, power=power)

# --- passives ---
defsym("R", [("1", "~", 0, 4, 270, 2), ("2", "~", 0, -4, 90, 2)],
       [SR(-1.016, -2.54, 1.016, 2.54)])

_cplates = [SL([(-1.6, -0.762), (1.6, -0.762)], w=0.4),
            SL([(-1.6, 0.762), (1.6, 0.762)], w=0.4)]
defsym("C", [("1", "~", 0, 3, 270, 2.4), ("2", "~", 0, -3, 90, 2.4)], _cplates)
defsym("CP", [("1", "+", 0, 3, 270, 2.4), ("2", "-", 0, -3, 90, 2.4)],
       _cplates + [SL([(-2.6, 1.5), (-1.8, 1.5)], w=0.2),
                   SL([(-2.2, 1.1), (-2.2, 1.9)], w=0.2)])

# --- diodes: all fitted parts are Schottky (BAT85 / SS34), draw the S-bar ---
_dtri = SL([(-1.27, 1.27), (-1.27, -1.27), (1.27, 0), (-1.27, 1.27)],
           w=0.2, fill="outline")
defsym("D", [("1", "K", 4, 0, 180, 3), ("2", "A", -4, 0, 0, 3)],
       [_dtri,
        SL([(1.27, 1.27), (1.27, -1.27)], w=0.35),
        SL([(1.27, 1.27), (0.635, 1.27), (0.635, 0.889)], w=0.35),
        SL([(1.27, -1.27), (1.905, -1.27), (1.905, -0.889)], w=0.35)])

defsym("LED", [("1", "K", 4, 0, 180, 3), ("2", "A", -4, 0, 0, 3)],
       [_dtri,
        SL([(1.27, 1.27), (1.27, -1.27)], w=0.35),
        SL([(-0.3, 1.5), (0.7, 2.5)], w=0.15),
        arrowhead((-0.3, 1.5), (0.7, 2.5), 1.0, 0.3, 0.7),
        SL([(0.7, 1.1), (1.7, 2.1)], w=0.15),
        arrowhead((0.7, 1.1), (1.7, 2.1), 1.0, 0.3, 0.7)])

# --- P-MOSFET, drawn horizontally: D left, S right, G below; body diode on
#     top with A at the drain side (conducts D->S: the state-1 bootstrap) ---
defsym("QPMOS", [("1", "G", 0, -6, 90, 2), ("2", "S", 6, 0, 180, 2),
                 ("3", "D", -6, 0, 0, 2)],
       [SL([(-5.08, 0), (-2.286, 0)]),
        SL([(5.08, 0), (2.286, 0)]),
        SL([(-2.286, 0), (-0.762, 0)], w=0.4),
        SL([(-0.508, 0), (0.508, 0)], w=0.4),
        SL([(0.762, 0), (2.286, 0)], w=0.4),
        SL([(-1.778, -0.889), (1.778, -0.889)], w=0.4),
        SL([(0, -5.08), (0, -0.889)]),
        SL([(-3.81, 0), (-3.81, 2.54), (-0.889, 2.54)]),
        SL([(-0.889, 3.302), (-0.889, 1.778), (0.889, 2.54), (-0.889, 3.302)],
           w=0.15, fill="outline"),
        SL([(0.889, 3.302), (0.889, 1.778)], w=0.3),
        SL([(0.889, 2.54), (3.81, 2.54), (3.81, 0)]),
        STXT("P-ch", 3.3, -1.6, 0.8)],
       hide_nums=False, hide_names=False)

# --- PNP, emitter up (Q2's emitter sits on the VSW rail): B left, E top,
#     C bottom; arrow on the emitter leg points INTO the base = PNP ---
defsym("QPNP", [("1", "C", 2, -5, 90, 2.6), ("2", "B", -6, 0, 0, 2),
                ("3", "E", 2, 5, 270, 2.6)],
       [SL([(-5.08, 0), (-0.254, 0)]),
        SL([(-0.254, 2.032), (-0.254, -2.032)], w=0.5),
        SL([(-0.254, 0.889), (2.54, 3.048)]),
        SL([(-0.254, -0.889), (2.54, -3.048)]),
        arrowhead((2.54, 3.048), (-0.254, 0.889), 0.62),
        SC(0.762, 0, 2.794)],
       hide_nums=False, hide_names=False)

# --- TL431 shunt reference: K top, A bottom, REF left tapping the bar ---
defsym("TL431", [("1", "REF", -4, 0, 0, 2), ("2", "A", 0, -4, 90, 3),
                 ("3", "K", 0, 4, 270, 3)],
       [SL([(-1.905, 1.27), (1.905, 1.27)], w=0.4),
        SL([(-1.905, -1.27), (1.905, -1.27), (0, 1.27), (-1.905, -1.27)],
           w=0.2, fill="outline"),
        SL([(-2.54, 0), (-2.54, 1.27), (-1.905, 1.27)]),
        SC(0, 0, 3.0)],
       hide_nums=False, hide_names=True)

# --- switches ---
defsym("SW_SPDT", [("1", "A", -4, 2, 0, 2), ("2", "COM", 4, 0, 180, 2),
                   ("3", "B", -4, -2, 0, 2)],
       [SC(-2.286, 2.54, 0.254, fill="outline"),
        SC(-2.286, -2.54, 0.254, fill="outline"),
        SC(2.286, 0, 0.254, fill="outline"),
        SL([(2.032, 0.127), (-2.032, 2.286)], w=0.3)])

defsym("SW_PUSH", [("1", "~", -4, 0, 0, 2), ("2", "~", 4, 0, 180, 2)],
       [SC(-2.286, 0, 0.254, fill="outline"),
        SC(2.286, 0, 0.254, fill="outline"),
        SL([(-2.54, 1.27), (2.54, 1.27)], w=0.3),
        SL([(0, 1.27), (0, 2.54)], w=0.3)])

# --- potentiometer, 5-pin (RV097NS "mono with switch"): CW top, CCW bottom,
#     wiper right.  Pins 4/5 exit left and are an SPST that makes at one end of
#     rotation -- NOT bracket lugs (that was wrong until 2026-07-27; the part
#     has none).  Drawn DETACHED from the resistor body, because it is: the
#     switch shares only the package, no copper.  See design.py on why both
#     ends sit on GND. ---
defsym("POT5", [("1", "CCW", 0, -4, 90, 2), ("2", "W", 4, 0, 180, 2),
                ("3", "CW", 0, 4, 270, 2), ("4", "S1", -4, 1, 0, 2),
                ("5", "S2", -4, -1, 0, 2)],
       [SR(-1.016, -2.54, 1.016, 2.54),
        SL([(2.54, 0), (1.7, 0)]),
        arrowhead((2.54, 0), (1.016, 0), 1.0, 0.4, 0.9),
        SL([(-2.0, 0.7), (-2.0, 1.3)]),      # S1 contact
        SL([(-2.0, -1.3), (-2.0, -0.7)]),    # S2 contact
        SL([(-2.0, -1.0), (-1.35, 1.35)])],  # blade, hinged on S2
       hide_nums=False, hide_names=True, num_size=0.7)

# --- solder jumper (open) ---
defsym("JP2", [("1", "~", -3, 0, 0, 1), ("2", "~", 3, 0, 180, 1)],
       [SL([(-2.286, 0.889), (-0.254, 0.889), (-0.254, -0.889),
            (-2.286, -0.889), (-2.286, 0.889)], w=0.15, fill="outline"),
        SL([(2.286, 0.889), (0.254, 0.889), (0.254, -0.889),
            (2.286, -0.889), (2.286, 0.889)], w=0.15, fill="outline")])

# --- testpoint / mounting hole ---
defsym("TPAD", [("1", "~", 0, -2, 90, 1.4)],
       [SC(0, 0, 0.75), SC(0, 0, 0.3, fill="outline")])
defsym("HOLE", [("1", "~", 0, -3, 90, 2.1)],
       [SC(0, 0, 1.1),
        SL([(-0.7, -0.7), (0.7, 0.7)], w=0.15),
        SL([(-0.7, 0.7), (0.7, -0.7)], w=0.15)])

# --- power symbols ---
defsym("GND", [("1", "GND", 0, 0, 270, 0, "power_in", True)],
       [SL([(0, 0), (0, -1.27)]),
        SL([(-1.27, -1.27), (1.27, -1.27)], w=0.3),
        SL([(-0.762, -1.778), (0.762, -1.778)], w=0.3),
        SL([(-0.254, -2.286), (0.254, -2.286)], w=0.3)], power=True)
defsym("P3V3", [("1", "3V3", 0, 0, 90, 0, "power_in", True)],
       [SL([(0, 0), (0, 1.27)]),
        SL([(-1.016, 1.27), (1.016, 1.27)], w=0.4)], power=True)
defsym("PWRFLAG", [("1", "pwr", 0, 0, 90, 0, "power_out", True)],
       [SL([(0, 0), (0, 0.762)]),
        SL([(0, 0.762), (-1.016, 1.27), (0, 1.778), (1.016, 1.27), (0, 0.762)],
           w=0.2, fill="outline")], power=True)

# --- connectors: one symbol per instance so pins carry real names ---
def defconn(ref, names, side):
    n = len(names)
    ys = [(n - 1) - 2 * i for i in range(n)]
    if side == "left":
        px, ang, bx1, bx2 = -6, 0, -4, 8
    else:
        px, ang, bx1, bx2 = 6, 180, -8, 4
    pins = [(str(i + 1), nm, px, y, ang, 2)
            for i, (nm, y) in enumerate(zip(names, ys))]
    gfx = [SR(g(bx1), g(-(n - 1) - 1.5), g(bx2), g((n - 1) + 1.5))]
    for (_, _, _, y, _, _) in pins:
        sx = g(bx1) if side == "left" else g(bx2)
        gfx.append(SL([(sx, g(y)),
                       (sx + (0.6 if side == "left" else -0.6), g(y))],
                      w=0.15))
    defsym("X_" + ref, pins, gfx, hide_nums=False, hide_names=False,
           name_size=0.9, num_size=0.8)

defconn("JA1", ["5V", "GND", "3V3", "GPIO4", "GPIO3", "GPIO2", "GPIO1",
                "GPIO0"], "left")
defconn("JB1", ["GPIO5", "GPIO6", "GPIO7", "GPIO8", "GPIO9", "GPIO10",
                "GPIO20", "GPIO21"], "right")
defconn("J2", ["SCK", "BCK", "DIN", "LCK", "GND", "VIN"], "left")
defconn("J3", ["LROUT", "AGND", "ROUT", "AGND", "A3V3", "FMT", "XSMT",
               "DEMP", "FLT"], "right")
defconn("J4", ["VCC", "GND", "OUT"], "left")
defconn("J5", ["OUT-", "B-"], "left")
defconn("J6", ["B+", "OUT+"], "left")
defconn("J9", ["IN+"], "left")
defconn("J7", ["TX", "GND"], "left")
defconn("J8", ["+", "-"], "right")
defconn("X1", ["SIG", "GND"], "left")
defconn("Y1", ["SIG", "GND"], "left")

def sym_for(ref):
    sym = norm(COMPONENTS[ref])[1]
    if sym.startswith("CONN") or sym == "PAD1":
        return "X_" + ref
    return sym

# ---------------------------------------------------------------------------
# lib symbol emission
# ---------------------------------------------------------------------------
HFONT = "(effects (font (size 1.27 1.27)) (hide yes))"

def lib_symbol(name):
    s = SYMS[name]
    head = f'    (symbol "HY:{name}"' + (" (power)" if s["power"] else "")
    out = [head]
    if s["hide_nums"]:
        out.append('      (pin_numbers hide)')
    out.append('      (pin_names (offset 0.508)'
               + (' hide' if s["hide_names"] else '') + ')')
    out.append('      (exclude_from_sim no) (in_bom yes) (on_board yes)')
    out.append(f'      (property "Reference" "U" (at 0 0 0) {HFONT})')
    out.append(f'      (property "Value" "{name}" (at 0 0 0) {HFONT})')
    out.append(f'      (property "Footprint" "" (at 0 0 0) {HFONT})')
    out.append(f'      (property "Datasheet" "" (at 0 0 0) {HFONT})')
    out.append(f'      (symbol "{name}_0_1"')
    for gline in s["gfx"]:
        out.append('        ' + gline)
    out.append('      )')
    out.append(f'      (symbol "{name}_1_1"')
    for p in s["pins"]:
        num, nm, xu, yu, ang, lenu = p[:6]
        etype = p[6] if len(p) > 6 else "passive"
        hide = p[7] if len(p) > 7 else False
        ns_, zs_ = s["name_size"], s["num_size"]
        out.append(
            f'        (pin {etype} line (at {fmt(g(xu))} {fmt(g(yu))} {ang})'
            f' (length {fmt(g(lenu))}){" hide" if hide else ""}'
            f' (name "{nm}" (effects (font (size {ns_} {ns_}))))'
            f' (number "{num}" (effects (font (size {zs_} {zs_})))))')
    out.append('      )')
    out.append('    )')
    return "\n".join(out)

# ---------------------------------------------------------------------------
# placement + wiring model (grid units, sheet Y down)
# ---------------------------------------------------------------------------
PLACED = {}          # ref -> (xu, yu, rot)
PWR = []             # (kind, xu, yu)
WIRES = []           # (net, [(xu,yu), ...], width_mm)
LABELS = []          # (net, xu, yu, rot)
TEXTS = []           # (txt, xu, yu, size, bold)
BOXES = []           # (x1,y1,x2,y2) dashed
PROPS = {}           # ref -> dict(ref=..., val=..., hide=bool)

def place(ref, xu, yu, rot=0, refpos=None, valpos=None, hideval=False):
    assert ref in COMPONENTS, ref
    assert ref not in PLACED, f"{ref} placed twice"
    PLACED[ref] = (xu, yu, rot)
    PROPS[ref] = dict(ref=refpos, val=valpos, hide=hideval)

def pinpos(ref, num):
    x, y, rot = PLACED[ref]
    for p in SYMS[sym_for(ref)]["pins"]:
        if p[0] == str(num):
            px, py = p[2], p[3]
            return {0:   (x + px, y - py),
                    90:  (x - py, y - px),
                    180: (x - px, y + py),
                    270: (x + py, y + px)}[rot]
    raise KeyError((ref, num))

def netof(ref, num):
    return norm(COMPONENTS[ref])[3][str(num)]

def W(net, *pts, w=0.1524):
    # NB: explicit width — kicad-cli 10's SVG plotter drops (width 0) wires
    # instead of substituting the schematic default
    res = []
    for p in pts:
        if isinstance(p[0], str):
            got = netof(p[0], p[1])
            assert got == net, f"wire {net}: {p} is on net {got}"
            res.append(pinpos(p[0], p[1]))
        else:
            res.append(p)
    for a, b in zip(res, res[1:]):
        assert a != b, f"zero-length wire in {net}: {a}"
        assert a[0] == b[0] or a[1] == b[1], f"diagonal wire {net}: {a}->{b}"
    WIRES.append((net, res, w))

def L(net, xu, yu, rot=0):
    LABELS.append((net, xu, yu, rot))

def LP(ref, num, rot):
    net = netof(ref, num)
    assert net, (ref, num)
    x, y = pinpos(ref, num)
    LABELS.append((net, x, y, rot))

def PS(kind, xu, yu):
    PWR.append((kind, xu, yu))

def T(txt, xu, yu, size=1.27, bold=False):
    TEXTS.append((txt, xu, yu, size, bold))

def BOX(x1, y1, x2, y2):
    BOXES.append((x1, y1, x2, y2))

# ===========================================================================
# BLOCK A — battery + charger (top-left)
# ===========================================================================
BOX(9, 26, 84, 100)
T("BATTERY + CHARGER", 10, 24, 1.6, True)

place("J8", 18, 38, refpos=(12, 33, "left"), hideval=True)
place("J5", 44, 34, refpos=(40, 30.5, "left"), hideval=True)
place("J6", 44, 42, refpos=(40, 38.5, "left"), hideval=True)
place("J9", 44, 50, refpos=(46, 47.5, "left"), hideval=True)
BOX(39, 28, 54, 55)
T("TP4056 USB-C charger module (socketed)", 39, 26.9, 1.0)
T("CSM4056T + DW01 + FS8205 (protected)\ncharge on its own USB-C only —\nRULE 2: charge with SW1 OFF", 40, 59, 1.0)
T("EEMB LP103454 2000 mAh LiPo\n(cell off-board — polarity silk!)", 12, 49, 1.0)
T("!! B− must NOT join GND\n(protection is low-side)", 62, 29.5, 1.0)

# B+ : JST + -> B+ pad, with the sense divider hanging off the run;
# B- crosses B+ once (pad order vs JST order — deliberate crossover)
W("BAT_PLUS", ("J6", 1), (28, 41), (28, 37), ("J8", 1))
L("BAT_PLUS", 34, 46.5, 180)
W("BAT_MINUS", ("J5", 2), (26, 35), (26, 39), ("J8", 2))
W("BAT_MINUS", (33, 35), (33, 33))
L("BAT_MINUS", 33, 33, 180)
# OUT- = system ground; PWR_FLAG anchors ERC here
W("GND", ("J5", 1), (36, 33), (36, 29), (58, 29), (58, 38))
PS("GND", 58, 38)
W("GND", (58, 36), (60, 36))
PS("PWRFLAG", 60, 36)
# OUT+ / IN+ head for the power path
W("VBAT_OUT", ("J6", 2), (36, 43), (36, 46), (62, 46))
L("VBAT_OUT", 62, 46)
W("VBUS_CHG", ("J9", 1), (36, 50), (36, 53), (62, 53))
L("VBUS_CHG", 62, 53)

# sense divider
place("R1", 34, 52, refpos=(32, 51, "right"), valpos=(32, 53, "right"))
place("R2", 34, 62, refpos=(32, 61, "right"), valpos=(32, 63, "right"))
place("C3", 38, 65)
place("TP5", 30, 56, refpos=(25, 56, "right"), hideval=True)
W("BAT_PLUS", (34, 41), ("R1", 1))
W("VBAT_SENSE", ("R1", 2), ("R2", 1))
W("VBAT_SENSE", (34, 58), (38, 58), ("C3", 1))
W("VBAT_SENSE", ("TP5", 1), (34, 58))
L("VBAT_SENSE", 30, 58, 180)
W("GND", ("R2", 2), (34, 68))
PS("GND", 34, 68)
W("GND", ("C3", 2), (38, 70))
PS("GND", 38, 70)
T("always-on: ~21 µA; /2 → GPIO1", 44, 75, 1.0)

# ===========================================================================
# BLOCK B — load-sharing power path (top-middle)
# ===========================================================================
BOX(88, 16, 236, 96)
T("LOAD-SHARING POWER PATH — carrier only, not on the breadboard (pcb.md §4)",
  89, 14.5, 1.6, True)

place("Q1", 108, 36, refpos=(110, 29.5, "left"), valpos=(110, 31.5, "left"))
place("JP1", 107, 24, refpos=(100, 21, "left"), valpos=(110.5, 21, "left"))
place("SW1", 192, 38, refpos=(188, 44.5, "left"), valpos=(186, 46.5, "left"))
place("C1", 208, 39, refpos=(209.5, 37.5, "left"), valpos=(209.5, 39.5, "left"))
place("C2", 216, 39, refpos=(217.5, 37.5, "left"), valpos=(217.5, 39.5, "left"))
place("D2", 103, 54, refpos=(99, 50.5, "left"), valpos=(106, 51, "left"))
place("D1", 102, 62, refpos=(98, 65.5, "left"), valpos=(103.5, 65.5, "left"))
place("D3", 218, 76, rot=180, refpos=(211, 73, "left"),
      valpos=(211, 79.5, "left"))
place("R6", 108, 70, refpos=(102, 68, "left"), valpos=(102, 70.5, "left"))
place("R7", 136, 46, refpos=(133, 45, "right"), valpos=(133, 47, "right"))
place("R8", 136, 58, refpos=(133, 57, "right"), valpos=(133, 59, "right"))
place("U1", 154, 52, refpos=(150, 62, "right"), valpos=(158, 60, "left"))
place("R9", 166, 48, rot=90, refpos=(163, 45, "left"), valpos=(168, 51, "left"))
place("R12", 176, 42, rot=180, refpos=(178, 39, "left"), valpos=(178, 45, "left"))
place("Q2", 182, 62, refpos=(186, 63.5, "left"), valpos=(186, 65.5, "left"))
place("TP1", 92, 48, refpos=(88.5, 45, "left"), hideval=True)
place("TP2", 146, 34, refpos=(142.5, 30.5, "left"), hideval=True)
place("TP3", 114, 56, refpos=(116, 57.5, "left"), hideval=True)

# VBAT_OUT -> Q1 drain, JP1 across it
W("VBAT_OUT", (90, 36), (98, 36), ("Q1", 3), w=0.3556)
L("VBAT_OUT", 90, 36, 180)
W("VBAT_OUT", (98, 36), (98, 24), ("JP1", 1))
# VSW rail
W("VSW", ("Q1", 2), (118, 36), (126, 36), (136, 36), ("TP2", 1), (176, 36),
  (184, 36), ("SW1", 1), w=0.3556)
W("VSW", ("JP1", 2), (118, 24), (118, 36))
L("VSW", 118, 30)
# VBUS_CHG spine feeding D2 (to VSW) and D1 (to GATE)
W("VBUS_CHG", (90, 50), ("TP1", 1), (96, 50), (96, 54), ("D2", 2))
L("VBUS_CHG", 90, 50, 180)
W("VBUS_CHG", (96, 54), (96, 62), ("D1", 2))
W("VSW", ("D2", 1), (126, 54), (126, 36))
# GATE spine
W("GATE", ("Q1", 1), (108, 44), (108, 58), (108, 62), (108, 64), ("R6", 1))
W("GATE", (108, 44), (112, 44))
L("GATE", 112, 44)
W("GATE", ("D1", 1), (108, 62))
W("GATE", ("TP3", 1), (108, 58))
W("GND", ("R6", 2), (108, 76))
PS("GND", 108, 76)
# threshold divider + TL431 + Q2
W("VSW", (136, 36), ("R7", 1))
W("VSW_SENSE", ("R7", 2), (136, 52), ("R8", 1))
W("VSW_SENSE", (136, 52), ("U1", 1))
L("VSW_SENSE", 144, 52, 270)
W("GND", ("R8", 2), (136, 64))
PS("GND", 136, 64)
W("GND", ("U1", 2), (154, 58))
PS("GND", 154, 58)
W("TL431_K", ("U1", 3), ("R9", 1))
L("TL431_K", 158, 48, 270)
W("Q2_B", ("R9", 2), (176, 48), ("Q2", 2))
W("Q2_B", (176, 48), ("R12", 1))
L("Q2_B", 176, 54)
W("VSW", ("R12", 2), (176, 36))
W("VSW", ("Q2", 3), (184, 36))
# Q2 collector joins the gate; D3 (DNP) from VLOAD
W("GATE", ("Q2", 1), (184, 76), (112, 76), (112, 64), (108, 64))
W("GATE", ("D3", 1), (184, 76))
W("VLOAD", ("D3", 2), (226, 76), (226, 36))
# VLOAD after the switch
W("VLOAD", ("SW1", 2), (200, 38), (200, 36), (208, 36), (216, 36), (226, 36),
  (232, 36), w=0.3556)
L("VLOAD", 232, 36)
W("GND", ("C1", 2), (208, 44))
PS("GND", 208, 44)
W("GND", ("C2", 2), (216, 44))
PS("GND", 216, 44)

T("JP1 — plan-A escape hatch: bridge, omit Q1 Q2 U1 D1 D2 + R7 R8", 122, 21, 1.0)
T("body diode D→S:\nVSW bootstraps in\nbattery mode (state 1)", 89, 28, 1.0)
T("trip ≈ 4.56 V typ (4.46–4.66 V @ 25 °C)\n8.2k/10k 1 % + TL431A — populate-time\nchoice, see pcb.md §4.2 divider table", 146, 65, 1.0)
T("SW1 downstream of Q1:\nVSW live in BOTH positions (state 6)", 186, 50, 1.0)
T("WiFi burst reservoir", 206, 53, 1.0)
T("D3: DNP naive-OR experiment — do not fit (§4.1)", 130, 79.5, 1.0)
T("!! state 4 (SuperMini USB + SW1 ON + battery): the trip is unreachable from battery mode — Q1 stays on and the\nport back-feeds the cell. RULE 1 stands until pcb.md §4.4 passes on an assembled carrier: SW1 OFF before ANY USB.",
  89, 88, 1.1)

# ===========================================================================
# BLOCK C — ESP32-C3 SuperMini (top-right)
# ===========================================================================
BOX(240, 26, 320, 72)
T("ESP32-C3 SUPERMINI (socketed, antenna → west edge)", 241, 24, 1.6, True)

place("JA1", 268, 44, refpos=(264, 33, "left"), valpos=(258, 55.5, "left"))
place("JB1", 296, 44, refpos=(298, 33, "left"), valpos=(292, 55.5, "left"))
for pnum in range(1, 9):
    LP("JA1", pnum, 180)
for pnum in (1, 2, 3, 6, 7, 8):
    LP("JB1", pnum, 0)
T("GPIO8/9: onboard, NC", 303, 44, 0.9)
T("module USB-C = flash / debug / console ONLY.\n!! RULE 1: SW1 OFF before plugging — VBUS ties\nstraight to the 5V pin (meter-confirmed).\nclone silk may print RX/TX = GPIO20/GPIO21.\n3V3 pin = module LDO out — powers everything else.", 241, 61, 1.0)

# ===========================================================================
# BLOCK D — DAC + scope out (bottom-middle)
# ===========================================================================
BOX(148, 114, 320, 148)
T("I2S DAC + SCOPE OUT", 149, 112.5, 1.6, True)

place("J2", 178, 128, refpos=(174, 119.5, "left"), valpos=(172.5, 137.5, "left"))
place("J3", 228, 128, refpos=(230, 117.5, "left"), valpos=(222, 139.5, "left"))
BOX(171.5, 118, 234.5, 140)
T("GY-PCM5102A (socketed, purple)", 171.5, 116.8, 1.0)
for pnum in range(1, 7):
    LP("J2", pnum, 180)
T("SCK → GND on the carrier:\nDAC clocks itself by PLL from BCK (§3.2)", 149, 143, 1.0)
T("J3 FMT/XSMT/DEMP/FLT are set by the module's own back bridges:\nFLT=L DEMP=L XSMT=H FMT=L — solder BEFORE first power-up;\nXSMT open = DAC hard-muted (#1 no-output cause — wiring.md §4)", 186, 144.8, 0.9)

place("R10", 248, 120, rot=90)
place("R11", 248, 132, rot=90)
place("X1", 264, 121, refpos=(266, 116.5, "left"), hideval=True)
place("Y1", 264, 133, refpos=(266, 137.5, "left"), hideval=True)
W("DAC_L", ("J3", 1), (240, 120), ("R10", 1))
W("DAC_L", (240, 120), (240, 117))
L("DAC_L", 240, 117, 90)
W("SCOPE_X", ("R10", 2), ("X1", 1))
W("SCOPE_X", (254, 120), (254, 117))
L("SCOPE_X", 254, 117, 90)
W("DAC_R", ("J3", 3), (240, 124), (240, 132), ("R11", 1))
L("DAC_R", 240, 128)
W("SCOPE_Y", ("R11", 2), ("Y1", 1))
W("SCOPE_Y", (254, 132), (254, 135))
L("SCOPE_Y", 254, 135, 270)
LP("J3", 2, 0)
LP("J3", 4, 0)
W("GND", ("X1", 2), (258, 124))
PS("GND", 258, 124)
W("GND", ("Y1", 2), (258, 136))
PS("GND", 258, 136)
T("→ solder ~50 cm RCA leads: X → scope CH1, Y → CH2\nXY mode, DC-coupled, 1 MΩ (never 50 Ω)\n±3 V full-scale per axis, ground-centred", 274, 124, 1.0)
T("R10/R11: fit 0 Ω if the module's ~470 Ω output R is confirmed in-line (§3.2)\nX/Y ground pads + J3 AGND = analog island, single-neck join near J2 GND (§6.3)", 240, 144, 0.9)

place("C5", 152, 128, refpos=(154, 126.5, "left"), valpos=(154, 128.5, "left"))
PS("P3V3", 152, 125)
W("GND", ("C5", 2), (152, 132))
PS("GND", 152, 132)
T("at J2 VIN", 149, 136, 0.9)

# ===========================================================================
# BLOCK E — mic (right, below DAC)
# ===========================================================================
BOX(266, 152, 320, 178)
T("MIC INPUT", 267, 150.5, 1.6, True)
place("J4", 288, 160, refpos=(284, 154.5, "left"), valpos=(280, 166.5, "left"))
for pnum in (1, 2, 3):
    LP("J4", pnum, 180)
T("→ MAX4466 on ~10 cm 3-wire pigtail (off-board),\ncapsule exits the enclosure, aimed at the PA;\ngain trimmer mid-travel to start", 267, 172, 1.0)
place("C4", 304, 162, refpos=(306, 160.5, "left"), valpos=(306, 162.5, "left"))
PS("P3V3", 304, 159)
W("GND", ("C4", 2), (304, 166))
PS("GND", 304, 166)
T("at mic socket\n(AGND island)", 307, 166, 0.9)

# ===========================================================================
# BLOCK F — controls + LEDs (bottom-left-middle)
# ===========================================================================
BOX(88, 114, 144, 216)
T("CONTROLS + LEDs", 89, 112.5, 1.6, True)

place("RV1", 104, 130, refpos=(93, 126, "left"), valpos=(108, 128, "left"))
PS("P3V3", 104, 126)
W("GND", ("RV1", 4), (98, 129), (98, 136), (104, 136))
W("GND", ("RV1", 5), (98, 131))
W("GND", ("RV1", 1), (104, 136))
PS("GND", 104, 136)
W("POT_WIPER", ("RV1", 2), (112, 130))
L("POT_WIPER", 112, 130)
T("CW = higher cutoff", 110, 135, 0.9)
T("pins 4/5 = end-of-travel SPST, unused:", 90, 138.5, 0.9)
T("both ends on GND (see design.py)", 90, 140.5, 0.9)

place("SW2", 104, 148, refpos=(100, 143.5, "left"), valpos=(108, 143.5, "left"))
LP("SW2", 1, 180)
W("GND", ("SW2", 2), (110, 148), (110, 150))
PS("GND", 110, 150)
T("internal pull-up + 30 ms debounce (config.h)", 90, 154.5, 0.9)

place("R3", 130, 130, refpos=(132, 128.5, "left"), valpos=(132, 130.5, "left"))
PS("P3V3", 130, 126)
W("GPIO2_PU", ("R3", 2), (130, 136))
L("GPIO2_PU", 130, 136, 270)
T("GPIO2 strap:\nhigh at reset,\nnothing else on it", 133, 121, 0.9)

place("J7", 116, 162, refpos=(112, 157.5, "left"), valpos=(112, 167.5, "left"))
LP("J7", 1, 180)
LP("J7", 2, 180)
T("UART0 TX log header", 126, 162, 0.9)

place("R4", 100, 184, rot=90, refpos=(98, 181, "left"), valpos=(97, 187, "left"))
place("D4", 112, 184, refpos=(114, 180.5, "left"), valpos=(121, 184, "left"))
LP("R4", 1, 180)
W("D4_A", ("R4", 2), ("D4", 2))
W("D4_A", (106, 184), (106, 181))
L("D4_A", 106, 181, 90)
W("GND", ("D4", 1), (118, 184), (118, 186))
PS("GND", 118, 186)

place("R5", 100, 198, rot=90, refpos=(98, 195, "left"), valpos=(97, 201, "left"))
place("D5", 112, 198, refpos=(114, 194.5, "left"), valpos=(121, 198, "left"))
LP("R5", 1, 180)
W("D5_A", ("R5", 2), ("D5", 2))
W("D5_A", (106, 198), (106, 201))
L("D5_A", 106, 201, 270)
W("GND", ("D5", 1), (118, 198), (118, 200))
PS("GND", 118, 200)
T("~0.5 mA per LED — dim by design (battery)", 90, 212, 0.9)

# ===========================================================================
# BLOCK G — test + mech (bottom-right)
# ===========================================================================
BOX(266, 182, 318, 198)
T("TEST + MECH", 267, 180.5, 1.6, True)
place("TP4", 272, 186, refpos=(267, 184, "left"), hideval=True)
W("3V3", ("TP4", 1), (272, 190), (276, 190), (280, 190), (282, 190))
W("3V3", (276, 190), (276, 188))
PS("P3V3", 276, 188)
PS("PWRFLAG", 282, 190)
place("H1", 292, 186, hideval=True)
place("H2", 300, 186, hideval=True)
place("H3", 308, 186, hideval=True)
place("H4", 316, 186, refpos=(312, 183.5, "left"), hideval=True)
W("GND", ("H1", 1), (292, 192), (300, 192))
W("GND", ("H2", 1), (300, 192))
W("GND", ("H3", 1), (308, 192), (300, 192))
W("GND", ("H4", 1), (316, 192), (308, 192))
PS("GND", 300, 192)
T("M3 holes — GND ring, fence-stitched (§6.2)", 267, 194.5, 0.9)
T("TP1 VBUS_CHG · TP2 VSW · TP3 GATE · TP5 VBAT_SENSE", 267, 196.8, 0.9)

# ===========================================================================
# BLOCK H — notes (bottom-left)
# ===========================================================================
BOX(9, 114, 84, 216)
T("READ ME — BUILD + SAFETY", 10, 112.5, 1.6, True)
T("GENERATED FILE — drawn by tools/gen_schematic.py from\ntools/design.py (the netlist source of truth). Never edit\nby hand: edit design.py, regenerate, re-run the gates\n(check_netlist.py, ERC, DRC --schematic-parity).", 11, 121, 1.1)
T("RULE 1 — SW1 OFF before plugging ANY USB.\nThe load-share block was meant to retire this rule; the\n2026-07-26 review shows it cannot (state 4, §4.1/§4.3).\nThe rule stays until §4.4 passes on an assembled board.", 11, 139, 1.1)
T("RULE 2 — charge with SW1 OFF (TP4056 cannot\nterminate into a load).", 11, 155, 1.1)
T("RULE 3 — one USB at a time: charger-USB = charging,\nmodule-USB = development, SW1 ON only with no USB.", 11, 160.5, 1.1)
T("SHOW BUILD (plan A, §4.5): bridge JP1 and DO NOT FIT\nQ1, Q2, U1, D1, D2, R7, R8. Never leave D2 fitted on a\nJP1-bridged board — the charger's USB would land on the\ncell raw, upstream of the switch.", 11, 166, 1.1)
T("BREADBOARD DELTA (wiring.md): no load-share block —\nOUT+ → switch → 5V pin direct. VBAT_RAW = BAT_PLUS,\nVBAT_SW ≈ VLOAD. All signal wiring is identical.", 11, 184, 1.1)
T("TO-92 LEAD ORDER (U1, Q2): vendors number the same\npackage opposite ways (TL431 onsemi vs TI; BC557 C-B-E\nvs E-B-C) — check the datasheet of the part actually\nbought (pcb.md §5).", 11, 199, 1.1)

T("HYPEROSCI SLAVE UNIT — CARRIER v1.1 WIRING DIAGRAM", 9, 9.5, 2.5, True)
T("4 units for the show · 70 × 50 mm 2-layer · modules socketed · ESP32-C3 SuperMini + PCM5102A + TP4056 + MAX4466",
  9, 12.7, 1.1)

# ===========================================================================
# integrity checks
# ===========================================================================
missing = set(COMPONENTS) - set(PLACED)
assert not missing, f"not placed: {sorted(missing)}"

touch = set()
for _, pts, _ in WIRES:
    touch.update(pts)
for net, x, y, rot in LABELS:
    touch.add((x, y))
for kind, x, y in PWR:
    touch.add((x, y))
for ref in COMPONENTS:
    for num, net in norm(COMPONENTS[ref])[3].items():
        if net is not None:
            p = pinpos(ref, num)
            assert p in touch, f"pin {ref}.{num} ({net}) at {p} unconnected"

named = {net for net, *_ in LABELS} | {"GND", "3V3"}
allnets = {net for c in COMPONENTS.values() for net in norm(c)[3].values()
           if net}
unnamed = allnets - named
assert not unnamed, f"nets without a name label: {sorted(unnamed)}"
for net, x, y, rot in LABELS:
    assert net in allnets, f"label for unknown net {net}"

# junction dots: from segment-endpoint incidence + pins + interior touches
segs = []
for _, pts, _ in WIRES:
    segs += list(zip(pts, pts[1:]))
endc = Counter()
for a, b in segs:
    endc[a] += 1
    endc[b] += 1
pinpts = Counter()
for ref in COMPONENTS:
    for num, net in norm(COMPONENTS[ref])[3].items():
        if net is not None:
            pinpts[pinpos(ref, num)] += 1
for kind, x, y in PWR:          # power-symbol pins tee into wires too
    pinpts[(x, y)] += 1

def on_interior(p, a, b):
    if a[0] == b[0] == p[0]:
        return min(a[1], b[1]) < p[1] < max(a[1], b[1])
    if a[1] == b[1] == p[1]:
        return min(a[0], b[0]) < p[0] < max(a[0], b[0])
    return False

JUNCTIONS = set()
for p in endc:
    inter = sum(1 for a, b in segs if on_interior(p, a, b))
    if endc[p] + pinpts.get(p, 0) + inter >= 3 or inter >= 1:
        JUNCTIONS.add(p)

# ---------------------------------------------------------------------------
# emission
# ---------------------------------------------------------------------------
out = ['(kicad_sch (version 20231120) (generator "hyperosci")',
       f'  (uuid "{ROOT}")',
       '  (paper "A3")',
       '  (title_block (title "HYPEROSCI carrier v1.1 — wiring diagram")'
       ' (date "2026-07-26") (rev "1.1")'
       ' (comment 1 "generated by tools/gen_schematic.py from tools/design.py'
       ' — do not hand-edit")'
       ' (comment 2 "gates: check_netlist.py, kicad-cli sch erc,'
       ' pcb drc --schematic-parity"))',
       '  (lib_symbols']
used = sorted({sym_for(r) for r in COMPONENTS} | {"GND", "P3V3", "PWRFLAG"})
for s in used:
    out.append(lib_symbol(s))
out.append('  )')

def prop(name, val, x, y, just=None, hide=False, size=1.27, ang=0):
    # property text renders at (symbol rotation + property angle): rotated
    # instances need ang=90 to keep their Reference/Value horizontal
    j = f' (justify {just})' if just else ''
    h = ' (hide yes)' if hide else ''
    v = str(val).replace('"', '\\"')
    return (f'    (property "{name}" "{v}" (at {fmt(x)} {fmt(y)} {ang})'
            f' (effects (font (size {size} {size})){j}{h}))')

for ref in COMPONENTS:
    x, y, rot = PLACED[ref]
    sym = sym_for(ref)
    val, _, _, pinmap, dnp = norm(COMPONENTS[ref])
    fp = footprint_of(ref)
    pp = PROPS[ref]
    sx, sy = g(x), g(y)
    body = [f'  (symbol (lib_id "HY:{sym}") (at {fmt(sx)} {fmt(sy)} {rot})'
            f' (unit 1)',
            f'    (exclude_from_sim no) (in_bom yes) (on_board yes)'
            f' (dnp {"yes" if dnp else "no"})',
            f'    (uuid "{uid("sym", ref)}")']
    pang = 90 if rot in (90, 270) else 0
    if pp["ref"]:
        rx, ry, rj = pp["ref"]
        body.append(prop("Reference", ref, g(rx), g(ry), rj, ang=pang))
    else:
        dx, dy, jst = (2, -1, "left") if rot in (0, 180) else (0, -3, None)
        body.append(prop("Reference", ref, g(x + dx), g(y + dy), jst, ang=pang))
    if pp["hide"]:
        body.append(prop("Value", val, sx, sy, hide=True, ang=pang))
    elif pp["val"]:
        vx, vy, vj = pp["val"]
        body.append(prop("Value", val, g(vx), g(vy), vj, size=1.02, ang=pang))
    else:
        dx, dy, jst = (2, 1, "left") if rot in (0, 180) else (0, 3, None)
        body.append(prop("Value", val, g(x + dx), g(y + dy), jst, size=1.02,
                         ang=pang))
    body.append(prop("Footprint", fp, sx, sy, hide=True))
    body.append(prop("Datasheet", "", sx, sy, hide=True))
    for num in pinmap:
        body.append(f'    (pin "{num}" (uuid "{uid("pin", ref, num)}"))')
    body.append(f'    (instances (project "carrier" (path "/{ROOT}"'
                f' (reference "{ref}") (unit 1))))')
    body.append('  )')
    out.append("\n".join(body))

for i, (kind, x, y) in enumerate(PWR, 1):
    refn = ("#FLG" if kind == "PWRFLAG" else "#PWR") + f"{i:03d}"
    value = {"GND": "GND", "P3V3": "3V3", "PWRFLAG": "PWR_FLAG"}[kind]
    sx, sy = g(x), g(y)
    vy = sy + 3.3 if kind == "GND" else sy - 2.8
    out.append("\n".join([
        f'  (symbol (lib_id "HY:{kind}") (at {fmt(sx)} {fmt(sy)} 0) (unit 1)',
        '    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)',
        f'    (uuid "{uid("pwr", str(i))}")',
        prop("Reference", refn, sx, sy, hide=True),
        prop("Value", value, sx, vy, size=0.8 if kind == "PWRFLAG" else 1.02),
        prop("Footprint", "", sx, sy, hide=True),
        prop("Datasheet", "", sx, sy, hide=True),
        f'    (pin "1" (uuid "{uid("pwrpin", str(i))}"))',
        f'    (instances (project "carrier" (path "/{ROOT}"'
        f' (reference "{refn}") (unit 1))))',
        '  )']))

for wi, (net, pts, wdt) in enumerate(WIRES):
    for si, (a, b) in enumerate(zip(pts, pts[1:])):
        out.append(f'  (wire (pts (xy {fmt(g(a[0]))} {fmt(g(a[1]))})'
                   f' (xy {fmt(g(b[0]))} {fmt(g(b[1]))}))'
                   f' (stroke (width {wdt}) (type default))'
                   f' (uuid "{uid("wire", str(wi), str(si))}"))')
for p in sorted(JUNCTIONS):
    out.append(f'  (junction (at {fmt(g(p[0]))} {fmt(g(p[1]))})'
               f' (diameter 0.9144)'
               f' (color 0 0 0 0) (uuid "{uid("junc", str(p))}"))')

for ref in COMPONENTS:
    for num, net in norm(COMPONENTS[ref])[3].items():
        if net is None:
            p = pinpos(ref, num)
            out.append(f'  (no_connect (at {fmt(g(p[0]))} {fmt(g(p[1]))})'
                       f' (uuid "{uid("nc", ref, num)}"))')

for li, (net, x, y, rot) in enumerate(LABELS):
    just = {0: "left", 90: "left", 180: "right", 270: "right"}[rot]
    out.append(f'  (global_label "{net}" (shape passive)'
               f' (at {fmt(g(x))} {fmt(g(y))} {rot}) (fields_autoplaced yes)'
               f' (effects (font (size 1.27 1.27)) (justify {just}))'
               f' (uuid "{uid("lbl", str(li), net)}"))')

for ti, (txt, x, y, size, bold) in enumerate(TEXTS):
    b = " (bold yes)" if bold else ""
    t = txt.replace('"', '\\"').replace("\n", "\\n")
    out.append(f'  (text "{t}" (exclude_from_sim no)'
               f' (at {fmt(g(x))} {fmt(g(y))} 0)'
               f' (effects (font (size {size} {size}){b}) (justify left))'
               f' (uuid "{uid("txt", str(ti))}"))')
for bi, (x1, y1, x2, y2) in enumerate(BOXES):
    pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    p = " ".join(f"(xy {fmt(g(a))} {fmt(g(b))})" for a, b in pts)
    out.append(f'  (polyline (pts {p}) (stroke (width 0.1524) (type dash))'
               f' (uuid "{uid("box", str(bi))}"))')

out.append('  (sheet_instances (path "/" (page "1")))')
out.append(')')

# ---------------------------------------------------------------------------
# write files (schematic + mirrored symbol lib, as before)
# ---------------------------------------------------------------------------
base = os.path.join(os.path.dirname(__file__), "..")
libout = ['(kicad_symbol_lib (version 20231120) (generator "hyperosci")']
for s in used:
    libout.append(lib_symbol(s).replace(f'"HY:{s}"', f'"{s}"'))
libout.append(')')
with open(os.path.join(base, "HYPEROSCI.kicad_sym"), "w") as f:
    f.write("\n".join(libout) + "\n")
with open(os.path.join(base, "sym-lib-table"), "w") as f:
    f.write('(sym_lib_table\n  (version 7)\n  (lib (name "HY")(type "KiCad")'
            '(uri "${KIPRJMOD}/HYPEROSCI.kicad_sym")(options "")'
            '(descr "HYPEROSCI generated symbols"))\n)\n')

path = os.path.join(base, "carrier.kicad_sch")
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print("wrote", os.path.abspath(path),
      f"({len(COMPONENTS)} components, {len(WIRES)} wire runs,"
      f" {len(JUNCTIONS)} junctions, {len(LABELS)} labels)")
