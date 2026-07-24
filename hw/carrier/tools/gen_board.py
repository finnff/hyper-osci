#!/usr/bin/env python3
"""Build carrier.kicad_pcb from design.py + the placement table below.

Floorplan per pcb.md §6.1 with one deliberate change (documented in
layout-notes.md): the TP4056 sits SOUTH of the DAC module, because
SuperMini (~24mm) + PCM5102A (~33mm) + TP4056 (~28mm) exceed 70mm and
cannot be colinear. USB-C still exits the east edge.

Coordinates: mm from the board's top-left corner (x east, y south).
Board: 70 x 50. Run from hw/carrier/:  python3 tools/gen_board.py
"""
import os, sys, uuid
sys.path.insert(0, os.path.dirname(__file__))
import pcbnew
from pcbnew import VECTOR2I, FromMM
from design import COMPONENTS, norm

NS = uuid.UUID("bfa2dcb2-90d5-4c42-9f4d-6f2ac2f0b001")
ROOT = str(uuid.uuid5(NS, "root-sheet"))

def mm(x, y):
    return VECTOR2I(FromMM(x), FromMM(y))

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(BASE, "carrier.kicad_pcb")
SYS = "/usr/share/kicad/footprints"

# Per-part footprint overrides — vertical (standing) axials wherever the
# horizontal 10.16mm span doesn't fit. NOT under modules (9mm standing height).
VERT_R = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical"
FP_OVERRIDE = {r: VERT_R for r in
               ["R1", "R2", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12"]}

# ref: (x, y, rot_degrees)  — anchor is the footprint's native origin.
# Rotation convention (audited): rot90 maps footprint-local (x,y)->(y,-x),
# rot270 the inverse; a PinSocket's pads run +y at rot0.
P = {
    # SuperMini rows: pad1 east (USB end), pads run WEST. Module west edge
    # overhangs board by ~1mm; antenna region x<6 gets a pour keepout.
    "JA1": (20.32,  9.30, 270), "JB1": (20.32, 24.54, 270),
    # DAC: J2 (1x6) column at x=29.21 running south; J3 (1x9) row at y=7.62
    # running west from LROUT(jack end). Finn's provisional corner geometry.
    "J2": (29.21,  7.62, 0), "J3": (52.07, 7.62, 270),
    "J4": (66.04,  9.39, 0),          # mic pigtail, NE analog zone
    # TP4056 (south of DAC): pad column x=45.72, OUT- B- B+ OUT+ going south.
    # NB module pad-row edge offset was never measured — module may sit
    # shifted N/S on these sockets; region 24..41.3 has margin for that.
    "J5": (45.72, 26.67, 0), "J6": (45.72, 31.75, 0),
    "J9": (64.77, 41.15, 0),          # IN+ sense wire pad (doc J6b)
    "J7": (2.54, 29.21, 0),           # debug header, SW corner area
    "J8": (57.68, 42.5, 0),           # LiPo JST-PH: wires exit south edge
    "X1": (57.15, 2.54, 270), "Y1": (33.02, 2.54, 90),
    # power path: Q1 under TP4056 (SMD), diodes under DAC (low), Rs standing
    # in a column at x36.83, TL431/BC557 in the open SW quadrant
    "Q1": (50.29, 27.94, 0),
    "Q2": (22.0, 34.29, 0),
    "U1": (32.6, 31.0, 90),           # rot90: REF@31.0, A@28.46, K@25.92 — beside the R col it senses
    # D1/D3 carry GATE/VBUS_CHG/VLOAD — they live in the power cluster,
    # NOT in the analog island where they'd wall off the §6.3 neck with
    # 7.62mm-pitch pad rows. J5/J6 pads run SOUTH at x45.72 — keep clear.
    "D1": (46.5, 23.0, 0),            # north of J5 col, east of D2 court
    "D2": (43.18, 16.2, 90),          # cathode/VSW pad south
    "D3": (60.96, 30.48, 90),         # DNP; open strip east of Q1/JP1,
                                      # outside the module for easy retrofit
    "R12": (36.83, 22.86, 0), "R9": (36.83, 26.16, 0), "R6": (36.83, 29.46, 0),
    "R7": (36.83, 32.76, 0), "R8": (36.83, 36.06, 0),
    "JP1": (54.61, 27.94, 0),         # beside Q1: it bridges Q1 D->S (VBAT_OUT->VSW)
    "C1": (26.03, 28.5, 90),         # pads run north: +VLOAD @ anchor
    "C2": (22.86, 12.95, 270),        # east of 5V pin, clear of row escapes
    # battery sense divider, NW near GPIO1 (JA1.7 @ 5.08,9.30)
    "R1": (9.5, 3.81, 0), "R2": (15.5, 3.81, 0), "C3": (24.13, 2.79, 0),
    "R3": (17.16, 17.0, 180),         # GPIO2 pull-up between the rows: pad1/3V3 east, pad2/GPIO2 west
    # DAC -> RCA series R, standing, in the island strip north of J3
    "R10": (55.88, 6.35, 0), "R11": (44.45, 4.06, 180),
    "C4": (60.5, 9.5, 270), "C5": (24.5, 19.5, 270),
    # controls along the south edge (H3 court..H4 court = x 7.5..62.5)
    "D4": (9.9, 46.99, 0), "D5": (17.05, 46.99, 0),
    "SW2": (23.54, 43.0, 0),          # anchor = pad1 corner (pads +x/+y)
    "RV1": (38.28, 47.5, 0), "SW1": (49.98, 46.0, 0),
    "R4": (8.9, 40.9, 0), "R5": (15.5, 41.28, 0),
    "TP1": (67.31, 21.59, 0), "TP2": (42.5, 36.5, 0),
    "TP3": (18.75, 34.29, 0), "TP4": (20.32, 19.05, 0),
    "TP5": (24.13, 6.35, 0),
    "H1": (4, 4, 0), "H2": (66, 4, 0), "H3": (4, 46, 0), "H4": (66, 46, 0),
}

# audit: (ref, pad, x, y) — hard positions that MUST land exactly
AUDIT = [
    ("JA1", "1", 20.32, 9.30), ("JA1", "8", 2.54, 9.30),
    ("JB1", "1", 20.32, 24.54), ("JB1", "8", 2.54, 24.54),
    ("J2", "1", 29.21, 7.62), ("J2", "6", 29.21, 20.32),
    ("J3", "1", 52.07, 7.62), ("J3", "9", 31.75, 7.62),
    ("J4", "3", 66.04, 14.47),
    ("J5", "2", 45.72, 29.21), ("J6", "2", 45.72, 34.29),
    ("X1", "2", 52.07, 2.54), ("Y1", "2", 38.10, 2.54),
    ("D2", "1", 43.18, 21.28),        # VSW cathode south (rot90 check)
    ("C1", "2", 26.03, 26.0),         # GND pad north (rot90 check)
    ("SW2", "1", 23.54, 43.0),
]

board = pcbnew.NewBoard(OUT)

# ---- nets -------------------------------------------------------------------
netinfo = {}
for ref, c in COMPONENTS.items():
    for net in norm(c)[3].values():
        if net and net not in netinfo:
            ni = pcbnew.NETINFO_ITEM(board, net)
            board.Add(ni)
            netinfo[net] = ni

# ---- footprints -------------------------------------------------------------
def load_fp(fpid):
    lib, name = fpid.split(":")
    if lib == "HYPEROSCI":
        return pcbnew.FootprintLoad(os.path.join(BASE, "HYPEROSCI.pretty"), name)
    return pcbnew.FootprintLoad(os.path.join(SYS, lib + ".pretty"), name)

fps = {}
for ref, c in COMPONENTS.items():
    val, sym, fpid, pinmap, dnp = norm(c)
    fpid = FP_OVERRIDE.get(ref, fpid)
    fp = load_fp(fpid)
    assert fp, f"footprint load failed: {fpid}"
    fp.SetReference(ref)
    fp.SetValue(val)
    x, y, rot = P[ref]
    fp.SetPosition(mm(x, y))
    fp.SetOrientationDegrees(rot)
    for pad in fp.Pads():
        net = pinmap.get(pad.GetNumber())
        if net:
            pad.SetNet(netinfo[net])
            if net == "GND":
                # solid zone connection: kills starved-thermal on pour-walled
                # pads; board is small enough to hand-solder against the pour
                pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
    fp.SetPath(pcbnew.KIID_PATH("/" + ROOT + "/" + str(uuid.uuid5(NS, "sym:" + ref))))
    if dnp and hasattr(fp, "SetDNP"):
        fp.SetDNP(True)
    if ref == "SW2" and hasattr(fp, "SetDuplicatePadNumbersAreJumpers"):
        fp.SetDuplicatePadNumbersAreJumpers(True)   # switch body bridges its twin pads
    r = fp.Reference()
    r.SetTextSize(mm(0.8, 0.8).x and VECTOR2I(FromMM(0.8), FromMM(0.8)))
    r.SetTextThickness(FromMM(0.12))
    board.Add(fp)
    fps[ref] = fp

# ---- pad-position audit -----------------------------------------------------
fails = []
for ref, num, ex, ey in AUDIT:
    # duplicate pad numbers exist (tactile, dual diode) — any twin matching passes
    got = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
           for p in fps[ref].Pads() if p.GetNumber() == num]
    ok = any(abs(ax - ex) < 0.01 and abs(ay - ey) < 0.01 for ax, ay in got)
    print(f"audit {ref}.{num}: want ({ex},{ey}) got {[(round(a,2), round(b,2)) for a,b in got]}"
          f" {'OK' if ok else 'FAIL'}")
    if not ok:
        fails.append((ref, num, got))

# ---- board outline ----------------------------------------------------------
def edge(x1, y1, x2, y2):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(mm(x1, y1)); s.SetEnd(mm(x2, y2))
    s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(FromMM(0.1))
    board.Add(s)

edge(0, 0, 70, 0); edge(70, 0, 70, 50); edge(70, 50, 0, 50); edge(0, 50, 0, 0)

# ---- silk graphics ----------------------------------------------------------
def silk_rect(x1, y1, x2, y2, layer=pcbnew.F_SilkS):
    for a, b in [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                 ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]:
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(mm(*a)); s.SetEnd(mm(*b))
        s.SetLayer(layer); s.SetWidth(FromMM(0.12))
        board.Add(s)

def silk_text(txt, x, y, size=1.0, layer=pcbnew.F_SilkS, rot=0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(txt)
    t.SetPosition(mm(x, y))
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(max(0.12, size * 0.15)))
    if rot:
        t.SetTextAngleDegrees(rot)
    board.Add(t)

# module outlines (fab layer look-alikes on silk so bare board shows sockets)
silk_rect(0, 8.0, 21.5, 25.8)                 # SuperMini (west edge overhang)
silk_text("ESP32-C3", 11, 15.5, 1.0)
silk_text("ANT", 2.5, 17.5, 0.8)
silk_rect(27.8, 6.2, 59.6, 23.2)              # GY-PCM5102A
silk_text("PCM5102A", 40, 12.5, 1.0)
silk_rect(43.6, 24.0, 70, 41.3)               # TP4056 (USB-C east)
silk_text("TP4056", 57, 27.5, 1.0)
silk_text("USB-C", 66, 38, 0.8)
silk_text("B- != GND!", 39.4, 25.4, 0.8)      # next to J5 (pcb.md §3.1)
silk_text("X", 57.15, 6.6, 1.5)
silk_text("Y", 33.02, 6.6, 1.5)
silk_text("IN+ (J6b)", 62.0, 39.9, 0.8)
silk_text("+", 56.3, 40.7, 1.2)               # J8 polarity (pad1 = BAT+)
silk_text("-", 61.0, 40.7, 1.2)
silk_text("VERIFY CELL POLARITY", 48, 49.2, 0.8)
silk_text("PWR", 49.98, 42.9, 0.8)
silk_text("CUTOFF", 38.28, 37.5, 0.8)
silk_text("MODE", 26.79, 40.9, 0.8)
silk_text("NET", 9.9, 44.3, 0.8)
silk_text("MODE", 17.05, 44.3, 0.8)
silk_text("HYPEROSCI carrier v1.0  2026-07", 20, 49.0, 0.9)
silk_text("UNIT #__", 11, 36.5, 1.0)
silk_text("JLCJLCJLCJLC", 55, 30.5, 0.8)      # fab order number goes here
silk_text("J1A", 22.5, 9.3, 0.8)
silk_text("J1B", 22.5, 24.54, 0.8)

# ---- zones ------------------------------------------------------------------
def add_zone(poly, layers, priority, name):
    z = pcbnew.ZONE(board)
    z.SetNet(netinfo["GND"])
    ls = pcbnew.LSET()
    for l in layers:
        ls.AddLayer(l)
    z.SetLayerSet(ls)
    o = z.Outline()
    o.NewOutline()
    for x, y in poly:
        o.Append(FromMM(x), FromMM(y))
    z.SetAssignedPriority(priority)
    z.SetMinThickness(FromMM(0.25))
    z.SetZoneName(name)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    board.Add(z)
    return z

BOTH = [pcbnew.F_Cu, pcbnew.B_Cu]
# 0.5mm inset from the board edge (copper-edge clearance)
add_zone([(0.5, 0.5), (69.5, 0.5), (69.5, 49.5), (0.5, 49.5)], BOTH, 0, "GND_main")
# AGND island (NE analog zone, §6.3): implemented by the moat rule-areas
# below carving the SAME GND_main zone — a separate same-net island zone
# would never merge with the main fill (KiCad excludes the lower-priority
# zone, leaving only a zero-width seam that connectivity ignores). With one
# zone the fill flows through the 3mm neck as one continuous polygon.

def rule_area(poly, layers, name, no_pour=True):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    ls = pcbnew.LSET()
    for l in layers:
        ls.AddLayer(l)
    z.SetLayerSet(ls)
    o = z.Outline()
    o.NewOutline()
    for x, y in poly:
        o.Append(FromMM(x), FromMM(y))
    for setter, v in [("SetDoNotAllowZoneFills", no_pour),
                      ("SetDoNotAllowCopperPour", no_pour),
                      ("SetDoNotAllowTracks", False),
                      ("SetDoNotAllowVias", False),
                      ("SetDoNotAllowPads", False),
                      ("SetDoNotAllowFootprints", False)]:
        if hasattr(z, setter):
            getattr(z, setter)(v)
    z.SetZoneName(name)
    board.Add(z)

# antenna keep-out: no pour under/around the SuperMini antenna end (§6.1 r3)
rule_area([(0, 6), (6, 6), (6, 28), (0, 28)], BOTH, "antenna_keepout")
# AGND moat: main pour may only touch the island at the 3mm neck (32..35,
# y13.5) nearest J2's GND pin — §6.3 single-neck rule, DRC-enforceable.
rule_area([(31, 0.5), (32, 0.5), (32, 13.5), (31, 13.5)], BOTH, "agnd_moat_w")
rule_area([(35, 13.5), (59, 13.5), (59, 14.5), (35, 14.5)], BOTH, "agnd_moat_s")
rule_area([(59, 13.5), (60, 13.5), (60, 16), (59, 16)], BOTH, "agnd_moat_se1")
rule_area([(60, 15), (70, 15), (70, 16), (60, 16)], BOTH, "agnd_moat_se2")

# ---- §6.3 neck strap --------------------------------------------------------
# Same-net zones never overlap in KiCad: the higher-priority island excludes
# the main fill, so the two pours only abut along a zero-width seam at the
# neck — which connectivity does NOT count as joined. This strap is explicit
# copper crossing the seam on both layers (plus F/B vias); route.py hard-
# blocks the corridor so no other net can land on it. x placement clears
# D3's east SMA pad (copper starts x33.54) by 0.44mm.
STRAP_X, STRAP_Y0, STRAP_Y1 = 33.02, 8.9, 17.5
for lay in BOTH:
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(mm(STRAP_X, STRAP_Y0)); t.SetEnd(mm(STRAP_X, STRAP_Y1))
    t.SetWidth(FromMM(1.0)); t.SetLayer(lay)
    t.SetNet(netinfo["GND"])
    board.Add(t)
for vy in (10.5, 13.0, 16.5):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(mm(STRAP_X, vy))
    v.SetDrill(FromMM(0.4)); v.SetWidth(FromMM(0.8))
    v.SetNet(netinfo["GND"])
    board.Add(v)

# ---- pre-seeded GND stitch grid (§6.2) --------------------------------------
# Placed BEFORE routing so the router treats them as obstacles. Post-route
# stitching cannot find legal via spots in the signal-dense quadrants
# (tracks ~1.27mm apart), which leaves severed pour regions; a seeded via
# inside a fragment anchors it to the opposite layer's pour.
_avoid = [(0, 6, 6.5, 28),                          # antenna keepout
          (31, 0.5, 32, 13.5), (35, 13.5, 59, 14.5),
          (59, 13.5, 60, 16), (60, 15, 70, 16)]     # AGND moats
_boxes, _holes = [], []
for _fp in board.GetFootprints():
    for _p in _fp.Pads():
        _bb = _p.GetBoundingBox()
        _boxes.append((_bb.GetLeft() / 1e6 - 0.70, _bb.GetTop() / 1e6 - 0.70,
                       _bb.GetRight() / 1e6 + 0.70, _bb.GetBottom() / 1e6 + 0.70))
        if _p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
            _dr = max(_p.GetDrillSizeX(), _p.GetDrillSizeY()) / 2e6
            if _dr > 0:
                _holes.append((_p.GetPosition().x / 1e6,
                               _p.GetPosition().y / 1e6, _dr))
nseed = 0
_yv = 5.0
while _yv < 48:
    _xv = 5.0
    while _xv < 68:
        ok = not any(a <= _xv <= c and b2 <= _yv <= d
                     for a, b2, c, d in _avoid)
        if ok:
            ok = not any(x1 <= _xv <= x2 and y1 <= _yv <= y2
                         for x1, y1, x2, y2 in _boxes)
        if ok:
            ok = not any((_xv - hx) ** 2 + (_yv - hy) ** 2 < (hr + 0.75) ** 2
                         for hx, hy, hr in _holes)
        if ok:
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(mm(_xv, _yv))
            v.SetDrill(FromMM(0.4)); v.SetWidth(FromMM(0.8))
            v.SetNet(netinfo["GND"])
            board.Add(v)
            nseed += 1
        _xv += 7.0
    _yv += 7.0
print(f"seeded {nseed} GND stitch vias")

# ---- design settings --------------------------------------------------------
bds = board.GetDesignSettings()
if hasattr(bds, "m_SolderMaskMinWidth"):
    bds.m_SolderMaskMinWidth = FromMM(0.2)   # JLCPCB min mask web

# ---- fill + save ------------------------------------------------------------
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
pcbnew.SaveBoard(OUT, board)
print("wrote", OUT)
if fails:
    print("AUDIT FAILURES:", fails)
    sys.exit(1)
print("audit clean")
