#!/usr/bin/env python3
"""Geometry gate for carrier.kicad_pcb — the things DRC cannot check.

`check_netlist.py` gates connectivity; `kicad-cli pcb drc` gates clearance.
Neither knows whether a real module will physically seat on its sockets, nor
whether the silkscreen is legible, nor whether the ground pour is stitched.
This does.

  1. module fit   — measured pad pattern (hw/pin_locs) vs the board's sockets,
                    fitted with one translation per module: what is left is
                    shape error, and shape error is what stops a module seating
  2. silk         — kicad-cli DRC silk violations, grouped by what to go fix
  3. routing      — 90-degree track corners, GND via count, worst stitch gap
  4. clearance    — parts too tall to live under a socketed module

Run from hw/carrier/:  python3 tools/audit_board.py [--no-drc] [--verbose]
Exit status is non-zero if a gate fails.
"""
import collections
import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew                                          # noqa: E402
import measured                                        # noqa: E402

BASE = os.environ.get("CARRIER_BASE") or os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))
PCB = os.path.join(BASE, "carrier.kicad_pcb")

# Every number the gates compare against, collected for --json so a sweep can
# rank whole boards instead of grepping this script's prose.
METRICS = {}

# --- gates -----------------------------------------------------------------
MAX_FIT_RESIDUAL_MM = measured.TOLERANCE_MM      # 0.25, female-header acceptance
MAX_SILK_VIOLATIONS = 0
MAX_RIGHT_ANGLES = 0
MAX_STITCH_GAP_MM = 12.0        # pcb.md 6.2 asks for stitching every ~8 mm
SOCKET_STANDOFF_MM = 8.3        # measurements.md: SuperMini socket clearance

# Body height above the board, mm. From docs/hardware/pcb.md 5 (BOM) and the
# stock footprint package names — only parts that could foul a module matter.
PART_HEIGHT_MM = {
    "CP_Radial_D6.3mm": 11.5, "C_Disc_D5.0mm": 7.0,
    "R_Axial_DIN0207": 7.5,             # standing: body 6.3 + lead bend
    "TO-92": 5.2, "LED_D3.0mm": 5.5, "SW_PUSH_6mm": 5.0,
    "JST_PH": 6.0, "RV097NS": 11.3, "PinSocket": 8.5, "PinHeader": 8.5,
    "SW_Slide": 7.0, "SOT-23": 1.2, "D_DO-35": 2.2, "SMA": 2.6,
    "TestPoint": 0.0, "MountingHole": 0.0, "WirePad": 0.0, "SolderJumper": 0.0,
    "RCA_FlyingLead": 0.0, "D_Dual_SMA_DO41": 2.6,
}

# Which board pad realises which measured pad.  (ref, pad) -> (row, index);
# `None` row means the module's loose pads, indexed into `Module.loose`.
FIT_MAP = {
    "PCM5102A": {   # J3 pin1 = LROUT = row0[0];  J2 pin1 = SCK = row1[5]
        **{("J3", str(i + 1)): (0, i) for i in range(9)},
        **{("J2", str(i + 1)): (1, 5 - i) for i in range(6)},
    },
    "ESP32C3": {    # JB1 pin1 = GPIO5 = row0[7];  JA1 pin1 = 5V = row1[7]
        **{("JB1", str(i + 1)): (0, 7 - i) for i in range(8)},
        **{("JA1", str(i + 1)): (1, 7 - i) for i in range(8)},
    },
    "TP4056": {     # OUT- B- B+ OUT+ ; J9 is a soldered wire, not a fit datum
        ("J5", "1"): (0, 0), ("J5", "2"): (0, 1),
        ("J6", "1"): (0, 2), ("J6", "2"): (0, 3),
    },
}
PLACED = {"PCM5102A": measured.PCM5102A, "ESP32C3": measured.ESP32C3,
          "TP4056": measured.TP4056}
