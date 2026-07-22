#!/usr/bin/env python3
"""Regression checks for the six bugs + the eight show names.

Run with HERSHEY_DIR pointing at a hershey-fonts tree. Exit 0 = all pass.
"""
import json
import math
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("HC_DIR", HERE))
# Isolate from the live board BEFORE import: State() now restores the saved
# live pattern, so without this the tests inherit whatever is on the scopes
# and assertions about default fields fail for the wrong reason. Must also
# never let a test write the real files -- that cost the show's presets once.
_TD = tempfile.mkdtemp(prefix="hctest-")
os.environ["HYPE_STATE"] = os.path.join(_TD, "state.json")
os.environ["HYPE_PRESETS"] = os.path.join(_TD, "presets.json")
import hype_controller as hc  # noqa: E402

FAIL = []
NAMES = ["HYPERWAVES", "Flux Collective", "Anémi", "Gan D",
         "Guild Navigator", "Eastern Distributor", "HyperLili", "Liso"]
FONTS = list(hc.TEXT_FONTS)


def ok(cond, label, extra=""):
    print(("  PASS " if cond else "  FAIL ") + label + (" — " + extra if extra else ""))
    if not cond:
        FAIL.append(label)


def params(tbl, rmax, amp, rot_speed=0.0, kind="text", freq=90.0,
           pulse_depth=0.0):
    return (kind, freq, amp * 32000.0, 3, 2, tbl, rmax,
            1.5, pulse_depth, rot_speed, False, False)


# ---------------------------------------------------------------- 1.1 crash
print("\n[1.1] OverflowError: text + spin + amp")
worst = []
for txt in ("W", "H", "LIVE|SET|2026", "HYPEROSCI", "M", "I") + tuple(NAMES):
    for f in FONTS:
        tbl, rmax = hc.build_text(txt, f)
        if not tbl:
            continue
        worst.append((rmax, txt, f))
        g = hc.PatternGen()
        try:
            for _ in range(400):  # 2 s of blocks, spin sweeps a full turn
                g.block(240, params(tbl, rmax, 1.0, rot_speed=0.5))
        except OverflowError as e:
            ok(False, f"{txt!r}/{f} amp=1.00 spin=0.5", str(e))
            break
    else:
        continue
    break
else:
    ok(True, "amp 1.00 + spin survives every string x font",
       f"{len(worst)} combinations, 400 blocks each")
worst.sort(reverse=True)
print(f"       worst rmax = {worst[0][0]:.3f} ({worst[0][1]!r}/{worst[0][2]})"
      f"  -> old ceiling amp {32767/(32000*worst[0][0]):.3f}")

# the guard must not shrink anything that was already safe
tbl, rmax = hc.build_text("HYPEROSCI", "simplex")
g = hc.PatternGen()
b = g.block(240, params(tbl, rmax, 1.0))
peak = max(abs(v) for v in memoryview(b).cast("h"))
# 0.9 * 32000 = 28800: span normalisation, unrotated. The point is that the
# guard is NOT binding here — 32767/rmax must sit above the requested amp.
ok(32767 / rmax > 32000 and peak == 28800,
   "safe strings keep full amplitude (guard not binding)",
   f"rmax={rmax:.3f} ceiling={32767/rmax:.0f} > amp=32000, peak={peak}")

# and it must actually clamp the unsafe one, not merely avoid raising
tbl, rmax = hc.build_text("W", "times")
g = hc.PatternGen()
peak = 0
for _ in range(200):
    b = g.block(240, params(tbl, rmax, 1.0, rot_speed=0.5))
    peak = max(peak, max(abs(v) for v in memoryview(b).cast("h")))
ok(32000 < peak <= 32767, "clamped output still uses the full int16 range",
   f"rmax={rmax:.3f} peak={peak}")

# ------------------------------------------------------------------ 1.2 rot
print("\n[1.2] rot resets when spin returns to 0")
g = hc.PatternGen()
tbl, rmax = hc.build_text("HYPEROSCI", "simplex")
for _ in range(37):
    g.block(240, params(tbl, rmax, 0.8, rot_speed=0.5))
