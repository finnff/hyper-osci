#!/usr/bin/env python3
"""Bake an SVG into the controller's int16 point table (a "figure").

Runs OFF the board — on the laptop, from the repo — and ships a .tbl file the
daemon loads with array('h').frombytes(). That split is the recommendation in
docs/osci-render-feature-port-feasibility.md §5: board-side XML parsing is
affordable (1.34 ms per 300 paths, measured) but it is the stage-failure
surface, and baking offline also buys the contour reordering below, which the
5 ms stream loop could never afford.

    python3 bake_svg.py art.svg -o figures/art.tbl

Policy, and it differs from osci-render's on purpose:
  - centrelines, not stroke outlines (osci-render traces the outline of a
    stroke, JUCE juce_DrawableShape.cpp:185 — two parallel passes plus caps);
  - a filled shape is traced as its boundary. Artwork authored with fills
    therefore draws every counter and every shared border. That is geometry,
    not a bug, and no parser fixes it (§5);
  - <text> is not geometry until converted: the baker refuses the file and
    names the nodes, rather than silently dropping the words.

Everything after flattening is the daemon's own code — this imports
hype_controller.normalize_resample — so a baked figure and a rendered word are
normalised and traced by identical arithmetic.
"""

import argparse
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hype_controller as hc            # noqa: E402  (path set above)

SVG_NS = "http://www.w3.org/2000/svg"
FIGURE_MAGIC = hc.FIGURE_MAGIC

# Flattening tolerance, in SVG user units, for the adaptive subdivision of
# curves. The table is resampled to 2000 equal-arc points afterwards, so this
# only has to stay well under the resample step; 0.05 units on a 200-unit
# drawing is ~1/4000 of the figure and costs a few hundred points.
FLATNESS = 0.05
MAX_SEGMENTS = 64        # per curve, whatever the tolerance asks for

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


# --------------------------------------------------------------------------
# transforms
# --------------------------------------------------------------------------

IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)   # a b c d e f, as in SVG


