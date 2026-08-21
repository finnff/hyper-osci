#!/usr/bin/env python3
"""kind="figure": the baked-table loader, and the show's own artwork.

Run ON THE BOARD (tests/README.md). Points HYPE_STATE/HYPE_PRESETS/HYPE_CYCLES
at a temp dir before importing the controller, like the other suites, so it
can never touch the show's real files. HYPE_FIGURES is pointed at a temp dir
too, then the real FIGURE_DIR is checked separately at the end.
"""

import os
import struct
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="hcfig")
REAL_FIGURES = os.environ.get("HYPE_FIGURES",
                              os.path.expanduser("~/hype_figures"))
os.environ["HYPE_STATE"] = os.path.join(TMP, "state.json")
os.environ["HYPE_PRESETS"] = os.path.join(TMP, "presets.json")

os.environ["HYPE_FIGURES"] = os.path.join(TMP, "figures")
os.mkdir(os.environ["HYPE_FIGURES"])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hype_controller as hc            # noqa: E402  (env set above)

FAIL = []


def ok(cond, label, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"   [{extra}]" if extra else ""))
    if not cond:
        FAIL.append(label)


def write_tbl(name, pts, magic=hc.FIGURE_MAGIC, count=None):
    raw = bytearray()
    for x, y in pts:
        raw += struct.pack("<hh", int(x * 32767), int(y * 32767))
    blob = magic + struct.pack("<I", len(pts) if count is None else count) + bytes(raw)
    with open(os.path.join(hc.FIGURE_DIR, name + ".tbl"), "wb") as f:
        f.write(blob)


SQUARE = [(-0.9, -0.9), (0.9, -0.9), (0.9, 0.9), (-0.9, 0.9)]

print("\n[figure] the loader")
write_tbl("square", SQUARE)
tbl = hc.load_figure("square")
ok(tbl is not None and len(tbl) == 4, "a baked table loads", f"{tbl}")
ok(tbl and max(abs(x - a) + abs(y - b)
               for (x, y), (a, b) in zip(tbl, SQUARE)) < 1e-4,
   "int16 round-trip keeps the geometry")
ok(hc.list_figures() == ["square"], "list_figures names it once",
   f"{hc.list_figures()}")

# The cache must not outlive the file it copied.
write_tbl("square", [(0.1, 0.1), (0.2, 0.2)])
os.utime(os.path.join(hc.FIGURE_DIR, "square.tbl"), (1e9, 1e9))
ok(len(hc.load_figure("square")) == 2, "a re-baked file is picked up, not cached")

print("\n[figure] a bad file draws a circle, it never crashes the stream")
write_tbl("wrongmagic", SQUARE, magic=b"NOPENOPE")
ok(hc.load_figure("wrongmagic") is None, "wrong magic is refused")
write_tbl("shortcount", SQUARE, count=99)
ok(hc.load_figure("shortcount") is None, "a count that disagrees is refused")
with open(os.path.join(hc.FIGURE_DIR, "empty.tbl"), "wb") as f:
    f.write(b"")
ok(hc.load_figure("empty") is None, "an empty file is refused")
ok(hc.load_figure("no_such_thing") is None, "a missing file is refused")
ok(hc.build_shape("figure", "", "simplex", "no_such_thing") == (None, 0.0),
   "build_shape reports no table, so block() falls back to a circle")

print("\n[figure] no path can escape FIGURE_DIR")
ok(hc.sanitize_figure("../../etc/passwd") == "passwd", "..-traversal is stripped",
   hc.sanitize_figure("../../etc/passwd"))
ok(hc.sanitize_figure("/etc/shadow") == "shadow", "an absolute path is stripped")
ok(hc.sanitize_figure("square.tbl") == "square", "the .tbl suffix is optional")

print("\n[figure] presets and live state carry the figure name")
p = hc.clean_preset({"name": "art", "kind": "figure", "figure": "square"})
ok(p["kind"] == "figure" and p["figure"] == "square",
   "clean_preset whitelists kind=figure and keeps the name")
old = hc.clean_preset({"name": "old build", "kind": "text", "text": "X"})
ok(old["figure"] == "", "a preset written before figures existed still loads")
missing = hc.clean_preset({"name": "x", "kind": "figure", "figure": "gone"})
ok(missing["figure"] == "gone",
   "a figure absent from THIS board keeps its name instead of being erased")

st = hc.State("figure")
st.figure = "square"
tbl, rmax = hc.build_shape("figure", "", "simplex", "square")
st.shape_tbl, st.shape_rmax = tbl, rmax
hc.save_live(st.live_snapshot())
st2 = hc.State("circle")
ok(st2.kind == "figure" and st2.figure == "square",
   "the live figure survives a restart", f"{st2.kind}/{st2.figure}")
ok(st2.shape_tbl is not None and len(st2.shape_tbl) == 2,
   "and its table is rebuilt before the first block")

print("\n[figure] the block loop draws the table, not a circle")
# Its own file: the cache check above deliberately rewrote "square".
WEDGE = [(0.9, 0.0), (0.0, 0.45), (-0.6, -0.3), (0.3, -0.8)]
write_tbl("wedge", WEDGE)
tbl4, rmax4 = hc.build_shape("figure", "", "simplex", "wedge")
g = hc.PatternGen()
amp = 0.9 * 32000.0
params = ("figure", 600.0, amp, 3, 2, tbl4, rmax4, 1.5, 0.0, 0.0, False, False)
buf = g.block(240, params)
vals = memoryview(buf).cast("h")
ok(len(buf) == 240 * 4, "a full 5 ms stereo block comes back", f"{len(buf)} B")
ok(max(abs(v) for v in vals) <= 32767, "and nothing overflows int16")
# 4 entries at 600 redraws/s is 0.05 entries per sample, so the block walks
# all four and nothing else: every emitted pair must BE a table entry.
want = {(int(x * amp), int(y * amp)) for x, y in tbl4}
got = {(vals[2 * i], vals[2 * i + 1]) for i in range(240)}
ok(got <= want, "every sample is a table entry, scaled", f"{sorted(got)}")
ok(len(got) == 4, "and the whole table is traced in one block", f"{len(got)}")
circ = hc.PatternGen().block(240, ("circle",) + params[1:])
ok(circ != buf, "a circle of the same amp/freq is a different waveform")

print("\n[figure] the show's own artwork, in FIGURE_DIR on this board")
os.environ["HYPE_FIGURES"] = REAL_FIGURES
hc.FIGURE_DIR = REAL_FIGURES
hc._figure_cache.clear()
shipped = hc.list_figures()
print(f"  {REAL_FIGURES}: {shipped or 'nothing baked'}")
for name in shipped:
    t = hc.load_figure(name)
    ok(t is not None and len(t) > 100, f"{name!r} loads")
    if t:
        r = hc.table_rmax(t)
        ok(0.5 < r < 1.28, f"{name!r} sits inside the int16 guard band",
           f"rmax {r:.3f}, {len(t)} pts")

print(f"\n{len(FAIL)} check(s) failed: {FAIL}" if FAIL else "\nall checks passed")
sys.exit(1 if FAIL else 0)
