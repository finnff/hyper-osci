#!/usr/bin/env python3
"""HYPEROSCI controller daemon: HYPE v1 streamer + web control panel.

Supersedes hype_sender.py (kept as a minimal protocol reference). Adds:
  - Embedded web UI (stdlib http.server, port 8080) — pattern / frequency /
    amplitude control, stream on/off, per-slave and all-slave mode toggles
    (network / local-mic / hybrid), identify, gain, reboot.
  - HYPE_CMD sender (JSON, unicast :5001) driving mode_manager::handle_command.

Streaming engine is inherited from hype_sender.py: SYNC multicast beacons,
deadline-stamped AUDIO unicast to every discovered slave, STATUS listener.

Usage (on the UNO-Q, as the AP host):
  python3 hype_controller.py [--iface-ip 192.168.50.1] [--http-port 8080]
Then browse http://10.42.0.128:8080 (USB tether) or http://192.168.50.1:8080.
"""

import argparse
import errno
import json
import math
import os
import select
import socket
import struct
import sys
import threading
import time
import unicodedata
from array import array
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HYPE_MAGIC = 0x45505948
HYPE_VERSION = 1
HYPE_AUDIO, HYPE_SYNC, HYPE_CMD, HYPE_STATUS = 1, 2, 3, 4

MCAST_GROUP = "239.0.0.1"
PORT_AUDIO, PORT_CTRL, PORT_STATUS = 5000, 5001, 5002

HDR = struct.Struct("<IBBHIQ")  # magic, ver, type, flags, seq, timestamp_us
AUDIO_HDR = struct.Struct("<IHH")  # sample_rate, frame_count, reserved
STATUS_PAYLOAD = struct.Struct("<6sBBBbHHIIIIi")  # + pattern byte + lost uint32

SAMPLE_RATE = 48000
FRAMES = 240  # 5 ms
PACKET_US = FRAMES * 1_000_000 // SAMPLE_RATE
# Deadline lead = the slaves' steady-state buffer depth. The UNO-Q's ath10k
# AP radio goes deaf for up to ~300 ms every ~1.44 s (firmware quirk — see
# config.h JB_CAPACITY_FRAMES); 350 ms lead rides through it against the
# slave's 512 ms jitter buffer. Latency is irrelevant for scope art — only
# slave-to-slave sync matters, and a shared deadline keeps them locked
# regardless of lead. 450 ms leaves ~150 ms in the buffer through a worst-case
# stall (vs ~50 ms at 350) while staying under the 512 ms ring.
LEAD_US = 450_000
SYNC_INTERVAL_US = 500_000
# How often stream_loop publishes its TX counters onto state.
TXSTAT_INTERVAL_US = 1_000_000
# What a full send buffer actually raises. `audio` is non-blocking, and a
# non-blocking UDP socket with no room reports EAGAIN — ENOBUFS is what you
# get on a blocking socket or straight off a full device queue. netstat's
# "send buffer errors" counts both without distinguishing them, which is
# exactly how a night of EAGAIN got written up as ENOBUFS. Both mean the one
# thing that matters: the packet died in this box and never reached the air.
TX_QUEUE_FULL = frozenset((errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS))

MODE_NAMES = {0: "local", 1: "network", 2: "hybrid"}
PATTERN_NAMES = {0: "mic", 1: "circle", 2: "lissajous", 3: "ramp", 4: "square"}


# ---------------------------------------------------------------------------
# Text rendering: Hershey single-stroke vector fonts -> XY point table.
# Same algorithm as osci-render's text path (glyph strokes -> normalized
# shapes -> constant arc-length traversal), see docs/text-rendering-findings.md.
# ---------------------------------------------------------------------------

HERSHEY_DIR = os.environ.get("HERSHEY_DIR", "/usr/share/hershey-fonts")
TEXT_FONTS = {  # friendly name -> file from the hershey-fonts-data package
    "simplex": "futural.jhf",
    "duplex": "futuram.jhf",
    "script": "scriptc.jhf",
    "gothic": "gothiceng.jhf",
    "times": "timesrb.jhf",
    "italic": "timesi.jhf",
}
TEXT_TABLE_POINTS = 2000  # equal-arc-length samples per rendered path
_glyph_cache = {}

# Streamed-pattern presets (artist names etc.), persisted across restarts.
PRESETS_FILE = os.environ.get(
    "HYPE_PRESETS", os.path.expanduser("~/hype_presets.json"))
PRESETS_MAX = 20   # the page's "max N" text tracks this, see __PRESETS_MAX__
# The LIVE pattern, persisted too. Without this a reboot came back drawing a
# circle whatever was on the scopes, and the only way to know was to look --
# on a dark stage, with no laptop. Same field set as a preset, so clean_preset
# clamps and whitelists both and an older build's file still loads.
STATE_FILE = os.environ.get(
    "HYPE_STATE", os.path.expanduser("~/hype_state.json"))
# "name" plus these. Every field is OPTIONAL on load and filled from the
# default here, so adding an entry can never disqualify a preset written by an
# older build — the filter used to require all of them, and the next save then
# rewrote the file from the surviving list, deleting the show's artist names
# with no log line.
PRESET_DEFAULTS = {"kind": "circle", "freq": 100.0, "amp": 0.8, "a": 3, "b": 2,
                   "text": "HYPEROSCI", "font": "simplex", "pulse_rate": 1.5,
                   "pulse_depth": 0.0, "rot": 0.0,
                   "flip_x": False, "flip_y": False}


def sanitize_text(s):
    """Anything a phone keyboard can produce -> drawable text.

    NFC first, so a decomposed 'e'+U+0301 becomes the single 'é' that
    render_text can compose an accent for; control characters out; newline
    (like '|') starts a display line.
    """
    s = unicodedata.normalize("NFC", str(s))
    return "".join(c for c in s if c == "\n" or c.isprintable())[:80]


def sanitize_name(s):
    # Preset names are embedded in onclick attributes client-side — keep them
    # to a safe charset instead of escaping in four places.
    return "".join(c for c in unicodedata.normalize("NFC", str(s))
                   if c.isalnum() or c in " ._-()&+")[:24].strip()


def clean_preset(p):
    """Preset dict -> fully populated, range-checked copy.

    Every value is clamped and every choice whitelisted right here, so the
    load path cannot install a font name that does not exist: render_text
    returns None for one, and block() then quietly draws a circle while
    /api/state still reports kind="text" — unbreakable on a dark stage.
    """
    q = {"name": sanitize_name(p.get("name", ""))}
    for k, default in PRESET_DEFAULTS.items():
        v = p.get(k, default)
        try:
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            v = default
        q[k] = v
    if q["kind"] not in ("circle", "lissajous", "rose", "text"):
        q["kind"] = PRESET_DEFAULTS["kind"]
    if q["font"] not in TEXT_FONTS:
        q["font"] = PRESET_DEFAULTS["font"]
    q["freq"] = min(2000.0, max(1.0, q["freq"]))
    q["amp"] = min(1.0, max(0.0, q["amp"]))
    q["a"] = min(9, max(1, q["a"]))
    q["b"] = min(9, max(1, q["b"]))
    q["pulse_rate"] = min(10.0, max(0.1, q["pulse_rate"]))
    q["pulse_depth"] = min(1.0, max(0.0, q["pulse_depth"]))
    q["rot"] = min(2.0, max(-2.0, q["rot"]))
    q["text"] = sanitize_text(q["text"])
    return q


def load_presets():
    try:
        with open(PRESETS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [q for q in (clean_preset(p) for p in data
                        if isinstance(p, dict) and p.get("name"))
            if q["name"]][:PRESETS_MAX]


def save_presets(presets):
    tmp = PRESETS_FILE + ".tmp"
    try:
        # ONE generation of undo, kept because the artist names ARE the show
        # and every path into this function is destructive: a delete, or
        # anything that empties the in-memory list, overwrites the file with
        # [] and leaves no trace to recover from. One generation covers a
        # single bad write, NOT a run of deletes -- delete five presets in a
        # row and the backup holds only the fourth.
        #   cp ~/hype_presets.json.bak ~/hype_presets.json && \
        #     sudo systemctl restart hyperosci-controller
        try:
            with open(PRESETS_FILE, "rb") as src:
                prev = src.read()
            if prev.strip() not in (b"", b"[]"):   # never shadow a good file
                with open(PRESETS_FILE + ".bak", "wb") as dst:
                    dst.write(prev)
        except OSError:
            pass                                   # no file yet: nothing to keep
        with open(tmp, "w") as f:
            json.dump(presets, f, indent=1)
        os.replace(tmp, PRESETS_FILE)  # atomic: never a half-written file
    except OSError as e:
        print(f"[presets] save failed: {e}", flush=True)


def load_live():
    """Saved live pattern -> clean dict, or None if there is no usable one.

    Anything unreadable, truncated or hand-edited into nonsense returns None
    and the daemon starts on defaults. A bad state file must never be able to
    stop the rig from booting.
    """
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return clean_preset(d) if isinstance(d, dict) else None


def save_live(d):
    """Atomically persist the live pattern. Returns an OSError, or None."""
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, STATE_FILE)  # atomic: never a half-written file
    except OSError as e:
        return e
    return None


# Interval timers: "show preset P on slaves 1+2 for 20 s every 5 minutes".
# Small cap on purpose -- these interrupt the show, and a screenful of rules
# nobody can reason about is worse than none.
TIMERS_FILE = os.environ.get(
    "HYPE_TIMERS", os.path.expanduser("~/hype_timers.json"))
TIMERS_MAX = 8
# targets = slave IDs, NOT ips: a slave keeps its id across a DHCP lease and
# across a reflash, and the id is what the dashboard prints on the card.
# Empty = every slave currently discovered.
TIMER_DEFAULTS = {"enabled": True, "preset": "", "targets": [],
                  "hold_s": 20, "every_s": 300}


def clean_timer(t):
    """Timer dict -> fully populated, range-checked copy (id filled by caller).

    Same contract as clean_preset: every field optional on load and filled
    from the defaults, so a rule written by an older build still loads.
    """
    q = {"id": max(0, int(t.get("id", 0)) if str(t.get("id", 0)).lstrip("-")
                   .isdigit() else 0)}
    q["preset"] = sanitize_name(t.get("preset", ""))
    q["enabled"] = bool(t.get("enabled", TIMER_DEFAULTS["enabled"]))
    ids = t.get("targets", [])
    if not isinstance(ids, (list, tuple)):
        ids = []
    seen = []
    for v in ids:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= n <= 255 and n not in seen:
            seen.append(n)
    q["targets"] = sorted(seen)[:16]
    for k in ("hold_s", "every_s"):
        try:
            q[k] = int(float(t.get(k, TIMER_DEFAULTS[k])))
        except (TypeError, ValueError):
            q[k] = TIMER_DEFAULTS[k]
    q["hold_s"] = min(600, max(1, q["hold_s"]))
    q["every_s"] = min(86400, max(5, q["every_s"]))
    # A period inside the hold would re-fire before the restore ever ran and
    # the show would never come back.
    q["every_s"] = max(q["every_s"], q["hold_s"] + 1)
    return q


def load_timers():
    try:
        with open(TIMERS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [q for q in (clean_timer(t) for t in data if isinstance(t, dict))
            if q["preset"]][:TIMERS_MAX]


def save_timers(timers):
    """Atomically persist the timer rules. No .bak generation, unlike
    save_presets: a lost rule is fifteen seconds of retyping, a lost artist
    name is the show."""
    tmp = TIMERS_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(timers, f, indent=1)
        os.replace(tmp, TIMERS_FILE)
    except OSError as e:
        print(f"[timers] save failed: {e}", flush=True)


def _jhf_glyphs(fname):
    """Parse a Hershey .jhf font -> {ascii: (left, right, [polyline, ...])}.

    .jhf line: 5-char glyph id, 3-char vertex count (includes the margin
    pair), then coordinate chars offset from 'R'; long glyphs wrap across
    lines; " R" is pen-up. Glyphs map sequentially onto ASCII from 32.
    """
    if fname in _glyph_cache:
        return _glyph_cache[fname]
    with open(os.path.join(HERSHEY_DIR, fname)) as f:
        lines = f.read().splitlines()
    glyphs = {}
    code = 32
    R = ord("R")
    i = 0
    while i < len(lines) and code < 127:
        line = lines[i]
        i += 1
        if not line.strip():
            continue
        nverts = int(line[5:8])
        need = 8 + 2 * nverts
        while len(line) < need and i < len(lines):
            line += lines[i]
            i += 1
        left, right = ord(line[8]) - R, ord(line[9]) - R
        strokes, cur = [], []
        for j in range(1, nverts):
            cx, cy = line[8 + 2 * j], line[9 + 2 * j]
            if cx == " " and cy == "R":  # pen up
                if cur:
                    strokes.append(cur)
                    cur = []
            else:  # Hershey Y grows downward; flip so +Y is up (scope)
                cur.append((ord(cx) - R, R - ord(cy)))
        if cur:
            strokes.append(cur)
        glyphs[code] = (left, right, strokes)
        code += 1
    _glyph_cache[fname] = glyphs
    return glyphs


# Combining marks drawn as extra strokes, in the faces' own units: x is centred
# on the base glyph's ink and y=0 sits just above it (just below, for the
# BELOW set). Cap height is 21 units and the ' glyph spans 6 of them, so a
# ~4.5-unit accent box matches the proportions Hershey drew.
_MARKS = {
    "\u0300": [[(-2.2, 4.6), (2.2, 0.4)]],                       # grave
    "\u0301": [[(-2.2, 0.4), (2.2, 4.6)]],                       # acute
    "\u0302": [[(-2.8, 0.8), (0.0, 4.6), (2.8, 0.8)]],           # circumflex
    "\u0303": [[(-3.0, 1.2), (-1.9, 3.4), (-0.6, 3.6),           # tilde
                (0.6, 1.4), (1.9, 1.2), (3.0, 3.4)]],
    "\u0304": [[(-2.8, 2.4), (2.8, 2.4)]],                       # macron
    "\u0306": [[(-2.6, 4.4), (-1.8, 1.6), (0.0, 1.0),            # breve
                (1.8, 1.6), (2.6, 4.4)]],
    "\u0307": [[(0.0, 1.6), (0.0, 3.6)]],                        # dot above
    "\u0308": [[(-2.0, 1.6), (-2.0, 3.8)], [(2.0, 1.6), (2.0, 3.8)]],
    "\u0309": [[(-0.6, 0.6), (0.8, 1.8), (0.4, 3.2), (-0.8, 3.4)]],  # hook
    "\u030a": [[(0.0, 0.6), (1.1, 1.1), (1.6, 2.2), (1.1, 3.3),  # ring
                (0.0, 3.8), (-1.1, 3.3), (-1.6, 2.2), (-1.1, 1.1),
                (0.0, 0.6)]],
    "\u030b": [[(-2.8, 0.6), (-0.8, 4.6)], [(0.6, 0.6), (2.6, 4.6)]],
    "\u030c": [[(-2.8, 4.6), (0.0, 0.8), (2.8, 4.6)]],           # caron
    "\u0327": [[(0.0, 0.0), (0.7, -1.5), (-1.1, -2.1), (-1.6, -3.6)]],
    "\u0328": [[(0.6, 0.0), (-0.7, -1.4), (0.5, -2.6), (1.8, -2.2)]],
}
_MARKS_BELOW = frozenset(("\u0327", "\u0328"))  # cedilla, ogonek

# No Hershey glyph and no decomposition we can draw: expand to ASCII the face
# does have, so a name pasted off a phone keyboard renders instead of turning
# into a row of '?'.
_FOLD = {
    " ": " ", "­": "", "«": "<<", "»": ">>",
    "×": "x", "÷": "/", "‐": "-", "‑": "-",
    "‒": "-", "–": "-", "—": "-", "―": "-",
    "‘": "'", "’": "'", "‚": ",", "“": '"',
    "”": '"', "„": '"', "•": ".", "…": "...",
    "‹": "<", "›": ">", "−": "-",
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe",
    "Œ": "OE", "ø": "o", "Ø": "O", "þ": "th",
    "Þ": "Th", "ð": "d", "Ð": "D", "đ": "d",
    "Đ": "D", "ł": "l", "Ł": "L", "ı": "i",
    "ȷ": "j", "µ": "u", "ƒ": "f",
}


def _glyph_for(glyphs, ch):
    """One character -> [(left, right, strokes), ...] — 0, 1 or more glyphs.

    A .jhf file holds printable ASCII and nothing else (95 glyphs), so a bare
    table lookup turns every accented letter into '?'. Decompose instead: the
    base letter comes from the face, the combining marks are drawn from
    _MARKS, and 'é' is a real 'e' under a real acute. Marks we cannot draw are
    dropped (bare base letter beats '?'); characters with no base at all fold
    to ASCII.
    """
    g = glyphs.get(ord(ch))
    if g is not None:
        return [g]
    dec = unicodedata.normalize("NFD", ch)
    base = dec[0]
    g = glyphs.get(ord(base))
    if g is None:
        sub = _FOLD.get(ch, _FOLD.get(base))
        if sub is None:
            q = glyphs.get(ord("?"))
            return [q] if q is not None else []
        return [x for c in sub for x in _glyph_for(glyphs, c)]
    marks = [m for m in dec[1:] if m in _MARKS]
    if not marks:
        return [g]
    left, right, strokes = g
    if base in "ij" and any(m not in _MARKS_BELOW for m in marks):
        # Drop the tittle: a stroke lying entirely above the x-height.
        strokes = [s for s in strokes if min(y for _, y in s) <= 5]
    ink = [p for s in strokes for p in s] or [(0, 0)]
    xs = [p[0] for p in ink]
    cx = (min(xs) + max(xs)) / 2.0
    out = list(strokes)
    above = max(y for _, y in ink) + 2.0
    below = min(y for _, y in ink) - 0.5
    for m in marks:
        if m in _MARKS_BELOW:
            out += [[(cx + mx, below + my) for mx, my in s] for s in _MARKS[m]]
            below -= 4.5
        else:
            out += [[(cx + mx, above + my) for mx, my in s] for s in _MARKS[m]]
            above += 5.0
    return [(left, right, out)]


def render_text(text, font):
    """Text -> [(x, y), ...] table normalized to [-1, 1], resampled to equal
    arc steps so the beam moves at constant speed (uniform trace brightness).
    Pen-up gaps cost zero table entries: the beam jumps in one sample.
    '|' in the text starts a new line. Returns None if nothing is drawable.
    """
    try:
        glyphs = _jhf_glyphs(TEXT_FONTS[font])
    except (OSError, KeyError, ValueError, IndexError):
        return None
    strokes = []
    line_h = 32.0
    for row, linetext in enumerate(text.replace("|", "\n").split("\n")):
        dy = -row * line_h
        # rstrip so a stray trailing space cannot pull the line off centre
        gl = [g for ch in linetext.rstrip() for g in _glyph_for(glyphs, ch)]
        # centre each line on its own width (not left-aligned in the block)
        x = -sum(right - left for left, right, _ in gl) / 2.0
        for left, right, gstrokes in gl:
            for st in gstrokes:
                strokes.append([(x + sx - left, sy + dy) for sx, sy in st])
            x += right - left
    pts = [p for s in strokes for p in s]
    if len(pts) < 2:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    k = 1.8 / span  # 90% of full scale, same headroom as osci-render
    strokes = [[((px - cx) * k, (py - cy) * k) for px, py in s]
               for s in strokes]

    total = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                for s in strokes for a, b in zip(s, s[1:]))
    if total <= 0.0:
        return None
    step = total / TEXT_TABLE_POINTS
    tbl = []
    need = 0.0  # arc distance (jumps excluded) at which the next point falls
    dist = 0.0
    for s in strokes:
        for a, b in zip(s, s[1:]):
            seglen = math.hypot(b[0] - a[0], b[1] - a[1])
            if seglen == 0.0:
                continue
            while need <= dist + seglen:
                t = (need - dist) / seglen
                tbl.append((a[0] + (b[0] - a[0]) * t,
                            a[1] + (b[1] - a[1]) * t))
                need += step
            dist += seglen
    return tbl or None


