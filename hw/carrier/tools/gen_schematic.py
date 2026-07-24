#!/usr/bin/env python3
"""Emit carrier.kicad_sch from design.py.

Style: every component is a plain box symbol with its pins in a left-hand
column; each pin carries a global label with the net name (or a no-connect
marker). Connectivity lives entirely in design.py — this file is only
presentation. Run from hw/carrier/:  python3 tools/gen_schematic.py
Verify with:  kicad-cli sch erc carrier.kicad_sch
"""
import os, sys, uuid
sys.path.insert(0, os.path.dirname(__file__))
from design import COMPONENTS, GROUPS, norm

NS = uuid.UUID("bfa2dcb2-90d5-4c42-9f4d-6f2ac2f0b001")
ROOT = str(uuid.uuid5(NS, "root-sheet"))

def uid(*key):
    return str(uuid.uuid5(NS, ":".join(key)))

FONT = "(effects (font (size 1.27 1.27)))"
HFONT = "(effects (font (size 1.27 1.27)) (hide yes))"
SFONT = "(effects (font (size 1.02 1.02)))"

# ---- symbol library ---------------------------------------------------------
# name -> list of (pad_number, pin_name)
SYMS = {
    "R":    [("1", "~"), ("2", "~")],
    "C":    [("1", "~"), ("2", "~")],
    "CP":   [("1", "+"), ("2", "-")],
    "D":    [("1", "K"), ("2", "A")],
    "LED":  [("1", "K"), ("2", "A")],
    "QPMOS": [("1", "G"), ("2", "S"), ("3", "D")],
    "QPNP": [("1", "C"), ("2", "B"), ("3", "E")],
    "TL431": [("1", "REF"), ("2", "A"), ("3", "K")],
    "SW_SPDT": [("1", "A"), ("2", "COM"), ("3", "B")],
    "SW_PUSH": [("1", "1"), ("2", "2")],
    "POT5": [("1", "CCW"), ("2", "W"), ("3", "CW"), ("4", "LUG"), ("5", "LUG")],
    "JP2":  [("1", "1"), ("2", "2")],
    "TPAD": [("1", "TP")],
    "PAD1": [("1", "PAD")],
    "HOLE": [("1", "PAD")],
    "CONN2": [(str(i), str(i)) for i in range(1, 3)],
    "CONN3": [(str(i), str(i)) for i in range(1, 4)],
    "CONN6": [(str(i), str(i)) for i in range(1, 7)],
    "CONN8": [(str(i), str(i)) for i in range(1, 9)],
    "CONN9": [(str(i), str(i)) for i in range(1, 10)],
}

def pin_ys(n):
    top = (n - 1) * 2.54 / 2.0
    return [round(top - i * 2.54, 2) for i in range(n)]

def lib_symbol(name, pins):
    ys = pin_ys(len(pins))
    ymax, ymin = ys[0] + 1.27, ys[-1] - 1.27
    out = [f'    (symbol "HY:{name}" (exclude_from_sim no) (in_bom yes) (on_board yes)',
           f'      (property "Reference" "U" (at 0 {ymax + 2.54} 0) {FONT})',
           f'      (property "Value" "{name}" (at 0 {ymin - 2.54} 0) {FONT})',
           f'      (property "Footprint" "" (at 0 0 0) {HFONT})',
           f'      (property "Datasheet" "" (at 0 0 0) {HFONT})',
           f'      (symbol "{name}_0_1"',
           f'        (rectangle (start -5.08 {ymax}) (end 5.08 {ymin})'
           f' (stroke (width 0.254) (type default)) (fill (type background)))',
           f'      )',
           f'      (symbol "{name}_1_1"']
    for (num, pname), y in zip(pins, ys):
        out.append(f'        (pin passive line (at -7.62 {y} 0) (length 2.54)'
                   f' (name "{pname}" {SFONT}) (number "{num}" {SFONT}))')
    out += ['      )', '    )']
    return "\n".join(out)

