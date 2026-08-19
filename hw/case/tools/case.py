#!/usr/bin/env python3
"""HYPEROSCI slave enclosure -- generated, not drawn.

    hw/case/.venv/bin/python hw/case/tools/case.py

Reads board-geom.json (produced from carrier.kicad_pcb by export_geom.py), so
every X/Y coordinate below is derived from the board rather than transcribed.
Z is the exception: KiCad has no 3D model for RV1, SW1, the RCA pads or the
three socketed modules, so heights are measured constants in Z below.

Frame: model X = kicad_x - 35, model Y = 25 - kicad_y, Z up.
       z = 0 is the inside face of the case floor.
Outputs: out/base.{step,stl}  out/lid.{step,stl}  out/plunger.{step,stl}
"""
import json
import os
from math import cos, pi, sin

from build123d import *

HERE = os.path.dirname(os.path.abspath(__file__))
GEOM = json.load(open(os.path.join(HERE, "..", "board-geom.json")))
OUT = os.path.join(HERE, "..", "out")

# ---------------------------------------------------------------------------
# Parameters.  Everything you might want to change lives here.
# ---------------------------------------------------------------------------
P = dict(
    # --- shell ---
    wall=2.4, floor=2.0, lid_t=2.0,
    gap_w=3.0, gap_n=3.0, gap_e=3.0, gap_s=4.0,   # PCB edge -> inner wall face
    corner_r=3.0,
    # --- Z stack (measured; see README) ---
    cell_h=10.0, cell_clear=0.5,      # EEMB LP103454 is 10 mm; +0.5 for swell
    bay_sink=0.5, bay_sink_e=3.0,     # the cell drops this far INTO the floor,
                                      # and the recess runs bay_sink_e past its
                                      # east face so the leads leave the pouch
                                      # flat instead of over the step -- the tab
                                      # seal is the fragile part of a pouch.
                                      # 2 mm of floor is more than the bay needs
                                      # to be stiff, so this buys clearance
                                      # under the board without buying height.
    tail_clear=3.0,                   # swollen cell top -> board underside. The
                                      # KiCad models' lowest pin tails hang
                                      # 1.9 mm, so this is 1.1 mm of real air.
    pcb_t=1.6,
    above_pcb=18.5,                   # interior headroom = 8.5 socket + 1.0
                                      # module PCB + 7.0 heatsink + 2.0.
                                      # Heatsink measured 2026-08-19.
    # --- PCB mounting: M3 x 5.0 heat-set inserts, OD 4.6 ---
    # v1 printed these as bare 6.4 mm columns and the inserts had nothing to
    # bite into.  Each is now a solid pad tying into two walls; only the post
    # nearest J8 stays free-standing, so the battery lead can wrap around it.
    pad_r=5.0,                        # pad reaches this far from the hole
    post_fillet=2.0,
    keepout_d=6.5,                    # pcb.md 6.1 rule 7: the annulus that is
                                      # certified clear of parts on the board
    insert_d=4.0, insert_depth=5.6,
    # --- battery bay ---
    cell_l=56.0, cell_w=34.0, cell_slack=1.0,
    bay_margin=0.5,
    bay_from_e=15.0,                  # bay's east face, inboard of the E wall.
                                      # Far enough that the cell clears the H2
                                      # and H4 pads entirely: the cell is 34 mm
                                      # and the hole rows are 42 mm apart, so a
                                      # pad it overlaps can only be 2 mm thick
                                      # on the bay side.  Better to move the
                                      # cell than to starve two inserts.  The
                                      # extra 3 mm past that is the channel the
                                      # JST lead rises in, east of the cell.
    rib_h=3.0, rib_t=2.0,
    # --- lid ---
    # v1 was a fight to get on and could only be pried off, v2 overshot and came
    # out slack.  See the long note in build_lid().  The pockets in the base are
    # unchanged throughout -- snap_w and snap_from_lid still size them -- so all
    # three revisions print against the same base.
    lip_h=7.0, lip_t=1.6, lip_fit=0.30, lip_lead=0.6,
    snap_w=14.0, snap_from_lid=3.5,   # base pocket: 14 mm wide, 3.5 mm down
    snap_span=18.0,                   # lip detached from the plate over this,
                                      # leaving a beam built in at both ends
    snap_gap=1.2, snap_arm_t=0.8,     # ... 5.8 mm tall and 0.8 mm thick.  The
                                      # HEIGHT is the free lever: stiffness is
                                      # linear in it and bending strain does not
                                      # contain it at all, so a deeper tongue
                                      # buys grip for nothing.  Thickness and
                                      # engagement buy the same grip at 3rd and
                                      # 1st power in the strain, and the strain
                                      # is already at PLA's yield -- past that
                                      # the tab takes a set and goes loose by
                                      # itself, which is the failure we are
                                      # trying to get away from.
    snap_bead_w=4.0,                  # narrow, so the beam bends and not it
    snap_bead=0.70,                   # protrusion; minus lip_fit = 0.40 engaged
    snap_flat=0.5,                    # land at full protrusion: >= 2 layers at
                                      # any usual layer height, so the slicer
                                      # cannot round the bead away
    snap_lead_h=1.7, snap_play=0.05,  # 22 deg lead-in, 0.05 mm of lift slop.
                                      # These two trade against each other: the
                                      # ramp has to reach full protrusion before
                                      # it reaches the pocket's lower lip, so
                                      # every 0.1 mm of play spent costs ~0.2 mm
                                      # of ramp and steepens the lead-in.
                                      # v2 carried 0.25 as budget for the bridge
                                      # sagging bedward as it prints; it printed
                                      # loose rather than tight, so the sag is
                                      # not there to pay for.
    pry_d=8.0, pry_h=1.2,             # fingernail scoop at each seam mid-edge
    # --- openings ---
    rca_slot_w=12.0,                  # 2 flying leads per output, and the pads
                                      # they leave from span 8.4 mm
    mic_slot_w=8.0,
    jst_slot_w=10.0,
    pot_hole_d=9.0, pot_axis_z=7.0,   # RV097NS shaft axis above the PCB.  Open
                                      # to the top: the bushing crosses the wall
                                      # line, so a closed hole means threading
                                      # the board in at an angle (v1 feedback).
    sw1_slot_w=13.0, sw1_slot_h=10.0, sw1_slot_z0=-1.0,
    usb_w=17.0, usb_h=14.0, usb_z0=-0.6,
    btn_hole_d=9.0, led_hole_d=5.0,
    # optional pushed-on knob.  pcb.md 2 specs "no knob" -- that was about not
    # sourcing one; the shaft only clears the wall by 8.6 mm, which is thin to
    # pinch on stage.  13 mm keeps it clear of the SW1 slot 13 mm to the east.
    knob_d=13.0, knob_h=12.0, knob_bore=5.9, knob_bore_h=10.0, knob_flutes=14,
    vent_w=3.0, vent_gap=3.0,         # lid only -- the side vents are gone
)

