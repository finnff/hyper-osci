#!/usr/bin/env python3
"""Trace a monochrome bitmap into silkscreen polygons.

The board has a nearly empty back silkscreen (2 items against the front's 374),
and the fab prints it either way, so artwork there is free.  This turns a 1-bit
image into a polygon set `gen_board.py` can stamp onto B.SilkS.

Why not `bitmap2component`: it is GUI-only (no CLI, and it does nothing useful
under xvfb), and its output is an opaque .kicad_mod that nothing in this repo
could regenerate.  Why not a footprint at all: a board footprint with no
schematic symbol is an `extra_footprint` under `kicad-cli pcb drc
--schematic-parity`, which would move the documented 69-item baseline for a
decoration.  Board-level graphics are not parity-checked, so the artwork stays
invisible to every gate that matters.

`potrace` does the tracing (the same library bitmap2component links) with
`-a 0 -n`, which disables curve fitting so the output is polygons rather than
Béziers — KiCad silk wants line segments anyway.  potrace is needed only when
re-tracing; `gen_board.py` reads the committed JSON and has no such dependency.

KiCad has no polygon-with-holes primitive for graphics, so holes are **keyholed**
into the exterior ring: the nearest exterior/hole vertex pair is found and the
hole is spliced in as a zero-width slit.  At silkscreen resolution the slit is
invisible; the alternative (emitting holes as separate polygons) would fill the
counters of the '8' solid.

    /usr/bin/python3 tools/gen_artwork.py art/484848-mono.png art/484848.json
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

THRESHOLD = 128          # luminance below this is ink


def load_ink(path):
    """Binary ink mask from an image, honouring alpha if present."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or "transparency" in im.info:
        a = np.array(im.convert("RGBA"))
        # a transparent source (the original logo) means alpha *is* the shape
        if a[:, :, 3].min() < 250:
            return a[:, :, 3] > 110
        im = im.convert("L")
    return np.array(im.convert("L")) < THRESHOLD


def trace(ink):
    """potrace the mask, returning [(exterior, [holes...]), ...] in px."""
    with tempfile.TemporaryDirectory() as td:
        pbm = os.path.join(td, "in.pbm")
        gj = os.path.join(td, "out.geojson")
        Image.fromarray(np.where(ink, 0, 255).astype(np.uint8), "L") \
             .convert("1").save(pbm)
        # -a 0 : every corner is a corner (no curve fitting) -> polygons
        # -n   : no curve optimisation, so segments survive verbatim
        subprocess.run(["potrace", "-b", "geojson", "-a", "0", "-n",
                        "-o", gj, pbm], check=True)
        data = json.load(open(gj))
    out = []
    for feat in data["features"]:
        g = feat["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for rings in polys:
            out.append((rings[0], rings[1:]))
    return out


def area(ring):
    """Signed area; sign tells winding direction."""
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += x1 * y2 - x2 * y1
    return a / 2.0


def keyhole(exterior, holes):
    """Splice each hole into the exterior as a zero-width slit."""
    ring = [tuple(p) for p in exterior[:-1]]          # drop repeated closer
    for hole in holes:
        h = [tuple(p) for p in hole[:-1]]
        if not h:
            continue
        # a hole must wind opposite the exterior or the slit doubles back
        if (area(h + [h[0]]) > 0) == (area(ring + [ring[0]]) > 0):
            h = h[::-1]
        best, bi, bj = None, 0, 0
        for i, (px, py) in enumerate(ring):
            for j, (qx, qy) in enumerate(h):
                d = (px - qx) ** 2 + (py - qy) ** 2
                if best is None or d < best:
                    best, bi, bj = d, i, j
        # out[..i] -> hole[j..] -> hole[..j] -> back out at i
        ring = ring[:bi + 1] + h[bj:] + h[:bj + 1] + ring[bi:]
    return ring


def rdp(pts, eps):
    """Ramer-Douglas-Peucker, iterative so a long ring cannot blow the stack."""
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** 0.5
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            x, y = pts[k]
            d = (abs(dy * x - dx * y + x2 * y1 - y2 * x1) / n if n
                 else ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5)
            if d > worst:
                worst, wi = d, k
        if worst > eps:
            keep[wi] = True
            stack += [(i, wi), (wi, j)]
    return [p for p, k in zip(pts, keep) if k]


def simplify(rings, eps):
    """Drop vertices no silkscreen process could reproduce."""
    out = []
    for r in rings:
        closed = r + [r[0]]
        s = rdp(closed, eps)
        if len(s) >= 4:                  # keep only rings that survive as areas
            out.append(s[:-1])
    return out


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "art/484848-mono.png"
    dst = sys.argv[2] if len(sys.argv) > 2 else "art/484848.json"

    ink = load_ink(src)
    ys, xs = np.where(ink)
    if not len(xs):
        sys.exit("no ink found in " + src)
    polys = trace(ink)

    rings = [keyhole(e, h) for e, h in polys]
    nholes = sum(len(h) for _, h in polys)
    raw_v = sum(len(r) for r in rings)
    # The logo lands ~15 mm wide off a ~510 px source, i.e. 0.03 mm per source
    # pixel — an order of magnitude under the 0.15 mm the fab can hold.  Thin
    # the vertices to roughly a third of that and nothing visible changes.
    span = max(max(p[0] for r in rings for p in r) - min(p[0] for r in rings for p in r),
               max(p[1] for r in rings for p in r) - min(p[1] for r in rings for p in r))
    rings = simplify(rings, span * float(os.environ.get("ART_SIMPLIFY", "0.0015")))

    # normalise into a unit box, y down, aspect preserved on the long axis
    allx = [p[0] for r in rings for p in r]
    ally = [p[1] for r in rings for p in r]
    x0, x1 = min(allx), max(allx)
    y0, y1 = min(ally), max(ally)
    w, h = x1 - x0, y1 - y0
    s = 1.0 / max(w, h)
    # potrace's y axis points up; KiCad's points down
    norm = [[[round((p[0] - x0) * s, 5), round((y1 - p[1]) * s, 5)] for p in r]
            for r in rings]

    out = {"source": os.path.basename(src),
           "aspect_w_over_h": round(w / h, 5),
           "unit_w": round(w * s, 5), "unit_h": round(h * s, 5),
           "polygons": norm}
    json.dump(out, open(dst, "w"), indent=1)
    print(f"{src}: {len(rings)} polygons ({nholes} holes keyholed), "
          f"{raw_v} -> {sum(len(r) for r in rings)} vertices after simplify, "
          f"aspect {w / h:.3f}")
    print(f"wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
