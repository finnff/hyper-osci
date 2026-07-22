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
PRESETS_MAX = 10
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
                "net": {"iface": self.net_iface, "egress": self.net_egress},
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
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HYPEROSCI</title>
<style>
:root { --bg:#070b07; --panel:#0d140d; --line:#1d2b1d; --fg:#c9e8c9;
        --dim:#5f7a5f; --ph:#39ff14; --warn:#ffb347; --bad:#ff5252; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--fg); font:14px/1.45 ui-monospace,
       "JetBrains Mono",Menlo,Consolas,monospace; padding:16px; }
h1 { font-size:18px; letter-spacing:.35em; color:var(--ph);
     text-shadow:0 0 12px rgba(57,255,20,.55); }
header { display:flex; align-items:center; gap:16px; flex-wrap:wrap;
         margin-bottom:14px; }
.panel { background:var(--panel); border:1px solid var(--line);
         border-radius:8px; padding:14px; }
#top { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:14px; }
#scope { width:260px; height:260px; background:#020402; border-radius:8px;
         border:1px solid var(--line); flex:none; }
#controls { flex:1; min-width:280px; display:flex; flex-direction:column;
            gap:12px; }
.row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.row label { width:70px; color:var(--dim); flex:none; }
input[type=range] { flex:1; min-width:120px; accent-color:var(--ph); }
.val { width:72px; text-align:right; color:var(--ph); flex:none; }
button, select, input[type=number] { background:#101b10; color:var(--fg);
  border:1px solid var(--line); border-radius:5px; padding:5px 12px;
  font:inherit; cursor:pointer; }
button:hover { border-color:var(--ph); }
button.on { background:var(--ph); color:#031003; border-color:var(--ph);
            font-weight:bold; }
button.off { background:var(--bad); color:#1a0303; border-color:var(--bad);
             font-weight:bold; }
button.danger:hover { border-color:var(--bad); color:var(--bad); }
.seg { display:flex; gap:0; }
.seg button { border-radius:0; }
.seg button:first-child { border-radius:5px 0 0 5px; }
.seg button:last-child { border-radius:0 5px 5px 0; }
#slaves { display:grid; gap:14px;
          grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); }
.card h2 { font-size:15px; color:var(--ph); margin-bottom:2px; }
.card h2 small { color:var(--dim); font-weight:normal; margin-left:8px; }
.play { margin:4px 0 6px; font-size:13px; }
.play.ok { color:var(--ph); } .play.warn { color:var(--warn); }
.play.bad { color:var(--bad); }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:2px 10px;
         margin:8px 0; color:var(--dim); font-size:12.5px; }
.stats span { cursor:help; }
.stats b { color:var(--fg); font-weight:normal; }
.stats b.ok { color:var(--ph); } .stats b.warn { color:var(--warn); }
.stats b.bad { color:var(--bad); }
.badge { padding:1px 8px; border-radius:4px; border:1px solid var(--line);
         font-size:12px; cursor:help; }
.badge.net { color:var(--ph); border-color:var(--ph); }
.badge.mic { color:var(--warn); border-color:var(--warn); }
.stale { opacity:.35; }
#none { color:var(--dim); padding:24px; text-align:center; }
#none.down { color:var(--bad); line-height:1.7; }
#none.down code { color:var(--fg); background:#000; padding:1px 6px; }
#offbanner { display:none; color:var(--bad); border:1px solid var(--bad);
             border-radius:6px; padding:6px 12px; margin-bottom:12px; }
details { margin-top:16px; color:var(--dim); font-size:13px; }
details summary { cursor:pointer; color:var(--fg); }
details td { padding:2px 14px 2px 0; vertical-align:top; }
details th { text-align:left; color:var(--fg); font-weight:normal;
             padding-right:14px; white-space:nowrap; }
</style></head><body>
<header>
  <h1>HYPEROSCI</h1>
  <button id="stream" class="on" onclick="toggleStream()"
    title="Master switch: does this page send audio at all? OFF = nothing is sent and every slave draws its own local pattern. Independent of each slave's 'draw' setting.">STREAM ON</button>
  <span style="color:var(--dim)"
    title="set every slave's draw setting at once — same buttons as on each slave card">all draw:</span>
  <span class="seg" id="allseg"></span>
