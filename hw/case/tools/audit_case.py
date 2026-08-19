#!/usr/bin/env python3
"""Does the case actually clear the board?  Answered by boolean, not by eye.

    hw/case/.venv/bin/python hw/case/tools/audit_case.py

Intersects base + lid against the populated carrier and fails on any non-zero
volume.  Same idea as hw/carrier/tools/audit_board.py: the check is the gate.

The board solid comes from `kicad-cli pcb export step`, which is authoritative
for the 32 footprints that have a 3D model and silent about the 19 that don't
(RV1, SW1, the RCA pads, the TP4056 pad rows, the mounting holes...).  The
socketed modules are not in the board file at all -- the carrier only carries
their sockets.  STANDIN below fills both gaps with measured boxes and cylinders;
anything listed there is as good as its measurement and no better.
"""
import os
import subprocess
import sys
from math import atan, degrees, pi, radians, tan

from build123d import *

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import case as C  # noqa: E402

OUT = os.path.join(HERE, "..", "out")
PCB = os.path.normpath(os.path.join(HERE, "..", "..", "carrier", "carrier.kicad_pcb"))
BOARD_STEP = os.path.join(OUT, "carrier.step")
TOL_MM3 = 1.0

SOCKET = 8.5          # 2.54 mm female header, measurements.md
HEATSINK = 7.0        # glued to the SuperMini after the docs were written --
                      # in no document and in no 3D model.  Measured 2026-08-19.

# ("box", x0, y0, x1, y1, z0, z1) in kicad mm, z relative to `ref`
# ("cyl", axis, kx, ky, d, z0, z1) -- axis "z" or "y" (y = horizontal, south)
STANDIN = {
    # --- socketed modules: not in the board file at all ---
    "ESP32-C3 SuperMini": ("pcb", "box", 9.3, 15.6, 33.1, 33.4, SOCKET, SOCKET + 1.0),
    "SuperMini heatsink": ("pcb", "box", 11.0, 18.0, 26.0, 31.0, SOCKET + 1.0, SOCKET + 1.0 + HEATSINK),
    "SuperMini USB-C":    ("pcb", "box", 31.0, 20.5, 35.5, 28.5, SOCKET + 1.0, SOCKET + 4.2),
    "GY-PCM5102A":        ("pcb", "box", 25.2, 0.6, 57.2, 18.0, SOCKET, SOCKET + 1.0),
    "PCM5102A jack":      ("pcb", "box", 50.0, 1.0, 58.8, 10.0, SOCKET + 1.0, SOCKET + 6.5),
    "TP4056":             ("pcb", "box", 44.2, 25.5, 69.4, 42.8, 3.0, 4.2),
    "TP4056 USB-C jack":  ("pcb", "box", 66.0, 30.0, 70.5, 36.9, 4.2, 7.4),
    # --- THT parts KiCad has no 3D model for ---
    "RV1 pot body":       ("pcb", "box", 31.8, 37.0, 41.8, 50.0, 0.0, 11.3),
    "RV1 bushing":        ("pcb", "cyl", "y", 36.8, 57.0, 7.0, 0.0, 0.0),
    "RV1 shaft":          ("pcb", "cyl", "y", 36.8, 65.0, 6.1, 0.0, 0.0),
    "SW1 slide":          ("pcb", "box", 45.7, 44.5, 54.3, 48.3, 0.0, 7.0),
    "SW1 actuator":       ("pcb", "box", 47.5, 48.3, 52.5, 53.5, 1.5, 5.5),
    # --- the LiPo, which lives in the floor bay ---
    "LP103454 cell":      ("floor", "cell", 0, 0, 0, 0, 0, 0),
}
# RV1 cylinders are horizontal: (kx, k_y_end, dia) -- they run south from the
# board edge at kicad y=50 to k_y_end at the pot's shaft axis height.
POT_AXIS_Z = C.P["pot_axis_z"]


