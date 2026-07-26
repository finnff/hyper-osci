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
# until the part arrives — slots accept BOTH 2.0 and 2.54 mm pin pitch).
# Copper sits outboard (drill offset inward) so pad-pad clearance is 0.22mm. ---
footprint("SW_Slide_SPDT_DualPitch",
          "SPDT slide switch, slotted outer pads accept 2.0mm or 2.54mm pitch (SS12/SK12 class) - VERIFY vs real part",
          [pad_tht(1, -2.27, 0, (2.5, 1.9), (1.44, 0.9), "oval", (-0.2, 0)),
           pad_tht(2, 0, 0, 1.6, 0.9),
           pad_tht(3, 2.27, 0, (2.5, 1.9), (1.44, 0.9), "oval", (0.2, 0)),
           rect(-4.5, -2.0, 4.5, 2.0),
           text("1", -2.47, -2.9, size=0.8),
           rect(-5.0, -2.5, 5.0, 2.5, "F.CrtYd", 0.05),
           text("dual-pitch 2.0/2.54", 0, 3.5, "F.Fab", 0.8)])

# --- 3. RV097NS 9mm pot, 5-pin (measurements.md: body 27.3x9.5x11.3, metal
# shaft, no knob). Terminals P2.5mm; bracket lugs from the common RV09 drawing
# -- lug positions are the least-certain numbers here => paper-doll VERIFY. ---
footprint("RV097NS_Vertical",
          "RV097NS 9mm pot B10K 5-pin: 1=CCW 2=wiper 3=CW + 2 bracket lugs. Lug geometry from RV09 drawing - VERIFY paper-doll",
          [pad_tht(1, -2.5, 0, 2.0, 1.0, "rect"),
           pad_tht(2, 0, 0, 2.0, 1.0),
           pad_tht(3, 2.5, 0, 2.0, 1.0),
           pad_tht(4, -4.75, -7.0, (2.4, 3.2), (1.2, 2.0), "oval"),
           pad_tht(5, 4.75, -7.0, (2.4, 3.2), (1.2, 2.0), "oval"),
           # body outline stands 0.45mm clear of the bracket-lug pads; at the
           # old +/-6.0 x -8.7 it grazed them (silk-over-copper)
           rect(-6.4, -9.1, 6.4, 1.2),
           text("shaft", 0, -10.4, "F.Fab", 0.8),
           rect(-6.9, -9.6, 6.9, 1.7, "F.CrtYd", 0.05)])

# --- 4. JP1 power-path escape hatch (pcb.md §4.4): 2.54mm 0R/solder jumper ---
footprint("SolderJumper_P2.54",
          "2-pad 2.54mm jumper: bridge with 0R or solder blob (JP1 escape hatch)",
          [pad_tht(1, 0, 0, 2.0, 1.0, "rect"),
           pad_tht(2, 2.54, 0, 2.0, 1.0),
           rect(-1.4, -1.4, 3.94, 1.4, "F.CrtYd", 0.05)])

# --- 5. J6B: single wire pad for the TP4056 IN+ sense wire (pcb.md §2, 1.0mm) ---
footprint("WirePad_D1.0",
          "Single plated wire pad, 1.0mm drill (TP4056 IN+ / VBUS_CHG sense wire)",
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