</header>
<div id="offbanner">⏻ STREAM IS OFF — this page is sending no audio; every
 slave draws its own local pattern. Press STREAM ON to resume the network
 show (slaves need ~1 s to rebuffer).</div>
<div id="top">
  <canvas id="scope" width="520" height="520"
    title="Preview of the pattern being streamed (not a measurement — watch the real scope)"></canvas>
  <div id="controls" class="panel">
    <div style="color:var(--dim)"
      title="These settings shape the audio sent over WiFi. They only affect slaves whose draw setting is STREAM or HYBRID — local patterns are generated on the slave itself and ignore this panel.">streamed
      pattern <span id="pnote" style="color:var(--bad)"></span></div>
    <div class="row"><label title="Waveform streamed to all STREAM/HYBRID slaves">pattern</label>
      <span class="seg" id="kindseg">
        <button data-k="circle" title="X=cos Y=sin — one circle per period" onclick="setP({kind:'circle'})">circle</button>
        <button data-k="lissajous" title="X/Y sine ratio a:b — classic Lissajous figures" onclick="setP({kind:'lissajous'})">lissajous</button>
        <button data-k="rose" title="r=sin(a·t) rosette — 'a' petals (2a when a is even)" onclick="setP({kind:'rose'})">rose</button>
        <button data-k="text" title="draw text in a Hershey vector font (single-stroke, made for scopes)" onclick="setP({kind:'text'})">text</button>
      </span></div>
    <div class="row"><label title="Base repetition rate of the figure. Low = slower beam, brighter trace; high = faster redraw, dimmer">freq</label>
      <input type="range" id="freq" min="10" max="400" step="1"
             oninput="live()" onchange="setP({freq:+this.value})">
      <span class="val" id="freqv"></span></div>
    <div class="row"><label title="Deflection size: 100% ≈ 2.1 Vpp per axis at the DAC">amp</label>
      <input type="range" id="amp" min="0" max="100" step="1"
             oninput="live()" onchange="setP({amp:this.value/100})">
      <span class="val" id="ampv"></span></div>
    <div class="row" id="ratiorow"><label title="lissajous: X:Y frequency ratio · rose: petal count (a)">ratio</label>
      <input type="number" id="ra" min="1" max="9" style="width:64px"
             onchange="setP({a:+this.value})"> :
      <input type="number" id="rb" min="1" max="9" style="width:64px"
             onchange="setP({b:+this.value})"></div>
    <div class="row" id="textrow" style="display:none"><label
        title="text to draw — accents welcome (é ü ñ ç are composed from the face's own strokes), Enter starts a new line">text</label>
      <textarea id="text" maxlength="80" rows="2" spellcheck="false"
             style="flex:1;min-width:120px;background:#101b10;color:var(--fg);
                    border:1px solid var(--line);border-radius:5px;
                    padding:5px 8px;font:inherit;resize:vertical"
             onchange="setP({text:this.value})"></textarea>
      <select id="font" title="Hershey typeface"
              onchange="setP({font:this.value})">
        <option>simplex</option><option>duplex</option><option>script</option>
        <option>gothic</option><option>times</option><option>italic</option>
      </select>
      <button id="fxbtn" title="mirror left-right — fix a scope whose X polarity is inverted (text reads backwards)"
              onclick="setP({flip_x:!S.pattern.flip_x})">⇋ X</button>
      <button id="fybtn" title="mirror top-bottom — fix a scope whose Y polarity is inverted (text is upside down)"
              onclick="setP({flip_y:!S.pattern.flip_y})">⇵ Y</button></div>
    <div class="row" id="pulserow" style="display:none"><label
        title="amplitude pulse: how deep the text 'breathes' and how fast">pulse</label>
      <input type="range" id="pdepth" min="0" max="100" step="1"
             title="pulse depth — 0% = steady"
             oninput="live()" onchange="setP({pulse_depth:this.value/100})">
      <span class="val" id="pdepthv"></span>
      <input type="range" id="prate" min="1" max="80" step="1"
             title="pulse rate in Hz"
             oninput="live()" onchange="setP({pulse_rate:this.value/10})">
      <span class="val" id="pratev"></span></div>
    <div class="row" id="rotrow" style="display:none"><label
        title="continuous rotation in revolutions per second — 0 = static">spin</label>
      <input type="range" id="rot" min="-100" max="100" step="1"
             oninput="live()" onchange="setP({rot:this.value/100})">
      <span class="val" id="rotv"></span></div>
    <div class="row" id="presetrow"><label
        title="saved snapshots of everything in this panel — prepare artist names before the show, then switch in one tap">presets</label>
      <span id="plist" style="display:flex;gap:6px;flex-wrap:wrap"></span>
      <button title="save the current pattern settings as a named preset (max 10)"
              onclick="savePreset()">+ save</button></div>
  </div>