def board_solid():
    if not os.path.exists(BOARD_STEP) or os.path.getmtime(BOARD_STEP) < os.path.getmtime(PCB):
        os.makedirs(OUT, exist_ok=True)
        subprocess.run(["kicad-cli", "pcb", "export", "step", "--force", "--no-unspecified",
                        "--subst-models", "-o", BOARD_STEP, PCB], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("regenerated carrier.step from the board file")
    imported = import_step(BOARD_STEP)
    # kicad STEP: x = kicad_x, y = -kicad_y, z = 0 at the PCB's bottom face.
    # Move every solid explicitly -- relocating the compound alone leaves the
    # children where they were and the intersection silently tests thin air.
    loc = Location((-35.0, 25.0, C.Z_PCB_BOT))
    return Compound(children=[s.moved(loc) for s in imported.solids()])


def standin(spec):
    ref, kind = spec[0], spec[1]
    z0ref = C.Z_PCB_TOP if ref == "pcb" else 0.0
    if kind == "box":
        _, _, x0, y0, x1, y1, z0, z1 = spec
        return C.box(C.mx(x0), C.mx(x1), C.my(y0), C.my(y1), z0ref + z0, z0ref + z1)
    if kind == "cyl":                       # horizontal, running south from y=50
        _, _, _, kx, ky_end, dia, _, _ = spec
        length = ky_end - 50.0
        return Pos(C.mx(kx), C.my(50.0), C.Z_PCB_TOP + POT_AXIS_Z) * Rot(90, 0, 0) * \
            Cylinder(dia / 2, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
    if kind == "cell":                      # the LiPo, sitting on the case floor
        cl, cw = C.P["cell_l"], C.P["cell_w"]
        e = C.BAY_E - C.P["cell_slack"] / 2
        return C.box(e - cl, e, -cw / 2, cw / 2, C.Z_CELL_BOT, C.Z_CELL_TOP)
    raise ValueError(kind)


# Escape probes.  "Nothing collides" is only half the question -- an opening in
# the wrong place also collides with nothing, and a hole that is the right size
# in the wall can still be impossible to get the board into.  Each probe is a
# volume something must be able to occupy -- on its way out, or on its way in
# during assembly -- and every one must be clear of the base.
def probes():
    """name -> solid.  kicad mm where the board is the natural reference,
    model mm where the case is."""
    Zt, Zb = C.Z_PCB_TOP, C.Z_PCB_BOT
    p = {}

    def k(name, x0, y0, x1, y1, z0, z1):        # kicad mm, z above the PCB top
        p[name] = C.box(C.mx(x0), C.mx(x1), C.my(y0), C.my(y1), Zt + z0, Zt + z1)

    k("USB-C plug + overmold", 69.0, 28.4, 88.0, 38.4, 2.4, 9.4)
    k("mic pigtail out E",     66.0,  6.9, 88.0, 11.9, 1.0, 6.0)
    k("finger on SW1",         47.0, 48.0, 53.0, 62.0, 0.5, 5.5)
    k("RV1 knob swept vol",    30.3, 57.4, 43.3, 70.0, 0.5, 13.5)

    # The RCA leads leave from the pad *pair*, whose centre is 2.7 mm off the
    # footprint origin -- v1 aimed both slots at the origin and the leads came
    # out skewed.  Derive the probe from the same span the slot is cut from.
    for ref in ("X1", "Y1"):
        pd = C.GEOM["parts"][ref]["pads"]
        cx, w = (pd[0] + pd[2]) / 2, C.P["rca_slot_w"] - 1.0
        k(f"RCA {ref} leads out N", cx - w / 2, -20.0, cx + w / 2, 2.6, 1.0, 6.0)

    # The pot's bushing crosses the wall line, so the board cannot be threaded
    # in sideways -- it has to drop straight down.  This is the swept volume of
    # that drop, and it is why the pot opening is a slot and not a hole.
    xr = C.mx(C.GEOM["parts"]["RV1"]["pos"][0])
    z_axis, rb = Zt + C.P["pot_axis_z"], 3.75          # 7 mm bushing + 0.5 mm
    y0, y1 = C.IN_S - C.P["wall"] - 1, C.IN_S + 1
    # the swept volume is a capsule, not a box: round below the axis where the
    # bushing seats, straight-sided above it where it dropped through
    p["RV1 bushing drop-in"] = (
        Pos(xr, y1, z_axis) * Rot(90, 0, 0) * Cylinder(rb, y1 - y0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        + C.box(xr - rb, xr + rb, y0, y1, z_axis, C.Z_TOP + 1))

    # The battery lead rises in the channel east of the cell, then the plug
    # reaches J8 from the south.  Both are model-space: they follow the bay.
    # two legs: out of the recess at cell level, then south towards J8 at floor
    # level.  One box spanning both would demand recess depth where there is no
    # recess, which is a probe bug, not a case fault.
    w = C.P["bay_sink_e"]
    p["battery lead riser"] = (
        C.box(C.BAY_E, C.BAY_E + w, C.BAY_S, C.BAY_S + 3.0, C.Z_CELL_BOT, Zb)
        + C.box(C.BAY_E, C.BAY_E + w, C.BAY_S - 3.0, C.BAY_S, 0, Zb))
    jb = C.GEOM["parts"]["J8"]["body"]
    p["JST plug tail"] = C.box(C.mx(jb[0]), C.mx(jb[2]), C.my(jb[3]) - 8.0, C.my(jb[3]) + 0.5,
                               Zt, Zt + 6.0)
    return p


E_PLA, MU = 3000.0, 0.35        # MPa, and PLA-on-PLA sliding friction


def snap_report(lid):
    """What the lid actually asks of its clips.

    v1 passed every collision test and was still a bad snap: the numbers that
    matter are how far the spring has to move, how long it is while it moves,
    and how thick -- and none of those show up as a boolean overlap.  So
    measure them off the solid rather than trusting the parameter block.
    """
    def sect(y, z, dy=0.4, dz=0.05):
        s = lid & C.box(20, 45, y - dy / 2, y + dy / 2, z - dz / 2, z + dz / 2)
        return None if s is None or s.volume < 1e-9 else s.bounding_box()

    x, y = C.SNAPS[0]
    z_strip = C.Z_TOP - C.P["snap_from_lid"] + 0.5        # above the bead
    y0, y1 = C.snap_span(y)

    # Free span, measured as the void itself: fill the slice of lip the slot
    # lives in and subtract the lid, and what is left IS the slot.  (Walking a
    # probe along it and counting empty samples quantises the span to the step,
    # and the span is squared and cubed downstream -- a 0.5 mm rounding there
    # moved the reported strain by 0.15 %.)
    xo = C.SNAPS[0][0] - C.P["lip_fit"]
    slab = C.box(xo, xo - C.P["lip_t"] + 0.05, y0 - 4, y1 + 4,
                 C.Z_TOP - C.P["snap_gap"] + 0.05, C.Z_TOP - 0.05)
    void = slab - lid
    span = void.bounding_box().size.Y

    b = sect(y, C.Z_TOP - C.P["snap_from_lid"] - 0.5)     # through the bead
    st = sect(y1 - 1.0, z_strip)                          # strip, clear of it
    defl = b.max.X - C.IN_E                               # past the wall face
    t, h = st.max.X - st.min.X, C.P["lip_h"] - C.P["snap_gap"]
    bw = C.P["snap_bead_w"]
    L = (span - bw) / 2.0                                 # one fixed-guided half

    I = h * t ** 3 / 12.0
    F = 2 * 12 * E_PLA * I * defl / L ** 3                # both halves, N
    strain = 3 * defl * t / L ** 2
    lead = atan(C.P["snap_bead"] / C.P["snap_lead_h"])

    def cam(a):
        return F * (tan(a) + MU) / (1 - MU * tan(a))

    print(f"lid snap: span {span:.1f} mm bridged, strip {t:.2f} x {h:.2f} mm, "
          f"bead {bw:.1f} mm wide, deflection {defl:.2f} mm")
    # 2 % is the usual number quoted for PLA, but which 2 % matters here: the
    # strip's long axis lies in the layer plane and it bends about a vertical
    # axis, so the tension runs ALONG the extrusions, not across the layers.
    # v1's cantilever bent about a horizontal axis and pulled its root apart
    # layer from layer, which is the same strain against a much lower limit.
    print(f"          bending strain {100 * strain:.2f} % along the extrusions "
          f"(PLA yields near 2, and this is its strong direction), "
          f"spring {F:.1f} N at full deflection")
    print(f"          ~{cam(lead):.0f} N to push one on ({degrees(lead):.0f} deg lead-in), "
          f"~{cam(radians(45)):.0f} N to pull one off (45 deg release)")

    # And the part no static check catches: drive the lid down through the base
    # and watch what actually rubs.  A lid that only clears when seated is a lid
    # you cannot fit, and the seated pose is the one pose that always looks fine.
    base, worst, where = C.build_base(), 0.0, None
    for i in range(0, 13):        # from 0: seated is a pose that can fail too
        hit = base & lid.moved(Location((0, 0, i * 0.5)))
        if hit is None or hit.volume < 1e-6:
            continue
        bb = hit.bounding_box()
        d = max(bb.max.X - C.IN_E, C.IN_W - bb.min.X,
                bb.max.Y - C.IN_N, C.IN_S - bb.min.Y)
        if d > worst:
            worst, where = d, (i * 0.5, bb)
    h, bb = where
    print(f"          peak interference on the way in {worst:.2f} mm at {h:.1f} mm of lift, "
          f"z {bb.min.Z:.1f}..{bb.max.Z:.1f} -- all of it the beads"
          if abs(worst - defl) < 0.01 else
          f"          WARNING: peak interference {worst:.2f} mm > the {defl:.2f} mm "
          f"the beads are meant to give, at {h:.1f} mm of lift")

    # The bead's lead-in has to be fully retracted by the time it reaches the
    # pocket's lower lip, or it wedges against solid wall below the pocket
    # instead of dropping in.  snap_play and snap_lead_h both eat this margin,
    # and they eat it silently -- 0.06 mm survives the boolean and not a print.
    pr = C.snap_profile()
    z_cross = pr[0][1] + (C.P["lip_fit"] / C.P["snap_bead"]) * (pr[1][1] - pr[0][1])
    gap = z_cross - (C.Z_TOP - C.P["snap_from_lid"] - 2.2)
    print(f"          lead-in retracted {gap:+.2f} mm above the pocket's lower lip"
          + ("" if gap > 0.15 else "   <-- thin; a fat print will wedge"))


def main():
    print(f"board STEP + {len(STANDIN)} stand-ins vs base + lid\n")
    obstacles = [("board (kicad STEP)", board_solid())]
    obstacles += [(n, standin(s)) for n, s in STANDIN.items()]

    fails = []
    for case_name, case_part in (("base", C.build_base()), ("lid", C.build_lid())):
        for obs_name, obs in obstacles:
            hit = case_part & obs
            v = hit.volume if hit is not None else 0.0
            tag = "FAIL" if v > TOL_MM3 else "ok  "
            if v > TOL_MM3:
                fails.append((case_name, obs_name, v))
            print(f"  {tag}  {case_name:5s} x {obs_name:22s} {v:9.2f} mm3")

    print()
    base = C.build_base()
    for name, sol in probes().items():
        v = (base & sol)
        v = v.volume if v is not None else 0.0
        tag = "FAIL" if v > TOL_MM3 else "ok  "
        if v > TOL_MM3:
            fails.append(("escape", name, v))
        print(f"  {tag}  escape {name:24s} {v:9.2f} mm3")

    print()
    print(f"pot shaft protrudes {abs(C.my(65.0) - C.OUT_S):.1f} mm past the south wall "
          f"(outer face y={C.OUT_S:+.1f})")
    print(f"SW1 actuator tip y={C.my(53.5):+.1f}, south wall inner face y={C.IN_S:+.1f} "
          f"-> {C.my(53.5) - C.IN_S:+.1f} mm into the wall")
    tall = max(s[-1] for s in STANDIN.values() if s[1] == "box" and s[0] == "pcb")
    print(f"headroom above PCB {C.P['above_pcb']:.1f} mm vs tallest stand-in {tall:.1f} mm "
          f"-> {C.P['above_pcb'] - tall:+.1f} mm")
    brd = import_step(BOARD_STEP).bounding_box()
    print(f"board underside reaches z={C.Z_PCB_BOT + brd.min.Z:.2f}, cell top z={C.Z_CELL_TOP:.2f} "
          f"-> {C.Z_PCB_BOT + brd.min.Z - C.Z_CELL_TOP:+.2f} mm  "
          f"({C.Z_PCB_BOT + brd.min.Z - C.Z_BAY:+.2f} mm to a swollen cell)")
    ann_r, dep = 6.0, C.P["insert_depth"]
    full = pi * (ann_r ** 2 - (C.P["insert_d"] / 2) ** 2) * dep
    sup = []
    for ref, (x, y) in C.HOLES.items():
        cyl = Pos(x, y, C.Z_PCB_BOT - dep) * Cylinder(ann_r, dep, align=(Align.CENTER, Align.CENTER, Align.MIN))
        hit = base & cyl
        sup.append(f"{ref} {100 * (hit.volume if hit is not None else 0.0) / full:3.0f}%")
    print(f"insert support, material within a {2 * ann_r:.0f} mm circle over the insert's "
          f"{dep:.1f} mm: {'  '.join(sup)}")
    print(f"cell bay x {C.BAY_W:+.1f}..{C.BAY_E:+.1f}, free-standing wire post {C.WIRE_POST} at "
          f"{C.HOLES[C.WIRE_POST]}, lead channel {C.BAY_E:+.1f}..{C.HOLES[C.WIRE_POST][0] - C.P['pad_r']:+.1f}")
    for ref in ("X1", "Y1"):
        pd = C.GEOM["parts"][ref]["pads"]
        print(f"RCA {ref}: pads span {pd[2] - pd[0]:.2f} mm, slot {C.P['rca_slot_w']:.1f} mm "
              f"centred on the pair (origin offset {C.mx((pd[0] + pd[2]) / 2) - C.mx(C.GEOM['parts'][ref]['pos'][0]):+.2f} mm)")
    snap_report(C.build_lid())

    if fails:
        sys.exit(f"\n{len(fails)} collision(s)")
    print("\nno collisions")


if __name__ == "__main__":
    main()
