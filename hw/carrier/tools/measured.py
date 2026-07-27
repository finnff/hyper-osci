#!/usr/bin/env python3
"""Module geometry derived from the bench photogrammetry in ``hw/pin_locs``.

Every socket offset and module outline the carrier draws comes from here, and
here it comes from the measured pad coordinates — nothing is typed in by hand.
Re-run the photogrammetry, drop the new CSV/JSON in ``hw/pin_locs``, re-run
``gen_board.py``: the board follows.

Frames
------
The measurement frame has its origin on pad 0 of each module's *primary* row,
x along that row.  This module re-expresses everything in a **row-aligned**
frame (x exactly along the least-squares primary-row axis, y left-perpendicular)
so the row's fitted tilt does not leak into the offsets, then fits an *ideal*
2.54 mm socket to each row.  What callers get is the pair (offset, residual):
where to put the socket, and how far the worst real pad then sits from it.

Fit tolerance
-------------
Two different tolerances apply, and it matters which:

* Modules that arrive (or get built) with their **header pins already soldered
  in** — SuperMini, PCM5102A — present rigid pins at the module's own hole
  positions.  What must absorb the error is the carrier's *female* socket, and
  a 2.54 mm socket accepts roughly ``TOLERANCE_MM`` of misalignment per pin.
  The within-row scatter of a real module is irreducible (a 2.54 mm socket is
  the only thing you can buy), so it is reported, not gated; what *is* gated is
  the inter-row offset, which the layout chooses.
* The TP4056's four output pads are bare 2.0 mm holes we push our own pins
  through.  There the slack is the hole's: ``(D - 0.90) / 2`` for a 0.64 mm
  square pin, exposed as ``fit_slack_mm``.

Run standalone for the reconciliation table:  python3 tools/measured.py
"""
import csv
import json
import math
import os

PIN_LOCS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "pin_locs"))

PIN_DIAGONAL_MM = 0.90        # 0.64 mm square header pin, corner to corner
TOLERANCE_MM = 0.25           # acceptance of a 2.54 mm female header per pin


# ---------------------------------------------------------------------------
# raw load
# ---------------------------------------------------------------------------
def _load(module):
    with open(os.path.join(PIN_LOCS, module + ".json")) as f:
        meta = json.load(f)
    pads = []
    with open(os.path.join(PIN_LOCS, module + "_pins.csv")) as f:
        for r in csv.DictReader(f):
            pads.append({
                "row": int(r["row"]) if r["row"] != "" else None,
                "index": int(r["index"]) if r["index"] != "" else None,
                "x": float(r["x_mm"]), "y": float(r["y_mm"]),
                "x_px": float(r["x_px"]), "y_px": float(r["y_px"]),
                "hole": float(r["hole_dia_mm"]) if r["hole_dia_mm"] else None,
                "hole_nominal": (float(r["hole_dia_nominal_mm"])
                                 if r["hole_dia_nominal_mm"] else None),
            })
    return meta, pads


def _row_frame(meta):
    """Unit axes of the primary row: x along it, y to its left."""
    ux, uy = meta["rows"][0]["direction_mm"]
    n = math.hypot(ux, uy)
    ux, uy = ux / n, uy / n
    ox, oy = meta["rows"][0]["start_mm"]
    def to_frame(x, y):
        dx, dy = x - ox, y - oy
        return (dx * ux + dy * uy, -dx * uy + dy * ux)
    return to_frame


def _fit_socket(points, pitch, axis):
    """Least-squares placement of an ideal `pitch` socket over `points`.

    `points` are (a, b) in the row-aligned frame, ordered along the socket.
    `axis` is "x" or "y" — the direction the socket's pads run.  Returns
    (origin_a, origin_b, worst_residual) where origin is pad-0's ideal spot.
    """
    n = len(points)
    along = [p[0] for p in points] if axis == "x" else [p[1] for p in points]
    across = [p[1] for p in points] if axis == "x" else [p[0] for p in points]
    o_along = sum(v - pitch * i for i, v in enumerate(along)) / n
    o_across = sum(across) / n
    worst = max(math.hypot(v - (o_along + pitch * i), w - o_across)
                for i, (v, w) in enumerate(zip(along, across)))
    return ((o_along, o_across) if axis == "x" else (o_across, o_along)) + (worst,)