</div>
<div id="slaves" class="panel"><div id="none">no slaves discovered yet…</div></div>
<details><summary>ℹ how to read this dashboard</summary>
 <table>
 <tr><th>who decides what?</th><td>two switches only: the global <b>STREAM</b>
  button (does this page send audio at all) and each slave's <b>draw</b>
  setting. draw = <b>STREAM</b> plays this page's streamed pattern; <b>HYBRID</b>
  adds the slave's own mic at 50%; every other choice (mic / circle /
  lissajous / ramp / square) is generated on the slave itself. The controller
  only sends audio to STREAM/HYBRID slaves — a slave on a local pattern
  receives nothing (its rx/s = 0 is normal).</td></tr>
 <tr><th>fallback</th><td>a STREAM slave that stops receiving audio for 1 s
  (stream switched off, WiFi outage…) automatically draws its local pattern
  instead — whichever was last chosen (mic after power-up). So
  "LOCAL·mic" on a STREAM slave simply means: no stream right now.</td></tr>
 <tr><th>mic pattern</th><td>microphone on X, pot-filtered microphone on Y —
  the pot on the unit sets the filter cutoff. ramp &amp; square are
  alignment/test figures (DC deflection, filter ringing).</td></tr>
 <tr><th>buf</th><td>received audio queued ahead of its play deadline. Healthy
  ≈ 450 ms: the stream deliberately runs that far ahead because the UNO-Q's
  WiFi radio pauses for up to ~0.3 s every ~1.4 s (chip quirk) — the buffer
  rides those pauses out. Hitting 0 = an underrun (beam collapses to a dot
  until it refills, ~1 s).</td></tr>
 <tr><th>drop/s, lost/s &amp; under/s</th><td>should all sit at 0. Drops =
  packets arriving too late (or duplicated); lost = packets that never arrived
  at all (sequence gaps — silent WiFi loss); underruns = the buffer ran dry —
  each one is a visible dot-blink on the scope.</td></tr>
 <tr><th>gain</th><td>per-slave output scale (saved on the slave, survives
  reboot). Use it to match deflection between different scopes.</td></tr>
 </table>
</details>
<script>
"use strict";
let S = null;
const hist = {};   // ip -> previous counters for rate calculation

function post(path, body) {
  return fetch(path, {method:"POST", body:JSON.stringify(body)})
    .then(() => poll());
}
const setP = p => post("/api/pattern", p);
const cmd = (ip, c) => post("/api/cmd", {ip:ip, cmd:c});
let lastChange = 0;  // last stream-toggle/draw-command — "buffering" grace
function toggleStream() {
  lastChange = performance.now();
  post("/api/pattern", {stream: !S.stream_on});
}

// One draw setting per slave: STREAM/HYBRID = play this page's pattern,
// anything else = a pattern the slave generates itself (mode LOCAL).
const DRAWS = ["stream","hybrid","mic","circle","lissajous","ramp","square"];
const DRAW_TIPS = {
  stream:"play the audio streamed from this page (falls back to the local pattern if the stream stops)",
  hybrid:"this page's stream + the slave's own mic mixed in at 50%",
  mic:"local render on the slave: mic on X, pot-filtered mic on Y — the controller stops sending it audio",
  circle:"test pattern generated on the slave itself — the controller stops sending it audio",
  lissajous:"test pattern generated on the slave itself — the controller stops sending it audio",
  ramp:"local alignment pattern: slow full-scale triangles (DC/deflection go-no-go)",
  square:"local test pattern: sharp 4-corner jumps (interpolation-ringing test)"};