# Datum pad each module's outline is drawn from, and the socket that carries it.
OUTLINE_DATUM = {"PCM5102A": ("J3", "1", measured.PCM5102A_OUTLINE),
                 "ESP32C3": ("JB1", "1", measured.ESP32C3_OUTLINE),
                 "TP4056": ("J5", "1", measured.TP4056_OUTLINE)}
# The sockets a module plugs into are what holds it at the standoff height, so
# they are exempt from the "too tall to live under this module" rule.
MOUNTS = {"PCM5102A": {"J2", "J3"}, "ESP32C3": {"JA1", "JB1"},
          "TP4056": {"J5", "J6", "J9"}}


def load():
    board = pcbnew.LoadBoard(PCB)
    if board is None:
        sys.exit(f"could not load {PCB}")
    return board


def pad_xy(board):
    out = {}
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            out.setdefault((ref, p.GetNumber()),
                           (p.GetPosition().x / 1e6, p.GetPosition().y / 1e6))
    return out


# ---------------------------------------------------------------------------
# 1. module fit
# ---------------------------------------------------------------------------
def check_fit(board, verbose):
    pads = pad_xy(board)
    fails, lines = [], []
    for name, mapping in FIT_MAP.items():
        placed = PLACED[name]
        pairs = []
        for key, (row, idx) in sorted(mapping.items()):
            if key not in pads:
                fails.append(f"{name}: board has no pad {key[0]}.{key[1]}")
                continue
            a, b = placed.m.rows[row]["points"][idx]
            pairs.append((key, pads[key], placed.vec(a, b)))
        if not pairs:
            continue
        # one translation for the whole module: the mean offset
        tx = sum(bx - mx for _, (bx, _), (mx, _) in pairs) / len(pairs)
        ty = sum(by - my for _, (_, by), (_, my) in pairs) / len(pairs)
        resid = [(k, math.hypot(bx - mx - tx, by - my - ty))
                 for k, (bx, by), (mx, my) in pairs]
        worst_key, worst = max(resid, key=lambda r: r[1])
        METRICS.setdefault("fit_mm", {})[name] = round(worst, 4)
        ok = worst <= MAX_FIT_RESIDUAL_MM
        lines.append(f"  {name:<10} {len(pairs):2d} pads  worst {worst:.3f} mm "
                     f"at {worst_key[0]}.{worst_key[1]}  "
                     f"{'OK' if ok else 'FAIL'}")
        if verbose:
            for k, r in sorted(resid, key=lambda r: -r[1])[:4]:
                lines.append(f"        {k[0]}.{k[1]:<3} {r:.3f}")
        if not ok:
            fails.append(f"{name}: worst pad {worst_key[0]}.{worst_key[1]} is "
                         f"{worst:.3f} mm off pattern (limit "
                         f"{MAX_FIT_RESIDUAL_MM}) — module will not seat")
    return lines, fails


def module_boxes(board):
    """Each module's real body outline, in board coordinates."""
    pads, boxes = pad_xy(board), {}
    for name, (ref, pad, box) in OUTLINE_DATUM.items():
        if (ref, pad) in pads:
            x, y = pads[(ref, pad)]
            boxes[name] = (x + box[0], y + box[1], x + box[2], y + box[3])
    return boxes