class Module:
    """One measured module: fitted socket rows plus its outline."""

    def __init__(self, name):
        self.name = name
        self.meta, self.pads = _load(name)
        self.to_frame = _row_frame(self.meta)
        self.rows = []
        for ri, row in enumerate(self.meta["rows"]):
            members = sorted((p for p in self.pads if p["row"] == ri),
                             key=lambda p: p["index"])
            hole = row.get("drill_nominal_mm") or row["hole_dia_mean_mm"]
            self.rows.append({
                "points": [self.to_frame(p["x"], p["y"]) for p in members],
                "pixels": [(p["x_px"], p["y_px"]) for p in members],
                "n": len(members),
                "pitch_measured": row["pitch_mm_measured"],
                "even_pitch": row["even_pitch"],
                "hole_nominal_mm": hole,
                "fit_slack_mm": (hole - PIN_DIAGONAL_MM) / 2.0,
            })
        self.loose = [self.to_frame(p["x"], p["y"])
                      for p in self.pads if p["row"] is None]
        self.loose_px = [(p["x_px"], p["y_px"])
                         for p in self.pads if p["row"] is None]
        self.loose_hole = [p["hole"] for p in self.pads if p["row"] is None]
        self.corners = [self.to_frame(x, y)
                        for x, y in self.meta["board"]["corners_mm"]]

    def fit(self, row, pitch=2.54, axis="x"):
        """(origin_a, origin_b, worst_residual) for an ideal socket on `row`."""
        return _fit_socket(self.rows[row]["points"], pitch, axis)

    def pad(self, row, index, pitch=2.54, axis="x"):
        """Fitted position of one pad of `row`, in the row-aligned frame."""
        oa, ob, _ = self.fit(row, pitch, axis)
        return (oa + pitch * index, ob) if axis == "x" else (oa, ob + pitch * index)

    def handedness(self):
        """Sign of det[a, b] as the *photograph* sees it (component side up).

        The measurement's pixel coordinates carry the one fact the millimetre
        table cannot: which way round the module actually is.  Image y runs
        down, so a math-orientation determinant needs it negated.
        """
        (x0, y0), (x1, y1) = self.rows[0]["pixels"][0], self.rows[0]["pixels"][-1]
        ax, ay = x1 - x0, -(y1 - y0)                       # +a in math axes
        if len(self.rows) > 1:                             # +b: toward row 1
            far_px, far_b = self.rows[1]["pixels"][0], self.rows[1]["points"][0][1]
        elif self.loose:
            far_px, far_b = self.loose_px[0], self.loose[0][1]
        else:
            return None                                    # nothing off-axis
        bx, by = far_px[0] - x0, -(far_px[1] - y0)
        if far_b < 0:
            bx, by = -bx, -by
        return 1.0 if ax * by - ay * bx > 0 else -1.0

    def outline_extent(self):
        """(min_a, min_b, max_a, max_b) of the module body, row-aligned frame."""
        return (min(c[0] for c in self.corners), min(c[1] for c in self.corners),
                max(c[0] for c in self.corners), max(c[1] for c in self.corners))


# ---------------------------------------------------------------------------
# carrier-frame geometry
# ---------------------------------------------------------------------------
# Each module plugs in component-side-up, so the measured front view maps onto
# the KiCad top view by a proper rotation — never a mirror.  The mapping below
# is stated once per module as the sign pair applied to (a, b).

