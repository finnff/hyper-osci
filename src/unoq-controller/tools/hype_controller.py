#!/usr/bin/env python3
"""HYPEROSCI controller daemon: HYPE v1 streamer + web control panel.

Supersedes hype_sender.py (kept as a minimal protocol reference). Adds:
  - Embedded web UI (stdlib http.server, port 8080) — pattern / frequency /
    amplitude control, stream on/off, three per-node switches (streaming /
    logos / artists) that the controller turns into slave draw modes,
    identify, gain, reboot.
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
# Every kind the daemon will install. "text" and "figure" are the two that
# draw a precomputed point table; the rest are generated per sample.
PATTERN_KINDS = ("circle", "lissajous", "rose", "text", "figure")
# "name" plus these. Every field is OPTIONAL on load and filled from the
# default here, so adding an entry can never disqualify a preset written by an
# older build — the filter used to require all of them, and the next save then
# rewrote the file from the surviving list, deleting the show's artist names
# with no log line.
PRESET_DEFAULTS = {"kind": "circle", "freq": 100.0, "amp": 0.8, "a": 3, "b": 2,
                   "text": "HYPEROSCI", "font": "simplex", "pulse_rate": 1.5,
                   "pulse_depth": 0.0, "rot": 0.0, "figure": "",
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
    if q["kind"] not in PATTERN_KINDS:
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
    # Not checked against the directory: a preset may name artwork that is not
    # on THIS board (bench box, spare controller). load_figure logs and
    # block() draws a circle -- dropping the name would silently rewrite the
    # show's set list to something else on the next save.
    q["figure"] = sanitize_figure(q["figure"])
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


# Spreading the two cycles out in time.
#
# Both cycles interrupt the same single stream, so a burst that comes due
# while the other group is on air cannot also play: it would either queue
# (two bursts back to back, which reads as a fault) or cut the first one off
# mid-name. Neither is acceptable, so instead the two grids are offset from
# each other: "logos every 2 min" and "artists every 1 min" become 0, 120,
# 240 against 32, 92, 152 rather than both landing on the minute. Only the
# gaps matter, never the absolute moment, so a phase is free to be anything.
#
# Best effort by construction: periods that share no useful common multiple
# (97 s against 60 s) cannot be made to miss forever. The placement takes the
# best of what it can see and advance_cycle skips a burst it cannot fit,
# which is the guarantee underneath.
SCHED_WINDOWS = 12      # bursts per group considered when placing
SCHED_CANDIDATES = 60   # phases tried


def _fire_windows(phase, period, dur, n=SCHED_WINDOWS):
    return [(phase + k * period, phase + k * period + dur) for k in range(n)]


def _placement_score(cands, placed):
    """How well a candidate phase sits against what is already placed.

    -> (smallest gap, -collisions, when the first collision happens), ordered
    so that bigger is better. Three parts because the first one alone ties
    badly: periods that share no useful common multiple (97 s against 60 s)
    collide somewhere in every phase, and with only the gap to go on the
    search then picks phase 0 -- both groups firing together on the very
    first pass, which is the one collision the operator is guaranteed to see.
    """
    worst = None
    hits = 0
    first = None
    for a0, a1 in cands:
        for b0, b1 in placed:
            if a0 < b1 and b0 < a1:
                gap = -(min(a1, b1) - max(a0, b0))
                hits += 1
                if first is None or a0 < first:
                    first = a0
            elif b0 >= a1:
                gap = b0 - a1
            else:
                gap = a0 - b1
            if worst is None or gap < worst:
                worst = gap
    return (worst if worst is not None else 0, -hits,
            first if first is not None else float("inf"))


# ---------------------------------------------------------------------------
# Per-node policy and the pair cycles.
#
# One authority for every slave's draw mode: policy_loop. Before this the mode
# was set from four places (the slave sheet, "set all", an ident's hold, the
# slave's own button) and the only way to know what a scope would draw was to
# walk the rig and look. The idents are gone now, but the rule they forced
# stands: each node carries three switches -- stream, logos, artists -- and
# the mode follows from them plus what is on air:
#
#   stream off                  -> local (own mic). The scope is not in XY, or
#                                  its operator wants it out of the show.
#   a logo on air, logos off    -> local. Same for a name and artists off.
#   between bursts, and during  -> local, every node: the rig's resting state
#   a burst's mic pause            is the microphone.
#   otherwise                   -> network.
#
# The controller only streams audio to slaves in NETWORK mode (see the
# fan-out filter in stream_loop), so "local" also takes the node off the air
# and gives its share of the radio back to the rig.
#
# HYBRID is not reachable any more. It was network + own mic mixed, which on
# stage read as "the logo is broken" -- and with three switches to explain it
# was a fourth state nobody could predict. A node found in hybrid (its own
# button still cycles into it) is pulled back to whatever policy says.
# ---------------------------------------------------------------------------

NODES_FILE = os.environ.get(
    "HYPE_NODES", os.path.expanduser("~/hype_nodes.json"))
# Keyed by slave id and not by ip: an id survives a DHCP lease and a
# reflash, and it is what the tile prints.
NODE_DEFAULTS = {"stream": True, "logos": True, "artists": True}
# A slave with no entry is fully in the show. Anything else and a node added
# an hour before doors would come up dark for no stated reason.


def clean_node(d):
    """Per-node switches -> a full set of booleans."""
    if not isinstance(d, dict):
        d = {}
    return {k: bool(d.get(k, v)) for k, v in NODE_DEFAULTS.items()}


def load_nodes():
    """-> {slave id: switches}. Only nodes someone actually changed are in
    the file; the rest default in."""
    try:
        with open(NODES_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                sid = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= sid <= 255:
                out[sid] = clean_node(v)
    return out


def save_nodes(nodes):
    tmp = NODES_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({str(k): v for k, v in sorted(nodes.items())}, f,
                      indent=1)
        os.replace(tmp, NODES_FILE)
    except OSError as e:
        print(f"[nodes] save failed: {e}", flush=True)


# The pair cycles: "every 5 minutes, logo A for 30 s, an optional mic pause,
# logo B for 30 s -- then mic until the next five minutes is up".
#
# A cycle is a periodic interruption, not a loop that owns the stream. The
# rig's normal state is the microphone: every node on its own audio, which is
# what the room is there for. A burst takes the stream for as long as the
# pair needs and hands it straight back.
#
# Two groups, because a set runs its logos on one clock and its artist names
# on another, and because the two switches on every node are exactly these
# two groups.
CYCLES_FILE = os.environ.get(
    "HYPE_CYCLES", os.path.expanduser("~/hype_cycles.json"))
CYCLE_GROUPS = ("logos", "artists")
CYCLE_PAIRS_MAX = 6
# Seeds for a board that has never had the file — the show as it stands. They
# are not baked into the logic: every name is looked up in the preset list at
# run time, and the page edits both slots of every pair.
CYCLE_SEED = {
    "logos": [["HYPERWAVES", "Flux Collective"]],
    "artists": [["Anémi", "HyperLili"],
                ["Eastern Distributor", "Gan D"],
                ["Guild Navigator", "Liso"]],
}
CYCLE_DEFAULTS = {"enabled": False, "show_s": 5, "pause_s": 0, "idx": 0,
                  "every_s": 300}


def cycle_burst_s(c):
    """How long one burst holds the stream: A + optional mic pause + B."""
    return 2 * c["show_s"] + c["pause_s"]


def clean_cycle(c, group):
    """Cycle config -> fully populated, range-checked copy.

    Same optional-everything contract as clean_preset, so a file written by
    the build before periods loads and gets the default five minutes.
    """
    if not isinstance(c, dict):
        c = {}
    q = {"enabled": bool(c.get("enabled", CYCLE_DEFAULTS["enabled"]))}
    for k, lo, hi in (("show_s", 3, 600), ("pause_s", 0, 600),
                      ("every_s", 10, 86400)):
        try:
            q[k] = int(float(c.get(k, CYCLE_DEFAULTS[k])))
        except (TypeError, ValueError):
            q[k] = CYCLE_DEFAULTS[k]
        q[k] = min(hi, max(lo, q[k]))
    # A period inside the burst would come due again before the pair had
    # finished, and the mic would never get the stream back. Ten seconds of
    # microphone is the least that makes the interruption read as one.
    q["every_s"] = max(q["every_s"], cycle_burst_s(q) + 10)
    pairs = c.get("pairs")
    if not isinstance(pairs, (list, tuple)):
        pairs = CYCLE_SEED.get(group, [])
    out = []
    for pr in pairs[:CYCLE_PAIRS_MAX]:
        if not isinstance(pr, (list, tuple)):
            continue
        pair = [sanitize_name(pr[0] if len(pr) > 0 else ""),
                sanitize_name(pr[1] if len(pr) > 1 else "")]
        if any(pair):
            out.append(pair)
    q["pairs"] = out
    try:
        q["idx"] = int(c.get("idx", 0))
    except (TypeError, ValueError):
        q["idx"] = 0
    q["idx"] = min(max(0, q["idx"]), max(0, len(out) - 1))
    return q


def load_cycles():
    try:
        with open(CYCLES_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {g: clean_cycle(data.get(g), g) for g in CYCLE_GROUPS}


def save_cycles(cycles):
    tmp = CYCLES_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(cycles, f, indent=1)
        os.replace(tmp, CYCLES_FILE)
    except OSError as e:
        print(f"[cycle] save failed: {e}", flush=True)


def pattern_group(kind):
    """What is on air, in the two words the node switches use."""
    return "logos" if kind == "figure" else "artists" if kind == "text" else ""


def burst_steps(c, group, preset_names):
    """One burst of a cycle, as the list of steps it will actually play.

    A step is {"group", "preset", "secs"}; group None is the mic pause
    between the two names. A pair slot naming a preset that no longer exists
    is dropped rather than played as a gap nobody can explain -- the page
    renders this same list, so what it shows is what will run.
    """
    if not c.get("enabled") or not c.get("pairs"):
        return []
    pair = c["pairs"][min(c.get("idx", 0), len(c["pairs"]) - 1)]
    names = [n for n in pair if n and n in preset_names]
    steps = []
    for i, name in enumerate(names):
        if i and c["pause_s"]:
            steps.append({"group": None, "preset": "", "secs": c["pause_s"]})
        steps.append({"group": group, "preset": name, "secs": c["show_s"]})
    return steps


def cycle_phases(cycles):
    """-> {group: phase seconds}, offsets that keep the two bursts apart.

    The shorter period goes down first at phase 0 (it has the most bursts and
    the least room to move), then the other takes the offset that scores best
    against it -- the middle of the biggest gap, not merely the first spot
    that fits, so nudging a slider later does not immediately re-collide.
    A group that is off, or has nothing playable, is not scheduled around.
    """
    live = sorted(((g, c) for g, c in cycles.items()
                   if c.get("enabled") and c.get("pairs")),
                  key=lambda gc: (gc[1]["every_s"], gc[0]))
    phases = {g: 0 for g in cycles}
    placed = []
    for g, c in live:
        burst = cycle_burst_s(c)
        if not placed:
            placed = _fire_windows(0, c["every_s"], burst)
            continue
        step = max(1, c["every_s"] // SCHED_CANDIDATES)
        best_phase, best = 0, None
        for phase in range(0, c["every_s"], step):
            score = _placement_score(
                _fire_windows(phase, c["every_s"], burst), placed)
            if best is None or score > best:
                best_phase, best = phase, score
        phases[g] = best_phase
        placed += _fire_windows(best_phase, c["every_s"], burst)
    return phases


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
    return normalize_resample(strokes)


def normalize_resample(strokes):
    """[[(x, y), ...], ...] -> equal-arc-length point table in [-1, 1].

    Shared by render_text and the offline SVG baker (tools/bake_svg.py imports
    this module for it) so a baked figure and a rendered word are normalised
    and traced by exactly the same arithmetic. Pen-up gaps between strokes
    cost zero table entries: the beam jumps in one sample.
    """
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


# ---------------------------------------------------------------------------
# Baked figures: SVG artwork, flattened and resampled OFF the board by
# tools/bake_svg.py, shipped as an int16 point table. Deliberately not an
# on-board SVG parser: the study measured board-side XML at 1.34 ms/300 paths
# (affordable) but the parser is the stage-failure surface — malformed art,
# entity bombs, an upload endpoint on an unauthenticated :8080 — and offline
# baking also buys contour reordering the board could never afford. See
# docs/osci-render-feature-port-feasibility.md §5.
# ---------------------------------------------------------------------------

FIGURE_DIR = os.environ.get("HYPE_FIGURES",
                            os.path.expanduser("~/hype_figures"))
FIGURE_MAGIC = b"HYPEFIG1"     # bake_svg.py writes it; nothing else loads
FIGURE_MAX_POINTS = 20000      # a 40 kB table; refuse a runaway/corrupt file
_figure_cache = {}             # name -> table, keyed by (name, mtime, size)


def list_figures():
    """Names of the baked tables on the board, without the .tbl suffix."""
    try:
        return sorted(f[:-4] for f in os.listdir(FIGURE_DIR)
                      if f.endswith(".tbl"))
    except OSError:
        return []


def sanitize_figure(s):
    """Figure name -> a bare stem that cannot escape FIGURE_DIR."""
    s = os.path.basename(str(s)).strip()
    if s.endswith(".tbl"):
        s = s[:-4]
    return "".join(c for c in s if c.isalnum() or c in "._-")[:48]


def load_figure(name):
    """Baked table name -> [(x, y), ...], or None if it will not load.

    Same float-tuple shape render_text returns, so block() keeps one code
    path. Cached on (mtime, size): re-selecting a figure during a set must not
    re-read and re-unpack the file, and dropping a new bake in place still
    takes effect without a restart.
    """
    name = sanitize_figure(name)
    if not name:
        return None
    path = os.path.join(FIGURE_DIR, name + ".tbl")
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
        hit = _figure_cache.get(name)
        if hit and hit[0] == key:
            return hit[1]
        with open(path, "rb") as f:
            blob = f.read()
    except OSError as e:
        print(f"[figure] {name}: {e}", flush=True)
        return None
    head = len(FIGURE_MAGIC) + 4
    if len(blob) < head or blob[:len(FIGURE_MAGIC)] != FIGURE_MAGIC:
        print(f"[figure] {name}: not a baked table", flush=True)
        return None
    n = struct.unpack_from("<I", blob, len(FIGURE_MAGIC))[0]
    if not 1 < n <= FIGURE_MAX_POINTS or len(blob) != head + 4 * n:
        print(f"[figure] {name}: bad point count {n}", flush=True)
        return None
    raw = array("h")
    raw.frombytes(blob[head:])
    if sys.byteorder == "big":     # the file is little-endian by contract
        raw.byteswap()
    k = 1.0 / 32767.0
    tbl = [(raw[2 * i] * k, raw[2 * i + 1] * k) for i in range(n)]
    _figure_cache[name] = (key, tbl)
    return tbl


def build_shape(kind, text, font, figure=""):
    """Live pattern fields -> (table, rmax), the pair every writer must set.

    Only the table kinds have one; circle/lissajous/rose are generated per
    sample and get (None, 0.0), which is also what an unreadable figure or an
    unrenderable string yields — block() then draws a circle.
    """
    if kind == "text":
        tbl = render_text(text, font)
    elif kind == "figure":
        tbl = load_figure(figure)
    else:
        tbl = None
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
        # A burst is a 30 s interruption, not "what was on the scopes":
        # persisting it would bring the rig back drawing a logo after a power
        # blip, the exact failure this file prevents. dirty stays set, so the
        # restore lands on the very next tick.
        if state.cycle_run is not None:
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
    preset tap and the pair cycles. Lock order is the documented one:
    rebuild_lock, then state.lock, and the font parse happens between them so
    it never stalls the 5 ms stream pacing.
    """
    with state.rebuild_lock:
        p = clean_preset(p)   # never install an unknown font (circle!)
        tbl, rmax = build_shape(p["kind"], p["text"], p["font"], p["figure"])
        with state.lock:
            state.kind, state.freq, state.amp = p["kind"], p["freq"], p["amp"]
            state.ratio_a, state.ratio_b = p["a"], p["b"]
            state.pulse_rate = p["pulse_rate"]
            state.pulse_depth = p["pulse_depth"]
            state.rot_speed = p["rot"]
            state.flip_x, state.flip_y = p["flip_x"], p["flip_y"]
            state.text, state.font = p["text"], p["font"]
            state.figure = p["figure"]
            state.shape_tbl, state.shape_rmax = tbl, rmax
            state.shape_ver += 1
        state.dirty = True