# ---- instances --------------------------------------------------------------
def place(ref, sx, sy):
    val, sym, fp, pinmap, dnp = norm(COMPONENTS[ref])
    pins = SYMS[sym]
    ys = pin_ys(len(pins))
    ymax = ys[0] + 1.27
    body = [f'  (symbol (lib_id "HY:{sym}") (at {sx} {sy} 0) (unit 1)',
            f'    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp {"yes" if dnp else "no"})',
            f'    (uuid "{uid("sym", ref)}")',
            f'    (property "Reference" "{ref}" (at {sx + 6.35} {sy - ymax} 0) {FONT})',
            f'    (property "Value" "{val}" (at {sx + 6.35} {sy - ymax + 2.54} 0) {FONT})',
            f'    (property "Footprint" "{fp}" (at {sx} {sy} 0) {HFONT})',
            f'    (property "Datasheet" "" (at {sx} {sy} 0) {HFONT})']
    for num, _ in pins:
        body.append(f'    (pin "{num}" (uuid "{uid("pin", ref, num)}"))')
    body.append(f'    (instances (project "carrier" (path "/{ROOT}"'
                f' (reference "{ref}") (unit 1))))')
    body.append('  )')
    labels = []
    for (num, _), py in zip(pins, ys):
        # pin connection point on the sheet (rotation 0: y flips)
        px, pyy = sx - 7.62, round(sy - py, 2)
        net = pinmap[num]
        if net is None:
            labels.append(f'  (no_connect (at {px} {pyy}) (uuid "{uid("nc", ref, num)}"))')
        else:
            labels.append(
                f'  (global_label "{net}" (shape passive) (at {px} {pyy} 180)'
                f' (fields_autoplaced yes) (effects (font (size 1.27 1.27)) (justify right))'
                f' (uuid "{uid("lbl", ref, num)}"))')
    return "\n".join(body), "\n".join(labels)

def group_text(title, x, y):
    return (f'  (text "{title}" (exclude_from_sim no) (at {x} {y} 0)'
            f' {FONT} (uuid "{uid("txt", title)}"))')

out = ['(kicad_sch (version 20231120) (generator "hyperosci")',
       f'  (uuid "{ROOT}")',
       '  (paper "A2")',
       '  (title_block (title "HYPEROSCI carrier v1.0") (date "2026-07-24")'
       ' (rev "1.0") (comment 1 "generated by tools/gen_schematic.py from tools/design.py"))',
       '  (lib_symbols']
used = sorted({norm(c)[1] for c in COMPONENTS.values()})
for s in used:
    out.append(lib_symbol(s, SYMS[s]))
out.append('  )')

placed = set()
x = 50.8
for title, refs in GROUPS:
    y = 38.1
    out.append(group_text(title, x, y - 12.7))
    for ref in refs:
        n = len(SYMS[norm(COMPONENTS[ref])[1]])
        h = (n - 1) * 2.54
        sy = y + h / 2.0
        sy = round(round(sy / 1.27) * 1.27, 2)
        b, l = place(ref, x, sy)
        out.append(b)
        out.append(l)
        placed.add(ref)
        y = sy + h / 2.0 + 15.24
    x += 76.2
missing = set(COMPONENTS) - placed
assert not missing, f"not placed: {missing}"

out.append(f'  (sheet_instances (path "/" (page "1")))')
out.append(')')

libout = ['(kicad_symbol_lib (version 20231120) (generator "hyperosci")']
for sname in used:
    libout.append(lib_symbol(sname, SYMS[sname]).replace(f'"HY:{sname}"', f'"{sname}"'))
libout.append(')')
base = os.path.join(os.path.dirname(__file__), "..")
with open(os.path.join(base, "HYPEROSCI.kicad_sym"), "w") as f:
    f.write("\n".join(libout) + "\n")
with open(os.path.join(base, "sym-lib-table"), "w") as f:
    f.write('(sym_lib_table\n  (version 7)\n  (lib (name "HY")(type "KiCad")(uri "${KIPRJMOD}/HYPEROSCI.kicad_sym")(options "")(descr "HYPEROSCI generated symbols"))\n)\n')

path = os.path.join(os.path.dirname(__file__), "..", "carrier.kicad_sch")
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print("wrote", os.path.abspath(path), f"({len(COMPONENTS)} components)")
