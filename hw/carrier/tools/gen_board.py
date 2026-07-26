#!/usr/bin/env python3
"""Build carrier.kicad_pcb from design.py + the placement table below.

Floorplan per pcb.md §6.1 with one deliberate change (documented in
layout-notes.md): the TP4056 sits SOUTH of the DAC module, because
SuperMini (~24mm) + PCM5102A (~33mm) + TP4056 (~28mm) exceed 70mm and
cannot be colinear. USB-C still exits the east edge.

Coordinates: mm from the board's top-left corner (x east, y south).
Board: 70 x 50. Run from hw/carrier/:  python3 tools/gen_board.py
"""
import json, os, sys, uuid
sys.path.insert(0, os.path.dirname(__file__))
import pcbnew
from pcbnew import VECTOR2I, FromMM
from design import COMPONENTS, norm, FP_OVERRIDE
import measured

NS = uuid.UUID("bfa2dcb2-90d5-4c42-9f4d-6f2ac2f0b001")
ROOT = str(uuid.uuid5(NS, "root-sheet"))

def mm(x, y):
    return VECTOR2I(FromMM(x), FromMM(y))

def pad_zone_conn_full(pad):
    """Solid (non-thermal) zone connection. KiCad 8 renamed PAD::SetZoneConnection
    to SetLocalZoneConnection; accept either so the build runs on 7 and 9 alike."""
    for name in ("SetLocalZoneConnection", "SetZoneConnection"):
        fn = getattr(pad, name, None)
        if fn:
            fn(pcbnew.ZONE_CONNECTION_FULL)
            return
    raise RuntimeError("pcbnew PAD has no zone-connection setter")

HERE = os.path.dirname(os.path.abspath(__file__))
# CARRIER_BASE lets tools/search.py build many board variants side by side in
# scratch directories instead of overwriting the real one.  Unset = the repo.
BASE = os.environ.get("CARRIER_BASE") or os.path.normpath(os.path.join(HERE, ".."))
OUT = os.path.join(BASE, "carrier.kicad_pcb")
SYS = "/usr/share/kicad/footprints"
# Knobs a placement sweep varies.  Defaults reproduce the committed board.
ZONE_MIN_MM = float(os.environ.get("CARRIER_ZONE_MIN", "0.15"))
STITCH_SEED_PITCH = float(os.environ.get("CARRIER_SEED_PITCH", "7.0"))

REF_SIZE = 0.8          # reference designator text height, mm
# FP_OVERRIDE (standing axials) now lives in design.py so gen_schematic.py sees
# the same answer — see the note there.

# ---- module datums ----------------------------------------------------------
# Only the datum pad of each socketed module is chosen by hand; every other
# module-mating position falls out of the photogrammetry in hw/pin_locs via
# measured.py.  Re-measure a module and the board follows it.
#
# JB1 is the SuperMini's GPIO5..21 row and it is the NORTH one.  That is not a
# free choice: the antenna overhangs WEST (keepout x<6) and the USB-C end is
# therefore EAST, which puts the 5V row SOUTH — see the derivation in
# measured.py.  v1.0 had these two rows the other way round, which is a mirror
# image of the real module; measured.Placed._check_mirror now refuses it.
JB1_PIN1 = (20.32, 9.30)
JA1_PIN1 = tuple(a + b for a, b in zip(JB1_PIN1, measured.ESP32C3_JA1_FROM_JB1))
# DAC: J3 (1x9 analog/config) runs WEST from LROUT at the jack end; J2 (1x6
# I2S) hangs off it at the MEASURED offset — 4.06 mm further west and 0.58 mm
# further south than v1.0's provisional "+2.54 mm from FLT, collinear" guess.
J3_PIN1 = (52.07, 7.62)
J2_PIN1 = tuple(a + b for a, b in zip(J3_PIN1, measured.PCM5102A_J2_FROM_J3))
# TP4056: OUT- B- B+ OUT+ running south at x=45.72.  Not a 2.54 column — the
# measured spacing is 3.53 / 7.43 / 3.11, so J5 and J6 sit 10.96 mm apart and
# the module body reaches from y0-1.82 to y0+15.64.  y0 is chosen to clear the
# DAC body (south edge 23.59) to the north and J8's wire entry to the south.
J5_PIN1 = (45.72, 26.40)
J6_PIN1 = tuple(a + b for a, b in zip(J5_PIN1, measured.TP4056_J6_FROM_J5))
J9_PAD = tuple(a + b for a, b in zip(J5_PIN1, measured.TP4056_J9_FROM_J5))

