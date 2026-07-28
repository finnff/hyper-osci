#!/usr/bin/env python3
"""Generate the HYPEROSCI.pretty custom footprints (the ones no stock lib has).

Every dimension here traces to docs/hardware/measurements.md, pcb.md §2/§5, or
— for the module-mating footprints — to the photogrammetry in `hw/pin_locs`
via `measured.py`.  Re-run after editing, or after re-measuring a module:

    python3 tools/gen_footprints.py     (from hw/carrier/)
"""
import os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measured

OUT = os.path.join(os.path.dirname(__file__), "..", "HYPEROSCI.pretty")
os.makedirs(OUT, exist_ok=True)

def uid():
    return str(uuid.uuid4())

def prop(name, val, y, layer="F.Fab", hide=False):
    h = "\n\t\t(hide yes)" if hide else ""
    return f'''\t(property "{name}" "{val}"
\t\t(at 0 {y} 0){h}
\t\t(layer "{layer}")
\t\t(uuid "{uid()}")
\t\t(effects (font (size 1 1) (thickness 0.15)))
\t)'''

def pad_tht(num, x, y, size, drill, shape="circle", drill_off=None):
    if isinstance(drill, tuple):
        d = f"(drill oval {drill[0]} {drill[1]}"
    else:
        d = f"(drill {drill}"
    d += f" (offset {drill_off[0]} {drill_off[1]}))" if drill_off else ")"
    if isinstance(size, tuple):
        s = f"(size {size[0]} {size[1]})"
        shape = "oval" if shape == "circle" else shape
    else:
        s = f"(size {size} {size})"
    return f'''\t(pad "{num}" thru_hole {shape}
\t\t(at {x} {y})
\t\t{s}
\t\t{d}
\t\t(layers "*.Cu" "*.Mask")
\t\t(uuid "{uid()}")
\t)'''

def pad_smd(num, x, y, w, h):
    return f'''\t(pad "{num}" smd rect
\t\t(at {x} {y})
\t\t(size {w} {h})
\t\t(layers "F.Cu" "F.Paste" "F.Mask")
\t\t(uuid "{uid()}")
\t)'''

def line(x1, y1, x2, y2, layer="F.SilkS", w=0.12):
    return f'''\t(fp_line
\t\t(start {x1} {y1})
\t\t(end {x2} {y2})
\t\t(stroke (width {w}) (type solid))
\t\t(layer "{layer}")
\t\t(uuid "{uid()}")
\t)'''

def rect(x1, y1, x2, y2, layer="F.SilkS", w=0.12):
    return "\n".join([line(x1, y1, x2, y1, layer, w), line(x2, y1, x2, y2, layer, w),
                      line(x2, y2, x1, y2, layer, w), line(x1, y2, x1, y1, layer, w)])

def text(s, x, y, layer="F.SilkS", size=0.8):
    return f'''\t(fp_text user "{s}"
\t\t(at {x} {y} 0)
\t\t(layer "{layer}")
\t\t(uuid "{uid()}")
\t\t(effects (font (size {size} {size}) (thickness 0.15)))
\t)'''

def footprint(name, descr, body):
    parts = [f'(footprint "{name}"',
             '\t(version 20240108)',
             '\t(generator "hyperosci")',
             '\t(layer "F.Cu")',
             f'\t(descr "{descr}")',
             '\t(attr through_hole)',
             prop("Reference", "REF**", -1.0 - 2, "F.SilkS"),
             prop("Value", name, 2.5, "F.Fab"),
             prop("Datasheet", "", 0, hide=True),
             prop("Description", "", 0, hide=True)]
    parts += body
    parts.append(")")
    with open(os.path.join(OUT, name + ".kicad_mod"), "w") as f:
        f.write("\n".join(parts) + "\n")
    print("wrote", name)

# --- 1. RCA flying-lead pad pair (pcb.md §2 X/Y): signal + AGND solder pads ---
# Signal pad takes the stripped RCA core (1.5 mm drill), ground pad the shield
# bundle (2.0 mm drill). 5.08 mm apart, ground south (toward board interior).
footprint("RCA_FlyingLead_Pads",
          "Flying-lead solder pads for reused ~50cm RCA cable: 1=signal 2=shield/AGND",
# The S/G labels sit ON the pad axis, beyond each end, rather than beside the
# pads: X1 and Y1 are rotated 270 and 90, so a label offset sideways lands off
# the north board edge on one of them whichever side you pick.
          [pad_tht(1, 0, 0, 3.0, 1.5),
           pad_tht(2, 0, 5.08, 3.6, 2.0),
           text("S", 0, -2.4), text("G", 0, 7.5),
           # courtyard stays on the solder blobs (it is a mechanical keep-out);
           # the S/G silk is allowed to sit just outside it.  Widening it to
           # swallow the text made Y1's courtyard collide with R11.
           rect(-2.1, -2.1, 2.1, 7.2, "F.CrtYd", 0.05)])