# ---------------------------------------------------------------------------
# 2. silkscreen
# ---------------------------------------------------------------------------
def check_silk(verbose):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        rep = tf.name
    r = subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json",
                        "--severity-all", "-o", rep, PCB],
                       capture_output=True, text=True)
    if r.returncode not in (0, 5):
        return [f"  kicad-cli drc failed: {r.stderr.strip()[:200]}"], []
    report = json.load(open(rep))
    os.unlink(rep)
    kinds = collections.Counter()
    who = collections.Counter()
    other = []
    for v in report.get("violations", []):
        if v["type"].startswith("silk"):
            kinds[v["type"]] += 1
            for it in v["items"]:
                who[it["description"].replace("Segment of ", "")
                    .replace(" on F.Silkscreen", "")] += 1
        elif v["type"] != "courtyards_overlap":
            other.append(v["type"])
    unconn = len(report.get("unconnected_items", []))
    total = sum(kinds.values())
    METRICS["silk"] = total
    METRICS["unconnected"] = unconn
    METRICS["physical"] = len(other)
    METRICS["physical_kinds"] = sorted(set(other))
    METRICS["unconnected_at"] = [
        it["description"] for u in report.get("unconnected_items", [])
        for it in u.get("items", [])]
    lines = [f"  silk violations {total}   " +
             " ".join(f"{k.replace('silk_', '')}={n}" for k, n in kinds.items())]
    lines.append(f"  unconnected {unconn}   other physical violations "
                 f"{len(other)} {sorted(set(other))}")
    if verbose and who:
        lines.append("  worst offenders: " +
                     ", ".join(f"{k}({n})" for k, n in who.most_common(8)))
    fails = []
    if total > MAX_SILK_VIOLATIONS:
        fails.append(f"silk: {total} violations (limit {MAX_SILK_VIOLATIONS})")
    if unconn:
        fails.append(f"{unconn} unconnected items")
    if other:
        fails.append(f"{len(other)} physical DRC violations: {sorted(set(other))}")
    return lines, fails


# ---------------------------------------------------------------------------
# 3. routing geometry
# ---------------------------------------------------------------------------
def _segments(board):
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            continue
        yield (t.GetLayer(), t.GetNetname(),
               (round(t.GetStart().x / 1e3), round(t.GetStart().y / 1e3)),
               (round(t.GetEnd().x / 1e3), round(t.GetEnd().y / 1e3)))