# ref: (x, y, rot_degrees)  — anchor is the footprint's native origin.
# Rotation convention (audited): rot90 maps footprint-local (x,y)->(y,-x),
# rot270 the inverse; a PinSocket's pads run +y at rot0.
P = {
    # SuperMini rows: pad1 east (USB end), pads run WEST. Module west edge
    # overhangs board by ~0.7mm; antenna region x<6 gets a pour keepout.
    "JB1": JB1_PIN1 + (270,), "JA1": JA1_PIN1 + (270,),
    "J2": J2_PIN1 + (0,), "J3": J3_PIN1 + (270,),
    "J4": (66.04,  9.39, 0),          # mic pigtail, NE analog zone
    "J5": J5_PIN1 + (0,), "J6": J6_PIN1 + (0,),
    "J9": J9_PAD + (0,),              # IN+ sense wire pad (doc J6b)
    "J7": (2.54, 29.21, 0),           # debug header, SW corner area
    "J8": (57.68, 42.5, 0),           # LiPo JST-PH: wires exit south edge
    "X1": (57.15, 2.54, 270), "Y1": (33.02, 2.54, 90),
    # power path: Q1/JP1 under the TP4056 (both flat), diodes under the DAC
    # (both flat), and the standing parts in the open mid band y26..38 that
    # opened up once the 5V row moved south.
    "Q1": (50.29, 27.94, 0),
    "Q2": (6.0, 29.5, 0),
    "U1": (6.0, 35.5, 0),             # beside R7/R8, the divider it senses
    # D1/D3 carry GATE/VBUS_CHG/VLOAD — they live in the power cluster,
    # NOT in the analog island where they'd wall off the §6.3 neck with
    # 7.62mm-pitch pad rows. J5/J6 pads run SOUTH at x45.72 — keep clear.
    "D1": (46.5, 23.0, 0),            # north of J5 col, east of D2 court
    "D2": (43.18, 16.2, 90),          # cathode/VSW pad south
    "D3": (60.96, 23.0, 90),          # DNP; NORTH of the TP4056 body so it
                                      # can be retrofitted without unplugging
    # standing power-path resistors: none may sit under a socketed module
    # (~9mm tall against an 8.3mm socket standoff — measurements.md)
    "R7": (15.0, 33.5, 0), "R8": (15.0, 37.0, 0),
    "R9": (21.0, 33.5, 0), "R6": (27.0, 33.5, 0), "R12": (33.0, 33.5, 0),
    "JP1": (54.61, 27.94, 0),         # beside Q1: it bridges Q1 D->S (VBAT_OUT->VSW)
    "C1": (26.0, 29.5, 90),           # pads run north: +VLOAD @ anchor
    "C2": (17.5, 28.5, 0),            # VLOAD bypass, 5mm from the 5V pin
    # battery sense divider, NW near GPIO1 (JA1.7 @ 5.08, JA1 row)
    "R1": (9.5, 3.81, 0), "R2": (15.5, 3.81, 0), "C3": (24.13, 2.79, 0),
    "R3": (17.16, 17.0, 180),         # GPIO2 pull-up between the rows: pad1/3V3 east, pad2/GPIO2 west
    # DAC -> RCA series R, standing, in the island strip north of J3
    "R10": (55.88, 6.35, 0), "R11": (44.45, 4.06, 180),
    "C4": (60.5, 9.5, 270), "C5": (31.5, 25.5, 0),
    # controls along the south edge (H3 court..H4 court = x 7.5..62.5).
    # RV1 is 11.3mm tall and had to slide west: its body reaches 8.0mm north
    # of its pins and was fouling the TP4056 module's south-west corner.
    # RV1's Y is NOT free: the anchor is the wiper pad, and the pot's mounting
    # surface is 5.0mm south of it, so 45.0 puts that surface exactly on the
    # board's south edge (y=50) with the bushing and shaft hanging off it.
    # Moving RV1 south again would leave the body overhanging unsupported.
    "D4": (9.9, 46.99, 0), "D5": (15.5, 46.99, 0),
    "SW2": (21.5, 43.0, 0),           # anchor = pad1 corner (pads +x/+y)
    "RV1": (36.8, 45.0, 0), "SW1": (49.98, 46.0, 0),
    "R4": (8.9, 40.9, 0), "R5": (14.5, 41.28, 0),
    "TP1": (67.31, 21.59, 0), "TP2": (39.0, 30.0, 0),
    "TP3": (21.0, 37.5, 0), "TP4": (37.5, 28.0, 0),
    "TP5": (21.0, 3.5, 0),
    "H1": (4, 4, 0), "H2": (66, 4, 0), "H3": (4, 46, 0), "H4": (66, 46, 0),
}