class Placed:
    """A module mapped into carrier coordinates (x east, y south, mm).

    `sa`/`sb` are the signs taking the row-aligned measurement axes (a, b) to
    the carrier axes; `swap` transposes them first (for modules whose primary
    row runs north-south on the carrier).
    """

    def __init__(self, module, sa, sb, swap=False):
        self.m = module
        self.sa, self.sb, self.swap = sa, sb, swap
        self._check_mirror()

    def _check_mirror(self):
        """A module plugs in component-side-up, so the photograph's view and
        the KiCad top view are the same view: the (a, b) basis must have the
        same handedness in both.  If it does not, the socket rows are swapped
        end-for-end and every net lands on the wrong pin.  (This is not
        hypothetical — the v1.0 layout had exactly this bug on the SuperMini.)
        """
        want = self.m.handedness()
        if want is None:
            return
        # carrier top view in math axes: east = +X, south = -Y
        ax, ay = self.vec(1.0, 0.0)
        bx, by = self.vec(0.0, 1.0)
        got = 1.0 if (ax * -by - -ay * bx) > 0 else -1.0
        if got != want:
            raise AssertionError(
                f"{self.m.name}: declared carrier mapping is a MIRROR of the "
                f"measured module (photo handedness {want:+.0f}, layout "
                f"{got:+.0f}) — the two socket rows are swapped")

    def vec(self, a, b):
        """Measured (a, b) displacement -> carrier (dx, dy) displacement."""
        a, b = (b, a) if self.swap else (a, b)
        return (self.sa * a, self.sb * b)

    def outline(self, datum):
        """Module body as a carrier-frame (x0, y0, x1, y1) box relative to
        `datum`, a point in the row-aligned measurement frame."""
        a0, b0, a1, b1 = self.m.outline_extent()
        c = [self.vec(a - datum[0], b - datum[1])
             for a, b in ((a0, b0), (a1, b0), (a1, b1), (a0, b1))]
        return (min(p[0] for p in c), min(p[1] for p in c),
                max(p[0] for p in c), max(p[1] for p in c))


# --- PCM5102A --------------------------------------------------------------
# Datum: J3 pad 1 = LROUT = row 0 pad 0.  On the carrier the 1x9 row runs WEST
# from LROUT and the module body lies SOUTH, so measured +a -> carrier -x and
# measured +b -> carrier -y (a 180-degree rotation, det = +1).
PCM5102A = Placed(Module("PCM5102A"), sa=-1, sb=-1)
_p_row0 = PCM5102A.m.fit(0)                        # datum: fitted 1x9 socket
# row 1 is the 1x6 I2S column, measured pad 0 = VIN at the far end from SCK.
# The carrier's J2 pin 1 is SCK, so the socket is placed from its pad 5.
_p_row1 = PCM5102A.m.fit(1, 2.54, "y")
PCM5102A_J2_FROM_J3 = PCM5102A.vec(_p_row1[0] - _p_row0[0],
                                   _p_row1[1] + 2.54 * 5 - _p_row0[1])
PCM5102A_J2_RESIDUAL = _p_row1[2]
PCM5102A_J3_RESIDUAL = _p_row0[2]
PCM5102A_OUTLINE = PCM5102A.outline(_p_row0[:2])   # relative to J3 pad 1

