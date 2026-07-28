#!/usr/bin/env python3
"""Plot the fab package and refuse to ship one that does not verify.

pcb.md 8.4 is a checklist a human is supposed to walk before uploading.  Every
item on it is mechanical enough to assert, so this asserts them and only writes
the zip if they all hold.  The point is that "I plotted gerbers" and "I plotted
gerbers that match the board that passed DRC" are different claims.

Plot settings are chosen so one zip serves both fabs: Protel extensions, plain
RS-274X (X2 and netlist attributes off -- the X2 attributes still go out as G04
comments, which is what fabs actually parse), Excellon in mm with the route
command for oval holes.  That last one is not cosmetic: SW1's plated slots only
appear as slots under G00/G01, and pcb.md 7 says to verify them on the drill
file rather than the board.  Under the kicad-cli default (--excellon-oval-format
alternate) they would plot as something else entirely.

No paste layers.  No stencil is ordered and the only true SMD part is Q1 on
hand-solder pads.

Run from hw/carrier/:  /usr/bin/python3 tools/plot_fab.py [--skip-drc]
"""
import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile

BASE = os.environ.get("CARRIER_BASE") or os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))
PCB = os.path.join(BASE, "carrier.kicad_pcb")
FAB = os.path.join(BASE, "fab")
OUT = os.path.join(FAB, "gerbers")
ZIP = os.path.join(FAB, "hyperosci-carrier-v1.1-gerbers.zip")

LAYERS = ["F.Cu", "B.Cu", "F.Mask", "B.Mask", "F.Silkscreen", "B.Silkscreen",
          "Edge.Cuts"]
# (filename, what pcb.md 8.4 calls it)
EXPECT = [("carrier-F_Cu.gtl", "Copper,L1,Top"),
          ("carrier-B_Cu.gbl", "Copper,L2,Bot"),
          ("carrier-F_Mask.gts", "Soldermask,Top"),
          ("carrier-B_Mask.gbs", "Soldermask,Bot"),
          ("carrier-F_Silkscreen.gto", "Legend,Top"),
          ("carrier-B_Silkscreen.gbo", "Legend,Bot"),
          ("carrier-Edge_Cuts.gm1", "Profile,NP"),
          ("carrier.drl", None),
          ("carrier-job.gbrjob", None)]

BOARD_MM = (70.0, 50.0)
EDGE_TOL = 0.001
# pcb.md 7: the JLCPCB 2x-aspect fix, verified where it matters.
SLOTS = [("47.08", "47.98"), ("51.98", "52.88")]
SLOT_TOOL = "T4C0.900"

fails = []


