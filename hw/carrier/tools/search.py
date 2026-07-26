#!/usr/bin/env python3
"""Sweep the board's free parameters in parallel and rank the results.

Two things about this board have no analytic answer: the order the router
takes the nets in (an early net can wall off a corridor a later one needed)
and where a handful of discrete parts sit.  Both were being explored one run
at a time — edit, run, read, edit back — which is slow and, worse, throws the
losing boards away instead of comparing them.

So: every knob is an environment variable (`gen_board.py`, `route.py` and
`audit_board.py` all read `CARRIER_BASE`), each variant builds in its own
scratch directory, and N of them run at once.  Nothing in the repo is touched;
the winning board is left in the results directory for `--promote` to copy in.

    python3 tools/search.py                      # the default sweep
    python3 tools/search.py --jobs 16 --stage placement
    python3 tools/search.py --spec my_variants.json
    python3 tools/search.py --promote var-0031   # adopt one result

Ranking is lexicographic on what actually matters, worst first: unconnected
items, physical DRC violations, silk violations, unrouted nets, right-angle
corners, stitch gap.  Ties break toward fewer segments (a tidier route).
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
PY = os.environ.get("KICAD_PYTHON", "/usr/bin/python3")
SCRATCH = os.environ.get("CARRIER_SCRATCH") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "carrier-search")
TIMEOUT = int(os.environ.get("CARRIER_SEARCH_TIMEOUT", "1800"))


# ---------------------------------------------------------------------------
# variant construction
# ---------------------------------------------------------------------------
# (name, ROUTE_GND_HALO_MM, ROUTE_GND_HALO_COST) — how hard the router tries to
# stay out of the ring around a GND through-hole.  Cost 0 disables the halo
# entirely, which is the behaviour of every board built before this sweep.
HALOS = [("halo-off", 0.0, 0.0),
         ("halo-1.0x1.5", 1.0, 1.5),
         ("halo-1.3x1.5", 1.3, 1.5),
         ("halo-1.3x4.0", 1.3, 4.0)]

SEEDS = [20260726, 11, 22, 33]

# R8 is the part whose GND pad keeps ending up on a severed pour fragment: it
# is a standing resistor in the densest signal corner, and both its own pads
# and the traces threading past them are in play.  rot180 puts the GND pad on
# the west end instead of the east one.
PLACEMENTS = [("place-as-is", {}),
              ("place-R8-flip", {"R8": [15.0, 37.0, 180]}),
              ("place-R8-east", {"R8": [24.5, 37.5, 0]})]

POURS = [("pour-0.15", "0.15", "7.0"),
         ("pour-0.10", "0.10", "7.0"),
         ("pour-0.15-dense", "0.15", "5.5")]


def sweep(stage):
    """Build the variant list for a named stage."""
    out = []
    if stage in ("router", "full"):
        for hname, hmm, hcost in HALOS:
            for seed in SEEDS:
                out.append((f"{hname}_seed{seed}", {
                    "ROUTE_GND_HALO_MM": str(hmm),
                    "ROUTE_GND_HALO_COST": str(hcost),
                    "ROUTE_SEED": str(seed)}, {}))
    if stage in ("placement", "full"):
        for pname, place in PLACEMENTS:
            for hname, hmm, hcost in HALOS[2:]:
                for seed in SEEDS[:2]:
                    out.append((f"{pname}_{hname}_seed{seed}", {
                        "ROUTE_GND_HALO_MM": str(hmm),
                        "ROUTE_GND_HALO_COST": str(hcost),
                        "ROUTE_SEED": str(seed)}, place))
    if stage in ("pour", "full"):
        for qname, zmin, spitch in POURS:
            for seed in SEEDS[:2]:
                out.append((f"{qname}_seed{seed}", {
                    "CARRIER_ZONE_MIN": zmin,
                    "CARRIER_SEED_PITCH": spitch,
                    "ROUTE_SEED": str(seed)}, {}))
    # de-duplicate by the environment actually applied, keeping the first name
    seen, uniq = set(), []
    for name, env, place in out:
        key = (tuple(sorted(env.items())), json.dumps(place, sort_keys=True))
        if key not in seen:
            seen.add(key)
            uniq.append((name, env, place))
    return uniq


# ---------------------------------------------------------------------------
# one variant
# ---------------------------------------------------------------------------
RE_ROUTE = re.compile(r"using best attempt: (\d+) segments, (\d+) vias, (\d+) failures")
RE_UNPLACED = re.compile(r"^SILK_UNPLACED (\d+)$", re.M)


def run_variant(idx, name, env_extra, place, results, keep_all):
    tag = f"var-{idx:04d}"
    work = os.path.join(SCRATCH, tag)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    # gen_board loads the project's own footprints from CARRIER_BASE
    os.symlink(os.path.join(REPO, "HYPEROSCI.pretty"),
               os.path.join(work, "HYPEROSCI.pretty"))

    env = dict(os.environ)
    env["CARRIER_BASE"] = work
    # a variant that cannot place one designator is worse, not unrankable
    env["CARRIER_SILK_STRICT"] = "0"
    env.update(env_extra)
    if place:
        env["CARRIER_PLACE"] = json.dumps(place)

    rec = {"tag": tag, "name": name, "env": env_extra, "place": place}
    t0 = time.time()

    def step(script, args=()):
        return subprocess.run([PY, os.path.join(HERE, script), *args],
                              cwd=work, env=env, capture_output=True,
                              text=True, timeout=TIMEOUT)

    try:
        r = step("gen_board.py")
        m = RE_UNPLACED.search(r.stdout)
        rec["unplaced"] = int(m.group(1)) if m else 0
        rec["unplaced_what"] = [ln.strip() for ln in r.stdout.splitlines()
                                if ln.startswith("    ")]
        if r.returncode != 0:
            rec["stage"] = "gen_board"
            rec["error"] = (r.stdout + r.stderr).strip().splitlines()[-6:]
            return finish(rec, t0, work, keep_all)
        r = step("route.py")
        rec["route_rc"] = r.returncode
        m = RE_ROUTE.search(r.stdout)
        if m:
            rec["route"] = {"segments": int(m.group(1)), "vias": int(m.group(2)),
                            "unrouted": int(m.group(3))}
        rec["route_notes"] = [ln.strip() for ln in r.stdout.splitlines()
                              if "STITCH FAIL" in ln or "UNBRIDGEABLE" in ln
                              or "STUCK" in ln or "single GND cluster" in ln]
        if not m:
            rec["stage"] = "route"
            rec["error"] = (r.stdout + r.stderr).strip().splitlines()[-6:]
            return finish(rec, t0, work, keep_all)
        r = step("audit_board.py", ("--json",))
        for ln in r.stdout.splitlines():
            if ln.startswith("AUDIT_JSON "):
                rec["audit"] = json.loads(ln[len("AUDIT_JSON "):])
        if "audit" not in rec:
            rec["stage"] = "audit"
            rec["error"] = (r.stdout + r.stderr).strip().splitlines()[-6:]
    except subprocess.TimeoutExpired:
        rec["stage"] = "timeout"
    except Exception as exc:                                # noqa: BLE001
        rec["stage"] = "exception"
        rec["error"] = [repr(exc)]
    return finish(rec, t0, work, keep_all)


def finish(rec, t0, work, keep_all):
    rec["seconds"] = round(time.time() - t0, 1)
    board = os.path.join(work, "carrier.kicad_pcb")
    if os.path.exists(board) and (keep_all or "audit" in rec):
        dest = os.path.join(SCRATCH, "boards", rec["tag"] + ".kicad_pcb")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(board, dest)
        rec["board"] = dest
    shutil.rmtree(work, ignore_errors=True)
    return rec


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------
BAD = (99, 99, 999, 99, 99, 999, 999.0, 10 ** 9)


def key(rec):
    a = rec.get("audit")
    if not a:
        return BAD
    return (a.get("unconnected", 99), a.get("physical", 99), a.get("silk", 999),
            rec.get("route", {}).get("unrouted", 99), rec.get("unplaced", 99),
            a.get("right_angles", 999),
            a.get("stitch_gap_mm", 999.0), a.get("segments", 10 ** 9))


def describe(rec):
    a = rec.get("audit")
    if not a:
        return f"{rec['tag']}  {rec['name']:<34} FAILED at {rec.get('stage','?')}"
    return (f"{rec['tag']}  {rec['name']:<34} "
            f"unconn {a.get('unconnected', '?'):>2}  "
            f"phys {a.get('physical', '?'):>2}  "
            f"silk {a.get('silk', '?'):>3}  "
            f"unrouted {rec.get('route', {}).get('unrouted', '?'):>2}  "
            f"nosilk {rec.get('unplaced', '?'):>2}  "
            f"90deg {a.get('right_angles', '?'):>2}  "
            f"gap {a.get('stitch_gap_mm', '?'):>4}  "
            f"seg {a.get('segments', '?'):>4}  "
            f"{rec['seconds']:>6.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--stage", default="full",
                    choices=["router", "placement", "pour", "full"])
    ap.add_argument("--spec", help="JSON list of [name, env, place] triples")
    ap.add_argument("--keep-all", action="store_true",
                    help="keep boards from runs that never reached the audit")
    ap.add_argument("--promote", metavar="TAG",
                    help="copy a result board into the repo and stop")
    args = ap.parse_args()

    if args.promote:
        src = os.path.join(SCRATCH, "boards", args.promote + ".kicad_pcb")
        shutil.copy2(src, os.path.join(REPO, "carrier.kicad_pcb"))
        print(f"promoted {src} -> {REPO}/carrier.kicad_pcb")
        print("NOTE: re-run gen_board.py/route.py with this variant's env to "
              "reproduce it from source before committing.")
        return 0

    variants = (json.load(open(args.spec)) if args.spec else sweep(args.stage))
    os.makedirs(SCRATCH, exist_ok=True)
    print(f"{len(variants)} variants, {args.jobs} at a time -> {SCRATCH}")
    for i, (name, env, place) in enumerate(variants):
        print(f"  var-{i:04d}  {name}  {env}  {place or ''}")
    print(flush=True)

    results = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(run_variant, i, n, e, p, results, args.keep_all): i
                for i, (n, e, p) in enumerate(variants)}
        for fut in concurrent.futures.as_completed(futs):
            rec = fut.result()
            results.append(rec)
            print(f"[{len(results):3d}/{len(variants)}] {describe(rec)}",
                  flush=True)

    results.sort(key=key)
    out = os.path.join(SCRATCH, "results.json")
    json.dump(results, open(out, "w"), indent=1)
    print(f"\nwall clock {time.time() - t0:.0f}s   full results -> {out}")
    print("\nranked (best first):")
    for rec in results:
        print("  " + describe(rec))
        for note in rec.get("route_notes", []):
            if "single GND cluster" not in note:
                print("        " + note)
        for u in rec.get("audit", {}).get("unconnected_at", []):
            print("        unconnected: " + u)
    best = results[0] if results else None
    if best and key(best) != BAD:
        print(f"\nbest: {best['tag']}  {best['name']}")
        print(f"  env   {best['env']}")
        print(f"  place {best['place']}")
        print(f"  board {best.get('board')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