# Placement sweep hook.  A layout variant is then just data — CARRIER_PLACE is
# {"R8": [24.5, 37.5, 0], ...} — so tools/search.py can try dozens at once
# instead of the file being edited, run, and edited back one at a time.
for _ref, _v in json.loads(os.environ.get("CARRIER_PLACE", "{}")).items():
    assert _ref in P, f"CARRIER_PLACE: {_ref} is not a placed part"
    P[_ref] = tuple(_v)

# audit: (ref, pad, x, y) — hard positions that MUST land exactly
AUDIT = [
    ("JB1", "1") + JB1_PIN1, ("JB1", "8", 2.54, JB1_PIN1[1]),
    ("JA1", "1") + JA1_PIN1, ("JA1", "8", JA1_PIN1[0] - 17.78, JA1_PIN1[1]),
    ("J2", "1") + J2_PIN1, ("J2", "6", J2_PIN1[0], J2_PIN1[1] + 12.70),
    ("J3", "1") + J3_PIN1, ("J3", "9", 31.75, 7.62),
    ("J4", "3", 66.04, 14.47),
    ("J5", "2", 45.72, J5_PIN1[1] + measured.TP4056_PAIR_A_PITCH),
    ("J6", "1") + J6_PIN1,
    ("J6", "2", 45.72, J6_PIN1[1] + measured.TP4056_PAIR_B_PITCH),
    ("X1", "2", 52.07, 2.54), ("Y1", "2", 38.10, 2.54),
    ("D2", "1", 43.18, 21.28),        # VSW cathode south (rot90 check)
    ("C1", "2", 26.0, 27.0),          # GND pad north (rot90 check)
    ("SW2", "1", 21.5, 43.0),
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
    # NOTE: FootprintLoad() knows the path, not the library nickname, so the
    # loaded footprint keeps a BARE name ("R_Axial_..." not "Resistor_THT:R_..").
    # That makes `--schematic-parity` report a footprint_symbol_mismatch for
    # every part. Setting the full LIB_ID back does silence those, but then DRC
    # tries to resolve the nickname against the global footprint library table,
    # which a headless kicad-cli cannot see, and 41 lib_footprint_issues break
    # the "0 violations at every severity" gate. Bare names are the lesser evil.
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
                pad_zone_conn_full(pad)
    fp.SetPath(pcbnew.KIID_PATH("/" + ROOT + "/" + str(uuid.uuid5(NS, "sym:" + ref))))
    if dnp and hasattr(fp, "SetDNP"):
        fp.SetDNP(True)
    if ref == "SW2" and hasattr(fp, "SetDuplicatePadNumbersAreJumpers"):
        fp.SetDuplicatePadNumbersAreJumpers(True)   # switch body bridges its twin pads
    if ref in ("X1", "Y1"):
        # The RCA footprint prints "S"/"G" on the pad axis.  Which pair is the
        # X channel and which the Y is the one thing this legend has to answer,
        # and a separate letter placed by the ring search cannot answer it: the
        # north edge is full, so the search pushed both letters inboard until
        # they sat nearer each other than their own pads.  Rewrite the "S" in
        # place instead — it is already exactly where it should be, and the
        # signal/ground distinction survives in the "G" opposite it.
        for g in fp.GraphicalItems():
            if g.GetClass() == "PCB_TEXT" and g.GetText() == "S":
                g.SetText(ref[0])
                g.SetTextSize(VECTOR2I(FromMM(1.4), FromMM(1.4)))
                g.SetTextThickness(FromMM(0.22))
    r = fp.Reference()
    r.SetTextSize(VECTOR2I(FromMM(REF_SIZE), FromMM(REF_SIZE)))
    r.SetTextThickness(FromMM(0.12))
    r.SetLayer(pcbnew.F_SilkS)
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

# ---- silkscreen -------------------------------------------------------------
# v1.0 shipped 140 silk DRC violations: every designator sat wherever its
# footprint's default offset put it, and the module outlines were full
# rectangles ruled straight across the socket silk.  Both are fixed
# structurally rather than by nudging:
#
#   * module outlines become corner brackets on F.Silkscreen (the full
#     rectangle stays, on F.Fab, for documentation), so they no longer cross
#     anything;
#   * every label and every designator goes through `place_text`, which walks
#     a ring of candidate spots outward and takes the first that clears the
#     pads, the board edge and everything already placed.
#
# Anything that cannot be placed is reported and fails the build, so silk
# congestion shows up here instead of in the fab's DRC.
SILK_CLR = 0.20                    # silk-to-pad / silk-to-silk margin, mm
EDGE_CLR = 0.50                    # silk-to-board-edge margin, mm
occupied = []                      # boxes already claimed on F.Silkscreen
unplaced = []

def _box_of(item):
    bb = item.GetBoundingBox()
    return (bb.GetLeft() / 1e6 - SILK_CLR, bb.GetTop() / 1e6 - SILK_CLR,
            bb.GetRight() / 1e6 + SILK_CLR, bb.GetBottom() / 1e6 + SILK_CLR)

def _mk_text(s, size, rot, layer=pcbnew.F_SilkS):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(max(0.12, size * 0.15)))
    if rot:
        t.SetTextAngleDegrees(rot)
    return t