spun = g.rot
g.block(240, params(tbl, rmax, 0.8, rot_speed=0.0))
ok(spun > 0.1 and g.rot == 0.0, "spin off -> upright",
   f"after 37 spin blocks rot={spun:.4f} rad, then rot={g.rot}")
# and a fresh generator at the same table position must agree: no residual tilt
g2 = hc.PatternGen()
g2.tpos, g2.lfo = g.tpos, g.lfo
a = list(memoryview(g.block(240, params(tbl, rmax, 0.8))).cast("h"))
b = list(memoryview(g2.block(240, params(tbl, rmax, 0.8))).cast("h"))
ok(a == b, "post-reset output matches a never-spun generator exactly")

# -------------------------------------------------------------- 1.3/1.4 presets
print("\n[1.3/1.4] preset loader: bad font, missing + extra fields")
old = hc.PRESETS_FILE
fd, path = tempfile.mkstemp(suffix=".json")
os.close(fd)
hc.PRESETS_FILE = path
try:
    with open(path, "w") as f:
        json.dump([
            {"name": "full", "kind": "text", "freq": 90.0, "amp": 0.4,
             "a": 3, "b": 2, "text": "Anémi", "font": "gothic",
             "pulse_rate": 1.0, "pulse_depth": 0.2, "rot": 0.0,
             "flip_x": False, "flip_y": True},
            {"name": "old build", "kind": "text", "text": "Liso"},   # 1.4
            {"name": "bad font", "kind": "text", "text": "Gan D",
             "font": "comic sans"},                                   # 1.3
            {"name": "junk", "kind": "wat", "freq": "x", "amp": 99},
            {"name": "", "kind": "text"},
        ], f)
    ps = hc.load_presets()
    ok(len(ps) == 4, "old-build preset survives a new field",
       f"{[p['name'] for p in ps]}")
    ok(all(p["font"] in hc.TEXT_FONTS for p in ps),
       "every loaded font is real", f"{[p['font'] for p in ps]}")
    ok(ps[1]["freq"] == hc.PRESET_DEFAULTS["freq"] and ps[1]["amp"] == 0.8,
       "missing fields take defaults", f"freq={ps[1]['freq']}")
    j = next(p for p in ps if p["name"] == "junk")
    ok(j["kind"] == "circle" and j["freq"] == hc.PRESET_DEFAULTS["freq"]
       and j["amp"] == 1.0, "junk values clamped", str(j)[:90])
    # the fix that matters: a re-save must not lose anyone
    hc.save_presets(ps)
    ok(len(hc.load_presets()) == 4, "round-trip keeps all four")
    # and a bad font must not silently become a circle
    tbl, _ = hc.build_text(ps[2]["text"], ps[2]["font"])
    r = [math.hypot(x, y) for x, y in tbl]
    ok(max(r) - min(r) > 0.2, "loaded preset draws text, not a circle",
       f"radius spread {min(r):.3f}..{max(r):.3f}")
finally:
    os.unlink(path)
    hc.PRESETS_FILE = old

# ------------------------------------------------------------------ 1.6 mutex
print("\n[1.6] rebuild mutex + partial write-back")
st = hc.State("text")
ok(hasattr(st, "rebuild_lock"), "State carries the rebuild mutex")


# exercise the real handler methods without a socket
H = hc.make_http_handler(st, None)
inst = H.__new__(H)
inst._json = lambda obj, code=200: obj
inst._apply_pattern(st, {"text": "one"},
                    ("one", "gothic") + hc.build_text("one", "gothic"))
ok(st.text == "one" and st.font == "simplex",
   "a text-only POST does not write font back",
   f"text={st.text!r} font={st.font!r}")
inst._apply_pattern(st, {"font": "times"},
                    ("one", "times") + hc.build_text("one", "times"))
ok(st.font == "times" and st.text == "one", "a font-only POST sets only font")
ok(st.text_rmax > 0, "rmax travels with every table write",
   f"{st.text_rmax:.3f}")