# A commanded mode is only confirmed by the slave's next STATUS beacon, which
# is 1 Hz. Re-sending inside that window would be three packets per node per
# second, all saying the same thing, on the one radio the audio needs.
MODE_RESEND_US = 3_000_000


def node_flags(state, sid):
    """Slave id -> its three switches (defaults for a node nobody has set).

    Caller holds state.lock.
    """
    return state.node_flags.get(sid) or NODE_DEFAULTS


def desired_modes(state):
    """-> {ip: 0 local | 1 network}, the whole rig's draw modes from policy.

    Caller holds state.lock. Reads only; sending is reconcile_modes' job.
    """
    mic = state.cycle_mic
    group = pattern_group(state.kind)
    out = {}
    for ip, s in state.slaves.items():
        f = node_flags(state, s["id"])
        if not f["stream"]:
            out[ip] = 0            # out of the show entirely
        elif mic:
            out[ip] = 0            # between bursts, and the pause: the mic
        elif group and not f[group]:
            out[ip] = 0            # this node does not take logos / names
        else:
            out[ip] = 1
    return out


def reconcile_modes(state, cmds):
    """Send the set_mode commands policy calls for, and only those.

    Level-triggered on purpose: it compares against the mode the slave last
    reported, so a node that reboots, or whose own button was pressed, is
    pulled back within a beacon or two without anything having to notice the
    event. Returns the commands sent, for the tests.
    """
    now = mono_us()
    send = []
    with state.lock:
        want = desired_modes(state)
        for ip, w in want.items():
            if state.slaves[ip]["mode"] == w:
                state.mode_sent.pop(ip, None)   # settled where we want it
                continue
            last = state.mode_sent.get(ip)
            if last and last[0] == w and now - last[1] < MODE_RESEND_US:
                continue                        # already told, beacon pending
            state.mode_sent[ip] = (w, now)
            send.append((ip, w))
        for ip in [ip for ip in state.mode_sent if ip not in want]:
            state.mode_sent.pop(ip)             # slave went away
    for ip, w in send:
        cmds.send(ip, {"cmd": "set_mode",
                       "mode": "network" if w else "local"})
    return send


def _cycle_arm(state, group, now):
    """Put a group's next burst on the grid: one period out, plus its phase.

    A full period rather than "now" because a restart must not fire into a
    rig someone is still cabling up.
    """
    c = state.cycles[group]
    state.cycle_next[group] = now + (
        c["every_s"] + state.cycle_phase.get(group, 0)) * 1_000_000


def end_burst(state, restore_pattern=True):
    """Cut the burst on air short and put the rig back on the mic.

    The grid is deliberately untouched: the next burst lands exactly where it
    always would, so cutting one short does not shift the whole evening.
    """
    with state.lock:
        run, state.cycle_run = state.cycle_run, None
        if run is None:
            return
        state.cycle_step = None
        state.cycle_mic = any(c["enabled"] and c["pairs"]
                              for c in state.cycles.values())
    if restore_pattern:
        apply_preset(state, run["pattern"])
    print(f"[cycle] {run['group']} cut short"
          + ("" if restore_pattern else " (panel taken over)"), flush=True)


