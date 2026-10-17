#!/usr/bin/env python3
"""Phase 2 loop-fill: the loop_frames field, the settle window, and the
invariant against the slave's fill limit.

Run **on the board** like the other tests (see tests/README.md). Everything
checked here is a pure controller decision -- what goes in the AUDIO field,
when it must be 0, and that the controller's fill ceiling never falls below
the slave's -- so no WiFi and no running daemon. The invariant reads the
slave's protocol.h, which the board does not carry: push it alongside the
tests (HYPE_PROTOCOL_H to point at it). Without it the invariant FAILS,
deliberately: a controller that may assume a longer settle window than the
slave can fill would hide stale-pattern fills on the very first gap after
a pattern change.
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("HC_DIR", HERE))
_TD = tempfile.mkdtemp(prefix="hcloop-")
os.environ["HYPE_STATE"] = os.path.join(_TD, "state.json")
os.environ["HYPE_PRESETS"] = os.path.join(_TD, "presets.json")

import hype_controller as hc  # noqa: E402

FAIL = []


def ok(cond, label, extra=""):
    print(("  PASS " if cond else "  FAIL ") + label)
    if not cond:
        if extra:
            print("        " + extra)
        FAIL.append(label)


def params(kind="circle", freq=60.0, a=3, b=2, tbl=None,
           pulse_rate=0.0, pulse_depth=0.0, rot=0.0):
    """A State.pattern_params() tuple: (kind, freq, amp, a, b, tbl, rmax,
    pulse_rate, pulse_depth, rot_speed, flip_x, flip_y)."""
    return (kind, freq, 0.8 * 32000.0, a, b, tbl, 0.0,
            pulse_rate, pulse_depth, rot, False, False)


# ------------------------------------------------------------ the decision
print("[loop] loop_frames_for: the repeat length, or 0")
ok(hc.loop_frames_for(params(freq=50.0)) == 960, "50 Hz -> 960 (exact)")
ok(hc.loop_frames_for(params(freq=55.0)) == 873, "55 Hz -> 873")
ok(hc.loop_frames_for(params(freq=60.0)) == 800, "60 Hz -> 800 (exact)")
ok(hc.loop_frames_for(params(freq=70.0)) == 686, "70 Hz -> 686")
ok(hc.loop_frames_for(params(freq=700.0)) == 0,
   "700 Hz: rounding drift over a fill exceeds the 4-frame bound")
ok(hc.loop_frames_for(params(rot=0.2)) == 0,
   "spin breaks periodicity -> hold")
ok(hc.loop_frames_for(params(freq=55.0, pulse_rate=10.0,
                             pulse_depth=1.0)) == 0,
   "pulse 10 Hz at 100 % depth at 55 Hz -> hold")
ok(hc.loop_frames_for(params(freq=60.0, pulse_rate=2.0,
                             pulse_depth=0.25)) > 0,
   "HyperLili's pulse (0.25 at 2 Hz, 60 Hz) keeps its loop")
ok(hc.loop_frames_for(params(freq=5.0)) == 0,
   "loop longer than the 8192-frame ring bound -> hold")
ok(hc.loop_frames_for(params(b=2.5)) == 0,
   "a non-integer ratio never closes -> hold")

# ------------------------------------------------------------ settle window
print("\n[loop] settle window: 0 for L + F frames after a change, then L")
# 66 2/3 Hz: L = 720, so L + F = 7920 = exactly 33 packets of 240 frames --
# the window is exact in both frames and packets.
p66 = params(freq=200.0 / 3.0)
L = hc.loop_frames_for(p66)
ok(L == 720, "66 2/3 Hz -> 720", str(L))
ok(L + hc.LOOP_FILL_CEIL_FRAMES == 33 * hc.FRAMES,
   "the test's L + F divides the packet size",
   f"L+F={L + hc.LOOP_FILL_CEIL_FRAMES}")
lf = hc.LoopField()
seq = [lf.next(p66) for _ in range(40)]
ok(seq[0] == 0, "the first packet counts as a change")
ok(sum(1 for v in seq if v == 0) == 33,
   "exactly L + F frames of hold: 33 packets", str(seq[:6]))
ok(all(v == L for v in seq[33:]), "then L, for good")
ok(lf.next(params(freq=200.0 / 3.0)) == L,
   "a fresh-but-equal params tuple opens no new window")

tbl = [(0, 1), (1, 0), (0, -1), (-1, 0)]
lf2 = hc.LoopField()
for _ in range(40):
    lf2.next(params(kind="text", tbl=tbl))
ok(lf2.next(params(kind="text", tbl=tbl)) == 800, "steady: the field is L")
ok(lf2.next(params(kind="text", tbl=list(tbl))) == 0,
   "a rebuilt table with identical content re-opens the window")

# ---------------------------------------------------------------- invariant
print("\n[loop] the invariant: controller ceiling >= the slave's limit")
PROTO = os.environ.get("HYPE_PROTOCOL_H", os.path.normpath(os.path.join(
    HERE, "..", "..", "esp32-slave", "include", "protocol.h")))
try:
    with open(PROTO) as f:
        proto_src = f.read()
except OSError as e:
    ok(False, f"slave protocol.h not readable at {PROTO} ({e}) -- push it "
              "alongside the tests and point HYPE_PROTOCOL_H at it")
else:
    m = re.search(r"#define\s+HYPE_LOOP_FILL_MAX_MS\s+(\d+)", proto_src)
    ok(m is not None, "protocol.h defines HYPE_LOOP_FILL_MAX_MS")
    if m:
        ok(hc.LOOP_FILL_CEIL_MS >= int(m.group(1)),
           f"controller ceiling {hc.LOOP_FILL_CEIL_MS} ms >= slave "
           f"{m.group(1)} ms")

# ------------------------------------------------- the show's own presets
print("\n[loop] the show's presets all carry a usable loop")
SHOW = os.environ.get("HYPE_SHOW_PRESETS",
                      os.path.expanduser("~/hype_presets.json"))
if os.path.exists(SHOW):
    with open(SHOW) as f:
        data = json.load(f)
    fields = {}
    for p in data:
        if not (isinstance(p, dict) and p.get("name")):
            continue
        q = hc.clean_preset(p)
        fields[q["name"]] = hc.loop_frames_for(
            (q["kind"], q["freq"], q["amp"] * 32000.0, q["a"], q["b"],
             None, 0.0, q["pulse_rate"], q["pulse_depth"], q["rot"],
             q["flip_x"], q["flip_y"]))
    bad = {k: v for k, v in fields.items() if not v}
    ok(not bad, f"all {len(fields)} show presets keep a loop field",
       json.dumps(fields))
else:
    print(f"  (no preset file at {SHOW} -- preset check skipped)")

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