# --- TP4056 ----------------------------------------------------------------
# Datum: J5 pad 1 = OUT- = row 0 pad 0.  The pad column runs SOUTH on the
# carrier and the module body lies EAST (USB-C exits the east board edge).
# The body sits on the -b side of the pad row, so measured -b -> carrier +x and
# measured +a -> carrier +y: (a, b) -> (-b, +a), a proper rotation.
TP4056 = Placed(Module("TP4056"), sa=-1, sb=+1, swap=True)
_t = TP4056.m.rows[0]["points"]
# NOT an even-pitch row: the four pads are two pairs, so there is no socket to
# fit — pad 0 is the datum and the offsets stay as measured, projected onto the
# fitted row axis (the across-axis scatter is pick noise).
TP4056_PAD_OFFSETS = [round(p[0] - _t[0][0], 4) for p in _t]   # OUT- B- B+ OUT+
TP4056_PAIR_A_PITCH = TP4056_PAD_OFFSETS[1] - TP4056_PAD_OFFSETS[0]   # OUT- B-
TP4056_PAIR_B_PITCH = TP4056_PAD_OFFSETS[3] - TP4056_PAD_OFFSETS[2]   # B+ OUT+
TP4056_J6_FROM_J5 = TP4056.vec(TP4056_PAD_OFFSETS[2], 0.0)
TP4056_SPAN = TP4056_PAD_OFFSETS[3]
TP4056_HOLE_MM = TP4056.m.rows[0]["hole_nominal_mm"]
TP4056_SLACK_MM = TP4056.m.rows[0]["fit_slack_mm"]
# --- the two USB-C-end corner pads: J9 (IN+) and J10 (IN-) -----------------
# ~2.8 mm bare-copper squares on ~1.65 mm plated holes, one in line with each
# OUT pad, at the far end of the module from the output row.  They were a
# single wire pad for the IN+ sense tap; they are now the carrier's SECOND
# MOUNT ROW as well, because the four output pads alone leave the module a
# 21.65 mm cantilever with a USB-C plug being pushed into the far end.
#
# ONE number below is NOT photogrammetry, deliberately.  The picks put these
# pads 22.30 mm from the output row; the caliper (2026-07-28) says 21.65 mm.
# The similarity fit is calibrated on a 17.30 mm reference measured ACROSS the
# output row, so the along-row axis is metric and the perpendicular one is
# stretched — the same stretch reads the body as 25.75 mm long against a
# measured 25.2 mm.  Cross-check, and it is the convincing one: the picks put
# these pads 1.68 mm inboard of the clicked east edge, and the caliper body
# gives 25.2 - 1.935 - 1.68 = 21.59 mm.  So the rule here is
#   across the row  -> photogrammetry (the calibrated axis)
#   along the module -> caliper (the axis a similarity cannot get right)
# Full reduction: docs/hardware/measurements.md, "The second mount row".
TP4056_MOUNT_FROM_ROW_MM = 21.65     # CALIPER 2026-07-28, not photogrammetry
_m_north, _m_south = sorted(TP4056.m.loose, key=lambda p: p[0])
TP4056_J10_FROM_J5 = TP4056.vec(_m_north[0] - _t[0][0], -TP4056_MOUNT_FROM_ROW_MM)
TP4056_J9_FROM_J5 = TP4056.vec(_m_south[0] - _t[0][0], -TP4056_MOUNT_FROM_ROW_MM)
# What the mount row spans, and how far a pin may sit off before it will not
# enter the module's own hole: (D - 0.90) / 2 for a 0.64 mm square pin.
TP4056_MOUNT_SPAN_MM = _m_south[0] - _m_north[0]
TP4056_MOUNT_HOLE_MM = (sum(TP4056.m.loose_hole)
                        / len(TP4056.m.loose_hole))
TP4056_MOUNT_SLACK_MM = (TP4056_MOUNT_HOLE_MM - PIN_DIAGONAL_MM) / 2
TP4056_OUTLINE = TP4056.outline(_t[0])             # relative to J5 pad 1