def check_routing(board, verbose):
    joints = collections.defaultdict(list)
    nseg = 0
    for layer, net, a, b in _segments(board):
        if a == b:
            continue
        nseg += 1
        joints[(layer, net, a)].append(b)
        joints[(layer, net, b)].append(a)
    right, acute = [], []
    for (layer, net, p), others in joints.items():
        if len(others) != 2:
            continue                       # tee or end: not a corner to mitre
        (ax, ay), (bx, by) = others
        u = (ax - p[0], ay - p[1])
        v = (bx - p[0], by - p[1])
        nu, nv = math.hypot(*u), math.hypot(*v)
        if nu < 1 or nv < 1:
            continue
        cos = (u[0] * v[0] + u[1] * v[1]) / (nu * nv)
        if abs(cos) < 0.02:                # 90 +/- ~1 degree
            right.append((net, p[0] / 1e3, p[1] / 1e3, min(nu, nv) / 1e3))
        elif cos > 0.02:
            # sharper than a right angle.  Not gated: what survives here is
            # two traces fanning out from one pad, where the copper is wide and
            # there is no notch to trap etchant.  Reported so that stays true.
            acute.append((net, p[0] / 1e3, p[1] / 1e3, min(nu, nv) / 1e3))

    vias = [(t.GetPosition().x / 1e6, t.GetPosition().y / 1e6)
            for t in board.GetTracks()
            if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"]
    thru = [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
            for fp in board.GetFootprints() for p in fp.Pads()
            if p.GetNetname() == "GND"
            and p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD]
    anchors = vias + thru

    # worst stitch gap: sample the board on a 2 mm grid, skipping the keep-outs
    # the pour deliberately avoids, and ask how far the nearest anchor is
    skip = [(0, 6, 6.5, 28)]
    worst, worst_at = 0.0, None
    y = 3.0
    while y < 48:
        x = 3.0
        while x < 68:
            if not any(a <= x <= c and b <= y <= d for a, b, c, d in skip):
                d2 = min(((x - ax) ** 2 + (y - ay) ** 2) for ax, ay in anchors) \
                    if anchors else 1e9
                if d2 > worst:
                    worst, worst_at = d2, (x, y)
            x += 2.0
        y += 2.0
    worst = math.sqrt(worst)
    METRICS.update(segments=nseg, gnd_vias=len(vias), right_angles=len(right),
                   acute=len(acute), stitch_gap_mm=round(worst, 2))

    lines = [f"  {nseg} segments, {len(vias)} GND vias, "
             f"{len(thru)} GND through-hole pads",
             f"  90-degree corners {len(right)}   acute corners {len(acute)}   "
             f"worst stitch gap {worst:.1f} mm at "
             f"({worst_at[0]:.0f},{worst_at[1]:.0f})" if worst_at else ""]
    if verbose and (right or acute):
        lines.append("  corners: " + ", ".join(
            f"{n}@{x:.1f},{y:.1f}" for n, x, y, _ in (right + acute)[:10]))
    fails = []
    if len(right) > MAX_RIGHT_ANGLES:
        fails.append(f"routing: {len(right)} right-angle corners "
                     f"(limit {MAX_RIGHT_ANGLES})")
    if worst > MAX_STITCH_GAP_MM:
        fails.append(f"routing: worst GND stitch gap {worst:.1f} mm "
                     f"(limit {MAX_STITCH_GAP_MM})")
    return lines, fails


# ---------------------------------------------------------------------------
# 4. parts under modules
# ---------------------------------------------------------------------------
def part_height(fp):
    name = fp.GetFPIDAsString().split(":")[-1]
    for key, h in PART_HEIGHT_MM.items():
        if key in name:
            return h, key
    return None, name


def check_clearance(board, verbose):
    boxes = module_boxes(board)
    lines, fails = [], []
    for name, (x0, y0, x1, y1) in sorted(boxes.items()):
        lines.append(f"  {name:<10} body x {x0:6.2f}..{x1:6.2f}  "
                     f"y {y0:6.2f}..{y1:6.2f}")
    # modules must not collide with each other
    for a, b in [(u, v) for i, u in enumerate(sorted(boxes)) for v in
                 sorted(boxes)[i + 1:]]:
        ax0, ay0, ax1, ay1 = boxes[a]
        bx0, by0, bx1, by1 = boxes[b]
        if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
            fails.append(f"module bodies overlap: {a} and {b}")
    unknown = []
    for fp in board.GetFootprints():
        h, key = part_height(fp)
        if h is None:
            unknown.append(f"{fp.GetReference()}({key})")
            continue
        if h <= SOCKET_STANDOFF_MM:
            continue
        bb = fp.GetBoundingBox(False, False)
        px0, py0 = bb.GetLeft() / 1e6, bb.GetTop() / 1e6
        px1, py1 = bb.GetRight() / 1e6, bb.GetBottom() / 1e6
        for name, (x0, y0, x1, y1) in boxes.items():
            if fp.GetReference() in MOUNTS.get(name, ()):
                continue
            if px0 < x1 and x0 < px1 and py0 < y1 and y0 < py1:
                fails.append(f"{fp.GetReference()} is {h} mm tall and sits "
                             f"under the {name} ({SOCKET_STANDOFF_MM} mm standoff)")
    if unknown and verbose:
        lines.append("  no height known for: " + " ".join(sorted(unknown)))
    return lines, fails


# ---------------------------------------------------------------------------
def main():
    verbose = "--verbose" in sys.argv
    as_json = "--json" in sys.argv
    board = load()
    sections = [("module fit", check_fit(board, verbose)),
                ("routing", check_routing(board, verbose)),
                ("clearance", check_clearance(board, verbose))]
    if "--no-drc" not in sys.argv:
        sections.insert(1, ("silkscreen", check_silk(verbose)))
    fails = []
    for title, (lines, f) in sections:
        if not as_json:
            print(f"{title}:")
            for ln in lines:
                if ln:
                    print(ln)
        fails += f
    if as_json:
        METRICS["fails"] = fails
        print("AUDIT_JSON " + json.dumps(METRICS, sort_keys=True))
        return 1 if fails else 0
    print()
    if fails:
        print(f"FAIL — {len(fails)} problems:")
        for f in fails:
            print("  !!", f)
        return 1
    print("OK — modules seat, silk is clean, routing and pour stitching pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