def _text_box(t, x, y):
    """Where a text item really lands — asked of KiCad, not guessed.

    An estimated glyph advance is what put the one surviving silk overlap on
    the board; PCB_TEXT::GetBoundingBox knows the stroke font exactly, and
    handles rotation and multi-line strings for free.
    """
    t.SetPosition(mm(x, y))
    return _box_of(t)

def _text_wh(t):
    b = _text_box(t, 0, 0)
    return b[2] - b[0], b[3] - b[1]

def _free(b):
    if b[0] < EDGE_CLR or b[1] < EDGE_CLR or b[2] > 70 - EDGE_CLR \
            or b[3] > 50 - EDGE_CLR:
        return False
    return not any(b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3]
                   for o in occupied)

# Seed the occupancy map: exposed copper (silk over a pad is a real defect),
# then every graphic the footprints themselves put on the front silk.
#
# A body OUTLINE needs its interior occupied too, not just the four strokes.
# Each stroke's own bounding box is a sliver, so the enclosed area read as free
# and a neighbour's designator could land inside another part's outline —
# which is how "SW2" ended up sitting inside RV1's body, reading as the pot's
# label. Anything whose silk spans more than BODY_MM in both axes is a body
# outline rather than a mark (a polarity tick, a pin-1 dot, a "+"), so fill it.
# 8.0 is swept, not guessed: at 4.0 and 6.0 the small part outlines fill too and
# R8's designator has nowhere left to go.  At 8.0, RV1 is currently the only
# footprint that qualifies -- the rule is general, its bite today is not.
BODY_MM = 8.0
for _fp in board.GetFootprints():
    for _p in _fp.Pads():
        occupied.append(_box_of(_p))
    _silk = []
    for _g in _fp.GraphicalItems():
        if _g.GetLayer() == pcbnew.F_SilkS:
            _b = _box_of(_g)
            occupied.append(_b)
            _silk.append(_b)
    if _silk:
        _hull = (min(b[0] for b in _silk), min(b[1] for b in _silk),
                 max(b[2] for b in _silk), max(b[3] for b in _silk))
        if _hull[2] - _hull[0] > BODY_MM and _hull[3] - _hull[1] > BODY_MM:
            occupied.append(_hull)