# --- ESP32-C3 SuperMini ----------------------------------------------------
# Which measured row is which is a fact only the photographs carry:
# ESP32C3_BACK.jpg reads `5V G 3.3 4 3 2 1 0` down the LEFT column with USB-C
# up, so the component side (ESP32C3_FRONT.jpg) has that column on the RIGHT.
# Measured row 0 sits at x_px ~797 = the photo's left column = GPIO5..21 = JB1;
# row 1 is the 5V column = JA1.  Pad index 0 of each row is the antenna end
# (the 3.17 mm margin), index 7 the USB-C end.
#
# The carrier puts the antenna WEST (keepout x<6) and the USB-C end EAST, so
# rotating the photo 90 deg clockwise lands the 5V row SOUTH: measured
# +a -> carrier +x (east), +b -> carrier +y (south).
ESP32C3 = Placed(Module("ESP32C3"), sa=+1, sb=+1)
ESP32C3_PRIMARY_ROW = "JB1"                        # measured row 0
_e_jb1 = ESP32C3.m.pad(0, 7)                       # JB1 pin 1 = GPIO5, USB end
_e_ja1 = ESP32C3.m.pad(1, 7)                       # JA1 pin 1 = 5V,    USB end
ESP32C3_JA1_FROM_JB1 = ESP32C3.vec(_e_ja1[0] - _e_jb1[0], _e_ja1[1] - _e_jb1[1])
ESP32C3_JB1_RESIDUAL = ESP32C3.m.fit(0)[2]
ESP32C3_JA1_RESIDUAL = ESP32C3.m.fit(1)[2]
ESP32C3_OUTLINE = ESP32C3.outline(_e_jb1)          # relative to JB1 pad 1

# --- MAX4466 ---------------------------------------------------------------
# Off-board on a pigtail: only the 1x3 pitch matters, and it measures 2.54 mm.
MAX4466 = Placed(Module("MAX4466"), sa=+1, sb=+1)
MAX4466_RESIDUAL = MAX4466.m.fit(0)[2]


def _box(b):
    return "x %+.2f..%+.2f  y %+.2f..%+.2f" % (b[0], b[2], b[1], b[3])


def report():
    lines = ["measured module geometry (hw/pin_locs) -> carrier frame", ""]
    def row(what, value, resid="", note=""):
        lines.append(f"  {what:<32}{value:<28}{resid:<12}{note}")
    row("quantity", "value (mm)", "worst pin", "note")
    row("-" * 30, "-" * 26, "-" * 10, "-" * 30)

    row("PCM5102A J3 (1x9)", "datum = J3 pad 1",
        f"{PCM5102A_J3_RESIDUAL:.3f}", "within-row scatter, irreducible")
    row("PCM5102A J2 (1x6) from J3.1",
        "(%+.3f, %+.3f)" % PCM5102A_J2_FROM_J3,
        f"{PCM5102A_J2_RESIDUAL:.3f}", "layout chooses this offset")
    row("PCM5102A outline from J3.1", _box(PCM5102A_OUTLINE))

    row("TP4056 pads from OUT-", " ".join(f"{v:.2f}" for v in TP4056_PAD_OFFSETS),
        "", f"hole slack {TP4056_SLACK_MM:.2f} (own pins)")
    row("TP4056 J6 from J5.1", "(%+.3f, %+.3f)" % TP4056_J6_FROM_J5)
    row("TP4056 J9  (IN+) from J5.1", "(%+.3f, %+.3f)" % TP4056_J9_FROM_J5,
        "", "mount row: %.2f mm CALIPER, not picks" % TP4056_MOUNT_FROM_ROW_MM)
    row("TP4056 J10 (IN-) from J5.1", "(%+.3f, %+.3f)" % TP4056_J10_FROM_J5,
        "", "span %.2f, hole %.2f, slack %.2f"
            % (TP4056_MOUNT_SPAN_MM, TP4056_MOUNT_HOLE_MM,
               TP4056_MOUNT_SLACK_MM))
    row("TP4056 outline from J5.1", _box(TP4056_OUTLINE))

    row("ESP32-C3 JA1 (5V row) from JB1.1",
        "(%+.3f, %+.3f)" % ESP32C3_JA1_FROM_JB1,
        f"{ESP32C3_JA1_RESIDUAL:.3f}", "5V row is SOUTH — see comment")
    row("ESP32-C3 outline from JB1.1", _box(ESP32C3_OUTLINE))
    row("MAX4466 1x3 pitch", f"{MAX4466.m.rows[0]['pitch_measured']:.4f}",
        f"{MAX4466_RESIDUAL:.3f}", "pigtail — fit not constrained")
    lines += ["", f"  per-pin tolerance gate: {TOLERANCE_MM:.2f} mm "
              "(2.54 mm female header acceptance)"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