# concurrency: two overlapping rebuilds must not lose either field
st = hc.State("text")
errs = []


def poke(body, tf):
    for _ in range(60):
        with st.rebuild_lock:
            with st.lock:
                text, font = st.text, st.font
            text = body.get("text", text)
            font = body.get("font", font)
            inst._apply_pattern(st, body, (text, font) + hc.build_text(text, font))
        with st.lock:
            if st.text != tf[0] or st.font not in hc.TEXT_FONTS:
                errs.append((st.text, st.font))


t1 = threading.Thread(target=poke, args=({"text": "AAA"}, ("AAA",)))
t2 = threading.Thread(target=poke, args=({"font": "gothic"}, ("AAA",)))
t1.start(); t2.start(); t1.join(); t2.join()
ok(st.text == "AAA" and st.font == "gothic",
   "120 interleaved rebuilds keep both fields",
   f"text={st.text!r} font={st.font!r} strays={len(errs)}")

# ------------------------------------------------------------- the 8 names
print("\n[names] the show list")
print(f"  {'name':22s} {'font':8s} {'rmax':>5s} {'pts':>5s} {'L':>6s} "
      f"{'jump%':>6s} {'90Hz':>5s} {'40Hz':>5s}")
for nm in NAMES:
    for f in ("simplex", "gothic"):
        tbl, rmax = hc.build_text(nm, f)
        if not tbl:
            ok(False, f"{nm!r}/{f} renders")
            continue
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                for a, b in zip(tbl, tbl[1:]))
        step = L / len(tbl)
        jumps = sum(1 for a, b in zip(tbl, tbl[1:])
                    if math.hypot(b[0] - a[0], b[1] - a[1]) > 3 * step)
        d90 = len({int(i * len(tbl) * 90 / 48000) for i in range(48000 // 90)})
        d40 = len({int(i * len(tbl) * 40 / 48000) for i in range(48000 // 40)})
        print(f"  {nm:22s} {f:8s} {rmax:5.3f} {len(tbl):5d} {L:6.2f} "
              f"{100*jumps/len(tbl):5.1f}% {d90:5d} {d40:5d}")

# '?' must not appear: Anémi has to be five real glyphs
g_simplex = hc._jhf_glyphs(hc.TEXT_FONTS["simplex"])
q_strokes = g_simplex[ord("?")][2]
t_acc, _ = hc.build_text("Anémi", "simplex")
t_plain, _ = hc.build_text("Anemi", "simplex")
t_q, _ = hc.build_text("An?mi", "simplex")


def sig(t):
    return round(sum(math.hypot(b[0] - a[0], b[1] - a[1])
                     for a, b in zip(t, t[1:])), 4)


ok(sig(t_acc) != sig(t_q), "'Anémi' is not rendered as 'An?mi'",
   f"L(acc)={sig(t_acc)} L(?)={sig(t_q)}")
ok(sig(t_acc) > sig(t_plain), "the acute adds ink over 'Anemi'",
   f"L(acc)={sig(t_acc)} L(plain)={sig(t_plain)}")
for f in FONTS:
    t, _ = hc.build_text("Anémi", f)
    ok(t is not None and sig(t) > sig(hc.build_text("Anemi", f)[0]),
       f"accent composes in {f}")

print("\n[extras] wider accent coverage")
for s in ("ÀÁÂÃÄÅ", "àáâãäå", "çÇñÑ", "ïíìî", "õöøœ", "Straße", "Anémi—Liso",
          "  Gan D  ", "café ’n’ ñu"):
    t, r = hc.build_text(s, "simplex")
    ok(t is not None and len(t) > 100, f"{s!r} renders", f"pts={len(t)} rmax={r:.3f}")

# sanitizer must pass é through and still drop control chars
s = hc.sanitize_text("Anémi\x07\nLiso")
ok(s == "Anémi\nLiso", "sanitize_text: NFC composes, control chars dropped",
   repr(s))

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
sys.exit(1 if FAIL else 0)