def advance_cycle(state, tick_us):
    """Run the two pair cycles. Returns the step just started, or None.

    Each enabled group is a grid of burst times, phase-shifted apart. When a
    burst comes due the pair takes the stream; when it ends the rig goes back
    to the microphone until the next one. A burst that comes due while the
    other group is on air is skipped, not queued -- there is one stream, and
    two bursts back to back read as a fault.

    Frozen while the feed is silent: a cycle that kept counting through a
    two-minute mute would come back mid-pair with the clock in a place nobody
    could predict. Freezing pushes the grid out by the real time that passed.

    state.cycle_play asks for one group's burst NOW (a pair picked on the show
    screen). It is taken on the next tick or dropped: a request left pending
    through a mute would fire minutes later, when nobody asked for it.
    """
    now = mono_us()
    apply_p = None
    ended = None
    step = None
    # Before the lock: live_snapshot takes it, and this lock is not reentrant.
    # A burst records what the panel had so it can hand it straight back.
    before = state.live_snapshot()
    with state.lock:
        names = [p["name"] for p in state.presets]
        # What each group's selected pair would play, switched on or not: a
        # pair played on demand runs even when its group has no period.
        pair_plans = {g: burst_steps(dict(state.cycles[g], enabled=True), g,
                                     names)
                      for g in CYCLE_GROUPS}
        plans = {g: pair_plans[g] if state.cycles[g]["enabled"] else []
                 for g in CYCLE_GROUPS}
        live = [g for g in CYCLE_GROUPS if plans[g]]
        play, state.cycle_play = state.cycle_play, None
        # Only what the grid is built from restages it: the periods, the
        # burst lengths, and which groups play at all. The selected pair is
        # not -- re-laying the grid on a pair pick pushed the next burst a
        # full period out, so tapping NEXT a minute before the names were due
        # took them off the scopes for five.
        sig = repr([(g, state.cycles[g]["every_s"],
                     cycle_burst_s(state.cycles[g]), g in live)
                    for g in CYCLE_GROUPS])
        run = state.cycle_run
        if sig != state.cycle_sig:
            # Config changed under us (a switch, a period, a burst length).
            # The phases are only meaningful relative to each other, so both
            # grids are laid out again off this moment.
            state.cycle_sig = sig
            state.cycle_phase = cycle_phases(state.cycles)
            state.cycle_next = {}
            ended, run = run, None
        elif run is not None and run["steps"] != pair_plans[run["group"]]:
            # The burst on air is no longer what its pair would play (another
            # pair picked, a name deleted or renamed): stop it rather than
            # finish on a name the card no longer promises.
            ended, run = run, None
        if ended is not None:
            state.cycle_run = state.cycle_step = None
            state.cycle_mic = bool(live)
        # What a burst starting now hands back when it ends: the panel's own
        # pattern, which is not what is installed while a burst is on air.
        restore = (run or ended or {"pattern": before})["pattern"]
        if live and not state.stream_on:
            for g in live:                    # hold the grid where it is
                if g in state.cycle_next:
                    state.cycle_next[g] += tick_us
            if run is not None:
                run["until_us"] += tick_us
            run = None                        # and step nothing
        else:
            for g in list(state.cycle_next):
                if g not in live:
                    state.cycle_next.pop(g)
            for g in live:
                if g not in state.cycle_next:
                    _cycle_arm(state, g, now)
            if play is not None and pair_plans.get(play) and state.stream_on:
                # On demand: this pair takes the stream now, replacing any
                # burst on air. If the group runs on a period, its grid is
                # re-laid with this burst as its slot -- the next one a full
                # period out, the other group keeping its offset from it --
                # because a scheduled burst straight after this one would
                # read as a fault.
                if play in live:
                    anchor = now - state.cycle_phase.get(play, 0) * 1_000_000
                    for g in live:
                        every = state.cycles[g]["every_s"] * 1_000_000
                        nxt = anchor + state.cycle_phase.get(g, 0) * 1_000_000
                        while nxt <= now:
                            nxt += every
                        state.cycle_next[g] = nxt
                step = dict(pair_plans[play][0])
                state.cycle_run = {"group": play, "steps": pair_plans[play],
                                   "pos": 0,
                                   "until_us": now + step["secs"] * 1_000_000,
                                   "pattern": restore}
                run = None
            # Every group whose slot has passed loses that slot, whether or
            # not anything is on air. Computed before the burst on air is
            # stepped: left until the burst ended, a slot missed at second 30
            # of a 40 s burst fired the moment it released -- the queue, two
            # bursts back to back, which is what the phases exist to prevent.
            due = [g for g in live if state.cycle_next[g] <= now]
            if state.cycle_run is None and due:
                # Due together (phases cannot always separate incommensurate
                # periods): the longer period wins, because it is the one the
                # room sees least often. The loser waits for its own next slot.
                pick = max(due, key=lambda g: (state.cycles[g]["every_s"], g))
                step = dict(plans[pick][0])
                state.cycle_run = {"group": pick, "steps": plans[pick],
                                   "pos": 0,
                                   "until_us": now + step["secs"] * 1_000_000,
                                   "pattern": restore}
            for g in due:
                # Step the grid, never the clock: a burst that was skipped or
                # ran long must not walk its phase away over an evening.
                every = state.cycles[g]["every_s"] * 1_000_000
                while state.cycle_next[g] <= now:
                    state.cycle_next[g] += every
            if run is not None and now >= run["until_us"]:
                pos = run["pos"] + 1
                if pos < len(run["steps"]):
                    step = dict(run["steps"][pos])
                    run["pos"] = pos
                    run["until_us"] = now + step["secs"] * 1_000_000
                else:
                    ended, state.cycle_run = run, None
                run = None
            if run is None:
                # A step started or a burst ended. Mid-step (run still set)
                # both of these stand as they are.
                state.cycle_step = step
                state.cycle_mic = bool(live) and (step is None
                                                  or step["group"] is None)
        if step is not None and step["group"] is not None:
            apply_p = next((dict(x) for x in state.presets
                            if x["name"] == step["preset"]), None)
    if ended is not None and apply_p is None:
        # Hand the panel's own pattern back. Invisible while the rig is on the
        # mic, but it is what streams the moment the cycles are switched off,
        # and what persist_loop is allowed to write again.
        apply_preset(state, ended["pattern"])
        print(f"[cycle] {ended['group']} done — mic", flush=True)
    if apply_p is not None:
        apply_preset(state, apply_p)
    if step is not None:
        print("[cycle] " + (f"mic pause {step['secs']}s"
                            if step["group"] is None else
                            f"{step['group']} {step['preset']!r} "
                            f"for {step['secs']}s"), flush=True)
    return step