# ---------------------------------------------------------------------------
# Derived geometry
# ---------------------------------------------------------------------------
B = GEOM["board"]
BW, BH = B["x1"] - B["x0"], B["y1"] - B["y0"]        # 70.10 x 50.10


def mx(kx):  return kx - 35.0          # kicad x -> model x
def my(ky):  return 25.0 - ky          # kicad y -> model y (north = +Y)


IN_W, IN_E = mx(0) - P["gap_w"], mx(70) + P["gap_e"]      # -38 .. +38
IN_S, IN_N = my(50) - P["gap_s"], my(0) + P["gap_n"]      # -29 .. +28
OUT_W, OUT_E = IN_W - P["wall"], IN_E + P["wall"]
OUT_S, OUT_N = IN_S - P["wall"], IN_N + P["wall"]

Z_CELL_BOT = -P["bay_sink"]                               # -0.5  recessed floor
Z_CELL_TOP = Z_CELL_BOT + P["cell_h"]                     #  9.5
Z_BAY = Z_CELL_TOP + P["cell_clear"]                      # 10.0  swollen cell
Z_TAIL = Z_BAY                                            # lead tails start here
Z_PCB_BOT = Z_TAIL + P["tail_clear"]                      # 13.0
Z_PCB_TOP = Z_PCB_BOT + P["pcb_t"]                        # 14.6
Z_TOP = Z_PCB_TOP + P["above_pcb"]                        # 33.1  wall top
Z_FLOOR = -P["floor"]

# Battery bay.  Open to the east so the lead can only route from that corner
# (pcb.md 3.1 -- a reversed cell drives the pack backwards through the FS8205,
# so this keying is not cosmetic).
BAY_E = IN_E - P["bay_from_e"]
BAY_W = BAY_E - (P["cell_l"] + P["cell_slack"])
BAY_S = -(P["cell_w"] + P["cell_slack"]) / 2
BAY_N = -BAY_S