# --- 2. SW1 dual-pitch SPDT slide (pcb.md §5: SK12D07-class 1A, pitch unknown
# when this was drawn — slots accept BOTH 2.0 and 2.54 mm pin pitch).
#
# RESOLVED 2026-07-28, and the slot earned its keep: the ordered SS12D00 is
# 2.5 mm pitch, which is NEITHER of the two values above.  A hole drilled for
# either guess would have been wrong; the slot puts the pin 0.05 mm off centre.
# Body is 8.5 x 3.7 on the pin centreline, inside the 9.0 x 4.0 silk.  The
# descr below still says "VERIFY vs real part" and is deliberately left alone:
# editing it would only take effect on a re-run, and re-running this rewrites
# every UUID, which forces gen_board + route and throws away seed 33.  The
# verified numbers live in layout-notes.md VERIFY item 2, not in a string.
#
# The slot LENGTH is set by JLCPCB, not by us: a plated slot must be at least
# 2x as long as it is wide, or the fab raises a DFM query (costly against the
# order date) or silently converts it to a round hole — and a round 0.9mm hole
# at one fixed x accepts NEITHER pitch.  At 0.90mm wide that forces >= 1.80mm
# long, up from the 1.44 this footprint carried until 2026-07-28 (aspect 1.6).
#
# A 1.80 x 0.90 slot's two end arcs are 0.90mm apart, so a pin may sit anywhere
# within +/-0.45mm of the hole centre.  Centring at 2.45 puts that window at
# 2.00..2.90 from the switch's middle pin, which covers a 2.0mm-pitch part and
# a 2.54 one (SS12D00) alike.  2.45 is the LARGEST centre that still reaches
# 2.00, and largest is what we want: it drags the copper as far outboard as the
# pitch allows.  The old drill offset was buying that same headroom and is no
# longer needed.
#
# The PAD stays 2.5mm long, not 2.9.  Growing it with the slot leaves only
# 0.20mm to the centre pad, and while that clears JLCPCB's 0.127mm floor it is
# under this board's own 0.25mm netclass clearance — four DRC violations, on
# the gate that has to read zero before gerbers go out.  At 2.5 the gap is
# 0.40mm and the annular ring is still 0.35mm on the long axis, 0.50 across
# (JLCPCB wants 0.15). ---
footprint("SW_Slide_SPDT_DualPitch",
          "SPDT slide switch, slotted outer pads accept 2.0mm or 2.54mm pitch (SS12/SK12 class) - VERIFY vs real part",
          [pad_tht(1, -2.45, 0, (2.5, 1.9), (1.80, 0.9), "oval"),
           pad_tht(2, 0, 0, 1.6, 0.9),
           pad_tht(3, 2.45, 0, (2.5, 1.9), (1.80, 0.9), "oval"),
           rect(-4.5, -2.0, 4.5, 2.0),
           text("1", -2.45, -2.9, size=0.8),
           rect(-5.0, -2.5, 5.0, 2.5, "F.CrtYd", 0.05),
           text("dual-pitch 2.0/2.54", 0, 3.5, "F.Fab", 0.8)])