# Candidate directions, nearest-first: the four cardinals, then the diagonals,
# then half-diagonals so a designator can slide along the long side of a part
# instead of jumping to the next ring.
RING = [(0, 1), (0, -1), (1, 0), (-1, 0),
        (1, 1), (-1, 1), (1, -1), (-1, -1),
        (0.5, 1), (-0.5, 1), (0.5, -1), (-0.5, -1),
        (1, 0.5), (1, -0.5), (-1, 0.5), (-1, -0.5)]

def silk_line(x1, y1, x2, y2, layer=pcbnew.F_SilkS, w=0.12, claim=True):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(mm(x1, y1)); s.SetEnd(mm(x2, y2))
    s.SetLayer(layer); s.SetWidth(FromMM(w))
    board.Add(s)
    if claim and layer == pcbnew.F_SilkS:
        occupied.append((min(x1, x2) - w - SILK_CLR, min(y1, y2) - w - SILK_CLR,
                         max(x1, x2) + w + SILK_CLR, max(y1, y2) + w + SILK_CLR))

def silk_rect(x1, y1, x2, y2, layer=pcbnew.F_SilkS, w=0.12, claim=True):
    for a, b in [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                 ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]:
        silk_line(a[0], a[1], b[0], b[1], layer, w, claim)

def module_outline(box, arm=2.5):
    """Corner brackets on silk + the full rectangle on F.Fab.

    Brackets say 'the module reaches this far' without ruling a line through
    every socket and part inside it.  Clipped to the board so the overhanging
    modules do not throw silk-to-edge violations.
    """
    x0 = max(box[0], EDGE_CLR); y0 = max(box[1], EDGE_CLR)
    x1 = min(box[2], 70 - EDGE_CLR); y1 = min(box[3], 50 - EDGE_CLR)
    silk_rect(box[0], box[1], box[2], box[3], pcbnew.F_Fab, 0.1, claim=False)
    for cx, sx in ((x0, 1), (x1, -1)):
        for cy, sy in ((y0, 1), (y1, -1)):
            _arm(cx, cy, sx, 0, arm)
            _arm(cx, cy, 0, sy, arm)
    return (x0, y0, x1, y1)

def _arm(cx, cy, ux, uy, arm):
    """One bracket stroke, trimmed back to the last length that stays clear.

    The sockets sit right on the module corners, so a fixed-length arm walks
    straight over their silk.  Shortening beats deleting: even a 0.8mm tick
    still marks the corner.
    """
    L = arm
    while L >= 0.8:
        b = (min(cx, cx + ux * L) - 0.12 - SILK_CLR,
             min(cy, cy + uy * L) - 0.12 - SILK_CLR,
             max(cx, cx + ux * L) + 0.12 + SILK_CLR,
             max(cy, cy + uy * L) + 0.12 + SILK_CLR)
        if _free(b):
            silk_line(cx, cy, cx + ux * L, cy + uy * L)
            return
        L -= 0.4

MIN_TEXT = 0.8          # KiCad's default minimum-text-height DRC rule
LOCAL_REACH = 3.5       # how far a designator may stray from its courtyard, mm

def _sweep(t, x0, y0, x1, y1, ax, ay, step, keep):
    """Grid-search a window for the free spot nearest (ax, ay).

    `keep` is a box the text must stay out of — a part's own courtyard, so a
    designator lands beside its part rather than on top of it.
    """
    best = None
    gy = y0
    while gy <= y1:
        gx = x0
        while gx <= x1:
            b = _text_box(t, gx, gy)
            if keep is None or not (b[0] < keep[2] and keep[0] < b[2]
                                    and b[1] < keep[3] and keep[1] < b[3]):
                if _free(b):
                    d = (gx - ax) ** 2 + (gy - ay) ** 2
                    if best is None or d < best[0]:
                        best = (d, gx, gy, b)
            gx += step
        gy += step
    return best