def table_rmax(tbl):
    """Largest |point| in a table — the per-block amplitude ceiling.

    render_text normalises by the bounding-box SPAN, which bounds |x| and |y|
    at 0.9 each but lets the radius reach 0.9*sqrt(2) = 1.273. Rotation mixes
    the axes, so amp*r can pass int16 and array("h") raises OverflowError
    inside the stream loop, which has no handler: the daemon dies and comes
    back at CLI defaults with the live look gone. block() caps against this.
    """
    return max((math.hypot(x, y) for x, y in tbl), default=0.0) if tbl else 0.0


def build_text(text, font):
    """(text, font) -> (table, rmax), the pair every writer of state must set."""
    tbl = render_text(text, font)
    return tbl, table_rmax(tbl)


def mono_us():
    return time.monotonic_ns() // 1000


def persist_loop(state, period=3.0):
    """Debounce the live pattern to disk: a slider drag is one write, not 60.

    Its own thread on purpose. os.replace on the eMMC can block for tens of
    milliseconds, which off the stream thread is nothing and on it would be
    several missed 5 ms blocks -- an audible gap on every scope at once.
    """
    last, warned = None, False
    while True:
        time.sleep(period)
        # An interval timer's hold is a 20 s interruption, not "what was on
        # the scopes": persisting it would bring the rig back drawing the
        # ident after a power blip, the exact failure this file prevents.
        # dirty stays set, so the restore lands on the very next tick.
        if state.timer_hold is not None:
            continue
        if not state.dirty:
            continue
        state.dirty = False   # a write landing here just re-arms us next tick
        cur = state.live_snapshot()
        if cur == last:
            continue
        err = save_live(cur)
        if err is None:
            last, warned = cur, False
        elif not warned:
            warned = True     # once per outage, not every 3 s forever
            print(f"[state] cannot save {STATE_FILE}: {err}", flush=True)


def apply_preset(state, p):
    """Install a preset-shaped dict as the live streamed pattern.

    The one place that does the rebuild dance, shared by the dashboard's
    preset tap and the interval timers. Lock order is the documented one:
    rebuild_lock, then state.lock, and the font parse happens between them so
    it never stalls the 5 ms stream pacing.
    """
    with state.rebuild_lock:
        p = clean_preset(p)   # never install an unknown font (circle!)
        tbl, rmax = build_text(p["text"], p["font"])
        with state.lock:
            state.kind, state.freq, state.amp = p["kind"], p["freq"], p["amp"]
            state.ratio_a, state.ratio_b = p["a"], p["b"]
            state.pulse_rate = p["pulse_rate"]
            state.pulse_depth = p["pulse_depth"]
            state.rot_speed = p["rot"]
            state.flip_x, state.flip_y = p["flip_x"], p["flip_y"]
            state.text, state.font = p["text"], p["font"]
            state.text_tbl, state.text_rmax = tbl, rmax
            state.text_ver += 1
        state.dirty = True


# A slave's draw mode is only known from its STATUS beacon, which arrives
# once a second. Straight after a release the controller's copy still says
# "network" for a slave it has just put back on local, so a hold starting
# inside that window would snapshot the wrong mode and restore the slave to
# network for good. Two seconds is past one beacon plus jitter; it also stops
# two rules stacking stings back to back, which looks broken anyway.
HOLD_COOLDOWN_US = 2_500_000

# Why a fire was refused. The string is the reason the dashboard shows, so
# there is one wording to keep true instead of the handler guessing at three
# possibilities. FIRE_RETRY marks the transient ones: timer_loop tries those
# again in a moment rather than costing the rule its whole turn.
FIRE_HOLDING = "another timer is already on air"
FIRE_COOLDOWN = ("a hold just released — the slaves' modes are not "
                 "trustworthy yet")
FIRE_MUTED = "STREAM is off — a hold would pull the slaves onto a dead stream"
FIRE_NO_PRESET = "that preset no longer exists"
FIRE_RETRY = (FIRE_HOLDING, FIRE_COOLDOWN)


def fire_timer(state, cmds, t):
    """Start a hold: install the rule's preset, put its targets on the stream.

    Returns None once the hold is running, else the FIRE_* reason it did not
    start.
    """
    before = state.live_snapshot()
    with state.lock:
        if state.timer_hold is not None:
            return FIRE_HOLDING
        if mono_us() < state.hold_cooldown_us:
            return FIRE_COOLDOWN
        # A hold drags its targets onto the stream, so with the stream muted
        # it is not an interruption but a dark scope for the whole hold --
        # and the slaves it took were drawing their own mic input a moment
        # earlier. Muting for a changeover is exactly when a rule comes due.
        if not state.stream_on:
            return FIRE_MUTED
        p = next((dict(x) for x in state.presets
                  if x["name"] == t["preset"]), None)
        if p is None:
            return FIRE_NO_PRESET
        ips = [ip for ip, s in state.slaves.items()
               if not t["targets"] or s["id"] in t["targets"]]
        state.timer_hold = {
            "id": t["id"], "preset": t["preset"],
            "until_us": mono_us() + t["hold_s"] * 1_000_000,
            "pattern": before,
            "modes": {ip: state.slaves[ip]["mode"] for ip in ips}}
    apply_preset(state, p)
    for ip in ips:
        cmds.send(ip, {"cmd": "set_mode", "mode": "network"})
    print(f"[timer] hold {t['preset']!r} on "
          f"{','.join(ips) if ips else 'nobody (no slave matched)'} "
          f"for {t['hold_s']}s", flush=True)
    return None


def end_hold(state, cmds, restore_pattern=True):
    """Finish a hold: targets back to the draw setting they had, and — unless
    the operator took the panel over mid-hold — the pre-hold pattern back."""
    with state.lock:
        h, state.timer_hold = state.timer_hold, None
        if h is None:
            return
        # Armed in the same acquisition that clears the hold. Split across
        # two, timer_loop can land in between, see "not holding" with the
        # cooldown still at its old value, and fire on mode data the STATUS
        # beacons have not refreshed yet -- the very thing it guards.
        state.hold_cooldown_us = mono_us() + HOLD_COOLDOWN_US
    for ip, mode in h["modes"].items():
        cmds.send(ip, {"cmd": "set_mode",
                       "mode": MODE_NAMES.get(mode, "local")})
    if restore_pattern:
        apply_preset(state, h["pattern"])
    print(f"[timer] release {h['preset']!r}"
          + ("" if restore_pattern else " (panel taken over)"), flush=True)


def timer_loop(state, cmds, tick=0.25):
    """Fire and release the interval rules.

    Its own thread for the same reason as persist_loop: a font rebuild on the
    stream thread is an audible gap on every scope at once.

    One hold at a time — there is a single streamed pattern, so two rules
    interrupting together would only fight over it. A rule that comes due
    during someone else's hold keeps its due time and fires just after that
    one releases, once the cooldown has passed.
    """
    while True:
        time.sleep(tick)
        now = mono_us()
        due = None
        with state.lock:
            holding = state.timer_hold is not None
            expired = holding and now >= state.timer_hold["until_us"]
            if not holding:
                for t in state.timers:
                    if not t["enabled"]:
                        continue
                    # First sight of a rule schedules it a full period out: a
                    # restart must not fire into a rig someone is still
                    # cabling up.
                    nxt = state.timer_next.setdefault(
                        t["id"], now + t["every_s"] * 1_000_000)
                    if nxt <= now:
                        due = dict(t)
                        break
        if expired:
            end_hold(state, cmds)
        elif due is not None:
            # Only a fire that actually happened costs the rule its turn.
            # Rearming before the call meant a rule queued behind another
            # rule's hold came due 250 ms after the release, hit the
            # post-release cooldown, and then waited a whole period -- the
            # one case this loop's docstring promises to handle.
            reason = fire_timer(state, cmds, due)
            delay = (HOLD_COOLDOWN_US if reason in FIRE_RETRY
                     else due["every_s"] * 1_000_000)
            with state.lock:
                # Skip a rule deleted during the fire (its timer_next is
                # already gone, and re-adding it would orphan the entry), and
                # never stomp a due time an HTTP save/toggle just set.
                if any(x["id"] == due["id"] for x in state.timers) \
                        and state.timer_next.get(due["id"], 0) <= now:
                    state.timer_next[due["id"]] = now + delay