HOLES = {r: (mx(kx), my(ky)) for r, (kx, ky) in GEOM["mounting_holes"].items()}
_J8 = (mx(GEOM["parts"]["J8"]["pos"][0]), my(GEOM["parts"]["J8"]["pos"][1]))
# The pad nearest the battery connector stays a free-standing post: the JST
# lead is 40 mm long for a ~20 mm run, and the slack gets wrapped around it.
WIRE_POST = min(HOLES, key=lambda r: (HOLES[r][0] - _J8[0]) ** 2 + (HOLES[r][1] - _J8[1]) ** 2)


def box(x0, x1, y0, y1, z0, z1):
    (x0, x1), (y0, y1), (z0, z1) = sorted((x0, x1)), sorted((y0, y1)), sorted((z0, z1))
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


def bay_keepout():
    """Where the cell is.  Only as tall as the cell -- above it the mounting
    pads are free to be full width again."""
    m = P["bay_margin"]
    return box(BAY_W - m, BAY_E + m, BAY_S - m, BAY_N + m, Z_CELL_BOT, Z_BAY)


def tail_keepout():
    """Under the board above the lead-tail line, only the certified 6.5 mm
    annulus at each mounting hole may reach the underside.  This is what stops
    a big mounting pad from sitting on somebody's clipped THT leads."""
    k = box(mx(B["x0"]) - 0.5, mx(B["x1"]) + 0.5, my(B["y1"]) - 0.5, my(B["y0"]) + 0.5,
            Z_TAIL, Z_PCB_BOT + 0.01)
    for x, y in HOLES.values():
        k -= Pos(x, y, Z_TAIL - 0.1) * Cylinder(
            P["keepout_d"] / 2, P["tail_clear"] + 0.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return k


def roofed(w, z0, z1):
    """Profile (u, z) of a rectangular opening with a self-supporting 45 deg roof."""
    return [(-w / 2, z0), (w / 2, z0), (w / 2, z1), (0.0, z1 + w / 2), (-w / 2, z1)]


def open_top(w, z0):
    """Profile (u, z) of a slot open all the way to the top of the wall, so the
    thing that passes through it drops in from above instead of threading."""
    return [(-w / 2, z0), (w / 2, z0), (w / 2, Z_TOP + 1), (-w / 2, Z_TOP + 1)]


# Each wall gets a sketch plane parked just outside its outer face, with the
# sketch's u along the wall and v along +Z, extruded inward.  Cutting from a
# plane through the middle of the box instead punches the opposite wall as well.
def _wall_plane(side):
    d = P["wall"] + 4
    return {
        "N": (Plane(origin=(0, IN_N + d, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0)), 1),
        "S": (Plane(origin=(0, IN_S - d, 0), x_dir=(-1, 0, 0), z_dir=(0, 1, 0)), -1),
        "E": (Plane(origin=(IN_E + d, 0, 0), x_dir=(0, -1, 0), z_dir=(-1, 0, 0)), -1),
        "W": (Plane(origin=(IN_W - d, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)), 1),
    }[side]


def wall_cutter(side, u, pts):
    """Cut a (u, z) profile through one wall.  u is the model coordinate that
    runs along that wall (x for N/S, y for E/W)."""
    plane, sign = _wall_plane(side)
    sk = plane * Polygon(*[(sign * u + du, z) for du, z in pts], align=None)
    return extrude(sk, amount=P["wall"] + 8)


def pad_centre(ref):
    """Model x of the centre of a footprint's copper.  For the X/Y RCA landings
    the footprint origin is the signal pad, 2.7 mm off the pair's centre."""
    pd = GEOM["parts"][ref]["pads"]
    return mx((pd[0] + pd[2]) / 2.0)


# ---------------------------------------------------------------------------
# BASE
# ---------------------------------------------------------------------------
def build_base():
    outer = box(OUT_W, OUT_E, OUT_S, OUT_N, Z_FLOOR, Z_TOP)
    outer = fillet(outer.edges().filter_by(Axis.Z), P["corner_r"])
    cav = box(IN_W, IN_E, IN_S, IN_N, 0, Z_TOP + 1)
    cav = fillet(cav.edges().filter_by(Axis.Z), max(P["corner_r"] - P["wall"], 0.6))
    part = outer - cav

    # --- PCB mounting pads: one block of material per corner ---------------
    pads = None
    for ref, (x, y) in HOLES.items():
        r = P["pad_r"]
        if ref == WIRE_POST:
            blk = box(x - r, x + r, y - r, y + r, 0, Z_PCB_BOT)
            blk = fillet(blk.edges().filter_by(Axis.Z), P["post_fillet"])
        else:
            # from the two nearest walls, *past* the hole by pad_r
            blk = box(IN_E if x > 0 else IN_W, x - r if x > 0 else x + r,
                      IN_N if y > 0 else IN_S, y - r if y > 0 else y + r, 0, Z_PCB_BOT)
        pads = blk if pads is None else pads + blk
    pads -= bay_keepout()       # ... but not where the cell is,
    pads -= tail_keepout()      # ... and not on anybody's lead tails.
    part += pads
    for x, y in HOLES.values():
        part -= Pos(x, y, Z_PCB_BOT - P["insert_depth"]) * Cylinder(
            P["insert_d"] / 2, P["insert_depth"] + 0.01, align=(Align.CENTER, Align.CENTER, Align.MIN))

    # --- battery bay ribs: located north, west and south; east left open ---
    t, h = P["rib_t"], P["rib_h"]
    part += box(BAY_W - t, BAY_E, BAY_N, BAY_N + t, 0, h)          # north rib
    part += box(BAY_W - t, BAY_E - 14.0, BAY_S - t, BAY_S, 0, h)   # south rib, gapped for the lead
    part += box(BAY_W - t, BAY_W, BAY_S - t, BAY_N + t, 0, h)      # west rib
    # ... and the cell drops bay_sink into the floor, which both locates it and
    # is 0.5 mm the board underside does not have to give up.
    part -= box(BAY_W, BAY_E + P["bay_sink_e"], BAY_S, BAY_N, Z_CELL_BOT, 0.01)

    # --- wall openings -----------------------------------------------------
    d = P["wall"] + 4
    # north wall: the two RCA flying-lead pairs, centred on the pad pair (not
    # on the footprint origin -- v1 got that wrong and skewed both by 2.7 mm).
    for ref in ("X1", "Y1"):
        part -= wall_cutter("N", pad_centre(ref),
                            open_top(P["rca_slot_w"], Z_PCB_TOP + 0.9))
    # south wall: pot shaft, power switch, battery lead + JST plug tail
    xr = mx(GEOM["parts"]["RV1"]["pos"][0])
    z_axis = Z_PCB_TOP + P["pot_axis_z"]
    part -= Pos(xr, IN_S - 1, z_axis) * Rot(-90, 0, 0) * Cylinder(P["pot_hole_d"] / 2, d)
    part -= wall_cutter("S", xr, open_top(P["pot_hole_d"], z_axis))
    xs = mx(GEOM["parts"]["SW1"]["pos"][0])
    part -= wall_cutter("S", xs, roofed(P["sw1_slot_w"], Z_PCB_TOP + P["sw1_slot_z0"],
                                        Z_PCB_TOP + P["sw1_slot_z0"] + P["sw1_slot_h"]))
    xj = mx(GEOM["parts"]["J8"]["pos"][0]) + 2.0
    part -= wall_cutter("S", xj, open_top(P["jst_slot_w"], Z_PCB_TOP))
    # east wall: TP4056 charge port (generous -- the jack sits ~0.5 mm proud of
    # the board edge, so the opening has to clear a USB-C plug overmold) + mic
    yu = my(GEOM["usb_charge"]["y"])
    part -= wall_cutter("E", yu, roofed(P["usb_w"], Z_PCB_TOP + P["usb_z0"],
                                        Z_PCB_TOP + P["usb_z0"] + P["usb_h"]))
    ym = my(GEOM["parts"]["J4"]["pos"][1])
    part -= wall_cutter("E", ym, open_top(P["mic_slot_w"], Z_PCB_TOP + 0.9))

    # --- lid snap pockets (east + west walls; north/south are full of cutouts)
    for x, y in SNAPS:
        n = 1 if x > 0 else -1
        part -= box(IN_E - 0.4 if n > 0 else IN_W - 1.2, IN_E + 1.2 if n > 0 else IN_W + 0.4,
                    y - P["snap_w"] / 2 - 0.3, y + P["snap_w"] / 2 + 0.3,
                    Z_TOP - P["snap_from_lid"] - 2.2, Z_TOP - P["snap_from_lid"])
    return part


SNAPS = [(IN_E, 6.0), (IN_E, -19.0), (IN_W, 14.0), (IN_W, -14.0)]


# ---------------------------------------------------------------------------
# LID
# ---------------------------------------------------------------------------
def snap_span(y):
    """The stretch of lip that gets detached from the plate for one snap,
    centred on the bead but shifted clear of the corners rather than run into
    them -- a corner is the one part of the ring that must stay stiff."""
    sp, lo, hi = P["snap_span"], IN_S + 2.0, IN_N - 2.0
    y0, y1 = y - sp / 2, y + sp / 2
    if y0 < lo: y0, y1 = lo, lo + sp
    if y1 > hi: y0, y1 = hi - sp, hi
    return y0, y1


def snap_profile():
    """Bead section as (q, z), q measured outward from the strip's outer face.

    Both faces are ramps, which v1's square block was not:
      up  -- rises snap_lead_h over snap_bead, ~18 deg off the insertion axis,
             so the wall's top edge cams the strip inward instead of butting it;
      down - 45 deg, so a firm pull cams it back out.  A square top face cannot
             release at all; the only way past it is to bend the clip by hand.
    The 45 deg face is set so it meets the pocket's ledge snap_play below where
    the lid seats -- engagement is snap_bead - lip_fit, and nothing more."""
    p, e, fl = P["snap_bead"], P["snap_bead"] - P["lip_fit"], P["snap_flat"]
    z_a = Z_TOP - P["snap_from_lid"] - P["snap_play"] - e     # top of the flat
    return [(0.0, z_a - fl - P["snap_lead_h"]), (p, z_a - fl), (p, z_a), (0.0, z_a + p)]


def build_lid():
    plate = box(OUT_W, OUT_E, OUT_S, OUT_N, Z_TOP, Z_TOP + P["lid_t"])
    plate = fillet(plate.edges().filter_by(Axis.Z), P["corner_r"])

    f, t, zb = P["lip_fit"], P["lip_t"], Z_TOP - P["lip_h"]
    lo = box(IN_W + f, IN_E - f, IN_S + f, IN_N - f, zb, Z_TOP)
    li = box(IN_W + f + t, IN_E - f - t, IN_S + f + t, IN_N - f - t, zb - 1, Z_TOP + 1)
    ring = lo - li
    # Lead-in on the tongue.  v1's was square-ended, so it had to be lined up
    # before it would start to enter; half of "too tight" was that.
    ring = chamfer(ring.edges().group_by(Axis.Z)[0], P["lip_lead"])
    part = plate + ring

    # --- retention ---------------------------------------------------------
    # v1 hung a 14 x 1.6 mm cantilever off the plate with the bead 3.5 mm down.
    # A 3.5 mm lever arm is not a spring: seating 0.6 mm of interference on it
    # strains the root about 7 % (PLA gives up around 2) and needs a couple of
    # hundred newtons, and both bead faces were square, so it butted going on
    # and could not cam off at all.  That is a clip designed to snap off.
    #
    # Same pockets in the base, different spring.  Slot the lip away from the
    # plate over snap_span and the strip left below is a beam built in at BOTH
    # ends: span carries the deflection instead of a stubby lever, so the same
    # job costs ~1.2 % strain and single-digit newtons.  It prints as a plain
    # bridge between the two anchors -- no support, no overhang.
    g, at, bw = P["snap_gap"], P["snap_arm_t"], P["snap_bead_w"]
    for x, y in SNAPS:
        n = 1 if x > 0 else -1
        xo = x - f * n                                   # strip's outer face
        y0, y1 = snap_span(y)
        deep = n * (t + 1)
        part -= box(xo, xo - deep, y0, y1, Z_TOP - g, Z_TOP + 0.01)   # detach
        part -= box(xo - n * at, xo - deep, y0, y1, zb - 0.01, Z_TOP - g)  # thin
        pl = Plane(origin=(0, y + bw / 2, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
        part += extrude(pl * Polygon(*[(xo + n * q, z) for q, z in snap_profile()],
                                     align=None), amount=bw)

    # A flush seam has nothing to lift by.  One scoop per side, between the
    # snaps, so a fingernail can start the lid before the beads have to give.
    for x in (OUT_E, OUT_W):
        yp = sum(y for xx, y in SNAPS if (xx > 0) == (x > 0)) / 2.0
        part -= Pos(x, yp, Z_TOP) * Cylinder(P["pry_d"] / 2, P["pry_h"],
                                             align=(Align.CENTER, Align.CENTER, Align.MIN))

    # MODE button (SW2) -- reached with the plunger, and the two status LEDs
    xb, yb = mx(GEOM["parts"]["SW2"]["pos"][0]), my(GEOM["parts"]["SW2"]["pos"][1])
    part -= Pos(xb, yb, Z_TOP - 1) * Cylinder(P["btn_hole_d"] / 2, P["lid_t"] + 3,
                                              align=(Align.CENTER, Align.CENTER, Align.MIN))
    for ref in ("D4", "D5"):
        xl, yl = mx(GEOM["parts"][ref]["pos"][0]), my(GEOM["parts"][ref]["pos"][1])
        part -= Pos(xl, yl, Z_TOP - 1) * Cylinder(P["led_hole_d"] / 2, P["lid_t"] + 3,
                                                  align=(Align.CENTER, Align.CENTER, Align.MIN))
    # vents over the SuperMini / heatsink.  The lid is the only vented face:
    # side vents put holes at wire height and were dropped after v1.
    for i in range(-3, 4):
        x = mx(20.3) + i * (P["vent_w"] + P["vent_gap"])
        part -= box(x - P["vent_w"] / 2, x + P["vent_w"] / 2, -11, 11, Z_TOP - 1, Z_TOP + P["lid_t"] + 1)
    return part


def build_knob():
    """Push-fit knob for RV1.  Bore is 0.1 mm under the 6 mm knurled shaft."""
    d, h = P["knob_d"], P["knob_h"]
    k = Cylinder(d / 2, h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    k = chamfer(k.edges().filter_by(GeomType.CIRCLE).sort_by(Axis.Z)[-1], 1.0)
    for i in range(P["knob_flutes"]):
        a = 2 * pi * i / P["knob_flutes"]
        k -= Pos(d / 2 * cos(a), d / 2 * sin(a), -0.5) * Cylinder(
            1.1, h + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
    k -= Pos(0, 0, -0.01) * Cylinder(P["knob_bore"] / 2, P["knob_bore_h"],
                                     align=(Align.CENTER, Align.CENTER, Align.MIN))
    k -= Pos(0, d / 2 - 1.4, h - 1.0) * Box(1.6, 4.0, 2.2,
                                            align=(Align.CENTER, Align.CENTER, Align.MIN))
    return k


def build_plunger():
    """Loose pushrod for SW2: the lid sits ~18 mm above a 5 mm button."""
    d, head = P["btn_hole_d"] - 0.6, P["btn_hole_d"] + 3.5
    h = Z_TOP - (Z_PCB_TOP + 5.0) + 1.0
    p = Pos(0, 0, 0) * Cylinder(d / 2, h, align=(Align.CENTER, Align.CENTER, Align.MIN))
    p += Pos(0, 0, h) * Cylinder(head / 2, P["lid_t"] + 0.8, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return p


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print(f"interior  x {IN_W:+.1f} .. {IN_E:+.1f}   y {IN_S:+.1f} .. {IN_N:+.1f}   z 0 .. {Z_TOP:.1f}")
    print(f"exterior  {OUT_E - OUT_W:.1f} x {OUT_N - OUT_S:.1f} x {Z_TOP + P['floor'] + P['lid_t']:.1f} mm")
    print(f"PCB top at z = {Z_PCB_TOP:.1f}, pads {Z_PCB_BOT:.1f} mm tall, "
          f"cell {Z_CELL_BOT:+.1f} .. {Z_CELL_TOP:.1f} (bay ceiling {Z_BAY:.1f}), "
          f"floor under the cell {P['floor'] - P['bay_sink']:.1f} mm")
    print(f"bay  x {BAY_W:+.1f} .. {BAY_E:+.1f}   y {BAY_S:+.1f} .. {BAY_N:+.1f}   "
          f"free-standing wire post = {WIRE_POST}")
    for ref in ("X1", "Y1"):
        print(f"RCA {ref} slot centre x = {pad_centre(ref):+.2f} "
              f"(footprint origin {mx(GEOM['parts'][ref]['pos'][0]):+.2f})")
    for name, fn in (("base", build_base), ("lid", build_lid),
                     ("plunger", build_plunger), ("knob", build_knob)):
        part = fn()
        export_step(part, os.path.join(OUT, f"{name}.step"))
        export_stl(part, os.path.join(OUT, f"{name}.stl"))
        bb = part.bounding_box()
        print(f"  {name:8s} vol {part.volume / 1000:7.1f} cm3   "
              f"bbox {bb.size.X:.1f} x {bb.size.Y:.1f} x {bb.size.Z:.1f}")
