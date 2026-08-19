#!/usr/bin/env python3
"""carrier.kicad_pcb -> board-geom.json  (the case's only view of the board).

Run with KiCad's interpreter, which owns pcbnew:

    /usr/bin/python3 hw/case/tools/export_geom.py

Nothing here is typed by hand: every coordinate is read out of the board file,
so if the carrier is ever re-routed or a part moves, the case follows. The
one thing this cannot give us is Z -- KiCad has no 3D model for RV1, SW1, the
RCA pads or the three socketed modules (19 refs), so heights live in case.py
as measured constants. See README.md.
"""
import json
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "..", "..", "carrier", "carrier.kicad_pcb")
OUT = os.path.join(HERE, "..", "board-geom.json")


def main():
    board = pcbnew.LoadBoard(PCB)
    if board is None:
        sys.exit(f"could not load {PCB}")

    bb = board.GetBoardEdgesBoundingBox()
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}

    def pos(ref):
        p = fps[ref].GetPosition()
        return [round(p.x / 1e6, 3), round(p.y / 1e6, 3)]

    def body(ref):
        """Courtyard bbox in mm -- the part's real plan extent, not its pads."""
        cy = fps[ref].GetCourtyard(pcbnew.F_CrtYd)
        b = cy.BBox()
        return [round(b.GetLeft() / 1e6, 3), round(b.GetTop() / 1e6, 3),
                round(b.GetRight() / 1e6, 3), round(b.GetBottom() / 1e6, 3)]

    def pad_span(ref):
        """Bbox of the footprint's copper, pad sizes included.

        The X/Y RCA landings are pad *pairs* whose footprint origin sits on the
        signal pad, 2.54 mm off the pair's centre -- v1 centred the wall slots
        on the origin and the flying leads came out 2.5 mm skewed, pulling on
        the joints.  The slot is centred on this span instead.
        """
        xs, ys = [], []
        for p in fps[ref].Pads():
            q, sz = p.GetPosition(), p.GetSize()
            xs += [(q.x - sz.x / 2) / 1e6, (q.x + sz.x / 2) / 1e6]
            ys += [(q.y - sz.y / 2) / 1e6, (q.y + sz.y / 2) / 1e6]
        return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]

    def pad(ref, num):
        for p in fps[ref].Pads():
            if p.GetNumber() == num:
                q = p.GetPosition()
                return [round(q.x / 1e6, 3), round(q.y / 1e6, 3)]
        raise KeyError(f"{ref}.{num}")

    # The TP4056's USB-C jack is centred across the module; the module's span
    # is fixed by its two pad rows (J5 north, J6 south) plus the mount row.
    tp_y = (pad("J5", "1")[1] + pad("J6", "2")[1]) / 2.0

    geom = {
        "_source": "hw/carrier/carrier.kicad_pcb",
        "_generated_by": "hw/case/tools/export_geom.py",
        "board": {
            "x0": round(bb.GetLeft() / 1e6, 3), "y0": round(bb.GetTop() / 1e6, 3),
            "x1": round(bb.GetRight() / 1e6, 3), "y1": round(bb.GetBottom() / 1e6, 3),
            "thickness": round(board.GetDesignSettings().GetBoardThickness() / 1e6, 3),
        },
        # 4x M3, 3.2 mm drill, 6.5 mm annular keep-out (pcb.md 6.1 rule 7)
        "mounting_holes": {r: pos(r) for r in ("H1", "H2", "H3", "H4")},
        "parts": {
            "RV1": {"pos": pos("RV1"), "body": body("RV1")},   # pot, south wall
            "SW1": {"pos": pos("SW1"), "body": body("SW1")},   # power slide, south wall
            "SW2": {"pos": pos("SW2"), "body": body("SW2")},   # mode button, lid
            "D4":  {"pos": pos("D4")},                          # NET led, lid
            "D5":  {"pos": pos("D5")},                          # MODE led, lid
            "X1":  {"pos": pos("X1"),  "body": body("X1"), "pads": pad_span("X1")},
            "Y1":  {"pos": pos("Y1"),  "body": body("Y1"), "pads": pad_span("Y1")},
            "J4":  {"pos": pos("J4"),  "body": body("J4")},    # mic pigtail, east wall
            "J8":  {"pos": pos("J8"),  "body": body("J8")},    # JST battery in, south
            "JA1": {"body": body("JA1")}, "JB1": {"body": body("JB1")},  # SuperMini
            "J2":  {"body": body("J2")},  "J3":  {"body": body("J3")},   # DAC
        },
        # TP4056 USB-C jack: y from the pad rows, x at the board's east edge.
        "usb_charge": {"y": round(tp_y, 3), "x": round(bb.GetRight() / 1e6, 3)},
    }

    with open(OUT, "w") as fh:
        json.dump(geom, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {os.path.normpath(OUT)}")
    b = geom["board"]
    print(f"  board {b['x1'] - b['x0']:.2f} x {b['y1'] - b['y0']:.2f} x {b['thickness']:.2f} mm")
    for r, xy in geom["mounting_holes"].items():
        print(f"  {r} {xy}")
    print(f"  USB-C charge port at y={geom['usb_charge']['y']:.2f}")
    for r in ("X1", "Y1"):
        pd = geom["parts"][r]["pads"]
        print(f"  {r} pad span x {pd[0]:.2f}..{pd[2]:.2f}  centre {(pd[0] + pd[2]) / 2:.2f}"
              f"  (footprint origin x={geom['parts'][r]['pos'][0]:.2f})")


if __name__ == "__main__":
    main()