class State:
    """Shared between the stream loop and HTTP threads. Lock-protected."""

    def __init__(self, pattern):
        self.lock = threading.Lock()
        self.kind = pattern       # circle | lissajous | rose | text
        self.freq = 100.0         # base Hz
        self.amp = 0.8            # 0..1 of int16 headroom
        self.ratio_a = 3          # lissajous X multiplier / rose petal count
        self.ratio_b = 2          # lissajous Y multiplier
        self.text = "HYPEROSCI"
        self.font = "simplex"     # TEXT_FONTS key
        self.pulse_rate = 1.5     # amplitude-LFO Hz
        self.pulse_depth = 0.0    # 0..1 of amp (0 = steady)
        self.rot_speed = 0.0      # revolutions/s (0 = static)
        self.flip_x = False       # mirror left-right (scope polarity)
        self.flip_y = False       # mirror top-bottom (scope polarity)
        # Come back drawing whatever was on the scopes, not a circle. Restored
        # before the table is built, so the very first block is already right
        # -- no flash of the default pattern on stage. Command-line --pattern
        # only decides the FIRST ever boot, before a state file exists.
        saved = load_live()
        if saved:
            (self.kind, self.freq, self.amp, self.ratio_a, self.ratio_b,
             self.text, self.font, self.pulse_rate, self.pulse_depth,
             self.rot_speed, self.flip_x, self.flip_y) = (
                saved["kind"], saved["freq"], saved["amp"], saved["a"],
                saved["b"], saved["text"], saved["font"],
                saved["pulse_rate"], saved["pulse_depth"], saved["rot"],
                saved["flip_x"], saved["flip_y"])
        # None off-board; rmax rides along so block() can cap amp (see
        # table_rmax) without walking the table.
        self.text_tbl, self.text_rmax = build_text(self.text, self.font)
        self.text_ver = 1         # bumped on rebuild; UI refetches preview
        self.presets = load_presets()  # [{name + PRESET_DEFAULTS keys}, ...]
        # Deliberately NOT persisted: a restart always comes back streaming.
        # The failure modes are not symmetric -- a rig that boots silent after
        # a power blip because someone muted it during setup is far worse than
        # one that boots drawing when you wanted quiet, and the STREAM button
        # says which it is at a glance.
        self.stream_on = True
        # Set by any writer of the fields above; cleared by persist_loop.
        self.dirty = False
        self.slaves = {}          # ip -> dict(status fields + last_us)
        # Held across snapshot -> render -> write-back of the text table. Two
        # overlapping rebuilds would otherwise revert each other's field, and
        # would put two CPU-bound threads on top of stream_loop — the regime
        # where block gaps reach 300-600 ms and every scope rebuffers.
        # Ordering: take this BEFORE self.lock, never the other way round.
        self.rebuild_lock = threading.Lock()
        # Written by stream_loop's bind_egress transition, read by snapshot.
        # None = not probed yet; False = no interface holds net_iface, which
        # means no AP, which means no slave can ever appear. The dashboard
        # says so out loud -- an empty slave list looks identical whether the
        # AP is down or the slaves are simply off.
        self.net_iface = ""
        self.net_egress = None
        # ip -> {"ok","full","err","pct"}, rebuilt once a second by
        # stream_loop and stored atomically. Never mutated in place, so
        # snapshot() can read it without taking a lock the audio path would
        # then have to contend for.
        self.net_tx = {}
        # Interval timers. All three are touched by both timer_loop and the
        # HTTP threads, so all three live under self.lock.
        self.timers = load_timers()
        self.next_timer_id = 1 + max([t["id"] for t in self.timers], default=0)
        # id -> mono_us of the next fire. Runtime only: a rule that survived a
        # restart starts its period from boot rather than firing immediately
        # into a rig someone is still cabling up.
        self.timer_next = {}
        # None, or the rule currently interrupting the show:
        # {"id", "preset", "until_us", "pattern": pre-hold live snapshot,
        #  "modes": {ip: mode int before we forced it to network}}
        self.timer_hold = None
        # Set on release; see HOLD_COOLDOWN_US.
        self.hold_cooldown_us = 0

    def pattern_params(self):
        with self.lock:
            return (self.kind, self.freq, self.amp * 32000.0,
                    self.ratio_a, self.ratio_b, self.text_tbl, self.text_rmax,
                    self.pulse_rate, self.pulse_depth, self.rot_speed,
                    self.flip_x, self.flip_y)

    def live_snapshot(self):
        """Just the persisted subset, taken under the lock."""
        with self.lock:
            return {"kind": self.kind, "freq": self.freq, "amp": self.amp,
                    "a": self.ratio_a, "b": self.ratio_b, "text": self.text,
                    "font": self.font, "pulse_rate": self.pulse_rate,
                    "pulse_depth": self.pulse_depth, "rot": self.rot_speed,
                    "flip_x": self.flip_x, "flip_y": self.flip_y}

    def snapshot(self):
        now = mono_us()
        with self.lock:
            return {
                "pattern": {"kind": self.kind, "freq": self.freq,
                            "amp": self.amp, "a": self.ratio_a,
                            "b": self.ratio_b, "text": self.text,
                            "font": self.font,
                            "pulse_rate": self.pulse_rate,
                            "pulse_depth": self.pulse_depth,
                            "rot": self.rot_speed,
                            "flip_x": self.flip_x, "flip_y": self.flip_y,
                            "tver": self.text_ver},
                "presets": [p["name"] for p in self.presets],
                "stream_on": self.stream_on,
                "slaves": [dict(s, age_ms=(now - s["last_us"]) // 1000)
                           for s in self.slaves.values()],
                "net": {"iface": self.net_iface, "egress": self.net_egress,
                        "tx": self.net_tx},
                "timers": [dict(t, next_in=(
                    None if not t["enabled"] else
                    max(0, (self.timer_next.get(t["id"], now) - now) // 1000000)
                )) for t in self.timers],
                "hold": None if not self.timer_hold else {
                    "id": self.timer_hold["id"],
                    "preset": self.timer_hold["preset"],
                    "left_s": max(0, (self.timer_hold["until_us"] - now)
                                  // 1000000)},
            }


class PatternGen:
    """Stateful stereo test-pattern generator (X = L, Y = R).

    Single base phase; ratios are multipliers so ratio changes stay
    phase-continuous (no scope-trace jump mid-stream).
    """

    def __init__(self):
        self.phase = 0.0
        self.tpos = 0.0  # text: fractional index into the point table
        self.lfo = 0.0   # text: pulse-LFO phase
        self.rot = 0.0   # text: current rotation angle

    def block(self, n, params):
        (kind, freq, amp, a, b, ttbl, rmax,
         pulse_rate, pulse_depth, rot_speed, flip_x, flip_y) = params
        out = array("h", bytes(4 * n))  # n stereo frames, zeroed
        two_pi = 2.0 * math.pi
        sin = math.sin
        if kind == "text" and ttbl:
            # Walk the precomputed equal-arc-length table at freq redraws/s.
            # Pulse (amp LFO) and rotation advance per block — 5 ms steps are
            # smooth for the sub-10 Hz rates these run at.
            m = len(ttbl)
            tstep = m * freq / SAMPLE_RATE  # table entries per sample
            ae = amp * (1.0 - pulse_depth * (0.5 - 0.5 * sin(self.lfo)))
            if rmax > 0.0:
                ae = min(ae, 32767.0 / rmax)  # int16 guard, see table_rmax
            # Spin off means upright. Without the else the angle stays frozen
            # wherever the slider left it: the axes keep mixing, so "turn spin
            # off" is not an escape from anything and the word sits crooked
            # with no control that can straighten it.
            if rot_speed:
                rot = self.rot
                self.rot = (rot + two_pi * rot_speed * n / SAMPLE_RATE) % two_pi
            else:
                rot = self.rot = 0.0
            rc, rs = math.cos(rot), sin(rot)
            fx = -1.0 if flip_x else 1.0  # mirror before rotation
            fy = -1.0 if flip_y else 1.0
            axx, axy = ae * rc * fx, ae * rs * fy  # X = x*axx - y*axy
            ayx, ayy = ae * rs * fx, ae * rc * fy  # Y = x*ayx + y*ayy
            pos = self.tpos
            if pos >= m:  # table swapped for a shorter one mid-stream
                pos %= m
            for i in range(n):
                x, y = ttbl[int(pos)]
                out[2 * i] = int(x * axx - y * axy)      # X
                out[2 * i + 1] = int(x * ayx + y * ayy)  # Y
                pos += tstep
                if pos >= m:
                    pos -= m
            self.tpos = pos
            self.lfo = (self.lfo +
                        two_pi * pulse_rate * n / SAMPLE_RATE) % two_pi
            return out.tobytes()
        # text with no drawable table falls through to a plain circle.
        step = two_pi * freq / SAMPLE_RATE
        p = self.phase
        if kind == "lissajous":
            half_pi = math.pi / 2.0
            for i in range(n):
                out[2 * i] = int(amp * sin(a * p + half_pi))      # X
                out[2 * i + 1] = int(amp * sin(b * p))            # Y
                p += step
        elif kind == "rose":
            cos = math.cos
            for i in range(n):
                r = sin(a * p)
                out[2 * i] = int(amp * r * cos(p))                # X
                out[2 * i + 1] = int(amp * r * sin(p))            # Y
                p += step
        else:  # circle
            half_pi = math.pi / 2.0
            for i in range(n):
                out[2 * i] = int(amp * sin(p + half_pi))          # X (cos)
                out[2 * i + 1] = int(amp * sin(p))                # Y
                p += step
        # Wrap only between blocks: ratios stay integer-coherent within one.
        self.phase = p % two_pi
        return out.tobytes()


class CmdSender:
    """Unicast HYPE_CMD (JSON) to slaves on :5001."""

    def __init__(self, state):
        self.state = state
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0
        self.lock = threading.Lock()

    def send(self, ip, cmd_obj):
        payload = json.dumps(cmd_obj, separators=(",", ":")).encode()
        with self.lock:
            seq = self.seq
            self.seq += 1
        pkt = HDR.pack(HYPE_MAGIC, HYPE_VERSION, HYPE_CMD, 0, seq,
                       mono_us()) + payload
        targets = [ip]
        if ip == "all":
            with self.state.lock:
                targets = list(self.state.slaves.keys())
        for t in targets:
            try:
                self.sock.sendto(pkt, (t, PORT_CTRL))
            except OSError as e:
                print(f"[cmd] send to {t} failed: {e}", flush=True)
        print(f"[cmd] {ip} <- {cmd_obj}", flush=True)


# ---------------------------------------------------------------------------
# Web UI (single embedded page, no external assets — works on the AP with no
# internet). Polls /api/state at 1 Hz; controls POST to /api/pattern|/api/cmd.
# ---------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HYPEROSCI</title>
<style>
:root {
  --bg:#070b07; --panel:#0d140d; --line:#33492f; --fg:#c9e8c9;
  --dim:#7e9c7e; --ph:#39ff14; --warn:#ffb347; --bad:#ff5252;
}
html { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; padding: 0; touch-action: manipulation; }
body { background: var(--bg); color: var(--fg); font: 14px/1.45 ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace; padding: 16px; }
h1 { font-size: 18px; letter-spacing: .35em; color: var(--ph); text-shadow: 0 0 12px rgba(57,255,20,.55); }

/* === Connection banner === */
.disconnection-banner {
  display: none; background: #3d1010; color: var(--bad); padding: 8px; text-align: center; border-radius: 4px; margin-bottom: 8px;
}
body.disconnected .disconnection-banner { display: block; }
body.disconnected { filter: saturate(0.3); }

/* === Mode toggle === */
#mode-toggle {
  margin-left: auto; padding: 6px 12px; border: 1px solid var(--line);
  background: var(--panel); color: var(--fg); border-radius: 4px; cursor: pointer; font: inherit;
}
#mode-toggle:hover { border-color: var(--ph); }
#conn-status { font-size: 12px; padding: 4px 8px; border-radius: 4px; }
#conn-status.ok { color: var(--ph); }
#conn-status.stale { color: var(--warn); }

/* === Panel base === */
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }

/* === Rig strip (SHOW) === */
.rig-strip {
  position: sticky;
  top: 0;
  z-index: 100;
  display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
  gap: 4px; padding: 8px;
}
.rig-tile {
  background: var(--panel); border: 1px solid var(--line); border-radius: 4px;
  padding: 8px; cursor: pointer; text-align: center; min-height: 72px;
  display: flex; flex-direction: column; align-items: center; gap: 2px;
}
.rig-tile:active { border-color: var(--ph); }
.rig-tile.lost { border-color: var(--bad); background: #1a0a0a; }
.tile-id { font-weight: bold; font-size: 16px; }
.tile-mode { font-size: 11px; color: var(--dim); }
.tile-status { font-size: 10px; color: var(--bad); font-weight: bold; display: none; }
.rig-tile.lost .tile-status { display: block; }
.tile-age { font-size: 10px; color: var(--dim); }
.battery-bar { display: flex; gap: 2px; margin-top: 2px; }
.battery-bar span { width: 12px; height: 6px; background: var(--line); border-radius: 1px; }
.battery-bar span.filled { background: var(--ph); }

/* === Stale indicator (red border, not opacity) === */
.stale { border-left: 3px solid var(--bad); }
.stale::before { content: "LAST SEEN "; color: var(--bad); font-weight: bold; font-size: 10px; display: block; }

/* === Danger buttons (visible without hover) === */
button.danger { border-color: var(--bad); color: var(--bad); }
button.danger:hover { background: var(--bad); color: var(--bg); }

/* === Preview canvas === */
#preview-wrap { width: 100%; display: flex; justify-content: center; margin: 4px 0; }
canvas#preview { max-width: 100%; height: auto; background: #020402; border: 1px solid var(--line); border-radius: 8px; }

/* === On air section === */
.on-air-label { color: var(--dim); font-size: 12px; text-transform: uppercase; letter-spacing: .1em; }
.on-air-name { font-size: 18px; color: var(--ph); text-align: center; margin: 4px 0; }
.on-air-detail { font-size: 12px; color: var(--dim); text-align: center; }

/* === Thumb-friendly buttons === */
.thumb-btn {
  min-height: 56px; min-width: 56px; border: 1px solid var(--line);
  background: var(--panel); color: var(--fg); border-radius: 4px;
  font-size: 16px; display: flex; align-items: center; justify-content: center; cursor: pointer;
}
.thumb-btn:active { background: var(--ph); color: var(--bg); }
.thumb-btn:disabled { opacity: .4; }

/* === Nav row (PREV/NEXT) === */
.nav-row { display: flex; gap: 8px; margin: 8px 0; }
.nav-row .thumb-btn { flex: 1; }
.nav-next { font-size: 12px; color: var(--dim); text-align: center; }

/* === Live dials === */
.dial-row { display: flex; align-items: center; gap: 12px; margin: 8px 0; }
.dial-row label { width: 80px; color: var(--dim); flex: none; }
.dial-row input[type="range"] { flex: 1; height: 24px; accent-color: var(--ph); }
.dial-row .val { width: 80px; text-align: right; color: var(--ph); flex: none; }

/* === Set list === */
.setlist-section { margin: 12px 0; }
.setlist-header { font-size: 12px; color: var(--dim); text-transform: uppercase; letter-spacing: .1em; margin-bottom: 4px; }
.preset-row {
  display: flex; align-items: center; padding: 10px 8px; border-bottom: 1px solid var(--line);
  cursor: pointer; min-height: 48px;
}
.preset-row:hover { background: var(--panel); }
.preset-row.on-air { border-left: 3px solid var(--ph); background: #0a1a0a; }
.preset-row .idx { color: var(--dim); width: 28px; text-align: right; margin-right: 8px; }
.preset-row .name { flex: 1; }
.preset-row .tag { font-size: 10px; color: var(--ph); text-transform: uppercase; margin-left: 8px; }

/* === Panic row === */
.panic-row { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
button.blackout {
  background: #1a0a0a; border: 2px solid var(--bad); color: var(--bad);
  padding: 16px; font-size: 18px; font-weight: bold; flex: 1; border-radius: 4px; cursor: pointer;
}
button.blackout:active { background: var(--bad); color: var(--bg); }
.feed-toggle { display: flex; gap: 0; flex: 1; }
.feed-toggle button {
  flex: 1; padding: 12px; border: 1px solid var(--line); background: var(--panel);
  color: var(--fg); cursor: pointer; font: inherit; font-size: 13px;
}
.feed-toggle button:first-child { border-radius: 4px 0 0 4px; }
.feed-toggle button:last-child { border-radius: 0 4px 4px 0; }
.feed-toggle button.active { background: var(--ph); color: var(--bg); }
.feed-toggle button.silent { border-color: var(--bad); color: var(--bad); }

/* === Toast notification === */
#toast {
  position: fixed; bottom: 16px; left: 50%; transform: translateX(-50%);
  background: var(--panel); border: 1px solid var(--warn); color: var(--warn);
  padding: 12px 24px; border-radius: 4px; z-index: 200; display: none;
  max-width: 90vw; text-align: center; font-size: 13px;
}
#toast.visible { display: block; }
#toast.error { border-color: var(--bad); color: var(--bad); }
#toast.success { border-color: var(--ph); color: var(--ph); }

/* === Collapsible sections (SETUP) === */
.collapsible-header {
  cursor: pointer; padding: 10px 12px; background: var(--panel); margin: 4px 0;
  border: 1px solid var(--line); border-radius: 4px; font-weight: bold;
  display: flex; align-items: center; gap: 8px;
}
.collapsible-header:hover { border-color: var(--ph); }
.collapsible-header::before { content: "▸"; transition: transform 0.15s; display: inline-block; }
.collapsible-header.open::before { transform: rotate(90deg); }
.collapsible-content { display: none; padding: 12px; }
.collapsible-content.open { display: block; }

/* === Inline rename === */
.inline-rename { display: flex; gap: 4px; margin: 4px 0; }
.inline-rename input { flex: 1; background: #101b10; border: 1px solid var(--line); color: var(--fg); padding: 4px 8px; border-radius: 4px; font: inherit; }
.inline-rename button { padding: 4px 12px; }

/* === Confirm dialog === */
.confirm-overlay {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,.7);
  z-index: 150; display: none; align-items: center; justify-content: center;
}
.confirm-overlay.active { display: flex; }
.confirm-box { background: var(--panel); border: 1px solid var(--warn); padding: 24px; border-radius: 8px; max-width: 400px; text-align: center; }
.confirm-box p { margin-bottom: 16px; }
.confirm-box .confirm-btns { display: flex; gap: 8px; justify-content: center; }
.confirm-box .confirm-btns button { padding: 8px 24px; border-radius: 4px; }

/* === Stats grid === */
.stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
.stats-grid .stat { display: flex; flex-direction: column; }
.stats-grid .stat label { color: var(--dim); font-size: 11px; }
.stats-grid .stat value { font-size: 14px; }

/* === Chips === */
.chip { display: inline-block; padding: 4px 8px; border: 1px solid var(--line); border-radius: 4px; margin: 2px; cursor: pointer; background: var(--panel); }
.chip.active { background: #1a2b1a; border-color: var(--ph); color: var(--ph); }
.chip:hover { border-color: var(--ph); }

/* === Slave sheet overlay === */
.slave-sheet {
  position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: var(--bg);
  z-index: 100; overflow-y: auto; display: none;
}
.slave-sheet.active { display: block; }
.slave-sheet-header {
  position: sticky; top: 0; z-index: 101; background: var(--bg);
  padding: 12px 16px; border-bottom: 1px solid var(--line); display: flex;
  align-items: center; justify-content: space-between;
}
.slave-sheet-header h2 { font-size: 16px; color: var(--ph); }
.slave-sheet-close {
  background: none; border: 1px solid var(--line); color: var(--fg);
  font-size: 20px; padding: 4px 12px; cursor: pointer; border-radius: 4px;
}
.slave-sheet-close:hover { border-color: var(--ph); }
.slave-sheet-inner { padding: 16px; }
.slave-sheet-inner h3 { color: var(--dim); font-size: 12px; text-transform: uppercase; letter-spacing: .1em; margin: 12px 0 6px; }
.slave-sheet-inner h3:first-child { margin-top: 0; }

/* Verdict */
.verdict { background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 12px; margin-bottom: 12px; }
.verdict.bad { border-color: var(--bad); color: var(--bad); }
.verdict.ok { border-color: var(--ph); }

/* Source 3-way */
.source-3way { display: flex; gap: 4px; }
.source-3way button { flex: 1; padding: 12px 8px; border: 1px solid var(--line); background: var(--panel); color: var(--fg); cursor: pointer; text-align: center; font: inherit; }
.source-3way button:first-child { border-radius: 4px 0 0 4px; }
.source-3way button:last-child { border-radius: 0 4px 4px 0; }
.source-3way button.active { background: #1a2b1a; border-color: var(--ph); color: var(--ph); }

/* Expandable section */
.expandable { border: 1px solid var(--line); border-radius: 4px; margin: 8px 0; }
.expandable-header { cursor: pointer; padding: 8px 12px; background: var(--panel); display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.expandable-header:hover { border-color: var(--ph); }
.expandable-header::after { content: "▸"; }
.expandable-header.open::after { content: "▾"; }
.expandable-content { display: none; padding: 8px 12px; font-size: 13px; line-height: 1.5; color: var(--dim); }
.expandable-content.open { display: block; }

/* Battery bar in sheet */
.battery-row { display: flex; align-items: center; gap: 12px; padding: 4px 0; }
.battery-row .battery-bar { margin: 0; }

/* Action buttons row */
.action-row { display: flex; gap: 8px; margin: 12px 0; }
.action-row button { flex: 1; padding: 12px; border-radius: 4px; }

/* === Generic === */
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.row label { width: 80px; color: var(--dim); flex: none; }
input[type="range"] { flex: 1; min-width: 120px; accent-color: var(--ph); }
.val { width: 72px; text-align: right; color: var(--ph); flex: none; }
select, input[type="number"], input[type="text"], textarea {
  background: #101b10; color: var(--fg); border: 1px solid var(--line);
  border-radius: 5px; padding: 5px 12px; font: inherit;
}
button { background: #101b10; color: var(--fg); border: 1px solid var(--line); border-radius: 5px; padding: 5px 12px; font: inherit; cursor: pointer; }
button:hover { border-color: var(--ph); }
button.on { background: var(--ph); color: #031003; border-color: var(--ph); }
button.off { background: #1a0a0a; color: var(--dim); }

/* === Timer table === */
.timer-table { width: 100%; border-collapse: collapse; }
.timer-table th { text-align: left; color: var(--dim); font-weight: normal; font-size: 12px; padding: 4px 8px; border-bottom: 1px solid var(--line); }
.timer-table td { padding: 6px 8px; border-bottom: 1px solid var(--line); }
.timer-table .status-cannot-fire { color: var(--bad); font-size: 12px; }

/* === Help section === */
.help-content { line-height: 1.6; }
.help-content h3 { color: var(--ph); margin: 12px 0 6px; font-size: 14px; }
.help-content p { margin-bottom: 8px; color: var(--dim); }
.help-content code { background: var(--panel); padding: 1px 4px; border-radius: 3px; }

/* === Media queries === */
@media (max-width: 640px), (max-height: 500px) {
  body { padding: 8px; }
  .rig-strip { grid-template-columns: repeat(4, 1fr); }
  .rig-tile { min-height: 60px; padding: 6px; }
  .tile-id { font-size: 14px; }
  .dial-row { flex-direction: column; align-items: stretch; gap: 2px; }
  .dial-row label { width: auto; }
}

/* === Mode class toggles === */
body.show .setup-only { display: none !important; }
body.setup .show-only { display: none !important; }
/* Both modes show header */
body.show .both-modes, body.setup .both-modes { display: block; }
</style>
</head>
<body>
<!-- Disconnection banner (F1) -->
<div id="conn-banner" class="disconnection-banner"></div>

<!-- === HEADER (both modes) === -->
<header class="both-modes">
  <h1>HYPEROSCI</h1>
  <span id="conn-status" class="ok">●</span>
  <button id="mode-toggle" onclick="toggleMode()">SETUP</button>
</header>

<!-- === SHOW MODE === -->
<div id="show-section" class="show-only">

  <!-- Rig strip (sticky) -->
  <div id="rig-strip" class="rig-strip panel">
    <!-- Tiles are built dynamically by JS -->
  </div>

  <!-- On air -->
  <div class="panel">
    <div class="on-air-label">ON AIR</div>
    <div id="preview-wrap"><canvas id="preview" width="400" height="400"></canvas></div>
    <div class="on-air-name" id="on-air-name">—</div>
    <div class="on-air-detail" id="on-air-detail"></div>
  </div>

  <!-- PREV / NEXT -->
  <div class="nav-row">
    <button class="thumb-btn" id="btn-prev" onclick="goPrev()">◀ PREV</button>
    <button class="thumb-btn" id="btn-next" onclick="goNext()">NEXT ▶</button>
  </div>
  <div class="nav-next" id="nav-next-hint"></div>

  <!-- Live dials -->
  <div class="panel">
    <div class="dial-row">
      <label>SIZE</label>
      <input type="range" id="amp" min="0" max="100" step="1" oninput="liveDials()" onchange="setP({amp:this.value/100})">
      <span class="val" id="ampv">—</span>
    </div>
    <div class="dial-row">
      <label>SPEED</label>
      <input type="range" id="freq" min="10" max="2000" step="1" oninput="liveDials()" onchange="setP({freq:+this.value})">
      <span class="val" id="freqv">—</span>
    </div>
  </div>

  <!-- Set list -->
  <div class="setlist-section">
    <div class="setlist-header">SET LIST</div>
    <div class="panel" id="setlist">
      <!-- Built dynamically -->
    </div>
  </div>

  <!-- Panic row -->
  <div class="panic-row">
    <button class="blackout" id="btn-blackout" onclick="toggleBlackout()">BLACKOUT</button>
    <div class="feed-toggle" id="feed-toggle">
      <button id="feed-on" onclick="setStream(true)">SENDING</button>
      <button id="feed-off" onclick="setStream(false)">SILENT</button>
    </div>
  </div>

</div>

<!-- === SETUP MODE === -->
<div id="setup-section" class="setup-only">

  <!-- Pattern -->
  <div class="collapsible-header open" onclick="toggleCollapsible(this)">Pattern</div>
  <div class="collapsible-content open panel">
    <div class="row">
      <label>pattern</label>
      <span class="seg" id="kindseg">
        <button data-k="circle" onclick="setP({kind:'circle'})">circle</button>
        <button data-k="lissajous" onclick="setP({kind:'lissajous'})">lissajous</button>
        <button data-k="rose" onclick="setP({kind:'rose'})">rose</button>
        <button data-k="text" onclick="setP({kind:'text'})">text</button>
      </span>
    </div>
    <!-- kind-specific controls -->
    <div class="row" id="ratiorow" style="display:none">
      <label id="ratio-label">ratio</label>
      <input type="number" id="ra" min="1" max="9" style="width:64px" onchange="setP({a:+this.value})"> :
      <input type="number" id="rb" min="1" max="9" style="width:64px" onchange="setP({b:+this.value})">
    </div>
    <div class="row" id="petalsrow" style="display:none">
      <label>petals</label>
      <input type="number" id="petals" min="1" max="9" style="width:80px" onchange="setP({a:+this.value})">
      <span style="color:var(--dim);font-size:12px">(rose petal count — uses same input as ratio `a`)</span>
    </div>
    <div class="row" id="textrow" style="display:none">
      <label>text</label>
      <textarea id="text" maxlength="80" rows="2" spellcheck="false" style="flex:1;min-width:120px;background:#101b10;color:var(--fg);border:1px solid var(--line);border-radius:5px;padding:5px 8px;font:inherit;resize:vertical"></textarea>
      <button onclick="setP({text:document.getElementById('text').value})">Apply</button>
      <select id="font" onchange="setP({font:this.value})">
        <option>simplex</option><option>duplex</option><option>script</option>
        <option>gothic</option><option>times</option><option>italic</option>
      </select>
    </div>
    <div class="row" id="pulsedepthrow">
      <label>pulse depth</label>
      <input type="range" id="pdepth" min="0" max="100" step="1" oninput="showPdepth()" onchange="setP({pulse_depth:this.value/100})">
      <span class="val" id="pdepthv">—</span>
    </div>
    <div class="row" id="puleraterow">
      <label>pulse rate</label>
      <input type="range" id="prate" min="1" max="80" step="1" oninput="showPrate()" onchange="setP({pulse_rate:this.value/10})">
      <span class="val" id="pratev">—</span>
    </div>
    <div class="row" id="spinrow">
      <label>spin</label>
      <input type="range" id="rot" min="-100" max="100" step="1" oninput="showRot()" onchange="setP({rot:this.value/100})">
      <span class="val" id="rotv">—</span>
      <button onclick="document.getElementById('rot').value=0;showRot();setP({rot:0})">0</button>
    </div>
    <div class="row">
      <label>amplitude</label>
      <input type="range" id="s-amp" min="0" max="100" step="1" oninput="showAmp()" onchange="setP({amp:this.value/100})">
      <span class="val" id="s-ampv">—</span>
    </div>
    <div class="row">
      <label>frequency</label>
      <input type="range" id="s-freq" min="1" max="2000" step="1" oninput="showFreq()" onchange="setP({freq:+this.value})">
      <span class="val" id="s-freqv">—</span>
    </div>
    <div class="row">
      <label>mirror X</label>
      <button id="fxbtn" onclick="setP({flip_x:!S.pattern.flip_x})">toggle</button>
      <span style="color:var(--dim);font-size:12px">affects text only, applies to all scopes</span>
    </div>
    <div class="row">
      <label>mirror Y</label>
      <button id="fybtn" onclick="setP({flip_y:!S.pattern.flip_y})">toggle</button>
      <span style="color:var(--dim);font-size:12px">affects text only, applies to all scopes</span>
    </div>
  </div>

  <!-- Set list (setup) -->
  <div class="collapsible-header open" onclick="toggleCollapsible(this)">Set List</div>
  <div class="collapsible-content open panel">
    <div style="margin-bottom:8px">
      <span id="preset-count" style="color:var(--dim);font-size:12px"></span>
      <button id="edit-toggle" onclick="toggleEditMode()" style="margin-left:8px">Edit</button>
    </div>
    <div id="setup-setlist">
      <!-- Built dynamically with edit controls -->
    </div>
    <div style="margin-top:8px;display:flex;gap:4px;">
      <button onclick="setupSavePreset()">Save preset</button>
      <button id="setup-updbtn" style="display:none" onclick="setupUpdatePreset()">Update current</button>
    </div>
  </div>

  <!-- Idents (timers) -->
  <div class="collapsible-header" onclick="toggleCollapsible(this)">Idents</div>
  <div class="collapsible-content panel">
    <div id="holdnote" style="color:var(--ph);margin-bottom:8px"></div>
    <div id="timer-table-wrap">
      <!-- Built dynamically -->
    </div>
    <div class="row" id="taddrow" style="margin-top:10px">
      <label>add</label>
      <select id="tpreset"></select>
      <span style="color:var(--dim)">on</span>
      <span class="seg" id="ttargets"></span>
      <span>for</span>
      <input type="number" id="thold" min="1" max="600" value="20" style="width:66px">
      <span>s every</span>
      <input type="number" id="tevery" min="0.1" max="1440" step="any" value="5" style="width:74px">
      <span>min</span>
      <button onclick="addTimer()">+ add</button>
    </div>
    <div id="timer-cannot-fire" style="color:var(--bad);font-size:12px;margin-top:6px;display:none">
      Feed is SILENT — timers cannot fire until sending resumes.
    </div>
  </div>

  <!-- Rig -->
  <div class="collapsible-header" onclick="toggleCollapsible(this)">Rig</div>
  <div class="collapsible-content panel">
    <!-- Set all row -->
    <div class="row" style="margin-bottom:12px">
      <label style="width:auto">set all</label>
      <span class="seg" id="allseg"></span>
    </div>
    <div id="setup-rig">
      <!-- Per-slave cards built dynamically -->
    </div>
  </div>

  <!-- Diagnostics -->
  <div class="collapsible-header" onclick="toggleCollapsible(this)">Diagnostics</div>
  <div class="collapsible-content panel">
    <div id="diag-section">
      <!-- Built dynamically -->
    </div>
  </div>

  <!-- Help -->
  <div class="collapsible-header" onclick="toggleCollapsible(this)">Help</div>
  <div class="collapsible-content panel">
    <div class="help-content">
      <h3>How it works</h3>
      <p>Two switches control what the scopes draw: the global <strong>feed</strong> toggle (SENDING/SILENT) and each slave's <strong>SOURCE</strong> setting.</p>
      <p><strong>STREAM</strong>: slave plays the audio streamed from this page. Falls back to its local pattern if the stream stops.</p>
      <p><strong>HYBRID</strong>: streamed pattern + slave's own mic mixed at 50%.</p>
      <p><strong>ON ITS OWN</strong>: slave generates its own pattern (mic, circle, lissajous, ramp, square). The controller sends it no audio.</p>
      <h3>Fallback</h3>
      <p>A STREAM slave that stops receiving audio for ~1 s automatically draws its local pattern instead — whichever was last set. "ON ITS OWN" pattern doubles as this safety net.</p>
      <h3>buf / lead</h3>
      <p>Received audio queued ahead of playback. Healthy ≈ 450 ms: the stream runs ahead because the WiFi radio pauses for ~300 ms every ~1.4 s. Hitting 0 = buffer underrun (beam blinks).</p>
      <h3>drop/s, lost/s, under/s</h3>
      <p>Should all be 0. Drops = late/duplicate packets. Lost = packets that never arrived (WiFi loss). Underruns = buffer ran dry (visible blink).</p>
      <h3>tx-drop vs lost</h3>
      <p>High lost/s with tx-drop ≈ 0 means the WiFi air is bad. High tx-drop means packets died in this controller's send buffer — no antenna fix. This distinction cost a night of debugging.</p>
      <h3>Blackout</h3>
      <p>Forces amplitude to 0 and sets all slaves to network mode. Does NOT write to slave flash. Survives controller crash (slaves fall back to local pattern).</p>
      <h3>Presets</h3>
      <p>Snapshots of pattern settings saved on the controller. Survive restarts with one generation of undo (.bak file). Max 20 presets. Do NOT update during a timer hold — it will save the ident, not the artist.</p>
      <h3>Interval timers</h3>
      <p>Periodically interrupt the show with a preset (e.g., station ident between acts). Only one hold can run at a time. A timer that cannot fire (feed is SILENT) will say so instead of counting down.</p>
    </div>
  </div>

</div>

<!-- === SLAVE SHEET OVERLAY === -->
<div id="slave-sheet" class="slave-sheet">
  <div class="slave-sheet-header">
    <h2 id="sheet-title">SLAVE</h2>
    <button class="slave-sheet-close" onclick="closeSlaveSheet()">✕</button>
  </div>
  <div class="slave-sheet-inner" id="sheet-body">
    <!-- Populated dynamically -->
  </div>
</div>

<!-- === CONFIRM DIALOG === -->
<div id="confirm-overlay" class="confirm-overlay">
  <div class="confirm-box">
    <p id="confirm-question"></p>
    <div class="confirm-btns">
      <button class="danger" id="confirm-yes">Yes</button>
      <button id="confirm-no">No</button>
    </div>
  </div>
</div>

<!-- === TOAST === -->
<div id="toast"></div>

<!-- === SETUP INLINE RENAME === -->
<div id="rename-overlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:150;align-items:center;justify-content:center;">
  <div style="background:var(--panel);border:1px solid var(--line);padding:16px;border-radius:8px;max-width:320px;width:90%;">
    <p style="margin-bottom:8px;color:var(--dim);">Rename preset:</p>
    <div class="inline-rename">
      <input type="text" id="rename-input" maxlength="48">
      <button onclick="submitRename()">Save</button>
    </div>
    <button onclick="document.getElementById('rename-overlay').style.display='none'" style="margin-top:8px;width:100%">Cancel</button>
  </div>
</div>

<script>
"use strict";

/* === STATE === */
var S = null;
var pollFailures = 0;
var lastKnownState = null;
var activeSlaveSheet = null;
var curPresetIdx = 0;
/* parseFloat(null) is NaN, not null — and NaN !== null, so an unset key used to
   send the FIRST press down the undo branch and post {amp: NaN}. The panic
   button has to work on a phone that has never loaded this page before. */
var blackoutAmp = null;
try {
  var _ba = parseFloat(localStorage.getItem('blackoutAmp'));
  if (isFinite(_ba)) blackoutAmp = _ba;
} catch(e) {}
var editModeOn = false;
var renameTarget = null;
var renameNewName = null;

/* Per-slave change tracking (F2 fix) */
var slaveLastChange = {};

/* Incremental DOM elements (F4 fix) */
var slaveTiles = {};
var presetRowEls = {};
var setupRigEls = {};

/* Expected slave roster — remembered across polls (F8 fix) */
var knownSlaveIds = (function() {
  try { var d = localStorage.getItem('hyperosci-known-ids'); return d ? JSON.parse(d) : []; }
  catch(e) { return []; }
})();
function persistKnownIds() {
  localStorage.setItem('hyperosci-known-ids', JSON.stringify(knownSlaveIds));
}

/* === TOAST (F5 fix — replaces alert) === */
function showToast(msg, duration, cls) {
  duration = duration || 3000;
  cls = cls || 'warn';
  var toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'visible ' + cls;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(function() { toast.className = ''; }, duration);
}

/* === NON-BLOCKING CONFIRM (F5 fix — replaces confirm) === */
function askConfirm(question, onYes, onNo) {
  var overlay = document.getElementById('confirm-overlay');
  document.getElementById('confirm-question').textContent = question;
  overlay.classList.add('active');
  document.getElementById('confirm-yes').onclick = function() {
    overlay.classList.remove('active');
    if (onYes) onYes();
  };
  document.getElementById('confirm-no').onclick = function() {
    overlay.classList.remove('active');
    if (onNo) onNo();
  };
}

/* === MODE TOGGLE === */
function getMode() {
  return localStorage.getItem('hyperosci-mode') || (window.innerWidth < 900 ? 'show' : 'setup');
}

function setMode(mode) {
  document.body.className = mode;
  localStorage.setItem('hyperosci-mode', mode);
  document.getElementById('mode-toggle').textContent = mode === 'show' ? 'SETUP' : 'SHOW';
  render();
}

function toggleMode() {
  var cur = getMode();
  setMode(cur === 'show' ? 'setup' : 'show');
}

/* === POST (with toast instead of alert) === */
function post(path, body) {
  return fetch(path, {method:"POST", body:JSON.stringify(body)})
    .then(function(r) { return r.json().catch(function() { return null; }); })
    .then(function(d) {
      if (d && (d.err || d.note)) {
        showToast(d.err || d.note, 4000, 'error');
      }
      return d;
    })
    .catch(function() {})
    .then(function() { return refresh(); });   /* not poll() — see poll() */
}

var setP = function(p) { return post("/api/pattern", p); };
var cmd = function(ip, c) { return post("/api/cmd", {ip:ip, cmd:c}); };

/* === SLAVE CHANGE TRACKING (F2 fix — per-slave) === */
function noteSlaveChange(id) {
  if (id === 'all') {
    for (var i = 0; i < (S && S.slaves ? S.slaves.length : 0); i++) {
      slaveLastChange[S.slaves[i].id] = performance.now();
    }
  } else {
    slaveLastChange[id] = performance.now();
  }
}

/* === STREAM TOGGLE (F13 fix — guards against null S) === */
function setStream(on) {
  if (!S) return;
  noteSlaveChange('all');
  post("/api/pattern", {stream: on});
}

/* === TOGGLE COLLAPSIBLE === */
function toggleCollapsible(header) {
  header.classList.toggle('open');
  var content = header.nextElementSibling;
  if (content && content.classList.contains('collapsible-content')) {
    content.classList.toggle('open');
  }
}

/* === LIVE DIALS === */
function liveDials() {
  document.getElementById('ampv').textContent = document.getElementById('amp').value + ' %';
  document.getElementById('freqv').textContent = document.getElementById('freq').value + ' Hz';
  drawPreview();
}

function showPdepth() {
  document.getElementById('pdepthv').textContent = document.getElementById('pdepth').value + ' %';
}

function showPrate() {
  document.getElementById('pratev').textContent = (document.getElementById('prate').value / 10).toFixed(1) + ' Hz';
}

function showRot() {
  document.getElementById('rotv').textContent = (document.getElementById('rot').value / 100).toFixed(2) + ' rev/s';
}

/* SETUP's own dials — distinct ids, or getElementById returns SHOW's copy and
   the panel you are dragging never moves. */
function showAmp() {
  document.getElementById('s-ampv').textContent = Math.round(document.getElementById('s-amp').value) + ' %';
}

function showFreq() {
  document.getElementById('s-freqv').textContent = document.getElementById('s-freq').value + ' Hz';
}

/* === FORMAT HELPERS === */
function fmtUp(s) {
  if (s >= 3600) return (s/3600).toFixed(1) + 'h';
  if (s >= 60) return Math.floor(s/60) + 'm' + (s%60) + 's';
  return s + 's';
}

function fmtN(n) {
  if (n == null) return '—';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'k';
  return '' + n;
}

function fmtLeft(s) {
  if (s == null) return '';
  if (s >= 60) return Math.floor(s/60) + 'm' + String(s%60).padStart(2,'0') + 's';
  return s + 's';
}

function esc(t) {
  return t.replace(/[&<>"]/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
  });
}

/* === PRESET HELPERS === */
var curPreset = localStorage.getItem('hypePreset') || '';
function setCur(n) {
  curPreset = n || '';
  if (curPreset) localStorage.setItem('hypePreset', curPreset);
  else localStorage.removeItem('hypePreset');
}

/* === BLACKOUT (§5.6 — amp=0, not gain=0) === */
function toggleBlackout() {
  if (blackoutAmp === null) {
    if (!S) return;
    blackoutAmp = S.pattern.amp;
    localStorage.setItem('blackoutAmp', blackoutAmp.toString());
    /* Force all slaves to network mode first (N15) */
    for (var i = 0; i < S.slaves.length; i++) {
      cmd(S.slaves[i].ip, {cmd: 'set_mode', mode: 'network'});
    }
    setP({amp: 0});
    showToast('BLACKOUT — amplitude zeroed', 2000, 'error');
    document.getElementById('btn-blackout').textContent = 'UNDO BLACKOUT';
    document.getElementById('btn-blackout').style.borderColor = 'var(--ph)';
    document.getElementById('btn-blackout').style.color = 'var(--ph)';
  } else {
    setP({amp: blackoutAmp});
    blackoutAmp = null;
    try { localStorage.removeItem('blackoutAmp'); } catch(e) {}
    showToast('Blackout cancelled', 2000, 'success');
    document.getElementById('btn-blackout').textContent = 'BLACKOUT';
    document.getElementById('btn-blackout').style.borderColor = '';
    document.getElementById('btn-blackout').style.color = '';
  }
}

/* === NAV (PREV/NEXT) === */
function goPrev() {
  var presets = S ? (S.presets || []) : [];
  if (!presets.length) return;
  var curName = (S.hold && S.hold.preset) || curPreset;
  curPresetIdx = presets.indexOf(curName);
  if (curPresetIdx < 0) curPresetIdx = presets.length - 1;
  curPresetIdx = (curPresetIdx - 1 + presets.length) % presets.length;
  applyPreset(presets[curPresetIdx]);
}

function goNext() {
  var presets = S ? (S.presets || []) : [];
  if (!presets.length) return;
  var curName = (S.hold && S.hold.preset) || curPreset;
  curPresetIdx = presets.indexOf(curName);
  if (curPresetIdx < 0) curPresetIdx = 0;
  curPresetIdx = (curPresetIdx + 1) % presets.length;
  applyPreset(presets[curPresetIdx]);
}

function applyPreset(name) {
  if (!S) return;
  setCur(name);
  noteSlaveChange('all');
  post("/api/preset", {op: 'load', name: name});
}

/* === PREVIEW CANVAS === */
var TP = {ver: -1, pts: []};

async function fetchPreview() {
  try {
    var d = await (await fetch("/api/textpreview")).json();
    TP = d;
    drawPreview();
  } catch(e) {}
}

function drawPreview() {
  if (!S) return;
  var c = document.getElementById('preview');
  if (!c) return;
  var g = c.getContext('2d');
  var p = S.pattern;
  var w = c.width, h = c.height;
  var amp = 0;
  var ampSlider = document.getElementById('amp');
  if (ampSlider) amp = (+ampSlider.value / 100) * w * 0.44;
  else amp = p.amp * w * 0.44;

  g.fillStyle = '#020402';
  g.fillRect(0, 0, w, h);
  g.strokeStyle = '#0e1a0e';
  g.lineWidth = 1;
  for (var i = 1; i < 8; i++) {
    g.beginPath(); g.moveTo(i*w/8, 0); g.lineTo(i*w/8, h); g.stroke();
    g.beginPath(); g.moveTo(0, i*h/8); g.lineTo(w, i*h/8); g.stroke();
  }
  g.strokeStyle = '#39ff14';
  g.lineWidth = 2;
  g.shadowColor = '#39ff14';
  g.shadowBlur = 8;
  g.beginPath();

  if (p.kind === 'text' && TP.pts.length) {
    var sx = p.flip_x ? -1 : 1;
    var sy = p.flip_y ? -1 : 1;
    for (var i = 0; i < TP.pts.length; i++) {
      var px = w/2 + amp*sx*TP.pts[i][0];
      var py = h/2 - amp*sy*TP.pts[i][1];
      i ? g.lineTo(px, py) : g.moveTo(px, py);
    }
  } else {
    var N = 1200;
    var k = p.kind;
    var a = p.a;
    var b = p.b;
    var hp = Math.PI/2;
    for (var i = 0; i <= N; i++) {
      var t = 2*Math.PI*i/N;
      var x, y;
      if (k === 'lissajous') { x = Math.sin(a*t + hp); y = Math.sin(b*t); }
      else if (k === 'rose') { var r = Math.sin(a*t); x = r*Math.cos(t); y = r*Math.sin(t); }
      else { x = Math.cos(t); y = Math.sin(t); }
      var px = w/2 + amp*x;
      var py = h/2 - amp*y;
      i ? g.lineTo(px, py) : g.moveTo(px, py);
    }
  }
  g.stroke();
  g.shadowBlur = 0;
}

/* === SLAVE VERDICT (one sentence) === */
function slaveVerdict(s) {
  var p = S.pattern;
  if (s.source) {
    var what = s.mode === 2 ? p.kind + ' + own mic (HYBRID)' : p.kind;
    if (p.kind === 'text') what = 'text "' + esc(p.text) + '" (' + (p.font||'duplex') + ')' + (s.mode === 2 ? ' + mic' : '');
    return {cls: 'ok', txt: 'Playing network stream — ' + what + ' @ ' + p.freq + ' Hz'};
  }
  if (s.mode === 0) return {cls: 'ok', txt: 'Drawing local ' + (s.lpat || 'mic') + ' (generated on slave)'};
  if (!S.stream_on) return {cls: 'warn', txt: 'Drawing local ' + (s.lpat || 'mic') + ' — feed is SILENT'};
  /* Stream not arriving */
  var changeTime = slaveLastChange[s.id];
  if (changeTime && performance.now() - changeTime < 4000) {
    return {cls: 'warn', txt: 'Buffering stream… (normal after a change)'};
  }
  return {cls: 'bad', txt: 'Not receiving the stream — drawing local ' + (s.lpat || 'mic') + ' instead'};
}

function modeName(s) {
  if (s.source) return s.mode === 2 ? 'HYBRID' : 'STREAM';
  return 'OWN';
}

function modeGlyph(s) {
  if (s.source) return s.mode === 2 ? '■HYB' : '■NET';
  return '▲OWN';
}

/* === BATTERY BAR === */
function batteryLevel(vbat_mv) {
  /* Approximate LiPo: 4200 = 100%, 3200 = 0% */
  if (vbat_mv == null || vbat_mv === 0) return 0;
  return Math.min(5, Math.max(0, Math.round((vbat_mv - 3200) / 200)));
}

function batteryEstimate(vbat_mv) {
  if (vbat_mv == null || vbat_mv === 0) return '? h';
  /* rough: ~3.8V = ~2h typical for a small LiPo driving an oscilloscope */
  var frac = (vbat_mv - 3200) / 1000;
  if (frac <= 0) return '<1h';
  return '~' + Math.round(frac * 2) + ' h';
}

/* === RIG TILE BUILDING (F4 fix — incremental DOM) === */
function ensureTileGrid() {
  var strip = document.getElementById('rig-strip');
  if (!strip) return;
  /* Merge known IDs with current state */
  var ids = [];
  if (S && S.slaves) {
    for (var i = 0; i < S.slaves.length; i++) ids.push(S.slaves[i].id);
  }
  /* Add known IDs not in current state */
  for (var i = 0; i < knownSlaveIds.length; i++) {
    if (ids.indexOf(knownSlaveIds[i]) < 0) ids.push(knownSlaveIds[i]);
  }
  ids.sort(function(a,b) { return a - b; });

  /* Create tiles for new IDs */
  for (var i = 0; i < ids.length; i++) {
    var id = ids[i];
    if (!slaveTiles[id]) {
      var tile = document.createElement('div');
      tile.className = 'rig-tile';
      tile.setAttribute('data-id', id);
      tile.onclick = (function(tid) { return function() { openSlaveSheet(tid); }; })(id);
      tile.innerHTML = '<div class="tile-id">?</div><div class="tile-mode">?</div><div class="tile-status">LOST</div><div class="battery-bar"><span></span><span></span><span></span><span></span><span></span></div><div class="tile-age"></div>';
      strip.appendChild(tile);
      slaveTiles[id] = tile;
    }
  }
  /* Track known IDs */
  for (var i = 0; i < ids.length; i++) {
    if (knownSlaveIds.indexOf(ids[i]) < 0) {
      knownSlaveIds.push(ids[i]);
    }
  }
  persistKnownIds();
}

function patchTile(s) {
  var tile = slaveTiles[s.id];
  if (!tile) return;
  var verdict = slaveVerdict(s);
  var age = s.age_ms / 1000;
  var vbat = batteryLevel(s.vbat_mv || 0);
  var tiles = tile.querySelectorAll('.battery-bar span');
  /* Reset battery bar */
  for (var i = 0; i < tiles.length; i++) tiles[i].classList.remove('filled');
  for (var i = 0; i < vbat; i++) {
    if (!tiles[i]) {
      var span = document.createElement('span');
      tile.querySelector('.battery-bar').appendChild(span);
    }
    tiles[i].classList.add('filled');
  }
  /* Remove extra cells */
  for (var i = vbat; i < tiles.length; i++) tiles[i].classList.remove('filled');

  tile.querySelector('.tile-id').textContent = s.id;
  tile.querySelector('.tile-mode').textContent = modeGlyph(s);

  var statusEl = tile.querySelector('.tile-status');
  var ageEl = tile.querySelector('.tile-age');
  tile.classList.remove('lost', 'stale');

  if (age > 5) {
    tile.classList.add('lost');
    statusEl.style.display = 'block';
    statusEl.textContent = 'LOST ' + Math.round(age) + 's';
    ageEl.textContent = '';
  } else if (age > 3) {
    tile.classList.add('stale');
    statusEl.style.display = 'none';
    ageEl.textContent = Math.round(age) + 's ago';
  } else {
    statusEl.style.display = 'none';
    ageEl.textContent = age.toFixed(1) + 's';
  }
}

/* === SET LIST (SHOW mode — no delete) === */
function renderSetList() {
  var container = document.getElementById('setlist');
  if (!container) return;
  var presets = S.presets || [];
  var curName = (S.hold && S.hold.preset) || curPreset;

  /* Build rows incrementally (F4) */
  var existingNames = Object.keys(presetRowEls);
  var newNames = presets.map(function(p) { return p.name || p; });

  /* Remove rows for deleted presets */
  for (var i = 0; i < existingNames.length; i++) {
    var nm = existingNames[i];
    if (newNames.indexOf(nm) < 0) {
      presetRowEls[nm].remove();
      delete presetRowEls[nm];
    }
  }

  /* Create/update rows */
  container.innerHTML = '';
  for (var i = 0; i < newNames.length; i++) {
    var nm = typeof presets[i] === 'string' ? presets[i] : presets[i].name;
    if (!presetRowEls[nm]) {
      var row = document.createElement('div');
      row.className = 'preset-row';
      row.onclick = (function(name) { return function() { applyPreset(name); }; })(nm);
      row.innerHTML = '<span class="idx"></span><span class="name"></span><span class="tag"></span>';
      presetRowEls[nm] = row;
    }
    var row = presetRowEls[nm];
    row.querySelector('.idx').textContent = (i + 1);
    row.querySelector('.name').textContent = nm;
    row.classList.toggle('on-air', nm === curName);
    if (nm === curName) row.querySelector('.tag').textContent = 'ON AIR';
    else row.querySelector('.tag').textContent = '';
    container.appendChild(row);
  }

  if (!presets.length) {
    container.innerHTML = '<div style="color:var(--dim);text-align:center;padding:12px">No presets saved</div>';
  }
}

/* === ON AIR === */
function renderOnAir() {
  var curName = curPreset;
  var holdIndicator = '';
  if (S.hold && S.hold.preset) {
    curName = S.hold.preset;
    holdIndicator = '▶ HOLD ';
  }
  var presets = S.presets || [];
  /* Index off the preset name, before the text decoration below -- decorated,
     it never matches the set list and every tile read "1 of N". */
  var idx = presets.length ? presets.indexOf(curName) : -1;

  /* Show text content for text patterns (N17) */
  if (S.pattern.kind === 'text' && S.pattern.text) {
    curName = curName ? S.pattern.text + ' (' + curName + ')' : S.pattern.text;
  }

  document.getElementById('on-air-name').textContent = curName || '—';
  /* On a phone that has not loaded a preset this session there is no way to
     know which one is on air. "1 of 7" was a guess printed as fact, and PREV
     stepped from it. Say the kind and stop. */
  document.getElementById('on-air-detail').textContent = (S.pattern.kind || '?')
    + (idx >= 0 ? ' · ' + (idx + 1) + ' of ' + presets.length : '');

  /* NAV next hint */
  document.getElementById('nav-next-hint').textContent =
    (idx >= 0 && presets.length > 1)
      ? 'next → ' + presets[(idx + 1) % presets.length] : '';

  /* Enable/disable nav */
  document.getElementById('btn-prev').disabled = presets.length < 2;
  document.getElementById('btn-next').disabled = presets.length < 2;

  /* Feed toggle */
  var on = S.stream_on;
  document.getElementById('feed-on').classList.toggle('active', on);
  document.getElementById('feed-off').classList.toggle('active', !on);
  document.getElementById('feed-off').classList.toggle('silent', !on);
}

/* === SLAVE SHEET === */
function openSlaveSheet(id) {
  if (!S) return;
  var slave = null;
  for (var i = 0; i < S.slaves.length; i++) {
    if (S.slaves[i].id === id) { slave = S.slaves[i]; break; }
  }
  if (!slave) return;
  activeSlaveSheet = id;
  document.getElementById('sheet-title').textContent = 'SLAVE ' + id + '  ' + slave.ip;

  var verdict = slaveVerdict(slave);
  var vbat = batteryLevel(slave.vbat_mv || 0);
  var vbatStr = (slave.vbat_mv / 1000).toFixed(2) + ' V';
  var battEst = batteryEstimate(slave.vbat_mv || 0);
  var rssi = slave.rssi + ' dBm';
  var rssiLabel = slave.rssi > -60 ? 'good' : slave.rssi > -75 ? 'marginal' : 'poor';
  var buf = Math.round(slave.depth / 48) + ' ms';

  /* Source: active mode */
  var activeSource = slave.source ? (slave.mode === 2 ? 'hybrid' : 'stream') : 'local';

  /* TX-drop info */
  var net = S.net || {};
  var tx = (net.tx || {})[slave.ip] || {};
  var txDropPct = tx.pct || 0;
  var txDropFull = tx.full || 0;
  var txDropErr = tx.err || 0;
  var whyHtml = '';
  if (!slave.source && S.stream_on) {
    if (txDropPct > 0) {
      whyHtml = '<p>' + fmtN(slave.lost || 0) + ' packets never arrived.</p>';
      whyHtml += '<p>' + txDropPct.toFixed(1) + '% died in this controller\\'s send buffer before reaching the air.</p>';
      whyHtml += '<p style="color:var(--warn)">That is airtime, not antenna — try reducing packet rate or increasing airtime.</p>';
    } else {
      whyHtml = '<p>' + fmtN(slave.lost || 0) + ' packets lost, tx-drop ≈ 0.</p>';
      whyHtml += '<p style="color:var(--warn)">This is WiFi loss — signal, interference, or range.</p>';
    }
  } else if (!S.stream_on) {
    whyHtml = '<p>Feed is SILENT — no audio is being sent to any slave.</p>';
  } else {
    whyHtml = '<p>Slave is receiving the stream normally.</p>';
  }

  var body = document.getElementById('sheet-body');
  body.innerHTML =
    '<div class="verdict ' + verdict.cls + '">' + verdict.txt + '</div>' +

    '<h3>SOURCE</h3>' +
    '<div class="source-3way">' +
      '<button class="' + (activeSource === 'stream' ? 'active' : '') + '" onclick="setSlaveSource(\\'' + slave.ip + '\\',\\'network\\')">STREAM</button>' +
      '<button class="' + (activeSource === 'hybrid' ? 'active' : '') + '" onclick="setSlaveSource(\\'' + slave.ip + '\\',\\'hybrid\\')">HYBRID<br><span style="font-size:11px;color:var(--dim)">+ its mic</span></button>' +
      '<button class="' + (activeSource === 'local' ? 'active' : '') + '" onclick="setSlaveSource(\\'' + slave.ip + '\\',\\'local\\')">ON ITS<br>OWN</button>' +
    '</div>' +

    '<div class="expandable">' +
      '<div class="expandable-header" onclick="this.classList.toggle(\\'open\\');this.nextElementSibling.classList.toggle(\\'open\\')">why?</div>' +
      '<div class="expandable-content">' + whyHtml + '</div>' +
    '</div>' +

    '<h3>POWER &amp; SIGNAL</h3>' +
    '<div class="battery-row">' +
      '<span style="color:var(--dim);width:80px">battery</span>' +
      '<div class="battery-bar">' +
        '<span class="' + (vbat >= 1 ? 'filled' : '') + '"></span>' +
        '<span class="' + (vbat >= 2 ? 'filled' : '') + '"></span>' +
        '<span class="' + (vbat >= 3 ? 'filled' : '') + '"></span>' +
        '<span class="' + (vbat >= 4 ? 'filled' : '') + '"></span>' +
        '<span class="' + (vbat >= 5 ? 'filled' : '') + '"></span>' +
      '</div>' +
      '<span>' + vbatStr + '</span>' +
      '<span style="color:var(--dim)">' + battEst + '</span>' +
    '</div>' +
    '<div class="battery-row">' +
      '<span style="color:var(--dim);width:80px">signal</span>' +
      '<span>' + rssi + '</span>' +
      '<span style="color:var(--dim)">' + rssiLabel + '</span>' +
    '</div>' +

    '<div class="expandable">' +
      '<div class="expandable-header" onclick="this.classList.toggle(\\'open\\');this.nextElementSibling.classList.toggle(\\'open\\')">all numbers</div>' +
      '<div class="expandable-content">' +
        '<p style="color:var(--dim);font-size:12px;margin-bottom:8px">LINK</p>' +
        '<div class="stats-grid">' +
          '<div class="stat"><label>buf</label><value>' + buf + '</value></div>' +
          '<div class="stat"><label>up</label><value>' + fmtUp(slave.uptime || 0) + '</value></div>' +
          '<div class="stat"><label>age</label><value>' + (slave.age_ms/1000).toFixed(1) + 's</value></div>' +
        '</div>' +
        '<p style="color:var(--dim);font-size:12px;margin:8px 0 4px">STREAM</p>' +
        '<div class="stats-grid">' +
          '<div class="stat"><label>rx</label><value>' + fmtN(slave.rx) + '</value></div>' +
          '<div class="stat"><label>drop</label><value>' + fmtN(slave.drop) + '</value></div>' +
          '<div class="stat"><label>lost</label><value>' + (slave.lost == null ? '—' : fmtN(slave.lost)) + '</value></div>' +
          '<div class="stat"><label>under</label><value>' + fmtN(slave.under) + '</value></div>' +
          '<div class="stat"><label>tx-drop</label><value>' + txDropPct + '%</value></div>' +
          '<div class="stat"><label>tx full</label><value>' + fmtN(txDropFull) + '</value></div>' +
        '</div>' +
        '<p style="color:var(--dim);font-size:12px;margin:8px 0 4px">POWER</p>' +
        '<div class="stats-grid">' +
          '<div class="stat"><label>vbat</label><value>' + slave.vbat_mv + ' mV</value></div>' +
          '<div class="stat"><label>rssi</label><value>' + slave.rssi + ' dBm</value></div>' +
        '</div>' +
      '</div>' +
    '</div>' +

    '<h3>ACTIONS</h3>' +
    '<div class="action-row">' +
      '<button onclick="cmd(\\'' + slave.ip + '\\',{cmd:\\'identify\\'})">FLASH LEDS</button>' +
      '<button class="danger" onclick="askConfirm(\\'Reboot slave ' + slave.id + '? (2 s outage)\\', function(){cmd(\\'' + slave.ip + '\\',{cmd:\\'reboot\\'})})">REBOOT</button>' +
    '</div>';

  document.getElementById('slave-sheet').classList.add('active');
}

function closeSlaveSheet() {
  document.getElementById('slave-sheet').classList.remove('active');
  activeSlaveSheet = null;
}

function setSlaveSource(ip, mode) {
  var slaveId = null;
  for (var i = 0; i < (S && S.slaves ? S.slaves.length : 0); i++) {
    if (S.slaves[i].ip === ip) { slaveId = S.slaves[i].id; break; }
  }
  if (slaveId) noteSlaveChange(slaveId);
  if (mode === 'network') cmd(ip, {cmd: 'set_mode', mode: 'network'});
  else if (mode === 'hybrid') cmd(ip, {cmd: 'set_mode', mode: 'hybrid'});
  else cmd(ip, {cmd: 'set_mode', mode: 'local'});
  /* Reopen sheet to refresh */
  setTimeout(function() { openSlaveSheet(parseInt(activeSlaveSheet)); }, 200);
}

/* === RATES (for diagnostics) === */
var rateHistory = {};
function rates(s) {
  var h = rateHistory[s.ip];
  var now = performance.now();
  var r = null;
  if (h && now - h.t > 300 && s.rx >= h.rx) {
    var dt = (now - h.t) / 1000;
    r = {
      rx: Math.round((s.rx - h.rx) / dt),
      drop: (s.drop - h.drop) / dt,
      und: (s.under - h.under) / dt,
      lost: (s.lost == null || h.lost == null) ? null : (s.lost - h.lost) / dt
    };
  }
  rateHistory[s.ip] = {rx: s.rx, drop: s.drop, under: s.under, lost: s.lost, t: now};
  return r;
}

/* === RENDER === */
function render() {
  if (!S) return;
  renderHeader();
  renderRigStrip();
  renderOnAir();
  renderFeedToggle();
  renderSetList();
  renderDials();
  /* Draw preview (N4: fetch for text patterns) */
  if (S.pattern.kind === 'text' && S.pattern.tver !== TP.ver) {
    fetchPreview();
  }
  drawPreview();
  if (document.body.className === 'setup') renderSetup();
}

function renderHeader() {
  var cs = document.getElementById('conn-status');
  if (pollFailures === 0) {
    cs.textContent = '●';
    cs.className = 'ok';
  } else {
    cs.textContent = '⚠ ' + pollFailures + 's';
    cs.className = 'stale';
  }
}

function renderRigStrip() {
  ensureTileGrid();
  /* Patch existing tiles with current state */
  var activeIds = {};
  if (S.slaves) {
    for (var i = 0; i < S.slaves.length; i++) {
      activeIds[S.slaves[i].id] = S.slaves[i];
      patchTile(S.slaves[i]);
    }
  }
  /* Hide tiles for slaves no longer known (but keep them as "lost") */
  for (var id in slaveTiles) {
    if (!activeIds[parseInt(id)]) {
      var tile = slaveTiles[id];
      tile.classList.add('lost');
      tile.querySelector('.tile-status').style.display = 'block';
      tile.querySelector('.tile-status').textContent = 'LOST';
    }
  }
}

function renderFeedToggle() {
  var on = S.stream_on;
  document.getElementById('feed-on').classList.toggle('active', on);
  document.getElementById('feed-off').classList.toggle('active', !on);
}

function renderDials() {
  var p = S.pattern;
  var act = document.activeElement;
  var ampEl = document.getElementById('amp');
  var freqEl = document.getElementById('freq');
  if (ampEl !== act) ampEl.value = Math.round(p.amp * 100);
  if (freqEl !== act) freqEl.value = p.freq;
  document.getElementById('ampv').textContent = Math.round(p.amp * 100) + ' %';
  document.getElementById('freqv').textContent = p.freq + ' Hz';
}

/* === SETUP RENDER === */
function renderSetup() {
  var p = S.pattern;

  /* Kind buttons */
  var kindBtns = document.querySelectorAll('#kindseg button');
  for (var i = 0; i < kindBtns.length; i++) {
    kindBtns[i].classList.toggle('on', kindBtns[i].getAttribute('data-k') === p.kind);
  }

  /* Show/hide kind-specific rows */
  var isText = p.kind === 'text';
  document.getElementById('ratiorow').style.display = (p.kind === 'lissajous') ? 'flex' : 'none';
  document.getElementById('petalsrow').style.display = (p.kind === 'rose') ? 'flex' : 'none';
  document.getElementById('textrow').style.display = isText ? 'flex' : 'none';
  document.getElementById('pulsedepthrow').style.display = isText ? 'flex' : 'none';
  document.getElementById('puleraterow').style.display = isText ? 'flex' : 'none';
  document.getElementById('spinrow').style.display = isText ? 'flex' : 'none';
  document.getElementById('pulsedepthrow').style.display = isText ? 'flex' : 'none';
  document.getElementById('puleraterow').style.display = isText ? 'flex' : 'none';
  document.getElementById('spinrow').style.display = isText ? 'flex' : 'none';

  /* Update ratio label */
  var rlabel = document.getElementById('ratio-label');
  if (p.kind === 'lissajous') rlabel.textContent = 'a : b';
  else if (p.kind === 'rose') rlabel.textContent = 'petals';

  /* Sync inputs (F9 fix — scoped) */
  var act = document.activeElement;
  var el = document.getElementById('ra'); if (el !== act) el.value = p.a;
  el = document.getElementById('rb'); if (el !== act) el.value = p.b;
  el = document.getElementById('petals'); if (el !== act) el.value = p.a;
  el = document.getElementById('text'); if (el !== act) el.value = p.text;
  el = document.getElementById('font'); if (el !== act) el.value = p.font;
  el = document.getElementById('pdepth'); if (el !== act) el.value = Math.round(p.pulse_depth * 100);
  el = document.getElementById('prate'); if (el !== act) el.value = Math.round(p.pulse_rate * 10);
  el = document.getElementById('rot'); if (el !== act) el.value = Math.round(p.rot * 100);
  document.getElementById('pdepthv').textContent = Math.round(p.pulse_depth * 100) + ' %';
  document.getElementById('pratev').textContent = (p.pulse_rate).toFixed(1) + ' Hz';
  document.getElementById('rotv').textContent = (p.rot).toFixed(2) + ' rev/s';
  el = document.getElementById('s-amp'); if (el !== act) el.value = Math.round(p.amp * 100);
  document.getElementById('s-ampv').textContent = Math.round((p.amp * 100)) + ' %';
  el = document.getElementById('s-freq'); if (el !== act) el.value = p.freq;
  document.getElementById('s-freqv').textContent = p.freq.toFixed(0) + ' Hz';
  document.getElementById('fxbtn').classList.toggle('on', p.flip_x);
  document.getElementById('fybtn').classList.toggle('on', p.flip_y);

  /* Text preview fetch */
  if (isText && p.tver !== TP.ver) fetchPreview();

  /* Allseg (set all) */
  var allseg = document.getElementById('allseg');
  if (allseg) {
    allseg.innerHTML = ['stream','hybrid','mic','circle','lissajous','ramp','square'].map(function(w) {
      return '<button class="chip" onclick="setAllSource(\\'' + w + '\\')">' +
        (w === 'stream' || w === 'hybrid' ? w.toUpperCase() : w) + '</button>';
    }).join('');
  }

  /* "Update current" is the only save path with the hold guard on it, and it
     shipped display:none with nothing to un-hide it. It is meaningful only
     once a preset has been loaded this session, so key it on that and say
     which one it will overwrite. */
  var upd = document.getElementById('setup-updbtn');
  if (upd) {
    upd.style.display = curPreset ? '' : 'none';
    upd.textContent = 'Update "' + curPreset + '"';
  }

  /* Setup set list */
  renderSetupSetList();
  renderTimers();
  renderSetupRig();
  renderDiagnostics();

  /* Timer cannot-fire indicator */
  var noFire = document.getElementById('timer-cannot-fire');
  if (noFire) noFire.style.display = (!S.stream_on) ? 'block' : 'none';

  renderTargetBtns();
}

/* === SETUP SET LIST === */
function renderSetupSetList() {
  if (document.activeElement && document.activeElement.closest('#setup-setlist')) return;
  var container = document.getElementById('setup-setlist');
  if (!container) return;
  var presets = S.presets || [];
  container.innerHTML = '';
  for (var i = 0; i < presets.length; i++) {
    var nm = typeof presets[i] === 'string' ? presets[i] : presets[i].name;
    var row = document.createElement('div');
    row.className = 'preset-row';
    row.innerHTML = '<span class="idx">' + (i+1) + '</span>' +
      '<span class="name">' + esc(nm) + '</span>' +
      '<span class="edit-controls" style="display:' + (editModeOn ? 'flex' : 'none') + ';gap:4px;margin-left:auto;">' +
        '<button onclick="event.stopPropagation();openRename(\\'' + nm + '\\', ' + i + ')" title="rename">✎</button>' +
        '<button class="danger" onclick="event.stopPropagation();delPreset(\\'' + nm + '\\')" title="delete">✕</button>' +
      '</span>';
    row.onclick = function(name) { return function() { applyPreset(name); }; }(nm);
    container.appendChild(row);
  }
  document.getElementById('preset-count').textContent = presets.length + ' of __PRESETS_MAX__';
}

function toggleEditMode() {
  editModeOn = !editModeOn;
  document.getElementById('edit-toggle').textContent = editModeOn ? 'Done' : 'Edit';
  renderSetupSetList();
}

/* One overlay, two jobs. Reassigning the shared Save button's onclick left the
   save-as closure installed whenever the dialog was dismissed any other way
   (Cancel, or a second thought) -- and the next tap of a row's rename pencil
   then saved the on-air pattern over that artist's preset, silently. A mode
   flag cannot leak: every path that opens the overlay sets it first. */
var overlayMode = 'rename';

function openRename(existingName, idx) {
  overlayMode = 'rename';
  renameTarget = existingName;
  var overlay = document.getElementById('rename-overlay');
  overlay.querySelector('p').textContent = 'Rename preset:';
  overlay.style.display = 'flex';
  var input = document.getElementById('rename-input');
  input.value = existingName;
  input.focus();
  input.select();
}

function setupSavePreset() {
  if (!S) return;
  var p = S.pattern;
  var def = (p.kind === 'text' ? p.text.split('\\n')[0] : p.kind).slice(0, 24);
  overlayMode = 'saveas';
  renameTarget = null;
  var overlay = document.getElementById('rename-overlay');
  overlay.querySelector('p').textContent = 'New preset name:';
  overlay.style.display = 'flex';
  var inp = document.getElementById('rename-input');
  inp.value = def;
  inp.focus();
  inp.select();
}

function submitRename() {
  var overlay = document.getElementById('rename-overlay');
  var name = document.getElementById('rename-input').value.trim();
  if (!name) return;
  overlay.style.display = 'none';
  if (overlayMode === 'saveas') {
    var names = (S && S.presets) || [];
    var exists = false;
    for (var i = 0; i < names.length; i++) {
      if ((typeof names[i] === 'string' ? names[i] : names[i].name) === name) exists = true;
    }
    /* Saving onto a name already in the set list is the destructive case, and
       it is the one the operator reaches by accident. Ask. */
    if (exists) {
      askConfirm('Overwrite preset "' + name + '" with current settings?', function() {
        setCur(name);
        post("/api/preset", {op: 'save', name: name});
      });
    } else {
      setCur(name);
      post("/api/preset", {op: 'save', name: name});
    }
    return;
  }
  if (name === renameTarget) return;
  post("/api/preset", {op: 'rename', name: renameTarget, new_name: name});
}

function setupUpdatePreset() {
  if (!curPreset) return showToast('No preset loaded yet — use Save preset', 3000, 'error');
  if (S.hold) {
    showToast('Cannot update: timer hold is on air. Update would save the ident, not the artist.', 5000, 'error');
    return;
  }
  askConfirm('Overwrite preset "' + curPreset + '" with current settings?', function() {
    post("/api/preset", {op: 'save', name: curPreset});
  });
}

function delPreset(n) {
  askConfirm('Delete preset "' + n + '"?', function() {
    if (n === curPreset) setCur('');
    post("/api/preset", {op: 'delete', name: n});
  });
}

/* === TIMERS === */
var tTargets = new Set();

function tgTarget(i) {
  if (tTargets.has(i)) tTargets.delete(i);
  else tTargets.add(i);
  renderTargetBtns();
}

function renderTargetBtns() {
  var ids = [];
  if (S && S.slaves) {
    var seen = {};
    for (var i = 0; i < S.slaves.length; i++) {
      var sid = S.slaves[i].id;
      if (!seen[sid]) { ids.push(sid); seen[sid] = true; }
    }
  }
  ids.sort(function(a,b) { return a - b; });
  var container = document.getElementById('ttargets');
  if (!container) return;
  var html = '<button class="' + (tTargets.size ? '' : 'on') + '" onclick="tTargets.clear();renderTargetBtns();">all</button>';
  for (var i = 0; i < ids.length; i++) {
    html += '<button class="' + (tTargets.has(ids[i]) ? 'on' : '') + '" onclick="tgTarget(' + ids[i] + ')">' + ids[i] + '</button>';
  }
  container.innerHTML = html;
}

function addTimer() {
  var preset = document.getElementById('tpreset').value;
  if (!preset) return showToast('Save a preset first — a timer shows a preset', 3000, 'error');
  post("/api/timer", {
    op: 'save',
    preset: preset,
    targets: [...tTargets],
    hold_s: +document.getElementById('thold').value,
    every_s: Math.round(+document.getElementById('tevery').value * 60)
  });
}

function renderTimers() {
  if (document.activeElement && document.activeElement.closest('#timer-table-wrap')) return;
  var ts = S.timers || [];
  var hold = S.hold;
  var container = document.getElementById('timer-table-wrap');
  if (!container) return;

  /* Fill preset select before early return (N12) */
  var sel = document.getElementById('tpreset');
  if (sel) {
    var cur = sel.value;
    var names = S.presets || [];
    sel.innerHTML = names.map(function(n) { return '<option>' + esc(n) + '</option>'; }).join('');
    if (names.indexOf(cur) >= 0) sel.value = cur;
  }

  document.getElementById('holdnote').textContent = hold
    ? '— on air: "' + (hold.preset || '?') + '", ' + fmtLeft(hold.left_s) + ' left' : '';

  if (!ts.length) {
    container.innerHTML = '<div style="color:var(--dim);padding:8px">No timers configured</div>';
    return;
  }

  var html = '<table class="timer-table"><tr><th>rule</th><th>schedule</th><th>status</th><th>actions</th></tr>';
  for (var i = 0; i < ts.length; i++) {
    var t = ts[i];
    var who = t.targets.length ? 'slave ' + t.targets.join('+') : 'every slave';
    var when = '';
    if (hold && hold.id === t.id) {
      when = '<span style="color:var(--ph);font-weight:bold">on air, ' + fmtLeft(hold.left_s) + ' left</span>';
    } else if (!t.enabled) {
      when = 'paused';
    } else if (t.next_in != null) {
      when = 'next in ' + fmtLeft(t.next_in);
    }

    /* Cannot fire indicator */
    if (t.enabled && !S.stream_on) {
      when += ' <span class="status-cannot-fire">⚠ cannot fire (feed SILENT)</span>';
    }

    html += '<tr>' +
      '<td>"' + esc(t.preset) + '" on ' + esc(who) + '</td>' +
      '<td>' + t.hold_s + 's every ' + (t.every_s >= 60 ? (t.every_s/60).toFixed(1) + ' min' : t.every_s + 's') + '</td>' +
      '<td>' + when + '</td>' +
      '<td>' +
        '<button class="' + (t.enabled ? 'on' : 'off') + '" onclick="timerOp(\\'toggle\\',' + t.id + ')">' + (t.enabled ? 'ON' : 'OFF') + '</button> ' +
        '<button onclick="timerOp(\\'fire\\',' + t.id + ')">run now</button> ' +
        '<button class="danger" onclick="delTimer(' + t.id + ',\\'' + esc(t.preset) + '\\')">✕</button>' +
      '</td></tr>';
  }
  html += '</table>';
  container.innerHTML = html;

}

function timerOp(op, id) { post("/api/timer", {op: op, id: id}); }

function delTimer(id, preset) {
  askConfirm('Delete timer for "' + preset + '"?', function() {
    post("/api/timer", {op: 'delete', id: id});
  });
}

/* === SETUP RIG (per-slave cards) === */
function renderSetupRig() {
  if (document.activeElement && document.activeElement.closest('#setup-rig')) return;
  var container = document.getElementById('setup-rig');
  if (!container) return;
  container.innerHTML = '';
  if (!S.slaves) return;

  for (var i = 0; i < S.slaves.length; i++) {
    var s = S.slaves[i];
    var verdict = slaveVerdict(s);
    var activeMode = s.source ? (s.mode === 2 ? 'hybrid' : 'stream') : 'local';

    var card = document.createElement('div');
    card.className = 'panel';
    card.style.marginBottom = '8px';
    card.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
        '<strong>SLAVE ' + s.id + '</strong> <small style="color:var(--dim)">' + s.ip + '</small>' +
      '</div>' +
      '<div class="verdict ' + verdict.cls + '" style="margin-bottom:8px">' + verdict.txt + '</div>' +
      '<div class="row">' +
        '<label>source</label>' +
        '<div class="source-3way">' +
          '<button class="' + (activeMode === 'stream' ? 'active' : '') + '" onclick="setSlaveSource(\\'' + s.ip + '\\',\\'network\\')">STREAM</button>' +
          '<button class="' + (activeMode === 'hybrid' ? 'active' : '') + '" onclick="setSlaveSource(\\'' + s.ip + '\\',\\'hybrid\\')">HYBRID</button>' +
          '<button class="' + (activeMode === 'local' ? 'active' : '') + '" onclick="setSlaveSource(\\'' + s.ip + '\\',\\'local\\')">OWN</button>' +
        '</div>' +
      '</div>' +
      '<div class="row" style="margin-top:8px">' +
        '<label>fallback</label>' +
        '<select onchange="cmd(\\'' + s.ip + '\\',{cmd:\\'set_pattern\\',pattern:this.value})">' +
          ['mic','circle','lissajous','ramp','square'].map(function(p) {
            return '<option value="' + p + '"' + (s.lpat === p ? ' selected' : '') + '>' + p + '</option>';
          }).join('') +
        '</select>' +
      '</div>' +
      '<div class="row" style="margin-top:8px">' +
        '<label>level</label>' +
        '<button onclick="cmd(\\'' + s.ip + '\\',{cmd:\\'set_gain\\',gain:0.9})" style="padding:4px 16px">−</button>' +
        '<button onclick="cmd(\\'' + s.ip + '\\',{cmd:\\'set_gain\\',gain:1.1})" style="padding:4px 16px">+</button>' +
        '<span style="color:var(--dim);font-size:12px;margin-left:8px">(nudges — value not readable)</span>' +
      '</div>' +
      '<div class="row" style="margin-top:8px">' +
        '<button onclick="cmd(\\'' + s.ip + '\\',{cmd:\\'identify\\'})">FLASH LEDS</button>' +
        '<button class="danger" onclick="askConfirm(\\'Reboot slave ' + s.id + '?\\')/* fixed below */">REBOOT</button>' +
      '</div>';

    container.appendChild(card);
  }

  /* Fix reboot buttons (inline onclick won't have closure, so use delegation) */
  var rebootBtns = container.querySelectorAll('.danger');
  var slaveList = S.slaves;
  for (var i = 0; i < rebootBtns.length; i++) {
    (function(idx) {
      rebootBtns[idx].onclick = function() {
        askConfirm('Reboot slave ' + slaveList[idx].id + '? (2 s outage)', function() {
          cmd(slaveList[idx].ip, {cmd: 'reboot'});
        });
      };
    })(i);
  }
}

function setAllSource(mode) {
  if (!S || !S.slaves) return;
  noteSlaveChange('all');
  for (var i = 0; i < S.slaves.length; i++) {
    let ip = S.slaves[i].ip;
    if (mode === 'stream') cmd(ip, {cmd: 'set_mode', mode: 'network'});
    else if (mode === 'hybrid') cmd(ip, {cmd: 'set_mode', mode: 'hybrid'});
    else cmd(ip, {cmd: 'set_pattern', pattern: mode}).then(function() {
      return cmd(ip, {cmd: 'set_mode', mode: 'local'});
    });
  }
}

/* === DIAGNOSTICS === */
function renderDiagnostics() {
  if (document.activeElement && document.activeElement.closest('#setup-diagnostics')) return;
  var container = document.getElementById('diag-section');
  if (!container) return;
  if (!S.slaves || !S.slaves.length) {
    container.innerHTML = '<div style="color:var(--dim)">No slave data</div>';
    return;
  }

  var html = '';
  for (var i = 0; i < S.slaves.length; i++) {
    var s = S.slaves[i];
    var r = rates(s);
    html += '<h3 style="color:var(--ph);margin:12px 0 4px">SLAVE ' + s.id + ' ' + s.ip + '</h3>';
    html += '<div class="stats-grid">';

    /* LINK group */
    html += '<div class="stat"><label style="grid-column:1/4;color:var(--dim);border-bottom:1px solid var(--line)">LINK</label></div>';
    html += '<div class="stat"><label>rssi</label><value>' + s.rssi + ' dBm</value></div>';
    html += '<div class="stat"><label>buf</label><value>' + Math.round(s.depth/48) + ' ms</value></div>';
    html += '<div class="stat"><label>up</label><value>' + fmtUp(s.uptime || 0) + '</value></div>';

    /* STREAM group */
    html += '<div class="stat"><label style="grid-column:1/4;color:var(--dim);border-bottom:1px solid var(--line);margin-top:4px">STREAM</label></div>';
    html += '<div class="stat"><label>rx/s</label><value>' + (r ? r.rx : '—') + '</value></div>';
    html += '<div class="stat"><label>drop/s</label><value>' + (r ? r.drop.toFixed(1) : '—') + '</value></div>';
    html += '<div class="stat"><label>lost/s</label><value>' + (r && r.lost != null ? r.lost.toFixed(1) : '—') + '</value></div>';
    html += '<div class="stat"><label>under/s</label><value>' + (r ? r.und.toFixed(1) : '—') + '</value></div>';
    html += '<div class="stat"><label>age</label><value>' + (s.age_ms/1000).toFixed(1) + 's</value></div>';
    html += '<div class="stat"><label>rx</label><value>' + fmtN(s.rx) + '</value></div>';
    html += '<div class="stat"><label>drop</label><value>' + fmtN(s.drop) + '</value></div>';
    html += '<div class="stat"><label>lost</label><value>' + (s.lost == null ? '—' : fmtN(s.lost)) + '</value></div>';
    html += '<div class="stat"><label>under</label><value>' + fmtN(s.under) + '</value></div>';

    /* POWER group */
    html += '<div class="stat"><label style="grid-column:1/4;color:var(--dim);border-bottom:1px solid var(--line);margin-top:4px">POWER</label></div>';
    html += '<div class="stat"><label>vbat</label><value>' + (s.vbat_mv || 0) + ' mV</value></div>';

    html += '</div>';
  }

  /* Network state */
  var net = S.net || {};
  html += '<h3 style="color:var(--ph);margin:16px 0 8px">NETWORK</h3>';
  if (net.egress === false) {
    html += '<div style="background:#3d1010;border:1px solid var(--bad);padding:12px;border-radius:4px;color:var(--bad)">' +
      '<strong>Wi-Fi AP is DOWN</strong> — no interface holds ' + (net.iface || '?') + '.<br>' +
      'On the controller: <code>sudo nmcli c up hyperosci-ap</code><br>' +
      '(this panel recovers on its own within a second — no restart needed)</div>';
  } else {
    html += '<div style="color:var(--ph)">AP is UP — stream can reach slaves</div>';
  }

  container.innerHTML = html;
}

/* === STALE RENDER (F1 — never freeze) === */
function renderStale(staleState, failures) {
  /* Update conn-status */
  var cs = document.getElementById('conn-status');
  cs.textContent = '⚠ ' + failures + 's';
  cs.className = 'stale';

  /* Mark tiles stale */
  if (staleState.slaves) {
    for (var i = 0; i < staleState.slaves.length; i++) {
      var s = staleState.slaves[i];
      var tile = slaveTiles[s.id];
      if (tile) {
        tile.classList.add('stale');
        tile.classList.remove('lost');
        var ageEl = tile.querySelector('.tile-age');
        if (ageEl) ageEl.textContent = 'STALE';
      }
    }
  }
}

/* === POLL (F1 — count failures, never freeze) ===
   refresh() fetches and draws once. poll() is the only thing that schedules
   the next one. They are separate because post() wants a fresh frame right
   after a command: when that called poll(), every tap forked a second
   self-perpetuating chain and the rate climbed for the rest of the night --
   six taps measured 1/s -> 7/s, and each request takes the same state.lock
   the 5 ms stream loop needs. */
async function refresh() {
  try {
    S = await (await fetch("/api/state")).json();
    pollFailures = 0;
    lastKnownState = S;
    document.body.classList.remove('disconnected');
  } catch (e) {
    pollFailures++;
    if (pollFailures >= 3) {
      document.body.classList.add('disconnected');
      document.getElementById('conn-banner').textContent =
        '⚠ No contact with controller for ' + pollFailures + 's — last state shown';
    }
    /* Still render with stale data so UI doesn't freeze (F1 fix) */
    if (lastKnownState) renderStale(lastKnownState, pollFailures);
  }
  /* render() gets its own catch: a render bug must not read as a dead
     controller -- that banner is the one message the operator is taught to
     trust. */
  try {
    render();
  } catch (e) {
    console.error('render failed', e);
  }
}

function poll() {
  /* finally, not then: a refresh that throws must still re-arm, or the page
     freezes on a stale frame with no banner to say so. */
  refresh().catch(function(e) { console.error('refresh failed', e); })
           .then(function() { setTimeout(poll, 1000); });
}

/* === INIT === */
(function init() {
  /* Set initial mode (F13 — neutral until first poll) */
  setMode(getMode());

  /* Header starts neutral */
  document.getElementById('conn-status').textContent = '○';

  /* Start polling */
  poll();
})();
</script>
</body>
</html>
"""
# Keep the page's "max N" in step with the constant. A targeted replace, not
# .format(): the page is full of JS braces.
PAGE = PAGE.replace("__PRESETS_MAX__", str(PRESETS_MAX))


def make_http_handler(state, cmds):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the daemon log for protocol events
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                self._json(state.snapshot())
            elif self.path == "/api/textpreview":
                with state.lock:
                    tbl, ver = state.text_tbl, state.text_ver
                dec = max(1, len(tbl) // 400) if tbl else 1
                self._json({"ver": ver,
                            "pts": [[round(x, 3), round(y, 3)]
                                    for x, y in (tbl or [])[::dec]]})
            else:
                self._json({"err": "not found"}, 404)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n)) if n else {}
            except (ValueError, json.JSONDecodeError):
                return self._json({"err": "bad json"}, 400)

            if self.path == "/api/pattern":
                rebuild = "text" in body or "font" in body
                # Only a rebuild needs the mutex; slider POSTs must not queue
                # behind a 36 ms font parse.
                new_tbl = None
                with state.rebuild_lock if rebuild else nullcontext():
                    if rebuild:
                        with state.lock:
                            text, font = state.text, state.font
                        if "text" in body:
                            text = sanitize_text(body["text"])
                        if body.get("font") in TEXT_FONTS:
                            font = body["font"]
                        # Rebuild OUTSIDE state.lock: font parsing must never
                        # stall the 5 ms stream pacing.
                        new_tbl = (text, font) + build_text(text, font)
                    self._apply_pattern(state, body, new_tbl)
                if set(body) - {"stream"}:   # a real pattern edit, not a mute
                    self._takeover(state)
                self._json({"ok": True})
            elif self.path == "/api/preset":
                return self._preset(state, body)
            elif self.path == "/api/timer":
                return self._timer(state, body)
            elif self.path == "/api/cmd":
                ip, cmd_obj = body.get("ip"), body.get("cmd")
                if not ip or not isinstance(cmd_obj, dict):
                    return self._json({"err": "need ip + cmd"}, 400)
                cmds.send(ip, cmd_obj)
                self._takeover(state)  # end any hold so draw change persists (F11)
                self._json({"ok": True})
            else:
                self._json({"err": "not found"}, 404)

        def _apply_pattern(self, state, body, new_tbl):
            with state.lock:
                if body.get("kind") in ("circle", "lissajous", "rose", "text"):
                    state.kind = body["kind"]
                if "freq" in body:
                    state.freq = min(2000.0, max(1.0, float(body["freq"])))
                if "amp" in body:
                    state.amp = min(1.0, max(0.0, float(body["amp"])))
                if "a" in body:
                    state.ratio_a = min(9, max(1, int(body["a"])))
                if "b" in body:
                    state.ratio_b = min(9, max(1, int(body["b"])))
                if "pulse_rate" in body:
                    state.pulse_rate = min(10.0, max(
                        0.1, float(body["pulse_rate"])))
                if "pulse_depth" in body:
                    state.pulse_depth = min(1.0, max(
                        0.0, float(body["pulse_depth"])))
                if "rot" in body:
                    state.rot_speed = min(2.0, max(-2.0, float(body["rot"])))
                if "flip_x" in body:
                    state.flip_x = bool(body["flip_x"])
                if "flip_y" in body:
                    state.flip_y = bool(body["flip_y"])
                if "stream" in body:
                    state.stream_on = bool(body["stream"])
                if new_tbl is not None:
                    # Write back only what this request set: a POST carrying
                    # just "text" must not also restore its stale snapshot of
                    # "font" over a concurrent font change.
                    if "text" in body:
                        state.text = new_tbl[0]
                    if body.get("font") in TEXT_FONTS:
                        state.font = new_tbl[1]
                    state.text_tbl, state.text_rmax = new_tbl[2], new_tbl[3]
                    state.text_ver += 1
            state.dirty = True   # persist_loop writes it within ~3 s

        def _preset(self, state, body):
            op = body.get("op")
            name = sanitize_name(body.get("name", ""))
            if op not in ("save", "load", "delete", "move", "rename") or not name:
                return self._json({"err": "need op + name"}, 400)
            # Each branch below decides under the lock and replies after it is
            # dropped. _json writes to the client socket, and a phone on a
            # marginal AP link can make that block -- held under state.lock
            # that is a stall in the 5 ms stream loop, i.e. an audible gap on
            # every scope at once.
            if op == "save":
                held = full = False
                plist = None
                with state.lock:
                    # F0: no saving the ident over an artist's preset mid-hold
                    if state.timer_hold is not None:
                        held = True
                    else:
                        snap = {"name": name,
                                "kind": state.kind, "freq": state.freq,
                                "amp": state.amp, "a": state.ratio_a,
                                "b": state.ratio_b, "text": state.text,
                                "font": state.font,
                                "pulse_rate": state.pulse_rate,
                                "pulse_depth": state.pulse_depth,
                                "rot": state.rot_speed,
                                "flip_x": state.flip_x,
                                "flip_y": state.flip_y}
                        for i, p in enumerate(state.presets):
                            if p["name"] == name:  # overwrite in place
                                state.presets[i] = snap
                                break
                        else:
                            if len(state.presets) >= PRESETS_MAX:
                                full = True
                            else:
                                state.presets.append(snap)
                        if not full:
                            plist = list(state.presets)
                if held:
                    return self._json(
                        {"err": "cannot save — timer hold active"}, 409)
                if full:
                    return self._json(
                        {"err": f"max {PRESETS_MAX} presets"}, 400)
                save_presets(plist)  # file IO outside the lock
                return self._json({"ok": True})
            if op == "move":
                try:
                    idx = int(body.get("index", -1))
                except (TypeError, ValueError):
                    return self._json({"err": "need index"}, 400)
                plist = None
                with state.lock:
                    try:
                        p = state.presets.pop(
                            next(i for i, x in enumerate(state.presets)
                                 if x["name"] == name))
                    except StopIteration:
                        p = None
                    if p is not None:
                        # Clamp: insert(-1) would quietly land it second to
                        # last, and a bad index must not reorder the set list.
                        idx = max(0, min(idx, len(state.presets)))
                        state.presets.insert(idx, p)
                        plist = list(state.presets)
                if plist is None:
                    return self._json({"err": "no such preset"}, 404)
                save_presets(plist)
                return self._json({"ok": True})
            if op == "rename":
                new_name = sanitize_name(body.get("new_name", ""))
                if not new_name:
                    return self._json({"err": "need new_name"}, 400)
                plist = tlist = None
                clash = False
                with state.lock:
                    # The page keys its set-list rows by name; two presets
                    # sharing one would collapse into a single row.
                    if any(p["name"] == new_name for p in state.presets):
                        clash = True
                    else:
                        for p in state.presets:
                            if p["name"] == name:
                                p["name"] = new_name
                                touched = [t for t in state.timers
                                           if t["preset"] == name]
                                for t in touched:
                                    t["preset"] = new_name
                                plist = list(state.presets)
                                # Rewritten in memory but never written out,
                                # the rule came back after a restart pointing
                                # at a name that no longer exists -- enabled,
                                # counting down, unable to ever fire.
                                tlist = list(state.timers) if touched else None
                                break
                if clash:
                    return self._json({"err": "name already used"}, 409)
                if plist is None:
                    return self._json({"err": "no such preset"}, 404)
                save_presets(plist)
                if tlist is not None:
                    save_timers(tlist)
                return self._json({"ok": True})
            if op == "delete":
                with state.lock:
                    state.presets = [p for p in state.presets
                                     if p["name"] != name]
                    plist = list(state.presets)
                    # A rule pointing at a preset that is gone can never fire,
                    # but it kept rendering ON with a live countdown -- the
                    # dashboard promising an ident that was never coming.
                    # Pause those rules and tell the operator instead.
                    paused = [t for t in state.timers
                              if t["preset"] == name and t["enabled"]]
                    for t in paused:
                        t["enabled"] = False
                    tlist = list(state.timers) if paused else None
                    stuck = (state.timer_hold is not None
                             and state.timer_hold["preset"] == name)
                save_presets(plist)
                if tlist is not None:
                    save_timers(tlist)
                if stuck:   # don't leave the show on a preset just deleted
                    end_hold(state, cmds)
                return self._json({"ok": True, "note": None if not paused else
                                   f"{len(paused)} timer(s) paused — they "
                                   f"showed \"{name}\""})
            with state.lock:  # load
                p = next((dict(p) for p in state.presets
                          if p["name"] == name), None)
            if p is None:
                return self._json({"err": "no such preset"}, 404)
            apply_preset(state, p)   # same mutex dance as /api/pattern
            self._takeover(state)
            return self._json({"ok": True})

        def _takeover(self, state):
            """A hand on the panel ends any interval hold: the targets go back
            to the draw setting they had, but the pattern stays as just set.
            Silently reverting the operator fifteen seconds later is worse
            than a timer missing one cycle.

            Called unconditionally. end_hold's read-and-clear is atomic and
            already a no-op when nothing holds, whereas an unlocked `if` here
            would miss a hold that starts between the test and the call and
            then revert the operator's pattern a hold later."""
            end_hold(state, cmds, restore_pattern=False)

        def _timer(self, state, body):
            op = body.get("op")
            if op not in ("save", "delete", "toggle", "fire"):
                return self._json({"err": "bad op"}, 400)
            if op == "save":
                t = clean_timer(body)
                if not t["preset"]:
                    return self._json({"err": "need a preset"}, 400)
                missing = full = False
                tlist = None
                with state.lock:
                    if not any(p["name"] == t["preset"]
                               for p in state.presets):
                        missing = True
                    else:
                        for i, old in enumerate(state.timers):
                            if old["id"] == t["id"] and t["id"]:
                                state.timers[i] = t
                                break
                        else:
                            if len(state.timers) >= TIMERS_MAX:
                                full = True
                            else:
                                t["id"] = state.next_timer_id
                                state.next_timer_id += 1
                                state.timers.append(t)
                        if not full:
                            # Re-arm to a full period out, so tweaking "every"
                            # at 4m59s does not fire the moment you let go --
                            # and so the countdown the page draws is right
                            # immediately, not from timer_loop's next tick.
                            state.timer_next[t["id"]] = (
                                mono_us() + t["every_s"] * 1_000_000)
                            tlist = list(state.timers)
                if missing:
                    return self._json({"err": "no such preset"}, 404)
                if full:
                    return self._json({"err": f"max {TIMERS_MAX} timers"}, 400)
                save_timers(tlist)
                # W8: echo the clamped period back, so a page that asked for
                # 2 s and got the 10 s minimum shows 10 s instead of quietly
                # drawing a schedule the controller is not running.
                return self._json({"ok": True, "id": t["id"],
                                   "every_s": t["every_s"],
                                   "hold_s": t["hold_s"]})
            try:
                tid = int(body.get("id", 0))
            except (TypeError, ValueError):
                return self._json({"err": "need id"}, 400)
            if op == "fire":
                with state.lock:
                    t = next((dict(x) for x in state.timers
                              if x["id"] == tid), None)
                if t is None:
                    return self._json({"err": "no such timer"}, 404)
                reason = fire_timer(state, cmds, t)
                if reason:
                    return self._json({"err": "not now — " + reason}, 409)
                # F12: a hand-fired hold costs the rule its turn. Without this
                # the schedule is untouched, so "run now" plays the ident and
                # then the rule plays it again seconds later -- twice, mid-set.
                with state.lock:
                    state.timer_next[tid] = (mono_us()
                                             + t["every_s"] * 1_000_000)
                return self._json({"ok": True})
            with state.lock:
                if op == "delete":
                    state.timers = [x for x in state.timers
                                    if x["id"] != tid]
                    state.timer_next.pop(tid, None)
                else:
                    for x in state.timers:
                        if x["id"] == tid:
                            x["enabled"] = not x["enabled"]
                            # Un-pausing waits a full period rather than
                            # firing off whatever due time went stale.
                            state.timer_next[tid] = (
                                mono_us() + x["every_s"] * 1_000_000)
                tlist = list(state.timers)
                # Read under the same lock that took tlist. Tested and
                # subscripted as two separate unlocked reads, a hold expiring
                # in between made this a None subscript -- a 500 on the
                # operator's tap, mid-show.
                stuck = (state.timer_hold is not None
                         and state.timer_hold["id"] == tid)
            # A rule deleted or switched off mid-hold must not leave the show
            # stuck on its preset.
            if stuck:
                end_hold(state, cmds)
            save_timers(tlist)
            return self._json({"ok": True})

    return Handler


# ---------------------------------------------------------------------------
# Streaming engine (from hype_sender.py, driven by State)
# ---------------------------------------------------------------------------

def tx_rollup(tx_ok, tx_full, tx_err, tx_prev):
    """Fold the per-slave TX counters into what /api/state publishes.

    Cumulative totals answer "how bad has tonight been"; the pct is windowed
    over the gap since the previous call, because that is the number that has
    to move while someone is standing at the rig changing something.

    Pure apart from `tx_prev`, which it advances to the totals it just read —
    so the next call's window starts here. Split out of stream_loop only so
    the interesting case (things are going wrong) is reachable from a test;
    on a healthy rig this returns 0.0 forever and proves nothing.

    Returns (published dict, worst windowed pct).
    """
    pub, worst = {}, 0.0
    for ip in set(tx_ok) | set(tx_full) | set(tx_err):
        ok = tx_ok.get(ip, 0)
        eno = tx_full.get(ip, 0)
        err = tx_err.get(ip, 0)
        p_ok, p_eno, p_err = tx_prev.get(ip, (0, 0, 0))
        tx_prev[ip] = (ok, eno, err)
        tried = (ok - p_ok) + (eno - p_eno) + (err - p_err)
        lost = (eno - p_eno) + (err - p_err)
        pct = round(100.0 * lost / tried, 1) if tried else 0.0
        worst = max(worst, pct)
        pub[ip] = {"ok": ok, "full": eno, "err": err, "pct": pct}
    return pub, worst


def stream_loop(state, iface_ip):
    def make_ctrl():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        return s

    def bind_egress(s):
        """Aim multicast at the AP interface. False until that IP exists."""
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                         socket.inet_aton(iface_ip))
            return True
        except OSError:
            return False  # EADDRNOTAVAIL: no interface holds iface_ip yet

    ctrl = make_ctrl()
    egress = None  # last reported bind state; None = nothing logged yet
    audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Non-blocking (protocol.md §10): if the AP stalls and the kernel TX queue
    # fills, a blocking sendto to slave 1 would stall the whole fan-out loop
    # past the 20 ms re-anchor threshold — rebuffering every scope at once.
    audio.setblocking(False)

    status = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    status.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    status.bind(("0.0.0.0", PORT_STATUS))
    status.setblocking(False)

    gen = PatternGen()
    seq_sync = 0
    seq_audio = 0
    frames_sent = 0

    start = mono_us()
    epoch = start + LEAD_US  # deadline of audio frame 0
    next_audio = start
    next_sync = start

    # Local TX accounting, per slave. `audio` is non-blocking, so when the
    # radio's queue backs up sendto raises ENOBUFS and the packet dies here,
    # before the air. This used to be an unlogged `pass`: 43% of a night's
    # packets vanished at this line and read downstream as air loss, which is
    # where an evening of debugging went. Per slave, because once there are
    # four scopes the question is always *which* link is under pressure.
    #
    # Deliberately lock-free: plain locals, published once a second as a
    # freshly built dict in one atomic attribute store. Taking state.lock per
    # packet would serialise the fan-out against the HTTP threads exactly
    # when the queue is already backed up — the stall the non-blocking socket
    # above exists to prevent.
    tx_ok, tx_full, tx_err = {}, {}, {}
    tx_prev = {}      # ip -> (ok, full, err) as of the last publish
    tx_last = ""      # most recent errno seen, named, for the dashboard
    next_txstat = start + TXSTAT_INTERVAL_US
    tx_warned_us = 0

    while True:
        now = mono_us()

        if now >= next_sync:
            # Re-aim the beacon socket every 500 ms. Booting before
            # hyperosci-ap exists makes IP_MULTICAST_IF fail with
            # EADDRNOTAVAIL; the socket then follows the DEFAULT ROUTE — out
            # of the USB tether, to a laptop — and nothing ever retries,
            # because the only retry hangs off the send-failure path and sends
            # stop failing the moment any route exists. The log stays clean
            # and no slave ever hears a beacon. This is not a hot path and
            # setsockopt is microseconds, so just do it unconditionally.
            ok = bind_egress(ctrl)
            if ok is not egress:
                egress = ok
                with state.lock:
                    state.net_egress = ok
                print(f"[net] multicast egress {iface_ip}: "
                      + ("bound" if ok else
                         "UNAVAILABLE — beacons follow the default route"),
                      flush=True)
            pkt = HDR.pack(HYPE_MAGIC, HYPE_VERSION, HYPE_SYNC, 0, seq_sync,
                           mono_us())
            try:
                ctrl.sendto(pkt, (MCAST_GROUP, PORT_CTRL))
            except OSError as e:
                # AP interface bounced (ENETUNREACH etc.) — survive it: the
                # slaves fall back to mic and rejoin; we must still be here.
                print(f"[net] sync send failed ({e}); recreating socket",
                      flush=True)
                ctrl.close()
                ctrl = make_ctrl()
                egress = None  # force a re-bind + log line next beacon
            seq_sync += 1
            next_sync += SYNC_INTERVAL_US

        with state.lock:
            streaming = state.stream_on
            # Only stream to slaves that will actually play it (NETWORK /
            # HYBRID). A LOCAL slave ignores the stream — sending it 200 pkt/s
            # just burns airtime and overflows its jitter buffer (which is
            # what used to inflate the drop counter).
            targets = [ip for ip, s in state.slaves.items()
                       if s["mode"] != 0]

        if streaming:
            # Send every due packet (catch-up burst after scheduler stalls,
            # capped so a long stall can't wedge the loop).
            burst = 0
            while now >= next_audio and burst < 10:
                # Keep deadlines glued to real time (see hype_sender.py): if
                # pacing drifted >20 ms, re-anchor the epoch — the slave sees
                # one discontinuity and cleanly rebuffers.
                ideal = epoch + frames_sent * 1_000_000 // SAMPLE_RATE
                drift = (now + LEAD_US) - ideal
                if abs(drift) > 20_000:
                    epoch += drift
                    print(f"[re-anchor] drift={drift/1000:.1f}ms", flush=True)
                deadline = epoch + frames_sent * 1_000_000 // SAMPLE_RATE
                payload = gen.block(FRAMES, state.pattern_params())
                pkt = (HDR.pack(HYPE_MAGIC, HYPE_VERSION, HYPE_AUDIO, 0,
                                seq_audio, deadline) +
                       AUDIO_HDR.pack(SAMPLE_RATE, FRAMES, 0) + payload)
                for ip in targets:
                    try:
                        audio.sendto(pkt, (ip, PORT_AUDIO))
                        tx_ok[ip] = tx_ok.get(ip, 0) + 1
                    except OSError as e:
                        # Still never raise: the remaining slaves need this
                        # packet and the slave conceals a gap. Just stop
                        # losing it silently.
                        n = errno.errorcode.get(e.errno, e.errno)
                        if e.errno in TX_QUEUE_FULL:
                            tx_full[ip] = tx_full.get(ip, 0) + 1
                        else:
                            tx_err[ip] = tx_err.get(ip, 0) + 1
                        tx_last = "%s %s" % (ip, n)
                seq_audio += 1
                frames_sent += FRAMES
                next_audio += PACKET_US
                burst += 1
                now = mono_us()
            if now - next_audio > 200_000:  # hopelessly behind: resync pacing
                next_audio = now
        else:
            # Paused: keep pacing anchored so resume re-anchors once, cleanly.
            next_audio = now + PACKET_US

        # Publish TX accounting: one atomic store per second, no lock.
        if now >= next_txstat:
            next_txstat = now + TXSTAT_INTERVAL_US
            pub, worst = tx_rollup(tx_ok, tx_full, tx_err, tx_prev)
            state.net_tx = pub
            # Leave the evidence in the journal too, throttled to 10 s —
            # the dashboard is not open at 03:00 when this starts.
            if worst >= 5.0 and now - tx_warned_us >= 10_000_000:
                tx_warned_us = now
                print("[net] TX dropped before the air: "
                      + ", ".join("%s %s%%" % (ip, d["pct"])
                                  for ip, d in sorted(pub.items())
                                  if d["pct"] > 0)
                      + (" (last error: %s)" % tx_last if tx_last else ""),
                      flush=True)

        # STATUS receive + slave discovery
        timeout = max(0.0, min(next_audio, next_sync) - mono_us()) / 1e6
        r, _, _ = select.select([status], [], [], min(timeout, 0.005))
        if r:
            try:
                data, (src_ip, _) = status.recvfrom(2048)
            except BlockingIOError:
                data = None
            if data and len(data) >= HDR.size + STATUS_PAYLOAD.size:
                magic, ver, typ, _, _, _ = HDR.unpack_from(data)
                if magic == HYPE_MAGIC and ver == HYPE_VERSION \
                        and typ == HYPE_STATUS:
                    (mac, sid, mode, source, rssi, vbat, depth, rx, dropped,
                     underruns, uptime, offset) = STATUS_PAYLOAD.unpack_from(
                        data, HDR.size)
                    # local_pattern byte appended in fw 2026-07-18; tolerate
                    # a not-yet-reflashed slave sending the 34-byte payload.
                    lp_off = HDR.size + STATUS_PAYLOAD.size
                    lpat = (PATTERN_NAMES.get(data[lp_off], "?")
                            if len(data) > lp_off else None)
                    # lost_packets uint32 appended after local_pattern
                    # (protocol.md §3.4, fw 2026-07-19): cumulative missing
                    # AUDIO seq — loss the slave's rx_dropped cannot see.
                    # None until the slave runs the new firmware.
                    lost = (struct.unpack_from("<I", data, lp_off + 1)[0]
                            if len(data) >= lp_off + 5 else None)
                    with state.lock:
                        if src_ip not in state.slaves:
                            print(f"[discovered] slave id={sid} at {src_ip} "
                                  f"mac={mac.hex(':')}", flush=True)
                        state.slaves[src_ip] = {
                            "ip": src_ip, "mac": mac.hex(":"), "id": sid,
                            "mode": mode, "source": source, "rssi": rssi,
                            "vbat_mv": vbat, "depth": depth, "rx": rx,
                            "drop": dropped, "under": underruns,
                            "lost": lost,
                            "uptime": uptime, "offs": offset,
                            "lpat": lpat, "last_us": mono_us(),
                        }

        # Two-stage eviction: mark at 5s, delete at 30s (F8 tombstone).
        # CRITICAL: use list() to avoid RuntimeError on dict mutation during iteration.
        # CRITICAL: use "gone" field, not "lost" (which collides with lost-packet counter).
        with state.lock:
            for ip, s in list(state.slaves.items()):
                age = now - s["last_us"]
                if age > 30_000_000:  # 30s: truly gone
                    print(f"[lost] {ip}", flush=True)
                    del state.slaves[ip]
                elif age > 5_000_000 and not s.get("gone"):  # 5s: mark lost
                    s["gone"] = True
                    s["lost_since"] = now
                    print(f"[lost] {ip} — marked (will delete at 30s)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface-ip", default="192.168.50.1",
                    help="local IP of the AP interface (multicast egress)")
    ap.add_argument("--pattern", default="circle",
                    choices=["circle", "lissajous", "rose", "text"],
                    help="first-boot pattern only; a saved ~/hype_state.json "
                         "wins over this")
    ap.add_argument("--http-port", type=int, default=8080)
    args = ap.parse_args()

    state = State(args.pattern)
    state.net_iface = args.iface_ip
    cmds = CmdSender(state)

    httpd = ThreadingHTTPServer(("0.0.0.0", args.http_port),
                                make_http_handler(state, cmds))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=persist_loop, args=(state,), daemon=True).start()
    threading.Thread(target=timer_loop, args=(state, cmds),
                     daemon=True).start()

    print(f"[controller] web on :{args.http_port}, iface={args.iface_ip}, "
          f"lead={LEAD_US/1000:.0f}ms", flush=True)
    shown = (f"text {state.text!r} {state.font}" if state.kind == "text"
             else state.kind)
    print(f"[controller] pattern {shown} {state.freq:g} Hz amp {state.amp:.2f}"
          + ("" if load_live() else "  (defaults — no saved state yet)"),
          flush=True)
    stream_loop(state, args.iface_ip)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