function drawCmd(ip, w) {
  lastChange = performance.now();
  if (w === "stream") return cmd(ip, {cmd:"set_mode", mode:"network"});
  if (w === "hybrid") return cmd(ip, {cmd:"set_mode", mode:"hybrid"});
  return cmd(ip, {cmd:"set_pattern", pattern:w})
      .then(() => cmd(ip, {cmd:"set_mode", mode:"local"}));
}
function drawSeg(ip, active) {
  return DRAWS.map(w =>
    `<button class="${w===active?'on':''}" title="${DRAW_TIPS[w]}"
       onclick="drawCmd('${ip}','${w}')">` +
    (w==="stream"||w==="hybrid" ? w.toUpperCase() : w) + "</button>").join("");
}

function fmtUp(s) {
  if (s >= 3600) return (s/3600).toFixed(1) + "h";
  if (s >= 60) return Math.floor(s/60) + "m" + (s%60) + "s";
  return s + "s";
}
const fmtN = n => n >= 1e6 ? (n/1e6).toFixed(1)+"M"
              : n >= 1e3 ? (n/1e3).toFixed(1)+"k" : ""+n;

function live() {
  document.getElementById("freqv").textContent =
      document.getElementById("freq").value + " Hz";
  document.getElementById("ampv").textContent =
      document.getElementById("amp").value + " %";
  document.getElementById("pdepthv").textContent =
      document.getElementById("pdepth").value + " %";
  document.getElementById("pratev").textContent =
      (document.getElementById("prate").value/10).toFixed(1) + " Hz";
  document.getElementById("rotv").textContent =
      (document.getElementById("rot").value/100).toFixed(2) + " rev/s";
  drawScope();
}

// Presets: snapshots of the whole streamed-pattern panel, kept on the
// controller (~/hype_presets.json) so they survive restarts.
function savePreset() {
  const p = S.pattern;
  const def = (p.kind === "text" ? p.text.split("\\n")[0] : p.kind).slice(0, 24);
  const name = prompt("preset name (e.g. the artist):", def);
  if (name && name.trim()) post("/api/preset", {op:"save", name:name.trim()});
}
const loadPreset = n => post("/api/preset", {op:"load", name:n});
function delPreset(n) {
  if (confirm(`delete preset "${n}"?`))
    post("/api/preset", {op:"delete", name:n});
}
function presetChips() {
  document.getElementById("plist").innerHTML = (S.presets || []).map(n =>
    `<span class="seg"><button title="apply this preset"
        onclick="loadPreset('${n}')">${esc(n)}</button><button class="danger"
        title="delete preset '${esc(n)}'" onclick="delPreset('${n}')">×</button></span>`
  ).join("") || '<span style="color:var(--dim)">none saved yet</span>';
}

// Text-path preview: fetched only when the server-side table changes (tver).
let TP = {ver: -1, pts: []};
async function fetchPreview() {
  try {
    const d = await (await fetch("/api/textpreview")).json();
    TP = d; drawScope();
  } catch (e) { /* controller restarting */ }
}

function drawScope() {
  if (!S) return;
  const c = document.getElementById("scope"), g = c.getContext("2d");
  const p = S.pattern, w = c.width, h = c.height;
  const amp = (+document.getElementById("amp").value / 100) * w * 0.44;
  g.fillStyle = "#020402"; g.fillRect(0, 0, w, h);
  g.strokeStyle = "#0e1a0e"; g.lineWidth = 1;         // graticule
  for (let i = 1; i < 8; i++) {
    g.beginPath(); g.moveTo(i*w/8, 0); g.lineTo(i*w/8, h); g.stroke();
    g.beginPath(); g.moveTo(0, i*h/8); g.lineTo(w, i*h/8); g.stroke();
  }
  g.strokeStyle = "#39ff14"; g.lineWidth = 2;
  g.shadowColor = "#39ff14"; g.shadowBlur = 8;
  g.beginPath();
  if (p.kind === "text") {
    const sx = p.flip_x ? -1 : 1, sy = p.flip_y ? -1 : 1;
    for (let i = 0; i < TP.pts.length; i++) {
      const px = w/2 + amp*sx*TP.pts[i][0], py = h/2 - amp*sy*TP.pts[i][1];
      i ? g.lineTo(px, py) : g.moveTo(px, py);
    }
    g.stroke(); g.shadowBlur = 0;
    return;
  }
  const N = 1200, k = p.kind, a = p.a, b = p.b, hp = Math.PI/2;
  for (let i = 0; i <= N; i++) {
    const t = 2*Math.PI*i/N;
    let x, y;
    if (k === "lissajous") { x = Math.sin(a*t + hp); y = Math.sin(b*t); }
    else if (k === "rose") { const r = Math.sin(a*t);
                             x = r*Math.cos(t); y = r*Math.sin(t); }
    else { x = Math.cos(t); y = Math.sin(t); }
    const px = w/2 + amp*x, py = h/2 - amp*y;
    i ? g.lineTo(px, py) : g.moveTo(px, py);
  }
  g.stroke(); g.shadowBlur = 0;
}