def _sizes(size, shrink):
    """Sizes to try, largest first, never below the DRC text-height floor."""
    if not shrink:
        return [size]
    out = [size]
    for s in (size * 0.85, size * 0.7, MIN_TEXT):
        if s >= MIN_TEXT and s < out[-1] - 1e-6:
            out.append(s)
    return out

def find_spot(s, ax, ay, size, halo=(0.0, 0.0), prefer=(), rot=0, shrink=True,
              scan=False):
    """Nearest clear patch of silk for `s`, anchored on (ax, ay) + `halo`.

    Returns (x, y, size, box) or None.  Offsets are computed from the text's
    own half-width/half-height, so a long string pushed east clears the part
    by the same margin a short one does.  If nothing fits, the text is retried
    a size down before giving up — legible-but-small beats absent.
    """
    dirs = list(prefer) + [d for d in RING if d not in prefer]
    for sz in _sizes(size, shrink):
        t = _mk_text(s, sz, rot)
        w, h = _text_wh(t)
        for step in range(10):
            gap = 0.30 + 0.5 * step
            for dx, dy in ([(0, 0)] if step == 0 and halo == (0, 0) else dirs):
                x = ax + dx * (halo[0] + w / 2 + gap)
                y = ay + dy * (halo[1] + h / 2 + gap)
                b = _text_box(t, x, y)
                if _free(b):
                    return (x, y, sz, b)
    # The ring only samples 16 bearings, which walks straight past a slot that
    # is barely wider than the text — the gap between X1 and H2's keep-out is
    # one.  Sweep the neighbourhood properly before giving up.
    keep = (ax - halo[0], ay - halo[1], ax + halo[0], ay + halo[1])
    for sz in _sizes(size, shrink):
        t = _mk_text(s, sz, rot)
        got = _sweep(t, ax - halo[0] - LOCAL_REACH, ay - halo[1] - LOCAL_REACH,
                     ax + halo[0] + LOCAL_REACH, ay + halo[1] + LOCAL_REACH,
                     ax, ay, 0.25, keep)
        if got:
            return (got[1], got[2], sz, got[3])
    if not scan:
        return None
    # Legends that are not tied to one part get a whole-board sweep: take the
    # free spot nearest the preferred anchor rather than giving up.
    for sz in _sizes(size, shrink):
        t = _mk_text(s, sz, rot)
        got = _sweep(t, EDGE_CLR, EDGE_CLR, 70 - EDGE_CLR, 50 - EDGE_CLR,
                     ax, ay, 0.5, None)
        if got:
            return (got[1], got[2], sz, got[3])
    return None

def place_text(s, ax, ay, size=REF_SIZE, halo=(0.0, 0.0), prefer=(),
               rot=0, layer=pcbnew.F_SilkS, name=None, shrink=True, scan=True):
    """Put `s` as close to (ax, ay) as a clear patch of silk allows."""
    spot = find_spot(s, ax, ay, size, halo, prefer, rot, shrink, scan)
    if spot is None:
        unplaced.append(name or s.replace("\n", " "))
        return None
    x, y, sz, b = spot
    t = _mk_text(s, sz, rot, layer)
    t.SetPosition(mm(x, y))
    board.Add(t)
    occupied.append(b)
    return (x, y)

def back_text(s, x, y, size=0.8, rot=0):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetPosition(mm(x, y))
    t.SetLayer(pcbnew.B_SilkS)
    t.SetMirrored(True)
    t.SetTextSize(VECTOR2I(FromMM(size), FromMM(size)))
    t.SetTextThickness(FromMM(max(0.12, size * 0.15)))
    if rot:
        t.SetTextAngleDegrees(rot)
    board.Add(t)