# --- 3. RV097NS 9mm pot, 5-pin "MONO (with Switch)" -- RIGHT-ANGLE, side-adjust.
#
# CORRECTED 2026-07-27.  Everything drawn here before that date was a VERTICAL
# pot with two mechanical bracket lugs (pads at +/-4.75, -7.0, oval 1.2x2.0
# slots) copied from "the common RV09 drawing".  The real part has NO bracket
# lugs at all: row 2 is a genuine SPST switch on the same 1.0mm drill as the
# rest, 2.25mm further in and 0.75mm further south than we had it.  The part
# would not have gone into the board.  gen_footprints had flagged those lug
# numbers as the least-certain on the board since the first commit, and pcb.md
# listed the paper-doll check as order-gating; this is that check, done.
#
# Source: the seller's mechanical drawing ("5Pin Mono (with Switch)",
# https://i.ebayimg.com/images/g/3KIAAOSwd9VjM8EZ/s-l1600.webp), cross-checked
# against KiCad's stock Potentiometer_Alps_RK097_Single_Horizontal_Switch
# (drawn from ALPS rk097.pdf).  The two agree to the last decimal.
#
# MOUNTING HOLE DETAIL, walking back from the mounting surface:
#     mounting surface --5.00--> pot row   (1,2,3 @ 2.5 pitch, 5.0 span)
#                      --6.25--> switch row (S1,S2 5.0 apart, in line with 1/3)
#     5 holes dia 1.0 +0.2/-0.0, for 0.8mm flat pins.
# Body 9.5 wide x 13.0 deep x ~11.35 tall (6.5 shaft axis + 4.85); shaft dia 6.0
# on an M7x0.75 bushing, 15mm proud of the mounting surface.  The 9.5 x 11.35
# cross-section is exactly the 2026-07-18 calipers (9.5 x 11.3), which is the
# independent confirmation that this drawing is our part.
#
# ORIENTATION HERE: pot row on y=0, body extends NORTH (-y), mounting surface
# at y=+5.0.  gen_board puts that surface ON the board's south edge (y=50), so
# the bushing and shaft hang off the edge and RV1's pot row sits at y=45.0.
# Pads 4/5 are the switch, both parked on GND -- see design.py for why.
_MNT = 5.0                                  # mounting surface, south of the pot row
footprint("RV097NS_Horizontal_Switch",
          "RV097NS 9mm pot B10K, 5-pin mono WITH SWITCH, right-angle. 1=CCW 2=wiper 3=CW, 4/5=SPST. Mounting surface +5.0mm south of the pot row - put it ON the board edge",
          [pad_tht(1, -2.5, 0, 2.0, 1.0, "rect"),
           pad_tht(2, 0, 0, 2.0, 1.0),
           pad_tht(3, 2.5, 0, 2.0, 1.0),
           pad_tht(4, -2.5, -6.25, 2.0, 1.0),
           pad_tht(5, 2.5, -6.25, 2.0, 1.0),
           # Silk: three sides only.  The fourth side IS the mounting surface,
           # which lands on Edge.Cuts -- silk there would be a plot violation,
           # so it stops 0.4mm short and the board edge draws the face.
           line(-4.75, -8.0, 4.75, -8.0),
           line(-4.75, -8.0, -4.75, _MNT - 0.4),
           line(4.75, -8.0, 4.75, _MNT - 0.4),
           text("SW", 0, -7.1, "F.SilkS", 0.8),   # 0.8 is the board minimum
           # F.Fab carries the true body plus the bushing and shaft, which are
           # off-board.  Fab is documentation only (not in the JLCPCB set) and
           # is what the 1:1 paper doll plots, so the shaft belongs here.
           rect(-4.75, -8.0, 4.75, _MNT, "F.Fab", 0.1),
           rect(-3.5, _MNT, 3.5, _MNT + 5.0, "F.Fab", 0.1),      # M7x0.75 bushing
           rect(-3.0, _MNT + 5.0, 3.0, _MNT + 15.0, "F.Fab", 0.1),  # dia 6 shaft
           text("shaft 15mm, off-board", 0, _MNT + 16.2, "F.Fab", 0.8),
           # Courtyard covers the on-board body only; the shaft overhangs the
           # board edge where nothing can collide with it.
           rect(-5.0, -8.3, 5.0, _MNT + 0.3, "F.CrtYd", 0.05)])

# --- 4. JP1 power-path escape hatch (pcb.md §4.4): 2.54mm 0R/solder jumper ---
footprint("SolderJumper_P2.54",
          "2-pad 2.54mm jumper: bridge with 0R or solder blob (JP1 escape hatch)",
          [pad_tht(1, 0, 0, 2.0, 1.0, "rect"),
           pad_tht(2, 2.54, 0, 2.0, 1.0),
           rect(-1.4, -1.4, 3.94, 1.4, "F.CrtYd", 0.05)])