// What is this slave's beam doing right now, in words?
const esc = t => t.replace(/[&<>"]/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function playing(s) {
  const lp = s.lpat || "mic", p = S.pattern;
  if (s.source) {
    let what = s.mode === 2 ? p.kind + " + own mic (HYBRID)" : p.kind;
    if (p.kind === "text") what = `text "${esc(p.text)}" (${p.font})` +
        (s.mode === 2 ? " + own mic (HYBRID)" : "");
    return {cls:"ok", txt:`▶ network stream — ${what} @ ${p.freq} Hz`};
  }
  if (s.mode === 0) return {cls:"ok",
      txt:`▶ local ${lp} (generated on the slave — no stream sent to it)`};
  if (!S.stream_on) return {cls:"warn",
      txt:`▶ local ${lp} — the STREAM switch is OFF`};
  if (performance.now() - lastChange < 4000) return {cls:"warn",
      txt:"▶ buffering the stream… (normal for ~1 s after a change)"};
  return {cls:"bad",
      txt:`▶ stream not arriving — drawing local ${lp} (WiFi problem?)`};
}

function rates(s) {
  const h = hist[s.ip], now = performance.now();
  let r = null;
  if (h && now - h.t > 300 && s.rx >= h.rx) {
    const dt = (now - h.t) / 1000;
    r = {rx: Math.round((s.rx-h.rx)/dt),
         drop: (s.drop-h.drop)/dt, und: (s.under-h.under)/dt,
         lost: (s.lost==null||h.lost==null) ? null : (s.lost-h.lost)/dt};
  }
  hist[s.ip] = {rx: s.rx, drop: s.drop, under: s.under, lost: s.lost, t: now};
  return r;
}

function card(s, r) {
  const src = s.source
    ? '<span class="badge net" title="beam source right now: the network stream">NET</span>'
    : `<span class="badge mic" title="beam source right now: a pattern generated locally on the slave">LOCAL·${s.lpat||"?"}</span>`;
  const p = playing(s);
  const active = s.mode===1 ? "stream" : s.mode===2 ? "hybrid"
                                       : (s.lpat||"mic");
  const fed = S.stream_on && s.mode !== 0;  // is it being sent audio at all?
  const rcls = v => v === 0 ? "ok" : v < 1 ? "warn" : "bad";
  const rline = r === null
    ? '<span title="rates appear after two status beacons">health <b>…</b></span>'
    : `<span title="audio packets accepted per second (200/s = a perfect stream; 0 is normal when the slave is not being sent audio)">rx/s <b class="${fed ? (r.rx>180?'ok':r.rx>0?'warn':'bad') : ''}">${r.rx}</b></span>
       <span title="late/stale/duplicate packets discarded per second — should be 0">drop/s <b class="${rcls(r.drop)}">${r.drop.toFixed(1)}</b></span>
       <span title="packets that never arrived per second, inferred from sequence gaps — catches silent WiFi/lwIP loss that drop/s cannot see. Should be 0">lost/s <b class="${r.lost==null?'':rcls(r.lost)}">${r.lost==null?"–":r.lost.toFixed(1)}</b></span>
       <span title="buffer ran dry per second — each one blinks the beam to a dot. Should be 0">under/s <b class="${rcls(r.und)}">${r.und.toFixed(1)}</b></span>
       <span title="seconds since the last status heartbeat (sent once per second)">age <b>${(s.age_ms/1000).toFixed(1)}s</b></span>`;
  return `<div class="card ${s.age_ms>3000?'stale':''}" style="padding:4px 0">
    <h2>SLAVE ${s.id} ${src}<small>${s.ip} · ${s.mac}</small></h2>
    <div class="play ${p.cls}">${p.txt}</div>
    <div class="stats">
      <span title="WiFi signal strength at the slave: −30 excellent · −60 good · −75 marginal · −85 unusable">rssi <b>${s.rssi} dBm</b></span>
      <span title="battery voltage measured by the slave (≈0 on the bench: VBAT pin grounded, no battery)">vbat <b>${s.vbat_mv} mV</b></span>
      <span title="audio buffered ahead of playback. Healthy ≈ 450 ms (the stream runs ahead on purpose to ride out WiFi pauses); 0 during local render">buf <b>${Math.round(s.depth/48)} ms</b></span>
      <span title="time since the slave booted">up <b>${fmtUp(s.uptime)}</b></span>
      ${rline}
      <span title="lifetime accepted audio packets">rx <b>${fmtN(s.rx)}</b></span>
      <span title="lifetime discarded packets (late, duplicate, or buffer-full)">drop <b>${fmtN(s.drop)}</b></span>
      <span title="lifetime underruns (buffer ran dry)">under <b>${fmtN(s.under)}</b></span>
      <span title="lifetime packets that never arrived (inferred from sequence gaps)">lost <b>${s.lost==null?"–":fmtN(s.lost)}</b></span>
    </div>
    <div class="row">
      <label style="width:auto;color:var(--dim)"
        title="what should this slave draw? STREAM/HYBRID play this page's streamed pattern; the rest are generated on the slave itself (and double as its fallback if the stream dies)">draw</label>
      <span class="seg">${drawSeg(s.ip, active)}</span>
    </div>
    <div class="row" style="margin-top:6px">
      <button title="blink this slave's LEDs for 3 s to identify it" onclick="cmd('${s.ip}',{cmd:'identify'})">ID</button>
      <label style="width:auto;color:var(--dim)" title="per-slave output scale (persisted on the slave) — match deflection between scopes">gain</label>
      <input type="range" min="0" max="100" value="100" style="width:90px"
        title="per-slave output scale (persisted on the slave)"
        onchange="cmd('${s.ip}',{cmd:'set_gain',gain:this.value/100})">
      <button class="danger" title="restart the slave (2 s outage)"
        onclick="if(confirm('reboot slave ${s.id}?'))
                 cmd('${s.ip}',{cmd:'reboot'})">reboot</button>
    </div></div>`;
}

function render() {
  const p = S.pattern;
  const sb = document.getElementById("stream");
  sb.textContent = S.stream_on ? "STREAM ON" : "STREAM OFF";
  sb.className = S.stream_on ? "on" : "off";
  document.getElementById("offbanner").style.display =
      S.stream_on ? "none" : "block";
  document.getElementById("controls").style.opacity =
      S.stream_on ? "1" : ".45";
  document.getElementById("pnote").textContent =
      S.stream_on ? "" : "— OFF, nothing is being sent";
  for (const b of document.querySelectorAll("#kindseg button"))
    b.className = b.dataset.k === p.kind ? "on" : "";
  const isText = p.kind === "text";
  document.getElementById("ratiorow").style.display =
      (p.kind === "circle" || isText) ? "none" : "flex";
  for (const id of ["textrow", "pulserow", "rotrow"])
    document.getElementById(id).style.display = isText ? "flex" : "none";
  document.getElementById("fxbtn").className = p.flip_x ? "on" : "";
  document.getElementById("fybtn").className = p.flip_y ? "on" : "";
  presetChips();
  if (isText && p.tver !== TP.ver) fetchPreview();
  // Don't fight the user mid-drag: only sync widgets nobody is touching.
  const act = document.activeElement;
  const sync = (id, v) => { const e = document.getElementById(id);
                            if (e !== act) e.value = v; };
  sync("freq", p.freq); sync("amp", Math.round(p.amp*100));
  sync("ra", p.a); sync("rb", p.b);
  sync("text", p.text); sync("font", p.font);
  sync("pdepth", Math.round(p.pulse_depth*100));
  sync("prate", Math.round(p.pulse_rate*10));
  sync("rot", Math.round(p.rot*100));
  live();
  // Rates must be sampled every poll even if the card DOM isn't rebuilt.
  const rr = {};
  for (const s of S.slaves) rr[s.ip] = rates(s);
  const div = document.getElementById("slaves");
  if (!S.slaves.length) {
    // An empty list has two very different causes and they look identical.
    const n = S.net || {};
    div.innerHTML = n.egress === false
      ? '<div id="none" class="down"><b>Wi-Fi AP is DOWN</b> — no interface holds '
        + n.iface + ', so no slave can associate and no audio is leaving this box.'
        + '<br>on the controller: <code>sudo nmcli c up hyperosci-ap</code>'
        + '<br>(this panel recovers on its own within a second — no restart needed)</div>'
      : '<div id="none">no slaves discovered yet…</div>';
  } else if (!(act && div.contains(act))) {  // keep gain sliders draggable
    div.innerHTML =
        S.slaves.sort((a,b)=>a.id-b.id).map(s => card(s, rr[s.ip])).join("");
  }
}

async function poll() {
  try { S = await (await fetch("/api/state")).json(); render(); }
  catch (e) { /* controller restarting; retry next tick */ }
}
document.getElementById("allseg").innerHTML = drawSeg("all", null);
poll(); setInterval(poll, 1000);
</script></body></html>
"""


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
                self._json({"ok": True})
            elif self.path == "/api/preset":
                return self._preset(state, body)
            elif self.path == "/api/cmd":
                ip, cmd_obj = body.get("ip"), body.get("cmd")
                if not ip or not isinstance(cmd_obj, dict):
                    return self._json({"err": "need ip + cmd"}, 400)
                cmds.send(ip, cmd_obj)
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
            if op not in ("save", "load", "delete") or not name:
                return self._json({"err": "need op + name"}, 400)
            if op == "save":
                with state.lock:
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
                            return self._json(
                                {"err": f"max {PRESETS_MAX} presets"}, 400)
                        state.presets.append(snap)
                    plist = list(state.presets)
                save_presets(plist)  # file IO outside the lock
                return self._json({"ok": True})
            if op == "delete":
                with state.lock:
                    state.presets = [p for p in state.presets
                                     if p["name"] != name]
                    plist = list(state.presets)
                save_presets(plist)
                return self._json({"ok": True})
            with state.lock:  # load
                p = next((dict(p) for p in state.presets
                          if p["name"] == name), None)
            if p is None:
                return self._json({"err": "no such preset"}, 404)
            # Same mutex as /api/pattern: a preset tap and a font change are
            # two rebuilds like any other pair.
            with state.rebuild_lock:
                p = clean_preset(p)  # never install an unknown font (circle!)
                tbl, rmax = build_text(p["text"], p["font"])  # outside lock
                with state.lock:
                    state.kind, state.freq, state.amp = (
                        p["kind"], p["freq"], p["amp"])
                    state.ratio_a, state.ratio_b = p["a"], p["b"]
                    state.pulse_rate = p["pulse_rate"]
                    state.pulse_depth = p["pulse_depth"]
                    state.rot_speed = p["rot"]
                    state.flip_x, state.flip_y = p["flip_x"], p["flip_y"]
                    state.text, state.font = p["text"], p["font"]
                    state.text_tbl, state.text_rmax = tbl, rmax
                    state.text_ver += 1
                state.dirty = True   # tapping a preset survives a reboot too
            return self._json({"ok": True})

    return Handler


# ---------------------------------------------------------------------------
# Streaming engine (from hype_sender.py, driven by State)
# ---------------------------------------------------------------------------

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
                    except OSError:
                        pass  # iface mid-bounce: drop; slave conceals
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

        # Forget slaves silent for > 5 s so we stop streaming into the void.
        with state.lock:
            for ip in [ip for ip, s in state.slaves.items()
                       if now - s["last_us"] > 5_000_000]:
                print(f"[lost] {ip}", flush=True)
                del state.slaves[ip]


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