def policy_loop(state, cmds, tick=0.25):
    """The one authority for slave draw modes, and the pair-cycle clock.

    Its own thread, like persist_loop: applying a preset parses a font or reads
    a table off the eMMC, and neither belongs on the 5 ms stream thread.
    """
    tick_us = int(tick * 1_000_000)
    while True:
        time.sleep(tick)
        try:
            advance_cycle(state, tick_us)
            reconcile_modes(state, cmds)
        except Exception as e:   # a rig that stops obeying policy is a dark
            # stage; one bad tick must not take the thread down with it.
            print(f"[policy] tick failed: {e}", flush=True)


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
        self.figure = ""          # baked table in FIGURE_DIR, kind="figure"
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
             self.rot_speed, self.flip_x, self.flip_y, self.figure) = (
                saved["kind"], saved["freq"], saved["amp"], saved["a"],
                saved["b"], saved["text"], saved["font"],
                saved["pulse_rate"], saved["pulse_depth"], saved["rot"],
                saved["flip_x"], saved["flip_y"], saved["figure"])
        # None off-board; rmax rides along so block() can cap amp (see
        # table_rmax) without walking the table.
        self.shape_tbl, self.shape_rmax = build_shape(
            self.kind, self.text, self.font, self.figure)
        self.shape_ver = 1        # bumped on rebuild; UI refetches preview
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
        # Per-node switches, and the pair cycles that drive the set. Both are
        # read every policy tick and written by the HTTP threads, so both are
        # under self.lock.
        self.node_flags = load_nodes()   # slave id -> stream/logos/artists
        self.cycles = load_cycles()      # "logos"/"artists" -> config
        # Cycle runtime. cycle_next is the grid: group -> mono_us of its next
        # burst. cycle_phase is the offset that keeps the two grids apart,
        # restaged whenever the config changes. cycle_run is the burst on air
        # (with the pre-burst pattern to hand back), cycle_step the step
        # inside it, and cycle_mic says the rig is on the microphone -- which
        # is what desired_modes reads, and is the normal state of the room.
        self.cycle_next = {}
        self.cycle_phase = cycle_phases(self.cycles)
        self.cycle_run = None
        self.cycle_step = None
        self.cycle_mic = False
        self.cycle_sig = None
        # A group whose selected pair should take the stream on the next
        # policy tick, set by POST /api/cycle {"play": true}. Read-and-clear.
        self.cycle_play = None
        # ip -> (mode, mono_us) of the last set_mode this box sent, so a
        # command is not repeated every tick while the 1 Hz beacon catches up.
        self.mode_sent = {}

    def pattern_params(self):
        with self.lock:
            return (self.kind, self.freq, self.amp * 32000.0,
                    self.ratio_a, self.ratio_b, self.shape_tbl,
                    self.shape_rmax,
                    self.pulse_rate, self.pulse_depth, self.rot_speed,
                    self.flip_x, self.flip_y)

    def live_snapshot(self):
        """Just the persisted subset, taken under the lock."""
        with self.lock:
            return {"kind": self.kind, "freq": self.freq, "amp": self.amp,
                    "a": self.ratio_a, "b": self.ratio_b, "text": self.text,
                    "font": self.font, "pulse_rate": self.pulse_rate,
                    "pulse_depth": self.pulse_depth, "rot": self.rot_speed,
                    "flip_x": self.flip_x, "flip_y": self.flip_y,
                    "figure": self.figure}

    def snapshot(self):
        now = mono_us()
        # Off the lock on purpose: a directory read on the eMMC can block for
        # tens of ms, and the stream loop takes this lock every 5 ms.
        figures = list_figures()
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
                            "figure": self.figure,
                            "sver": self.shape_ver},
                "figures": figures,
                "presets": [p["name"] for p in self.presets],
                "stream_on": self.stream_on,
                "slaves": [dict(s, age_ms=(now - s["last_us"]) // 1000)
                           for s in self.slaves.values()],
                "net": {"iface": self.net_iface, "egress": self.net_egress,
                        "tx": self.net_tx},
                "nodes": {str(sid): dict(f)
                          for sid, f in self.node_flags.items()},
                # Each group carries its own burst plan and its own countdown
                # -- the two cycles run on separate grids now, so there is no
                # single running order to show.
                "cycles": {g: dict(
                    c,
                    running=(self.cycle_run is not None
                             and self.cycle_run["group"] == g),
                    next_in=(None if g not in self.cycle_next else
                             max(0, (self.cycle_next[g] - now) // 1000000)),
                    phase=self.cycle_phase.get(g, 0),
                    burst_s=cycle_burst_s(c),
                    seq=[{"group": s["group"] or "", "preset": s["preset"],
                          "secs": s["secs"]}
                         for s in burst_steps(
                             c, g, [p["name"] for p in self.presets])],
                ) for g, c in self.cycles.items()},
                "mic": self.cycle_mic,
                "cycle": None if self.cycle_step is None else {
                    "group": self.cycle_step["group"] or "",
                    "preset": self.cycle_step["preset"],
                    "left_s": max(0, (self.cycle_run["until_us"] - now)
                                  // 1000000) if self.cycle_run else 0,
                },
            }


class PatternGen:
    """Stateful stereo test-pattern generator (X = L, Y = R).

    Single base phase; ratios are multipliers so ratio changes stay
    phase-continuous (no scope-trace jump mid-stream).
    """

    def __init__(self):
        self.phase = 0.0
        self.tpos = 0.0  # table kinds: fractional index into the point table
        self.lfo = 0.0   # table kinds: pulse-LFO phase
        self.rot = 0.0   # table kinds: current rotation angle

    def block(self, n, params):
        (kind, freq, amp, a, b, tbl, rmax,
         pulse_rate, pulse_depth, rot_speed, flip_x, flip_y) = params
        out = array("h", bytes(4 * n))  # n stereo frames, zeroed
        two_pi = 2.0 * math.pi
        sin = math.sin
        if kind in ("text", "figure") and tbl:
            # Walk the precomputed equal-arc-length table at freq redraws/s.
            # Pulse (amp LFO) and rotation advance per block — 5 ms steps are
            # smooth for the sub-10 Hz rates these run at.
            m = len(tbl)
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
                x, y = tbl[int(pos)]
                out[2 * i] = int(x * axx - y * axy)      # X
                out[2 * i + 1] = int(x * ayx + y * ayy)  # Y
                pos += tstep
                if pos >= m:
                    pos -= m
            self.tpos = pos
            self.lfo = (self.lfo +
                        two_pi * pulse_rate * n / SAMPLE_RATE) % two_pi
            return out.tobytes()
        # A table kind with no drawable table falls through to a plain circle.
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
/* AP down: nothing holds the AP address, so no slave CAN appear. Page level
   and in both modes -- on the show screen it otherwise looks exactly like
   every slave being switched off. */
.ap-banner {
  display: none; background: #3d1010; color: var(--bad); border: 1px solid var(--bad);
  padding: 8px; text-align: center; border-radius: 4px; margin-bottom: 8px;
}
.ap-banner.active { display: block; }
.ap-banner code { color: var(--fg); }

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
/* Three switches per node: stream / logos / artists. Lit = in the show. Big
   enough for a thumb in the dark, and they swallow the tap so the tile's own
   click does not open the slave sheet underneath. */
.tile-sw { display: flex; gap: 3px; margin-top: 3px; }
.tile-sw button {
  width: 22px; height: 20px; padding: 0; font-size: 11px; font-weight: bold;
  line-height: 18px; border: 1px solid var(--line); border-radius: 3px;
  background: #0d130d; color: var(--dim); cursor: pointer;
}
.tile-sw button.on { border-color: var(--ph); color: var(--bg); background: var(--ph); }
.tile-sw button.off { border-color: #4a2020; color: #6b3030; background: #150808; }

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
/* The rig is on the microphone: the canvas is the panel's pattern, which no
   scope is drawing, so it is dimmed and the line under it says so. */
.on-air-name.mic { color: var(--fg); }
.on-air-panel { font-size: 11px; color: var(--dim); text-align: center; margin-top: 4px; }

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

/* === Feed row === */
.panic-row { display: flex; gap: 8px; align-items: center; margin-top: 12px; }
/* Two doors off the show screen into the only settings a set needs: which
   logos run, and which artists are on. */
.settings-row { display: flex; gap: 8px; margin-top: 8px; }
.settings-row button {
  flex: 1; padding: 12px; font-size: 13px; letter-spacing: 0.05em;
}
.cycle-line {
  margin-top: 8px; font-size: 12px; color: var(--dim); text-align: center;
  min-height: 16px;
}
.cycle-line b { color: var(--ph); font-weight: bold; }
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
<div id="ap-banner" class="ap-banner"></div>

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
    <div class="on-air-panel" id="on-air-panel"></div>
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

  <!-- Feed + the two settings doors -->
  <div class="panic-row">
    <div class="feed-toggle" id="feed-toggle">
      <button id="feed-on" onclick="setStream(true)">SENDING</button>
      <button id="feed-off" onclick="setStream(false)">SILENT</button>
    </div>
  </div>
  <div class="settings-row">
    <button onclick="openCycleSheet('logos')">LOGO SETTINGS</button>
    <button onclick="openCycleSheet('artists')">ARTIST SETTINGS</button>
  </div>
  <div class="cycle-line" id="cycle-line"></div>

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
        <button data-k="figure" onclick="setP({kind:'figure'})">figure</button>
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
    <div class="row" id="figurerow" style="display:none">
      <label>artwork</label>
      <select id="figure" onchange="setP({figure:this.value})"></select>
      <span id="figurenote" style="color:var(--dim);font-size:12px"></span>
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
    <!-- Spin is hidden on purpose (2026-08-21): the show runs upright text and
         a slider left off-zero spun the words crooked with nothing on the page
         saying why. The row stays in the DOM (disabled) so the sync code below
         keeps working and re-enabling is a one-line revert; the renderer, the
         preset field and POST /api/pattern {"rot": …} are untouched. -->
    <div class="row" id="spinrow" style="display:none">
      <label>spin</label>
      <input type="range" id="rot" min="-100" max="100" step="1" disabled oninput="showRot()" onchange="setP({rot:this.value/100})">
      <span class="val" id="rotv">—</span>
      <button disabled onclick="document.getElementById('rot').value=0;showRot();setP({rot:0})">0</button>
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

  <!-- One copy of the settings DOM, opened from either mode: the sheet is
       also reachable from the show screen, which is the only place a phone
       can get at it mid-set. -->
  <div class="collapsible-header" onclick="toggleCollapsible(this)">Cycles</div>
  <div class="collapsible-content panel">
    <div class="settings-row">
      <button onclick="openCycleSheet('logos')">LOGO SETTINGS</button>
      <button onclick="openCycleSheet('artists')">ARTIST SETTINGS</button>
    </div>
  </div>

  <!-- Rig -->
  <div class="collapsible-header" onclick="toggleCollapsible(this)">Rig</div>
  <div class="collapsible-content panel">
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
      <p>What a scope draws comes from two places: the global <strong>feed</strong> toggle (SENDING/SILENT), and the three switches on that node's tile.</p>
      <p><strong>S — streaming</strong>: off takes the node out of the show entirely. It draws its own mic and the controller sends it no audio. Use it for a scope that is not in XY.</p>
      <p><strong>L — logos</strong>: off and the node sits on its mic while a logo is on air.</p>
      <p><strong>A — artists</strong>: same, for artist names.</p>
      <p>There is nothing else to set per node: the controller drives every slave's draw mode from those switches and what is on air, and pulls a node back if its own button or a reboot puts it somewhere else.</p>
      <h3>Cycles</h3>
      <p>The rig's normal state is the <strong>microphone</strong>: every node on its own audio. A cycle interrupts it on a period — every N minutes, name A for a few seconds, an optional mic pause, name B, then straight back to the mic until the next one.</p>
      <p>LOGO SETTINGS and ARTIST SETTINGS are the same machine with a different period. Artists carry the b2b line-ups — pick which pair is playing tonight. A burst obeys the three switches: it does not appear on a node with that group switched off.</p>
      <p>Tapping a pair on the show screen (or PREV / NEXT) puts it up straight away: with a cycle running it plays as a burst and the next one comes a full period later; with no cycle running its first name stays up. Picking a pair in ARTIST SETTINGS only changes which pair the next burst plays.</p>
      <p>With both cycles on, the controller offsets the two grids so their bursts do not collide — the card says by how much. Periods that share no useful multiple cannot always miss; when two do come due together the longer period wins and the other waits for its next slot, because the rig has one stream. Both freeze while the feed is SILENT.</p>
      <h3>Fallback</h3>
      <p>A streaming slave that stops receiving audio for ~1 s automatically draws its local pattern instead — whichever was last set.</p>
      <h3>buf / lead</h3>
      <p>Received audio queued ahead of playback. Healthy ≈ 450 ms: the stream runs ahead because the WiFi radio pauses for ~300 ms every ~1.4 s. Hitting 0 = buffer underrun (beam blinks).</p>
      <h3>drop/s, lost/s, under/s</h3>
      <p>Should all be 0. Drops = late/duplicate packets. Lost = packets that never arrived (WiFi loss). Underruns = buffer ran dry (visible blink).</p>
      <h3>tx-drop vs lost</h3>
      <p>High lost/s with tx-drop ≈ 0 means the WiFi air is bad. High tx-drop means packets died in this controller's send buffer — no antenna fix. This distinction cost a night of debugging.</p>
      <h3>Presets</h3>
      <p>Snapshots of pattern settings saved on the controller. Survive restarts with one generation of undo (.bak file). Max 20 presets. Do NOT update while a burst is on air — it would save the logo, not the artist. The controller refuses that one.</p>
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

<!-- === LOGO / ARTIST SETTINGS SHEET ===
     One sheet, two groups. The pair cycle is the same machine for both --
     every N minutes, A, an optional mic pause, B, then the microphone until
     the next one -- so it is the same markup with the group swapped, and
     there is one place to fix when it is wrong at two in the morning. -->
<div id="cycle-sheet" class="slave-sheet">
  <div class="slave-sheet-header">
    <h2 id="cycle-title">LOGO SETTINGS</h2>
    <button class="slave-sheet-close" onclick="closeCycleSheet()">✕</button>
  </div>
  <div class="slave-sheet-inner">
    <div class="panel">
      <div class="row">
        <label>cycle</label>
        <span class="seg" id="cyc-onoff">
          <button onclick="setCycle({enabled:true})">ON</button>
          <button onclick="setCycle({enabled:false})">OFF</button>
        </span>
        <span style="color:var(--dim);font-size:12px" id="cyc-state"></span>
      </div>
      <div class="row">
        <label>show every</label>
        <input type="number" id="cyc-every" min="0.2" max="1440" step="any"
               style="width:80px" oninput="cycTouched()"
               onchange="applyCycleTimes()">
        <span style="color:var(--dim)">min — mic in between</span>
      </div>
      <div class="row">
        <label>each name for</label>
        <input type="number" id="cyc-show" min="3" max="600" style="width:72px"
               oninput="cycTouched()" onchange="applyCycleTimes()">
        <span style="color:var(--dim)">s</span>
        <label style="width:auto">mic between them</label>
        <input type="number" id="cyc-pause" min="0" max="600" style="width:72px"
               oninput="cycTouched()" onchange="applyCycleTimes()">
        <span style="color:var(--dim)">s (0 = none)</span>
        <button onclick="applyCycleTimes()">Apply</button>
      </div>
      <div id="cyc-pairs"></div>
      <div style="margin-top:6px">
        <button id="cyc-addpair" onclick="addCyclePair()">+ pair</button>
      </div>
      <div class="cycle-line" id="cyc-order" style="text-align:left"></div>
    </div>
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
/* The settings sheet's group, or null when it is closed. */
var activeCycleGroup = null;
/* A number in the sheet the operator has changed but the board has not
   confirmed yet, and the timer that will send it. */
var cycDirty = false;
var cycCommit = null;
var editModeOn = false;
var renameTarget = null;
var renameNewName = null;

/* Per-slave change tracking (F2 fix) */
var slaveLastChange = {};

/* Incremental DOM elements (F4 fix) */
var slaveTiles = {};
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
    /* not poll() — see poll(). Resolves to the reply (or undefined), so a
       caller can act on what the controller actually did. */
    .then(function(d) { return refresh().then(function() { return d; }); });
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

/* === NAV (PREV/NEXT) ===
   Steps the artist pairs, because that is what the set list under it now is.
   Stepping single presets would disagree with the list on the same screen. */
function stepPair(delta) {
  var cfg = (S && S.cycles) ? S.cycles.artists : null;
  var pairs = cfg ? (cfg.pairs || []) : [];
  if (pairs.length < 2) return;
  var i = ((cfg.idx + delta) % pairs.length + pairs.length) % pairs.length;
  pickArtistPair(i);
}

function goPrev() { stepPair(-1); }

function goNext() { stepPair(1); }

function applyPreset(name) {
  if (!S) return;
  setCur(name);
  noteSlaveChange('all');
  post("/api/preset", {op: 'load', name: name});
}

/* === PREVIEW CANVAS === */
var TP = {ver: -1, pts: []};

/* text and figure both draw the server-side point table, so both need the
   preview fetch and both redraw when sver bumps. */
function isTableKind(k) { return k === 'text' || k === 'figure'; }

/* Fill the artwork picker from what is actually baked onto the board, and say
   so plainly when the live preset names a figure this board does not carry —
   the scopes draw a circle in that case, and an empty dropdown looked like a
   UI bug rather than a missing file. */
function syncFigures(p) {
  var sel = document.getElementById('figure');
  var note = document.getElementById('figurenote');
  if (!sel || !note) return;
  var figs = (S && S.figures) || [];
  var want = figs.join(',');   /* sanitize_figure bans commas */
  if (sel.dataset.have !== want) {
    sel.dataset.have = want;
    sel.innerHTML = figs.map(function(f) {
      return '<option>' + esc(f) + '</option>';
    }).join('');
  }
  if (document.activeElement !== sel && figs.indexOf(p.figure) >= 0) {
    sel.value = p.figure;
  }
  note.textContent = !figs.length
    ? 'no baked artwork on this board (tools/bake_svg.py)'
    : (p.figure && figs.indexOf(p.figure) < 0
       ? '"' + p.figure + '" is not on this board — drawing a circle' : '');
}

async function fetchPreview() {
  try {
    var d = await (await fetch("/api/shapepreview")).json();
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
  /* On the mic (or muted) no scope draws this, so it is drawn as a ghost. */
  var ghost = airState() === 'mic' || airState() === 'silent';
  g.globalAlpha = ghost ? 0.3 : 1;
  g.strokeStyle = '#39ff14';
  g.lineWidth = 2;
  g.shadowColor = '#39ff14';
  g.shadowBlur = ghost ? 0 : 8;
  g.beginPath();

  if ((p.kind === 'text' || p.kind === 'figure') && TP.pts.length) {
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
  g.globalAlpha = 1;
}

/* === SLAVE VERDICT (one sentence) ===
   Hybrid is not settable any more, but a slave's own button still cycles into
   it, so it is still drawn — named as the odd state it now is, rather than
   quietly reading as STREAM. policy_loop pulls it back within a beacon. */
function slaveVerdict(s) {
  var p = S.pattern;
  if (s.source) {
    var what = s.mode === 2 ? p.kind + ' + own mic (HYBRID — not a setting, being corrected)' : p.kind;
    if (p.kind === 'text') what = 'text "' + esc(p.text) + '" (' + (p.font||'duplex') + ')' + (s.mode === 2 ? ' + mic' : '');
    if (p.kind === 'figure') what = 'artwork "' + esc(p.figure || '?') + '"' + (s.mode === 2 ? ' + mic' : '');
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
  if (s.source) return s.mode === 2 ? 'HYBRID?' : 'STREAM';
  return 'OWN';
}

function modeGlyph(s) {
  if (s.source) return s.mode === 2 ? '■HYB?' : '■NET';
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
      /* The id is known the moment the tile exists, and the switches are set
         while the rig is still in its flight case — a tile reading "?" is a
         switch you cannot aim. Only the mode waits for a beacon. */
      tile.innerHTML = '<div class="tile-id">' + id + '</div><div class="tile-mode">—</div><div class="tile-status">LOST</div><div class="tile-sw">' +
        '<button data-sw="stream" title="streaming to this node">S</button>' +
        '<button data-sw="logos" title="show logos on this node">L</button>' +
        '<button data-sw="artists" title="show artist names on this node">A</button>' +
        '</div><div class="battery-bar"><span></span><span></span><span></span><span></span><span></span></div><div class="tile-age"></div>';
      var sws = tile.querySelectorAll('.tile-sw button');
      for (var j = 0; j < sws.length; j++) {
        sws[j].onclick = (function(tid, key) {
          return function(ev) {
            /* The tile itself opens the slave sheet; a switch must not. */
            ev.stopPropagation();
            toggleNodeSw(tid, key);
          };
        })(id, sws[j].getAttribute('data-sw'));
      }
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

/* === SET LIST (SHOW mode) ===
   Tonight's choice is which b2b is on, not which of eight names to tap: the
   pair cycle plays both halves on its own once a pair is picked. So the show
   screen lists the artist pairs and nothing else. The full preset list is
   still in SETUP, and both slots of every pair are edited in ARTIST SETTINGS.
   Picking a pair puts its A side up immediately — a tap that only changed a
   setting, with the scopes still on the last act's name, is how the wrong
   name stays on stage. */
function renderSetList() {
  var container = document.getElementById('setlist');
  if (!container) return;
  var cfg = (S.cycles && S.cycles.artists) ? S.cycles.artists : null;
  var pairs = cfg ? (cfg.pairs || []) : [];
  var live = onAirPreset();

  container.innerHTML = '';
  if (!pairs.length) {
    container.innerHTML = '<div style="color:var(--dim);text-align:center;padding:12px">'
      + 'No artist pairs — set them in ARTIST SETTINGS</div>';
    return;
  }
  for (var i = 0; i < pairs.length; i++) {
    var a = pairs[i][0] || '—', b = pairs[i][1] || '—';
    /* !!live: with nothing on air, '' would match a pair's empty slot. */
    var onAir = !!live && (pairs[i][0] === live || pairs[i][1] === live);
    var row = document.createElement('div');
    row.className = 'preset-row' + (onAir ? ' on-air' : '');
    row.onclick = (function(idx) {
      return function() { pickArtistPair(idx); };
    })(i);
    row.innerHTML = '<span class="idx">' + (i + 1) + '</span>'
      + '<span class="name">' + esc(a) + '  b2b  ' + esc(b) + '</span>'
      + '<span class="tag">'
      + (onAir ? 'ON AIR' : (cfg.idx === i ? 'SELECTED' : ''))
      + '</span>';
    container.appendChild(row);
  }
}

/* The tap puts the pair on the scopes now, and the controller decides how:
   while any cycle runs the rig rests on the mic, so the pair plays as a burst
   (and the artists' next scheduled one is a full period after it); with no
   cycle running, its first name is loaded as the streamed pattern. One POST,
   so the pick and the play cannot land out of order. Only a load changes the
   panel's pattern, so only a load becomes the "current" preset. */
function pickArtistPair(i) {
  if (!S) return;
  noteSlaveChange('all');
  post("/api/cycle", {group: 'artists', idx: i, play: true}).then(function(d) {
    if (d && d.loaded) setCur(d.loaded);
  });
}

/* === ON AIR ===
   What the scopes are drawing right now:
     'silent' -- the feed is muted; every scope is on its own audio.
     'burst'  -- a cycle step has the stream: S.cycle.preset is on air.
     'mic'    -- the rig is resting on the microphone, between bursts or in
                 the pause inside one. The panel's pattern streams to nobody.
     'panel'  -- no cycle running: the panel's pattern is what streams. */
function airState() {
  if (!S.stream_on) return 'silent';
  if (S.cycle && S.cycle.group) return 'burst';
  if (S.mic) return 'mic';
  return 'panel';
}

/* The preset name on the scopes, or '' when none is (or none is known). */
function onAirPreset() {
  var air = airState();
  if (air === 'burst') return S.cycle.preset;
  if (air === 'panel') return curPreset;
  return '';
}

/* The installed pattern by name: the preset it came from, plus the words or
   artwork it draws (N17). */
function patternLabel(name) {
  var p = S.pattern;
  if (p.kind === 'text' && p.text) return name ? p.text + ' (' + name + ')' : p.text;
  if (p.kind === 'figure' && p.figure) return name ? p.figure + ' (' + name + ')' : p.figure;
  return name || '—';
}

function renderOnAir() {
  var air = airState();
  var presets = S.presets || [];
  var name, detail, panel = '';
  if (air === 'burst') {
    name = patternLabel(S.cycle.preset);
    detail = '▶ ' + S.cycle.group.toUpperCase() + ' burst · '
      + fmtLeft(S.cycle.left_s) + ' left';
  } else if (air === 'panel') {
    /* Index off the preset name, before the decoration -- decorated, it
       never matches the set list and every tile read "1 of N". On a phone
       that has not loaded a preset this session there is no way to know
       which one is on air: "1 of 7" was a guess printed as fact, and PREV
       stepped from it. Say the kind and stop. */
    var idx = curPreset ? presets.indexOf(curPreset) : -1;
    name = patternLabel(curPreset);
    detail = (S.pattern.kind || '?')
      + (idx >= 0 ? ' · ' + (idx + 1) + ' of ' + presets.length : '');
  } else {
    /* No streamed pattern is on any scope. This used to keep naming the
       panel's pattern, so "hyperwaves" sat under ON AIR while every scope
       drew its own microphone. */
    var nb = nextBurst();
    if (air === 'silent') {
      name = '● FEED SILENT';
      detail = 'every scope on its own audio';
    } else if (S.cycle) {
      name = '● MIC pause';
      detail = 'between the two names · ' + fmtLeft(S.cycle.left_s) + ' left';
    } else {
      name = '● MIC';
      detail = 'every scope on its own audio'
        + (nb ? ' · ' + nb.g + ' in ' + fmtLeft(nb.left) : '');
    }
    /* Inside a burst's pause the installed pattern is the burst's, not the
       panel's, so only name the panel between bursts. */
    if (!S.cycle) {
      panel = 'panel: ' + patternLabel(curPreset) + ' — streams when '
        + (air === 'silent' ? 'the feed is back on' : 'the cycles are off');
    }
  }
  var nameEl = document.getElementById('on-air-name');
  nameEl.textContent = name;
  nameEl.classList.toggle('mic', air === 'silent' || air === 'mic');
  document.getElementById('on-air-detail').textContent = detail;
  document.getElementById('on-air-panel').textContent = panel;

  /* NAV hint and nav buttons follow the pairs, like the list below them. */
  var acfg = (S.cycles && S.cycles.artists) ? S.cycles.artists : null;
  var apairs = acfg ? (acfg.pairs || []) : [];
  var nextPair = apairs.length > 1
    ? apairs[(acfg.idx + 1) % apairs.length] : null;
  document.getElementById('nav-next-hint').textContent = nextPair
    ? 'next pair → ' + (nextPair[0] || '—') + ' b2b ' + (nextPair[1] || '—')
    : '';
  document.getElementById('btn-prev').disabled = apairs.length < 2;
  document.getElementById('btn-next').disabled = apairs.length < 2;

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
  if (!slave) {
    /* The node is off or out of range, but its switches are still the show's
       settings and still worth changing — that is what rigging is. */
    var s0 = nodeSw(id);
    activeSlaveSheet = id;
    document.getElementById('sheet-title').textContent = 'SLAVE ' + id + '  (not on the air)';
    document.getElementById('sheet-body').innerHTML =
      '<div class="verdict warn">No STATUS beacon from this node — powered off, or off the AP.</div>' +
      '<h3>IN THE SHOW</h3>' +
      '<div class="source-3way">' +
        '<button class="' + (s0.stream ? 'active' : '') + '" onclick="toggleNodeSw(' + id + ',\\'stream\\')">STREAMING</button>' +
        '<button class="' + (s0.logos ? 'active' : '') + '" onclick="toggleNodeSw(' + id + ',\\'logos\\')">LOGOS</button>' +
        '<button class="' + (s0.artists ? 'active' : '') + '" onclick="toggleNodeSw(' + id + ',\\'artists\\')">ARTISTS</button>' +
      '</div>' +
      '<p style="color:var(--dim);font-size:12px;margin-top:8px">These are kept on the controller and applied the moment the node reports in.</p>';
    document.getElementById('slave-sheet').classList.add('active');
    return;
  }
  activeSlaveSheet = id;
  document.getElementById('sheet-title').textContent = 'SLAVE ' + id + '  ' + slave.ip;

  var verdict = slaveVerdict(slave);
  var vbat = batteryLevel(slave.vbat_mv || 0);
  var vbatStr = (slave.vbat_mv / 1000).toFixed(2) + ' V';
  var battEst = batteryEstimate(slave.vbat_mv || 0);
  var rssi = slave.rssi + ' dBm';
  var rssiLabel = slave.rssi > -60 ? 'good' : slave.rssi > -75 ? 'marginal' : 'poor';
  var buf = Math.round(slave.depth / 48) + ' ms';

  var sw = nodeSw(slave.id);

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

    '<h3>IN THE SHOW</h3>' +
    '<div class="source-3way">' +
      '<button class="' + (sw.stream ? 'active' : '') + '" onclick="toggleNodeSw(' + slave.id + ',\\'stream\\')">STREAMING<br><span style="font-size:11px;color:var(--dim)">' + (sw.stream ? 'on' : 'off — own mic') + '</span></button>' +
      '<button class="' + (sw.logos ? 'active' : '') + '" onclick="toggleNodeSw(' + slave.id + ',\\'logos\\')">LOGOS<br><span style="font-size:11px;color:var(--dim)">' + (sw.logos ? 'on' : 'off') + '</span></button>' +
      '<button class="' + (sw.artists ? 'active' : '') + '" onclick="toggleNodeSw(' + slave.id + ',\\'artists\\')">ARTISTS<br><span style="font-size:11px;color:var(--dim)">' + (sw.artists ? 'on' : 'off') + '</span></button>' +
    '</div>' +
    '<p style="color:var(--dim);font-size:12px;margin:6px 0">Draw mode follows these: the node is on the stream when what is on air is switched on for it, and on its own mic otherwise.</p>' +

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
  renderApBanner();
  renderRigStrip();
  renderOnAir();
  renderFeedToggle();
  renderSetList();
  renderDials();
  /* Draw preview (N4: fetch for text patterns) */
  if (isTableKind(S.pattern.kind) && S.pattern.sver !== TP.ver) {
    fetchPreview();
  }
  drawPreview();
  renderCycleLine();
  if (document.body.className === 'setup') renderSetup();
  /* The settings sheet opens over either mode, so its countdown has to tick
     outside renderSetup. */
  if (activeCycleGroup) renderCycleSheet(false);
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

/* net.egress false: nothing holds the AP address, so no slave can join and
   every tile reads LOST for a reason no tile can show. Page level, both
   modes -- this used to live only in SETUP > Diagnostics, so on the phone an
   AP outage looked exactly like four slaves switched off. */
function renderApBanner() {
  var el = document.getElementById('ap-banner');
  var net = S.net || {};
  var down = net.egress === false;
  el.classList.toggle('active', down);
  if (down) {
    el.innerHTML = '⚠ Wi-Fi AP is DOWN — no slave can connect. On the controller: '
      + '<code>sudo nmcli c up hyperosci-ap</code> (no restart needed; the '
      + 'netwatch timer also retries every 30 s)';
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
    /* Switches are policy, not status: they read the same whether the node
       is on air or in a flight case. */
    patchTileSwitches(parseInt(id));
  }
}

/* === PER-NODE SWITCHES ===
   Keyed by slave id, not ip, and settable on a node that is powered off:
   rigging happens before the scopes come on, and the tile is there from the
   moment the id is known. */
function nodeSw(id) {
  var n = (S && S.nodes) ? S.nodes[String(id)] : null;
  return {
    stream: !n || n.stream !== false,
    logos: !n || n.logos !== false,
    artists: !n || n.artists !== false
  };
}

function toggleNodeSw(id, key) {
  var f = nodeSw(id);
  var body = {id: id};
  body[key] = !f[key];
  /* Paint it now: the poll is 1 Hz and a switch that looks dead for a second
     gets pressed twice. */
  if (S) {
    if (!S.nodes) S.nodes = {};
    var cur = S.nodes[String(id)] || {stream: true, logos: true, artists: true};
    cur[key] = body[key];
    S.nodes[String(id)] = cur;
    patchTileSwitches(id);
    /* The sheet is built once on open, so the buttons behind the tap would
       keep their old highlight until it was closed and reopened. */
    if (activeSlaveSheet === id) openSlaveSheet(id);
  }
  post("/api/node", body);
}

function patchTileSwitches(id) {
  var tile = slaveTiles[id];
  if (!tile) return;
  var f = nodeSw(id);
  var sws = tile.querySelectorAll('.tile-sw button');
  for (var i = 0; i < sws.length; i++) {
    var on = f[sws[i].getAttribute('data-sw')];
    sws[i].classList.toggle('on', !!on);
    sws[i].classList.toggle('off', !on);
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
  var isFig = p.kind === 'figure';
  document.getElementById('ratiorow').style.display = (p.kind === 'lissajous') ? 'flex' : 'none';
  document.getElementById('petalsrow').style.display = (p.kind === 'rose') ? 'flex' : 'none';
  document.getElementById('textrow').style.display = isText ? 'flex' : 'none';
  document.getElementById('figurerow').style.display = isFig ? 'flex' : 'none';
  /* Pulse works off the point table, so it belongs to both table kinds. */
  document.getElementById('pulsedepthrow').style.display = (isText || isFig) ? 'flex' : 'none';
  document.getElementById('puleraterow').style.display = (isText || isFig) ? 'flex' : 'none';
  /* spinrow deliberately absent here — it stays hidden on every kind. */

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
  syncFigures(p);
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

  /* Point-table preview fetch */
  if (isTableKind(p.kind) && p.sver !== TP.ver) fetchPreview();

  /* "Update current" is the only save path with the burst guard on it, and it
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
  renderSetupRig();
  renderDiagnostics();
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
  var def = (p.kind === 'text' ? p.text.split('\\n')[0]
             : p.kind === 'figure' ? (p.figure || 'figure') : p.kind).slice(0, 24);
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
  if (S.cycle) {
    showToast('Cannot update: a cycle burst is on air. Update would save the logo, not the artist.', 5000, 'error');
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

/* === LOGO / ARTIST SETTINGS SHEET ===
   A cycle is a periodic burst: every N minutes, A for show_s, an optional
   mic pause, B -- then the microphone until the next one. Which pair runs is
   a choice for artists (three b2b line-ups on the card) and usually a
   formality for logos. The page never invents the order -- it draws the
   group's own seq, the exact list the controller will play. */
function openCycleSheet(group) {
  activeCycleGroup = group;
  document.getElementById('cycle-title').textContent =
    group === 'logos' ? 'LOGO SETTINGS' : 'ARTIST SETTINGS';
  document.getElementById('cycle-sheet').classList.add('active');
  cycClearEdit();
  renderCycleSheet(true);
}

function closeCycleSheet() {
  /* Commit before the group goes away, or shutting the sheet on a number you
     just typed throws it away. setCycle reads activeCycleGroup up front. */
  if (cycDirty) applyCycleTimes();
  document.getElementById('cycle-sheet').classList.remove('active');
  activeCycleGroup = null;
}

function cycleCfg() {
  if (!S || !S.cycles || !activeCycleGroup) return null;
  return S.cycles[activeCycleGroup];
}

function cycClearEdit() {
  cycDirty = false;
  if (cycCommit) { clearTimeout(cycCommit); cycCommit = null; }
}

/* An edited number belongs to the operator until it is committed. The 1 Hz
   poll used to overwrite these three fields the moment focus left them, so a
   value typed and not Applied vanished with nothing said -- it read as the
   panel refusing the edit. Mark the field dirty (renderCycleSheet then leaves
   it alone) and send it shortly after the typing stops. */
function cycTouched() {
  cycDirty = true;
  if (cycCommit) clearTimeout(cycCommit);
  cycCommit = setTimeout(function() { applyCycleTimes(true); }, 700);
}

function setCycle(patch) {
  if (!activeCycleGroup) return;
  patch.group = activeCycleGroup;
  post("/api/cycle", patch).then(function() { return refresh(); })
    .then(function() { cycClearEdit(); renderCycleSheet(true); });
}

function applyCycleTimes(auto) {
  if (cycCommit) { clearTimeout(cycCommit); cycCommit = null; }
  var every = parseFloat(document.getElementById('cyc-every').value);
  var show = parseFloat(document.getElementById('cyc-show').value);
  var pause = parseFloat(document.getElementById('cyc-pause').value);
  if (!isFinite(every) || !isFinite(show) || !isFinite(pause)) {
    /* Half-typed: the field is empty because a number is being replaced.
       Posting now would send a 0 and get back whatever the clamp allows. On
       the timer, wait. On a blur or Apply, put the stored value back. */
    if (auto) { cycDirty = true; return; }
    cycClearEdit();
    renderCycleSheet(true);
    return;
  }
  setCycle({show_s: show, pause_s: pause, every_s: Math.round(every * 60)});
}

function addCyclePair() {
  var c = cycleCfg();
  if (!c) return;
  var pairs = (c.pairs || []).map(function(p) { return [p[0], p[1]]; });
  pairs.push(['', '']);
  setCycle({pairs: pairs});
}

function setCyclePair(i, slot, name) {
  var c = cycleCfg();
  if (!c) return;
  var pairs = (c.pairs || []).map(function(p) { return [p[0], p[1]]; });
  if (!pairs[i]) return;
  pairs[i][slot] = name;
  setCycle({pairs: pairs});
}

function delCyclePair(i) {
  var c = cycleCfg();
  if (!c) return;
  var pairs = (c.pairs || []).map(function(p) { return [p[0], p[1]]; });
  pairs.splice(i, 1);
  setCycle({pairs: pairs});
}

function presetOptions(sel) {
  var names = (S && S.presets) ? S.presets : [];
  var html = '<option value=""' + (sel ? '' : ' selected') + '>— none —</option>';
  for (var i = 0; i < names.length; i++) {
    html += '<option' + (names[i] === sel ? ' selected' : '') + '>' + esc(names[i]) + '</option>';
  }
  /* A pair naming a preset that has since been deleted or renamed keeps its
     slot visible instead of silently reading as "none". */
  if (sel && names.indexOf(sel) < 0) {
    html += '<option selected>' + esc(sel) + '</option>';
  }
  return html;
}

function renderCycleSheet(force) {
  if (!activeCycleGroup) return;
  var c = cycleCfg();
  if (!c) return;
  var typing = document.activeElement
    && document.activeElement.closest('#cycle-sheet')
    && document.activeElement.tagName === 'INPUT';
  var seg = document.getElementById('cyc-onoff').querySelectorAll('button');
  seg[0].classList.toggle('on', !!c.enabled);
  seg[1].classList.toggle('on', !c.enabled);
  if (force || (!typing && !cycDirty)) {
    document.getElementById('cyc-show').value = c.show_s;
    document.getElementById('cyc-pause').value = c.pause_s;
    document.getElementById('cyc-every').value = +(c.every_s / 60).toFixed(2);
  }

  var cyc = S.cycle;
  var st = '';
  if (!c.enabled) {
    st = 'off';
  } else if (!c.seq.length) {
    st = '';
  } else if (!S.stream_on) {
    st = 'frozen — feed is SILENT';
  } else if (cyc && cyc.group === activeCycleGroup) {
    st = 'ON AIR: "' + esc(cyc.preset) + '", ' + fmtLeft(cyc.left_s) + ' left';
  } else if (c.running) {
    st = 'on air: mic pause, ' + fmtLeft(cyc ? cyc.left_s : 0) + ' left';
  } else if (c.next_in != null) {
    /* The countdown is the whole point of a period: without it the operator
       cannot tell a cycle that is waiting from one that is broken. */
    st = 'next burst in ' + fmtLeft(c.next_in)
       + (cyc ? ' (' + cyc.group + ' on air now)' : ' — mic');
  }
  document.getElementById('cyc-state').textContent = st;

  var pairs = c.pairs || [];
  var html = '';
  for (var i = 0; i < pairs.length; i++) {
    var playing = (c.idx === i);
    html += '<div class="row" style="margin-top:6px">' +
      '<button class="' + (playing ? 'on' : '') + '" style="width:88px" onclick="setCycle({idx:' + i + '})">' +
        (playing ? 'PLAYING' : 'select') + '</button>' +
      '<select onchange="setCyclePair(' + i + ',0,this.value)">' + presetOptions(pairs[i][0]) + '</select>' +
      '<span style="color:var(--dim)">then</span>' +
      '<select onchange="setCyclePair(' + i + ',1,this.value)">' + presetOptions(pairs[i][1]) + '</select>' +
      '<button class="danger" onclick="delCyclePair(' + i + ')">✕</button>' +
      '</div>';
  }
  if (!pairs.length) {
    html = '<div style="color:var(--dim);padding:8px">No pairs — add one</div>';
  }
  document.getElementById('cyc-pairs').innerHTML = html;
  document.getElementById('cyc-addpair').style.display =
    pairs.length >= 6 ? 'none' : '';

  var order = c.seq || [];
  var every = c.every_s >= 60 ? +(c.every_s / 60).toFixed(2) + ' min'
                              : c.every_s + 's';
  document.getElementById('cyc-order').innerHTML = (c.enabled && order.length)
    ? 'every ' + every + ': ' + order.map(function(s) {
        return s.group === '' ? '<b>mic ' + s.secs + 's</b>'
                              : esc(s.preset) + ' ' + s.secs + 's';
      }).join(' → ') + ' → <b>mic</b> until the next one'
      /* Say the offset out loud. A cycle quietly running 32 s off the grid
         looks like a bug the first time someone times it with a phone. */
      + (c.phase ? '<br><span style="color:var(--dim);font-size:11px">offset +'
                   + c.phase + 's, so the two cycles never collide</span>' : '')
    : (c.enabled ? '<span style="color:var(--bad)">nothing to run — the selected pair names no preset in the set list</span>' : '');
}

/* One line on the show screen: what the cycle is doing, without opening
   anything. A rotating rig with no visible clock is how a preset gets loaded
   by hand ten seconds before the cycle replaces it. */
function renderCycleLine() {
  var el = document.getElementById('cycle-line');
  if (!el) return;
  var cyc = S.cycle;
  var cs = S.cycles || {};
  var on = cs.logos && (cs.logos.enabled || cs.artists.enabled);
  if (!on) { el.innerHTML = ''; return; }
  if (!S.stream_on) { el.innerHTML = 'cycles frozen — feed is SILENT'; return; }
  if (cyc) {
    el.innerHTML = (cyc.group === ''
        ? '<b>mic pause</b>'
        : cyc.group + ': <b>' + esc(cyc.preset) + '</b>')
      + ' · ' + fmtLeft(cyc.left_s) + ' left';
    return;
  }
  var next = nextBurst();
  el.innerHTML = next
    ? '<b>mic</b> · ' + next.g + ' in ' + fmtLeft(next.left)
    : '<b>mic</b>';
}

/* The group whose burst lands next, {g, left}, or null. Name the group, not
   just a number: with two cycles running the operator wants to know which
   one is coming. */
function nextBurst() {
  var cs = S.cycles || {};
  var next = null;
  ['logos', 'artists'].forEach(function(g) {
    var c = cs[g];
    if (!c || !c.enabled || !c.seq.length || c.next_in == null) return;
    if (!next || c.next_in < next.left) next = {g: g, left: c.next_in};
  });
  return next;
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
    var sw = nodeSw(s.id);

    var card = document.createElement('div');
    card.className = 'panel';
    card.style.marginBottom = '8px';
    card.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
        '<strong>SLAVE ' + s.id + '</strong> <small style="color:var(--dim)">' + s.ip + '</small>' +
      '</div>' +
      '<div class="verdict ' + verdict.cls + '" style="margin-bottom:8px">' + verdict.txt + '</div>' +
      '<div class="row">' +
        '<label>in the show</label>' +
        '<div class="source-3way">' +
          '<button class="' + (sw.stream ? 'active' : '') + '" onclick="toggleNodeSw(' + s.id + ',\\'stream\\')">STREAMING</button>' +
          '<button class="' + (sw.logos ? 'active' : '') + '" onclick="toggleNodeSw(' + s.id + ',\\'logos\\')">LOGOS</button>' +
          '<button class="' + (sw.artists ? 'active' : '') + '" onclick="toggleNodeSw(' + s.id + ',\\'artists\\')">ARTISTS</button>' +
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
            elif self.path == "/api/shapepreview":
                with state.lock:
                    tbl, ver = state.shape_tbl, state.shape_ver
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
                # The table now depends on the kind too, so a kind switch is
                # itself a rebuild: text -> figure must load the artwork, and
                # anything -> circle must drop the table it is not drawing.
                rebuild = bool({"text", "font", "figure", "kind"} & set(body))
                # Only a rebuild needs the mutex; slider POSTs must not queue
                # behind a 36 ms font parse.
                new_tbl = None
                with state.rebuild_lock if rebuild else nullcontext():
                    if rebuild:
                        with state.lock:
                            kind = state.kind
                            text, font, figure = (state.text, state.font,
                                                  state.figure)
                        if body.get("kind") in PATTERN_KINDS:
                            kind = body["kind"]
                        if "text" in body:
                            text = sanitize_text(body["text"])
                        if body.get("font") in TEXT_FONTS:
                            font = body["font"]
                        if "figure" in body:
                            figure = sanitize_figure(body["figure"])
                        # Rebuild OUTSIDE state.lock: font parsing and the
                        # table read must never stall the 5 ms stream pacing.
                        new_tbl = ((text, font, figure)
                                   + build_shape(kind, text, font, figure))
                    self._apply_pattern(state, body, new_tbl)
                if set(body) - {"stream"}:   # a real pattern edit, not a mute
                    self._takeover(state)
                self._json({"ok": True})
            elif self.path == "/api/preset":
                return self._preset(state, body)
            elif self.path == "/api/node":
                return self._node(state, cmds, body)
            elif self.path == "/api/cycle":
                return self._cycle(state, cmds, body)
            elif self.path == "/api/cmd":
                ip, cmd_obj = body.get("ip"), body.get("cmd")
                if not ip or not isinstance(cmd_obj, dict):
                    return self._json({"err": "need ip + cmd"}, 400)
                cmds.send(ip, cmd_obj)
                self._takeover(state)  # cut any burst so the change persists
                self._json({"ok": True})
            else:
                self._json({"err": "not found"}, 404)

        def _apply_pattern(self, state, body, new_tbl):
            with state.lock:
                if body.get("kind") in PATTERN_KINDS:
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
                    if "figure" in body:
                        state.figure = new_tbl[2]
                    state.shape_tbl, state.shape_rmax = new_tbl[3], new_tbl[4]
                    state.shape_ver += 1
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
                    # F0: no saving a logo over an artist's preset mid-burst
                    if state.cycle_run is not None:
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
                                "flip_y": state.flip_y,
                                "figure": state.figure}
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
                        {"err": "cannot save — a cycle burst is on air"}, 409)
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
                plist = clist = None
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
                                plist = list(state.presets)
                                # Rewritten in memory but never written out,
                                # a pair came back after a restart naming a
                                # preset that no longer exists: the burst
                                # would quietly play one name instead of two.
                                touched = False
                                for c in state.cycles.values():
                                    for pair in c["pairs"]:
                                        for i, n in enumerate(pair):
                                            if n == name:
                                                pair[i] = new_name
                                                touched = True
                                clist = dict(state.cycles) if touched else None
                                break
                if clash:
                    return self._json({"err": "name already used"}, 409)
                if plist is None:
                    return self._json({"err": "no such preset"}, 404)
                save_presets(plist)
                if clist is not None:
                    save_cycles(clist)
                return self._json({"ok": True})
            if op == "delete":
                gone = []
                with state.lock:
                    state.presets = [p for p in state.presets
                                     if p["name"] != name]
                    plist = list(state.presets)
                    # A pair naming a preset that is gone plays half a burst.
                    # Clear the slot and say so, rather than leaving the card
                    # promising a name that is never coming.
                    for g, c in state.cycles.items():
                        for pair in c["pairs"]:
                            for i, n in enumerate(pair):
                                if n == name:
                                    pair[i] = ""
                                    gone.append(g)
                    clist = dict(state.cycles) if gone else None
                save_presets(plist)
                if clist is not None:
                    save_cycles(clist)
                return self._json({"ok": True, "note": None if not gone else
                                   f"cleared from {len(gone)} "
                                   f"{'pair' if len(gone) == 1 else 'pairs'}"})
            with state.lock:  # load
                p = next((dict(p) for p in state.presets
                          if p["name"] == name), None)
            if p is None:
                return self._json({"err": "no such preset"}, 404)
            apply_preset(state, p)   # same mutex dance as /api/pattern
            self._takeover(state)
            return self._json({"ok": True})

        def _takeover(self, state):
            """A hand on the panel cuts any cycle burst short: what the
            operator just set stays up. Silently reverting them ten seconds
            later is worse than a cycle missing one burst.

            Called unconditionally. end_burst's read-and-clear is atomic and
            already a no-op when nothing is on air, whereas an unlocked `if`
            here would miss a burst that starts between the test and the call
            and then revert the operator's pattern a step later."""
            end_burst(state, restore_pattern=False)

        def _node(self, state, cmds, body):
            """Flip one node's switches. {"id": 100, "logos": false}

            Only the keys present move, so the page can send one switch
            without echoing a stale copy of the other two back over a change
            someone else just made from a second phone.
            """
            try:
                sid = int(body.get("id"))
            except (TypeError, ValueError):
                return self._json({"err": "need a slave id"}, 400)
            if not 0 <= sid <= 255:
                return self._json({"err": "id out of range"}, 400)
            with state.lock:
                f = dict(state.node_flags.get(sid) or NODE_DEFAULTS)
                for k in NODE_DEFAULTS:
                    if k in body:
                        f[k] = bool(body[k])
                state.node_flags[sid] = f
                nodes = {k: dict(v) for k, v in state.node_flags.items()}
            save_nodes(nodes)
            # Do not wait up to a tick to obey the tap: the operator is
            # looking at the scope while they press it.
            reconcile_modes(state, cmds)
            return self._json({"ok": True, "id": sid, "flags": f})

        def _cycle(self, state, cmds, body):
            """Edit one pair cycle. {"group": "artists", "enabled": true,
            "every_s": 300, "show_s": 30, "pause_s": 10, "idx": 1,
            "pairs": [[a, b], ...]}

            "play": true also puts the selected pair on the scopes now -- the
            show screen's pair pick. While any cycle runs the rig rests on
            the mic, so loading a name as the panel's pattern would show
            nothing: the pair plays as a burst instead. With no cycle running
            the panel's pattern is what streams, so the pair's first name is
            loaded there, as a preset tap would. The reply says which
            ("played" / "loaded"), so the page knows what is on air."""
            group = body.get("group")
            if group not in CYCLE_GROUPS:
                return self._json({"err": "need group logos|artists"}, 400)
            played, load, note = False, None, None
            with state.lock:
                cur = dict(state.cycles[group])
                cur.update({k: body[k] for k in
                            ("enabled", "show_s", "pause_s", "every_s",
                             "idx", "pairs") if k in body})
                c = clean_cycle(cur, group)
                state.cycles[group] = c
                # Let advance_cycle notice the change on its own terms: it
                # compares the grid's inputs, restages both phases and
                # re-arms, so a period edited at 4m59s does not fire as you
                # let go.
                cycles = {g: dict(v) for g, v in state.cycles.items()}
                if body.get("play"):
                    names = [p["name"] for p in state.presets]
                    steps = burst_steps(dict(c, enabled=True), group, names)
                    on_mic = any(burst_steps(v, g, names)
                                 for g, v in state.cycles.items())
                    if not steps:
                        note = "that pair names no preset in the set list"
                    elif not on_mic:
                        load = next((dict(p) for p in state.presets
                                     if p["name"] == steps[0]["preset"]),
                                    None)
                    elif state.stream_on:
                        state.cycle_play = group
                        played = True
                    else:
                        note = "pair selected — the feed is SILENT, so " \
                               "nothing played"
            save_cycles(cycles)
            if load is not None:
                apply_preset(state, load)   # same path as a preset tap
                self._takeover(state)
            advance_cycle(state, 0)
            reconcile_modes(state, cmds)
            return self._json({"ok": True, "group": group, "cycle": c,
                               "played": played,
                               "loaded": load["name"] if load else None,
                               "note": note})


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
    threading.Thread(target=policy_loop, args=(state, cmds),
                     daemon=True).start()

    print(f"[controller] web on :{args.http_port}, iface={args.iface_ip}, "
          f"lead={LEAD_US/1000:.0f}ms", flush=True)
    if state.kind == "text":
        shown = f"text {state.text!r} {state.font}"
    elif state.kind == "figure":
        # Say it here or nowhere: a preset naming artwork this board does not
        # carry draws a circle, and the journal is the only place that can
        # tell you why at 03:00 with no dashboard open.
        shown = (f"figure {state.figure!r}"
                 + ("" if state.shape_tbl else " — NOT ON THIS BOARD, drawing a circle"))
    else:
        shown = state.kind
    print(f"[controller] pattern {shown} {state.freq:g} Hz amp {state.amp:.2f}"
          + ("" if load_live() else "  (defaults — no saved state yet)"),
          flush=True)
    # Both of these decide what a scope draws, and both survive a reboot. A
    # rig that came back with a cycle running or a node switched out of the
    # show, and said neither, is twenty minutes of cable-checking.
    for g, c in state.cycles.items():
        if c["enabled"] and c["pairs"]:
            pair = c["pairs"][min(c["idx"], len(c["pairs"]) - 1)]
            print(f"[controller] {g} cycle on: {' / '.join(pair)}, "
                  f"{c['show_s']}s each every {c['every_s']}s"
                  + (f" (+{state.cycle_phase[g]}s offset)"
                     if state.cycle_phase.get(g) else "")
                  + (f", {c['pause_s']}s mic between" if c["pause_s"] else "")
                  + " — mic in between", flush=True)
    off = [f"{sid}({','.join(k for k, v in f.items() if not v)})"
           for sid, f in sorted(state.node_flags.items())
           if not all(f.values())]
    if off:
        print("[controller] node switches off: " + " ".join(off), flush=True)
    stream_loop(state, args.iface_ip)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