# --- 5. TP4056 USB-C-end mount pins (J9 = IN+, J10 = IN-) ------------------
# Same 1.0 mm hole as J5/J6, and it takes the same machined single-pin socket.
# This started as one wire pad for the IN+ sense tap; it is now a PAIR, and the
# second one is on no net at all, because the job is mechanical: J5/J6 sit at
# one end of the module and a USB-C plug is pushed into the other, 21.65 mm
# away.  Four pins in a single row resist that only by bending.  See pcb.md §5.
footprint("TP4056_MountPin",
          "TP4056 USB-C-end corner pad: 1.0mm drill for a machined single-pin "
          "socket. J9 = IN+ (VBUS_CHG sense tap, '+' silk on the module); "
          "J10 = IN- and is NC ON PURPOSE - it anchors the module's far end "
          "without assuming IN- and OUT- are the same node. Both are mount "
          "points first: without them the module is a 21.65mm cantilever.",
          [pad_tht(1, 0, 0, 2.2, 1.0),
           rect(-1.4, -1.4, 1.4, 1.4, "F.CrtYd", 0.05)])

# --- 6. D2 dual footprint (pcb.md §5): SMA (SS34) overlaid on DO-41 THT
# (1N5817/1N5822). Pad 1 = cathode on both. Fit ONE part only. ---
footprint("D_Dual_SMA_DO41",
          "Dual diode footprint: SMA (SS34) pads + DO-41 THT holes (1N5817/22). Pad1=cathode. Fit one.",
# Both pad sets fill the middle of this footprint, so the body outline lives on
# F.Fab only; the silk carries a single cathode bar parked north of every pad
# (an outline drawn round the part crossed the SMA lands - silk over copper).
          [pad_tht(1, -5.08, 0, 2.8, 1.5, "rect"),
           pad_tht(2, 5.08, 0, 2.8, 1.5),
           pad_smd(1, -2.4, 0, 2.5, 1.8),
           pad_smd(2, 2.4, 0, 2.5, 1.8),
           line(-6.6, -2.15, -3.4, -2.15, w=0.3),   # cathode band
           line(-3.9, -1.3, -3.9, 1.3, "F.Fab", 0.25),
           rect(-3.4, -1.4, 3.4, 1.4, "F.Fab", 0.1),
           rect(-6.6, -2.6, 6.6, 1.9, "F.CrtYd", 0.05)])

# --- 7. TP4056 output-pad landings (J5/J6) ---------------------------------
# measurements.md called these "~2.54 grid"; the photogrammetry says otherwise.
# The four pads on the module's 17.3 mm short edge sit at 0 / 3.53 / 10.96 /
# 14.07 mm — two pairs with a 7.43 mm gutter between them, spanning nearly the
# whole edge.  No stock 2.54 mm socket mates with that, so J5 and J6 become
# two pad pairs at the measured pitches; fit four single machined-pin sockets
# (or, giving up removability, solder wires).
#
# Tolerance: the module's own holes are 2.0 mm on a 0.64 mm square pin, so each
# pin may sit up to +/-0.55 mm off nominal before it fouls the barrel — well
# clear of the ~0.2 mm spread in the measurement.
_TP_PAIRS = [("TP4056_Pads_OUTminus_Bminus", measured.TP4056_PAIR_A_PITCH,
              ("OUT-", "B-"), "north pair: OUT- (system GND) then B-"),
             ("TP4056_Pads_Bplus_OUTplus", measured.TP4056_PAIR_B_PITCH,
              ("B+", "OUT+"), "south pair: B+ then OUT+ (switched battery)")]
for _name, _pitch, _labels, _what in _TP_PAIRS:
    footprint(_name,
              f"TP4056 output pads, {_what}. Pitch {_pitch:.3f}mm MEASURED "
              f"(hw/pin_locs/TP4056) - NOT a 2.54 grid. 1.0mm drill takes a "
              f"machined single socket or a soldered wire; module hole slack "
              f"is +/-{measured.TP4056_SLACK_MM:.2f}mm.",
              # The OUT-/B-/B+/OUT+ legends are NOT in the footprint: gen_board
              # places them, because only it knows what else is competing for
              # the strip of silk west of these pads.
              [pad_tht(1, 0, 0, 2.0, 1.0, "rect"),
               pad_tht(2, 0, _pitch, 2.0, 1.0),
               text(_labels[0], 0, -2.2, "F.Fab", 0.8),
               text(_labels[1], 0, _pitch + 2.2, "F.Fab", 0.8),
               rect(-1.5, -1.5, 1.5, _pitch + 1.5, "F.CrtYd", 0.05)])

print("done ->", os.path.abspath(OUT))