def mat_mul(m, n):
    """m then n applied after it — i.e. the SVG nesting order, outer @ inner."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1)


def mat_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(s):
    """SVG transform attribute -> matrix tuple."""
    m = IDENTITY
    for name, argstr in _TRANSFORM.findall(s or ""):
        v = [float(t) for t in _NUM.findall(argstr)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate":
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate":
            ang = math.radians(v[0])
            cos, sin = math.cos(ang), math.sin(ang)
            t = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(v) == 3:   # rotate about (cx, cy)
                t = mat_mul((1.0, 0.0, 0.0, 1.0, v[1], v[2]), t)
                t = mat_mul(t, (1.0, 0.0, 0.0, 1.0, -v[1], -v[2]))
        elif name == "skewX":
            t = (1.0, 0.0, math.tan(math.radians(v[0])), 1.0, 0.0, 0.0)
        elif name == "skewY":
            t = (1.0, math.tan(math.radians(v[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = mat_mul(m, t)
    return m


# --------------------------------------------------------------------------
# path data
# --------------------------------------------------------------------------

def _tokens(d):
    out, i, n = [], 0, len(d)
    while i < n:
        ch = d[i]
        if ch.isalpha():
            out.append(ch)
            i += 1
        elif ch in ", \t\r\n":
            i += 1
        else:
            m = _NUM.match(d, i)
            if not m:
                raise ValueError(f"unparsable character {ch!r} at offset {i}")
            out.append(float(m.group()))
            i = m.end()
    return out


def _bezier_steps(pts):
    """Adaptive segment count: control-polygon length against FLATNESS."""
    poly = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))
    return max(2, min(MAX_SEGMENTS, int(math.sqrt(poly / FLATNESS)) + 2))


def _arc_to_cubics(x0, y0, rx, ry, phi, large, sweep, x, y):
    """Endpoint-parameterised arc -> cubic segments (SVG F.6.5 + F.6.6)."""
    if rx == 0.0 or ry == 0.0 or (x0 == x and y0 == y):
        return [("L", x, y)]
    rx, ry = abs(rx), abs(ry)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x0 - x) / 2.0, (y0 - y) / 2.0
    x1 = cos_p * dx2 + sin_p * dy2
    y1 = -sin_p * dx2 + cos_p * dy2
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1.0:                      # F.6.6.2: scale the radii up to fit
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cx1 = co * rx * y1 / ry
    cy1 = -co * ry * x1 / rx
    cx = cos_p * cx1 - sin_p * cy1 + (x0 + x) / 2.0
    cy = sin_p * cx1 + cos_p * cy1 + (y0 + y) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.acos(max(-1.0, min(1.0, dot / n))) if n else 0.0
        return -a if ux * vy - uy * vx < 0 else a

    th1 = angle(1.0, 0.0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dth = angle((x1 - cx1) / rx, (y1 - cy1) / ry,
                (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dth > 0:
        dth -= 2.0 * math.pi
    elif sweep and dth < 0:
        dth += 2.0 * math.pi

    out = []
    n_seg = max(1, int(math.ceil(abs(dth) / (math.pi / 2.0))))
    delta = dth / n_seg
    t = 4.0 / 3.0 * math.tan(delta / 4.0)
    for i in range(n_seg):
        a0 = th1 + i * delta
        a1 = a0 + delta
        c0, s0 = math.cos(a0), math.sin(a0)
        c1, s1 = math.cos(a1), math.sin(a1)
        p0 = (cos_p * rx * c0 - sin_p * ry * s0 + cx,
              sin_p * rx * c0 + cos_p * ry * s0 + cy)
        p3 = (cos_p * rx * c1 - sin_p * ry * s1 + cx,
              sin_p * rx * c1 + cos_p * ry * s1 + cy)
        d0 = (cos_p * -rx * s0 - sin_p * ry * c0,
              sin_p * -rx * s0 + cos_p * ry * c0)
        d1 = (cos_p * -rx * s1 - sin_p * ry * c1,
              sin_p * -rx * s1 + cos_p * ry * c1)
        out.append(("C",
                    p0[0] + t * d0[0], p0[1] + t * d0[1],
                    p3[0] - t * d1[0], p3[1] - t * d1[1],
                    p3[0], p3[1]))
    return out


def path_polylines(d):
    """Path 'd' attribute -> [[(x, y), ...], ...] in the element's own units."""
    t = _tokens(d)
    i = 0
    polys, cur = [], []
    x = y = start_x = start_y = 0.0
    cmd = None
    prev_c = prev_q = None       # last control point, for S/T reflection

    def flush():
        if len(cur) > 1:
            polys.append(list(cur))
        del cur[:]

    def curve(pts):
        n = _bezier_steps([(x, y)] + pts)
        if len(pts) == 3:
            (x1, y1), (x2, y2), (x3, y3) = pts
            for k in range(1, n + 1):
                s = k / n
                u = 1.0 - s
                cur.append((u * u * u * x + 3 * u * u * s * x1
                            + 3 * u * s * s * x2 + s * s * s * x3,
                            u * u * u * y + 3 * u * u * s * y1
                            + 3 * u * s * s * y2 + s * s * s * y3))
        else:
            (x1, y1), (x2, y2) = pts
            for k in range(1, n + 1):
                s = k / n
                u = 1.0 - s
                cur.append((u * u * x + 2 * u * s * x1 + s * s * x2,
                            u * u * y + 2 * u * s * y1 + s * s * y2))

    while i < len(t):
        if isinstance(t[i], str):
            cmd = t[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    cur.append((start_x, start_y))
                    x, y = start_x, start_y
                flush()
                prev_c = prev_q = None
                continue
        if cmd is None:
            raise ValueError("path data starts with a number")
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            nx, ny = t[i], t[i + 1]
            i += 2
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            flush()
            cur.append((x, y))
            start_x, start_y = x, y
            cmd = "l" if rel else "L"      # implicit lineto for extra pairs
            prev_c = prev_q = None
        elif c == "L":
            nx, ny = t[i], t[i + 1]
            i += 2
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            cur.append((x, y))
            prev_c = prev_q = None
        elif c == "H":
            nx = t[i]
            i += 1
            x = x + nx if rel else nx
            cur.append((x, y))
            prev_c = prev_q = None
        elif c == "V":
            ny = t[i]
            i += 1
            y = y + ny if rel else ny
            cur.append((x, y))
            prev_c = prev_q = None
        elif c == "C":
            v = t[i:i + 6]
            i += 6
            pts = [(x + v[0], y + v[1]), (x + v[2], y + v[3]),
                   (x + v[4], y + v[5])] if rel else [
                (v[0], v[1]), (v[2], v[3]), (v[4], v[5])]
            curve(pts)
            prev_c, prev_q = pts[1], None
            x, y = pts[2]
        elif c == "S":
            v = t[i:i + 4]
            i += 4
            pts = [(x + v[0], y + v[1]), (x + v[2], y + v[3])] if rel else [
                (v[0], v[1]), (v[2], v[3])]
            r = (2 * x - prev_c[0], 2 * y - prev_c[1]) if prev_c else (x, y)
            curve([r, pts[0], pts[1]])
            prev_c, prev_q = pts[0], None
            x, y = pts[1]
        elif c == "Q":
            v = t[i:i + 4]
            i += 4
            pts = [(x + v[0], y + v[1]), (x + v[2], y + v[3])] if rel else [
                (v[0], v[1]), (v[2], v[3])]
            curve(pts)
            prev_q, prev_c = pts[0], None
            x, y = pts[1]
        elif c == "T":
            v = t[i:i + 2]
            i += 2
            p = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            r = (2 * x - prev_q[0], 2 * y - prev_q[1]) if prev_q else (x, y)
            curve([r, p])
            prev_q, prev_c = r, None
            x, y = p
        elif c == "A":
            v = t[i:i + 7]
            i += 7
            ex, ey = (x + v[5], y + v[6]) if rel else (v[5], v[6])
            for seg in _arc_to_cubics(x, y, v[0], v[1], math.radians(v[2]),
                                      bool(v[3]), bool(v[4]), ex, ey):
                if seg[0] == "L":
                    cur.append((seg[1], seg[2]))
                else:
                    curve([(seg[1], seg[2]), (seg[3], seg[4]),
                           (seg[5], seg[6])])
                x, y = cur[-1]
            prev_c = prev_q = None
        else:
            raise ValueError(f"unsupported path command {cmd!r}")
    flush()
    return polys


# --------------------------------------------------------------------------
# the other shape elements (Inkscape emits them alongside <path>)
# --------------------------------------------------------------------------

def _f(el, name, default=0.0):
    try:
        return float(el.get(name, default))
    except (TypeError, ValueError):
        return default


def _ellipse_poly(cx, cy, rx, ry):
    n = max(16, min(256, int(math.sqrt(math.pi * (rx + ry) / FLATNESS))))
    return [(cx + rx * math.cos(2 * math.pi * i / n),
             cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def shape_polylines(tag, el):
    """Non-<path> geometry -> polylines, or [] for a tag that carries none."""
    if tag == "rect":
        x, y = _f(el, "x"), _f(el, "y")
        w, h = _f(el, "width"), _f(el, "height")
        if w <= 0 or h <= 0:
            return []
        rx = _f(el, "rx", _f(el, "ry"))
        ry = _f(el, "ry", rx)
        rx, ry = min(rx, w / 2.0), min(ry, h / 2.0)
        if rx <= 0 or ry <= 0:
            return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
        d = (f"M{x + rx},{y} H{x + w - rx} A{rx},{ry} 0 0 1 {x + w},{y + ry} "
             f"V{y + h - ry} A{rx},{ry} 0 0 1 {x + w - rx},{y + h} "
             f"H{x + rx} A{rx},{ry} 0 0 1 {x},{y + h - ry} "
             f"V{y + ry} A{rx},{ry} 0 0 1 {x + rx},{y} Z")
        return path_polylines(d)
    if tag == "circle":
        r = _f(el, "r")
        return [_ellipse_poly(_f(el, "cx"), _f(el, "cy"), r, r)] if r > 0 else []
    if tag == "ellipse":
        rx, ry = _f(el, "rx"), _f(el, "ry")
        return ([_ellipse_poly(_f(el, "cx"), _f(el, "cy"), rx, ry)]
                if rx > 0 and ry > 0 else [])
    if tag == "line":
        return [[(_f(el, "x1"), _f(el, "y1")), (_f(el, "x2"), _f(el, "y2"))]]
    if tag in ("polyline", "polygon"):
        v = [float(t) for t in _NUM.findall(el.get("points", ""))]
        pts = list(zip(v[::2], v[1::2]))
        if len(pts) < 2:
            return []
        if tag == "polygon":
            pts.append(pts[0])
        return [pts]
    return []


# --------------------------------------------------------------------------
# document walk
# --------------------------------------------------------------------------

def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _hidden(el):
    style = el.get("style", "") or ""
    return (el.get("display") == "none" or "display:none" in style.replace(" ", "")
            or "visibility:hidden" in style.replace(" ", ""))


def svg_polylines(path):
    """SVG file -> polylines in user units, Y already flipped for the scope."""
    root = ET.parse(path).getroot()
    texts = []
    polys = []

    def walk(el, mat):
        tag = _local(el.tag)
        if tag in ("defs", "clipPath", "mask", "symbol", "marker"):
            return                       # referenced, not drawn on its own
        if _hidden(el):
            return
        mat = mat_mul(mat, parse_transform(el.get("transform")))
        if tag == "text":
            texts.append("".join(el.itertext()).strip() or "<empty>")
            return
        if tag == "use":
            texts.append("<use> (not resolved)")
            return
        if tag == "path":
            raw = path_polylines(el.get("d", ""))
        else:
            raw = shape_polylines(tag, el)
        for poly in raw:
            polys.append([mat_apply(mat, px, py) for px, py in poly])
        for child in el:
            walk(child, mat)

    walk(root, IDENTITY)
    if texts:
        raise SystemExit(
            "refusing to bake: this file still has live <text>/<use> nodes, "
            "which are not geometry and would silently vanish from the "
            "scopes:\n  " + "\n  ".join(texts[:8])
            + "\nIn Inkscape: select all, then Path > Object to Path.")
    # SVG Y grows downward; the scope's +Y is up.
    return [[(px, -py) for px, py in poly] for poly in polys]


# --------------------------------------------------------------------------
# retrace: contour order and direction
# --------------------------------------------------------------------------

def contour_stats(polys):
    """(drawn arc length, retrace length) for a contour list, in its units.

    The retrace total includes the wrap from the last contour's end back to
    the first contour's start: the table is walked in a ring, so the beam
    makes that hop on every single redraw, exactly like the interior ones.
    """
    drawn = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                for s in polys for a, b in zip(s, s[1:]))
    ends = [(s[-1], polys[(i + 1) % len(polys)][0])
            for i, s in enumerate(polys)]
    jump = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in ends)
    return drawn, jump


def _greedy(polys, first):
    """Nearest-neighbour chain from a chosen starting contour."""
    remaining = list(polys)
    out = [remaining.pop(first)]
    while remaining:
        cx, cy = out[-1][-1]
        best_i, best_d, best_rev = 0, float("inf"), False
        for i, s in enumerate(remaining):
            d0 = math.hypot(s[0][0] - cx, s[0][1] - cy)
            if d0 < best_d:
                best_i, best_d, best_rev = i, d0, False
            closed = (abs(s[0][0] - s[-1][0]) < 1e-9
                      and abs(s[0][1] - s[-1][1]) < 1e-9)
            if not closed:
                d1 = math.hypot(s[-1][0] - cx, s[-1][1] - cy)
                if d1 < best_d:
                    best_i, best_d, best_rev = i, d1, True
        s = remaining.pop(best_i)
        out.append(s[::-1] if best_rev else s)
    return out


def reorder(polys):
    """Contour order that minimises retrace, each contour free to reverse.

    The beam blanks nothing: every hop between contours is a visible line
    across the artwork, and document order is authoring order — on real
    artwork close to worst case. Greedy nearest-neighbour from every possible
    starting contour, keep the cheapest ring; greedy alone is hostage to its
    start, and the sweep is O(n^2) hops on a 16-contour figure, i.e. free
    offline. Open contours may be traced either way round; closed ones (first
    point == last) are left alone, since reversing them changes nothing but
    their winding.
    """
    if len(polys) < 3:
        return list(polys)
    best, best_jump = None, float("inf")
    for first in range(len(polys)):
        cand = _greedy(polys, first)
        jump = contour_stats(cand)[1]
        if jump < best_jump:
            best, best_jump = cand, jump
    return best


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def pack_table(tbl):
    raw = array("h", [0]) * (2 * len(tbl))
    for i, (x, y) in enumerate(tbl):
        raw[2 * i] = max(-32767, min(32767, int(round(x * 32767.0))))
        raw[2 * i + 1] = max(-32767, min(32767, int(round(y * 32767.0))))
    if sys.byteorder == "big":
        raw.byteswap()               # the file is little-endian by contract
    return FIGURE_MAGIC + struct.pack("<I", len(tbl)) + raw.tobytes()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("svg")
    ap.add_argument("-o", "--out", help="output .tbl (default: alongside "
                                        "the SVG)")
    ap.add_argument("--no-reorder", action="store_true",
                    help="keep the document's contour order (for comparison)")
    ap.add_argument("--min-ink", type=float, default=0.0, metavar="UNITS",
                    help="drop contours shorter than this many SVG user "
                         "units. Off by default: a speck is still the "
                         "artist's ink. Worth setting when a stray mark sits "
                         "far from the rest, because it both widens the "
                         "bounding box (shrinking everything else) and buys "
                         "two full-width retrace lines")
    args = ap.parse_args()

    polys = svg_polylines(args.svg)
    if not polys:
        raise SystemExit(f"{args.svg}: no drawable geometry found")
    if args.min_ink > 0.0:
        keep = [s for s in polys
                if sum(math.hypot(b[0] - a[0], b[1] - a[1])
                       for a, b in zip(s, s[1:])) >= args.min_ink]
        print(f"  dropped       {len(polys) - len(keep)} contour(s) under "
              f"{args.min_ink} units")
        polys = keep or polys
    before = contour_stats(polys)
    if not args.no_reorder:
        polys = reorder(polys)
    drawn, jump = contour_stats(polys)

    tbl = hc.normalize_resample(polys)
    if not tbl:
        raise SystemExit(f"{args.svg}: geometry has no length")
    out = args.out or os.path.splitext(args.svg)[0] + ".tbl"
    blob = pack_table(tbl)
    with open(out, "wb") as f:
        f.write(blob)

    # L and the retrace fraction are what §3/§5 budget on: L sets trace
    # brightness at a given freq, retrace is the share of the beam's travel
    # spent on lines that are not the artwork. Both are quoted in the ±1 box
    # the daemon draws in, so k mirrors normalize_resample's own scaling.
    pts = [p for s in polys for p in s]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    k = 1.8 / max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    frac = jump / (drawn + jump) if drawn + jump else 0.0
    was = before[1] / (before[0] + before[1]) if sum(before) else 0.0
    print(f"{args.svg} -> {out}")
    print(f"  contours      {len(polys)}")
    print(f"  source points {sum(len(s) for s in polys)}")
    print(f"  table points  {len(tbl)}   ({len(blob)} bytes)")
    print(f"  L (±1 box)    {drawn * k:.2f} drawn + {jump * k:.2f} retrace"
          f"   rmax {hc.table_rmax(tbl):.3f}")
    inks = sorted(sum(math.hypot(b[0] - a[0], b[1] - a[1])
                      for a, b in zip(s, s[1:])) for s in polys)
    if len(inks) > 1 and inks[0] < 0.02 * inks[-1]:
        print(f"  smallest      {inks[0]:.2f} units of ink against "
              f"{inks[-1]:.0f} for the longest contour — see --min-ink")
    print(f"  retrace       {frac:.3f} of beam travel"
          + ("" if args.no_reorder else f"   (document order: {was:.3f})"))
    if frac > 0.45:
        print("  NOTE: above the ~0.45 the shipping Hershey faces measure — "
              "expect visible retrace lines between contours.")


if __name__ == "__main__":
    main()