def check(ok, label, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"{args[0]} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def board_counts():
    """Pads and vias straight out of the s-expression, for reconciliation."""
    s = open(PCB).read()
    pads, vias = [], re.findall(r"\(via\b", s)
    for fp in re.split(r"\n\t\(footprint ", s)[1:]:
        pads += re.findall(r'\(pad\s+"[^"]*"\s+(\w+)\s+', fp)
    return pads.count("thru_hole"), pads.count("smd"), len(vias)


def extents(path):
    """mm bbox of the drawing body, skipping the format spec in the header."""
    body = open(path).read().split("APERTURE END LIST", 1)[-1]
    xs = [int(v) / 1e6 for v in re.findall(r"X(-?\d+)", body)]
    ys = [int(v) / 1e6 for v in re.findall(r"Y(-?\d+)", body)]
    return (min(xs), max(xs), min(ys), max(ys)) if xs and ys else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-drc", action="store_true",
                    help="for iterating on the plot itself; never for a real order")
    args = ap.parse_args()

    os.makedirs(FAB, exist_ok=True)

    if not args.skip_drc:
        print("DRC")
        # -o, or kicad-cli litters carrier-drc.rpt next to the board.
        out = run("kicad-cli", "pcb", "drc", "--severity-error", "--severity-warning",
                  "--exit-code-violations", "-o", os.path.join(FAB, "drc-report.txt"),
                  PCB)
        check("Found 0 violations" in out, "0 DRC violations", out.strip().split("\n")[0])
        check("Found 0 unconnected items" in out, "0 unconnected items")
        if fails:
            sys.exit("\nDRC is not clean -- not plotting.")

    shutil.rmtree(FAB + "/gerbers", ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    print("\nPlot")
    run("kicad-cli", "pcb", "export", "gerbers", "-o", OUT + "/",
        "--layers", ",".join(LAYERS),
        "--no-x2", "--no-netlist", "--check-zones", "--precision", "6", PCB)
    run("kicad-cli", "pcb", "export", "drill", "-o", OUT + "/",
        "--format", "excellon", "--drill-origin", "absolute",
        "--excellon-units", "mm", "--excellon-zeros-format", "decimal",
        "--excellon-oval-format", "route",
        "--generate-report", "--report-path", os.path.join(FAB, "drill-report.txt"),
        PCB)
    for name, _ in EXPECT:
        print(f"  {name}")

    print("\nVerify (pcb.md 8.4)")
    for name, fn in EXPECT:
        p = os.path.join(OUT, name)
        if not os.path.exists(p):
            check(False, f"{name} present")
            continue
        if fn:
            check(f"TF.FileFunction,{fn}" in open(p).read(), f"{name} -> {fn}")

    edge = extents(os.path.join(OUT, "carrier-Edge_Cuts.gm1"))
    w, h = edge[1] - edge[0], edge[3] - edge[2]
    check(abs(w - BOARD_MM[0]) < EDGE_TOL and abs(h - BOARD_MM[1]) < EDGE_TOL,
          "profile is 70 x 50 mm", f"{w:.3f} x {h:.3f}")
    check(open(os.path.join(OUT, "carrier-Edge_Cuts.gm1")).read().count("D01") == 4,
          "profile is a single closed 4-segment outline")

    for name in ("carrier-F_Cu.gtl", "carrier-B_Cu.gbl"):
        e = extents(os.path.join(OUT, name))
        inside = (e[0] >= edge[0] and e[1] <= edge[1]
                  and e[2] >= edge[2] and e[3] <= edge[3])
        check(inside, f"{name} inside the profile",
              f"X {e[0]:.3f}..{e[1]:.3f} Y {e[2]:.3f}..{e[3]:.3f}")

    tht, smd, vias = board_counts()
    for name, expect, why in (
            ("carrier-F_Cu.gtl", tht + smd + vias, f"{tht + smd} pads + {vias} vias"),
            ("carrier-B_Cu.gbl", tht + vias, f"{tht} THT pads + {vias} vias")):
        got = len(re.findall(r"D03", open(os.path.join(OUT, name)).read()))
        check(got == expect, f"{name} flashes reconcile", f"{got} vs {expect} ({why})")

    drl = open(os.path.join(OUT, "carrier.drl")).read()
    check(SLOT_TOOL in drl, f"slot tool {SLOT_TOOL}")
    for a, b in SLOTS:
        # (?![\d.]) rather than \b -- the coordinate is followed by "Y", and
        # \b never fires between two word characters.
        check(re.search(rf"G00X{a}Y[-\d.]+\s*\n\s*M15\s*\n\s*G01X{b}(?![\d.])", drl)
              is not None, f"SW1 slot routes X{a} -> X{b}", "0.90 mm travel, aspect 2.0")
    nvia = re.search(r"T1C0\.400\b", drl)
    check(nvia is not None, "via tool T1 = 0.400")

    for name in ("F.Paste", "B.Paste"):
        check(not any(name.replace(".", "_") in f for f, _ in EXPECT),
              f"no {name} (no stencil ordered)")

    if fails:
        sys.exit(f"\n{len(fails)} check(s) failed -- zip NOT written: {fails}")

    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for name, _ in EXPECT:
            z.write(os.path.join(OUT, name), name)
    print(f"\nAll checks passed.\n{ZIP}\n"
          f"  {os.path.getsize(ZIP) / 1024:.0f} KiB, {len(EXPECT)} files\n\n"
          "Still walk the two items 8.4 leaves to a human: load the zip in the fab's\n"
          "online viewer AND a second one, and confirm SW1 against paper-doll-1to1.pdf.")


if __name__ == "__main__":
    main()