def court(ref):
    """(cx, cy, half_w, half_h) of a placed footprint's courtyard."""
    bb = fps[ref].GetCourtyard(pcbnew.F_CrtYd).BBox()
    x0, y0 = bb.GetLeft() / 1e6, bb.GetTop() / 1e6
    x1, y1 = bb.GetRight() / 1e6, bb.GetBottom() / 1e6
    return ((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2)

def label(ref, s, size=0.8, prefer=()):
    """Legend for a specific part — must land next to it, so no board sweep."""
    cx, cy, hw, hh = court(ref)
    return place_text(s, cx, cy, size, (hw, hh), prefer, scan=False,
                      name=f"label {s} @{ref}")

def pad_label(ref, num, s, size=0.8, prefer=()):
    """Legend for one pad — same rule, anchored on the pad instead."""
    p = [q for q in fps[ref].Pads() if q.GetNumber() == num][0]
    bb = p.GetBoundingBox()
    cx = (bb.GetLeft() + bb.GetRight()) / 2e6
    cy = (bb.GetTop() + bb.GetBottom()) / 2e6
    hw = (bb.GetRight() - bb.GetLeft()) / 2e6
    hh = (bb.GetBottom() - bb.GetTop()) / 2e6
    return place_text(s, cx, cy, size, (hw, hh), prefer, scan=False,
                      name=f"pad label {s} @{ref}.{num}")

# --- module bodies, straight off the measured outlines ----------------------
def body(ref, pad, box):
    x, y = [p.GetPosition() for p in fps[ref].Pads()
            if p.GetNumber() == pad][0].x / 1e6, \
           [p.GetPosition() for p in fps[ref].Pads()
            if p.GetNumber() == pad][0].y / 1e6
    return (x + box[0], y + box[1], x + box[2], y + box[3])

ESP_BODY = body("JB1", "1", measured.ESP32C3_OUTLINE)
DAC_BODY = body("J3", "1", measured.PCM5102A_OUTLINE)
CHG_BODY = body("J5", "1", measured.TP4056_OUTLINE)
for _b in (ESP_BODY, DAC_BODY, CHG_BODY):
    module_outline(_b)

# --- fixed legends, most-constrained first ----------------------------------
place_text("ESP32-C3 SuperMini", (ESP_BODY[0] + ESP_BODY[2]) / 2, 20.6, 0.9,
           name="ESP32-C3 legend")
place_text("ANT", 2.6, 27.2, 0.8, name="ANT legend")
place_text("GY-PCM5102A", (DAC_BODY[0] + DAC_BODY[2]) / 2, 20.0, 0.9,
           name="PCM5102A legend")
place_text("TP4056", 57.0, 33.5, 0.9, name="TP4056 legend")
place_text("USB-C", 66.0, 30.0, 0.8, name="USB-C legend")
# TP4056 output pads: which of the four is which is not guessable, and B- is
# the one that gets a board killed if it is mistaken for GND.
WEST = ((-1, 0), (-1, -0.5), (-1, 0.5))
pad_label("J5", "1", "OUT-", prefer=WEST)
pad_label("J5", "2", "B-", prefer=WEST)
pad_label("J6", "1", "B+", prefer=WEST)
pad_label("J6", "2", "OUT+", prefer=WEST)
place_text("B- is NOT GND", 38.8, 32.0, 0.8, name="B- warning")
# X/Y are printed by the footprint itself (see the X1/Y1 case above) — the
# north edge has no room for a free-floating letter that stays next to its pad.
label("J9", "IN+", 0.8, prefer=((0, -1), (-1, 0)))
label("J8", "+ cell -", 0.8, prefer=((0, -1),))
place_text("VERIFY\nCELL\nPOLARITY", 62.5, 46.5, 0.8, name="cell warning")
label("SW1", "PWR", 0.8, prefer=((0, -1),))
label("RV1", "CUTOFF", 0.8, prefer=((0, -1),))
# SW2 and D5 are both "MODE", and the placer put the two words side by side —
# on the render they read as one part labelled MODE MODE.  The button is the
# one that gets the qualifier: the south edge around D4/D5 has no room for a
# longer string, and "NET / MODE" beside two LEDs is unambiguous on its own.
label("SW2", "MODE SW", 0.8, prefer=((0, -1),))
label("D4", "NET", 0.8, prefer=((0, -1),))
label("D5", "MODE", 0.8, prefer=((0, -1),))
place_text("HYPEROSCI carrier\nv1.1  2026-07", 8.0, 27.0, 0.8,
           name="board name")
place_text("UNIT #__", 8.0, 22.5, 0.9, name="unit box")
# The fab's order number goes on the BACK, and specifically into the antenna
# keep-out strip: v1.0 had it landing across Q1/JP1/D3, and anywhere else on
# the back is stitched with vias (which is what silk_over_copper catches).
# The keep-out carries no pour and no stitching by construction, so this strip
# of bare laminate is the one place on the board where silk is guaranteed
# clear — running vertically between the two SuperMini rows.
back_text("JLCJLCJLCJLC", 2.3, 17.0, 0.8, rot=90)
back_text("HYPEROSCI v1.1", 3.9, 17.0, 0.8, rot=90)

# --- reference designators ---------------------------------------------------
# Mounting holes carry no BOM line, so their designators are pure clutter.
HIDE_REF = {"H1", "H2", "H3", "H4"}
# Parts whose designator wants to go somewhere specific (everything else takes
# whatever the ring search finds first).
REF_PREFER = {
    "JB1": ((0, -1),), "JA1": ((0, 1),), "J2": ((-1, 0),), "J3": ((0, -1),),
    "J5": ((-1, 0),), "J6": ((-1, 0),), "J4": ((1, 0),),
    "R10": ((0, -1),), "R11": ((0, -1),),
    # TP3 goes early and westward, because SW2's designator wants the same gap.
    # SW2 is boxed in by D5/R5/TP3 and the board edge, and before the body-fill
    # above its designator walked ~9 mm east, landing INSIDE RV1's outline where
    # it read as the pot's label. With the fill it settles at SW2's north-east
    # corner instead -- but it gets there by taking TP3's spot, so TP3 is placed
    # first (REF_PREFER members go before everything else). Both then fit.
    "TP3": ((-1, 0), (0, -1)),
}
for ref in sorted(fps, key=lambda r: (r not in REF_PREFER, r)):
    r = fps[ref].Reference()
    if ref in HIDE_REF:
        r.SetVisible(False)
        continue
    cx, cy, hw, hh = court(ref)
    spot = find_spot(ref, cx, cy, REF_SIZE, (hw, hh), REF_PREFER.get(ref, ()))
    if spot is None:
        unplaced.append(f"designator {ref}")
        r.SetVisible(False)
        continue
    x, y, sz, b = spot
    r.SetPosition(mm(x, y))
    r.SetTextAngleDegrees(0)
    r.SetTextSize(VECTOR2I(FromMM(sz), FromMM(sz)))
    r.SetTextThickness(FromMM(max(0.12, sz * 0.15)))
    occupied.append(b)

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
    # 0.15mm, not 0.25: on a 2-layer board the routing chops the pour into
    # ribbons, and a 0.25mm floor discards every sliver narrower than that —
    # which is how a GND pad ends up on a fill fragment with no path home.
    # JLCPCB's 1oz minimum copper width is 0.127mm, so 0.15 is in spec.
    z.SetMinThickness(FromMM(ZONE_MIN_MM))
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
        _xv += STITCH_SEED_PITCH
    _yv += STITCH_SEED_PITCH
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
print(f"SILK_UNPLACED {len(unplaced)}")
if unplaced:
    print(f"SILK: {len(unplaced)} items had nowhere clear to sit:")
    for u in unplaced:
        print("   ", u)
if fails:
    print("AUDIT FAILURES:", fails)
# A sweep (tools/search.py) wants to score a variant that loses one designator,
# not be blocked by it — the count is reported either way and ranked against
# everything else.  For a normal build an unplaced item is still a hard stop.
STRICT = os.environ.get("CARRIER_SILK_STRICT", "1") != "0"
if fails or (unplaced and STRICT):
    sys.exit(1)
print("audit clean, silk placed")
