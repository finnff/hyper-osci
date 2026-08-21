#!/usr/bin/env python3
"""Regression checks for the per-node switches and the pair cycles.

Run ON THE BOARD (see tests/README.md): it imports hype_controller, which
parses Hershey fonts at import time and those only exist there.

What this is defending, in one sentence each:
  - a node switched out of a group draws its own mic while that group is on
    air, and nothing else can quietly put it back;
  - the cycle walks A, pause, B, pause and freezes rather than drifting when
    the feed is muted or an ident interrupts;
  - a pair naming a preset that is not in the set list is skipped instead of
    stalling the rotation on a name nobody can see.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("HC_DIR", HERE))
_TD = tempfile.mkdtemp(prefix="hcpolicy-")
os.environ["HYPE_STATE"] = os.path.join(_TD, "state.json")
os.environ["HYPE_PRESETS"] = os.path.join(_TD, "presets.json")
os.environ["HYPE_TIMERS"] = os.path.join(_TD, "timers.json")
os.environ["HYPE_NODES"] = os.path.join(_TD, "nodes.json")
os.environ["HYPE_CYCLES"] = os.path.join(_TD, "cycles.json")
os.environ["HYPE_FIGURES"] = os.path.join(_TD, "figures")
import hype_controller as hc  # noqa: E402

FAIL = []


def ok(cond, label, extra=""):
    print(("  PASS " if cond else "  FAIL ") + label
          + (" — " + str(extra) if extra else ""))
    if not cond:
        FAIL.append(label)


class StubCmds:
    def __init__(self):
        self.sent = []

    def send(self, ip, cmd_obj):
        self.sent.append((ip, cmd_obj))

    def modes(self):
        return {ip: c["mode"] for ip, c in self.sent
                if c.get("cmd") == "set_mode"}


def write_tbl(name, pts):
    """A minimal baked figure, so a cycle step installs a real table."""
    import struct
    os.makedirs(hc.FIGURE_DIR, exist_ok=True)
    with open(os.path.join(hc.FIGURE_DIR, name + ".tbl"), "wb") as f:
        f.write(hc.FIGURE_MAGIC + struct.pack("<I", len(pts)))
        for x, y in pts:
            f.write(struct.pack("<hh", int(x * 32767), int(y * 32767)))


write_tbl("hyperwaves", [(0.9, 0.0), (0.0, 0.9), (-0.9, 0.0), (0.0, -0.9)])
write_tbl("flux-collective", [(0.5, 0.5), (-0.5, 0.5), (-0.5, -0.5)])


def fresh():
    """Three slaves, all reporting local, and a four-preset set list.

    Switches and cycles are reset to pristine defaults rather than whatever
    an earlier section persisted: State() loads both from disk, and a fixture
    that inherits the previous test's file is a fixture that lies.
    """
    st = hc.State("circle")
    st.node_flags = {}
    st.cycles = {g: hc.clean_cycle({}, g) for g in hc.CYCLE_GROUPS}
    st.slaves = {
        "10.0.0.1": {"ip": "10.0.0.1", "id": 100, "mode": 0, "last_us": 0},
        "10.0.0.2": {"ip": "10.0.0.2", "id": 121, "mode": 0, "last_us": 0},
        "10.0.0.3": {"ip": "10.0.0.3", "id": 164, "mode": 0, "last_us": 0},
    }
    st.presets = [
        hc.clean_preset({"name": "HYPERWAVES", "kind": "figure",
                         "figure": "hyperwaves"}),
        hc.clean_preset({"name": "Flux Collective", "kind": "figure",
                         "figure": "flux-collective"}),
        hc.clean_preset({"name": "Anemi", "kind": "text", "text": "Anemi"}),
        hc.clean_preset({"name": "HyperLili", "kind": "text",
                         "text": "HyperLili"}),
    ]
    return st


def set_live(st, kind):
    with st.lock:
        st.kind = kind


# ------------------------------------------------------------------- nodes
print("[policy] node switches: defaults, filtering, round-trip")
ok(hc.clean_node({}) == {"stream": True, "logos": True, "artists": True},
   "an unset node is fully in the show", json.dumps(hc.clean_node({})))
ok(hc.clean_node({"logos": 0})["logos"] is False
   and hc.clean_node({"logos": 0})["artists"] is True,
   "one switch off leaves the others alone")
ok(hc.clean_node("junk") == hc.NODE_DEFAULTS,
   "a junk entry reads as the defaults, not a crash")
hc.save_nodes({100: {"stream": False, "logos": True, "artists": True}})
back = hc.load_nodes()
ok(back == {100: {"stream": False, "logos": True, "artists": True}},
   "a node file round-trips with an int key", json.dumps(back))
with open(os.environ["HYPE_NODES"], "w") as f:
    json.dump({"100": {"logos": False}, "nope": {}, "999": {}}, f)
back = hc.load_nodes()
ok(list(back) == [100] and back[100] == {"stream": True, "logos": False,
                                         "artists": True},
   "a non-numeric key and an out-of-range id are dropped, the survivor fills "
   "from the defaults", json.dumps(back))

# ------------------------------------------------------------ desired modes
print("\n[policy] what each switch does to a node's draw mode")
st = fresh()
set_live(st, "figure")
ok(set(hc.desired_modes(st).values()) == {1},
   "with every switch on, the whole rig is on the stream")

st.node_flags[100] = {"stream": False, "logos": True, "artists": True}
ok(hc.desired_modes(st)["10.0.0.1"] == 0,
   "streaming off takes the node off the stream whatever is on air")
set_live(st, "text")
ok(hc.desired_modes(st)["10.0.0.1"] == 0,
   "and it stays off for the other group too")

st = fresh()
st.node_flags[121] = {"stream": True, "logos": False, "artists": True}
set_live(st, "figure")
ok(hc.desired_modes(st) == {"10.0.0.1": 1, "10.0.0.2": 0, "10.0.0.3": 1},
   "logos off: that node alone sits on its mic while a logo is on air",
   str(hc.desired_modes(st)))
set_live(st, "text")
ok(hc.desired_modes(st)["10.0.0.2"] == 1,
   "and it comes straight back for an artist name")
set_live(st, "circle")
ok(hc.desired_modes(st)["10.0.0.2"] == 1,
   "a test pattern is neither group, so only the streaming switch applies")

st = fresh()
st.node_flags[164] = {"stream": True, "logos": True, "artists": False}
set_live(st, "text")
ok(hc.desired_modes(st)["10.0.0.3"] == 0,
   "artists off: the name does not appear on that scope")

print("\n[policy] a pause is the mic, for everyone")
st = fresh()
set_live(st, "figure")
st.cycle_step = {"group": None, "preset": "", "secs": 10}
ok(set(hc.desired_modes(st).values()) == {0},
   "every node is on its own mic during a cycle pause",
   str(hc.desired_modes(st)))

# --------------------------------------------------------------- reconcile
print("\n[policy] reconcile sends the difference, and only once")
st, cmds = fresh(), StubCmds()
set_live(st, "figure")
sent = hc.reconcile_modes(st, cmds)
ok(len(sent) == 3 and cmds.modes() == {"10.0.0.1": "network",
                                       "10.0.0.2": "network",
                                       "10.0.0.3": "network"},
   "three slaves reporting local are all commanded onto the stream",
   str(cmds.sent))
cmds.sent.clear()
hc.reconcile_modes(st, cmds)
ok(cmds.sent == [],
   "a second pass inside the beacon window repeats nothing", str(cmds.sent))
# The beacon lands: the slaves now report what was asked of them.
for s in st.slaves.values():
    s["mode"] = 1
cmds.sent.clear()
hc.reconcile_modes(st, cmds)
ok(cmds.sent == [] and st.mode_sent == {},
   "and once they agree, the pending record is dropped", str(st.mode_sent))

print("\n[policy] a node that drifts is pulled back")
st.slaves["10.0.0.2"]["mode"] = 2          # its own button cycled to hybrid
cmds.sent.clear()
hc.reconcile_modes(st, cmds)
ok(cmds.modes() == {"10.0.0.2": "network"},
   "hybrid is not a state policy accepts — the node is corrected",
   str(cmds.sent))
st.slaves["10.0.0.2"]["mode"] = 1
del st.slaves["10.0.0.3"]                  # slave dropped off the rig
cmds.sent.clear()
hc.reconcile_modes(st, cmds)
ok("10.0.0.3" not in st.mode_sent,
   "a slave that goes away leaves no pending command behind",
   str(st.mode_sent))

print("\n[policy] flipping a switch moves that node and nobody else")
st, cmds = fresh(), StubCmds()
set_live(st, "figure")
for s in st.slaves.values():
    s["mode"] = 1
st.node_flags[100] = {"stream": True, "logos": False, "artists": True}
hc.reconcile_modes(st, cmds)
ok(cmds.modes() == {"10.0.0.1": "local"},
   "one command, to the one node whose switch moved", str(cmds.sent))

# ------------------------------------------------------------------ cycles
print("\n[cycle] clamps and the seeded config")
c = hc.clean_cycle({}, "artists")
ok(c["enabled"] is False and c["show_s"] == 30 and c["pause_s"] == 0,
   "an empty config fills from the defaults, and starts OFF", json.dumps(c))
ok(len(c["pairs"]) == 3, "the artists seed carries the three b2b pairs",
   json.dumps(c["pairs"]))
ok(hc.clean_cycle({"show_s": 9999}, "logos")["show_s"] == 600,
   "show clamps to 600 s")
ok(hc.clean_cycle({"show_s": 0}, "logos")["show_s"] == 3,
   "and up to 3 s — a one-second logo is a flicker, not a logo")
ok(hc.clean_cycle({"idx": 9, "pairs": [["A", "B"]]}, "logos")["idx"] == 0,
   "an index past the end of the pair list is clamped")
ok(hc.clean_cycle({"pairs": [["A", ""], ["", ""], "junk"]}, "logos")["pairs"]
   == [["A", ""]],
   "an empty pair and a junk entry are dropped, a half pair is kept")

print("\n[cycle] the running order")
cyc = hc.load_cycles()
cyc["logos"].update(enabled=True, show_s=20, pause_s=5,
                    pairs=[["HYPERWAVES", "Flux Collective"]], idx=0)
names = ["HYPERWAVES", "Flux Collective", "Anemi", "HyperLili"]
seq = hc.cycle_sequence(cyc, names)
ok([(s["group"], s["preset"], s["secs"]) for s in seq]
   == [("logos", "HYPERWAVES", 20), (None, "", 5),
       ("logos", "Flux Collective", 20), (None, "", 5)],
   "A, pause, B, pause", str(seq))
cyc["logos"]["pause_s"] = 0
ok(len(hc.cycle_sequence(cyc, names)) == 2,
   "no pause seconds means no pause steps")
cyc["artists"].update(enabled=True, show_s=40, pause_s=0,
                      pairs=[["Anemi", "HyperLili"], ["Gone", "Missing"]],
                      idx=0)
seq = hc.cycle_sequence(cyc, names)
ok([s["preset"] for s in seq]
   == ["HYPERWAVES", "Flux Collective", "Anemi", "HyperLili"],
   "both cycles on: the logo pair runs, then the artist pair", str(seq))
cyc["artists"]["idx"] = 1
ok([s["preset"] for s in hc.cycle_sequence(cyc, names)]
   == ["HYPERWAVES", "Flux Collective"],
   "a pair naming presets that are not in the set list is skipped, not stuck")
cyc["logos"]["enabled"] = False
ok([s["group"] for s in hc.cycle_sequence(cyc, names)] == [],
   "switching a group off takes its steps out of the order")

# ------------------------------------------------------------ cycle clock
print("\n[cycle] stepping, freezing, restarting")
st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=2,
                          pairs=[["HYPERWAVES", "Flux Collective"]], idx=0)
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "HYPERWAVES" and st.kind == "figure"
   and st.figure == "hyperwaves",
   "the first step goes on air immediately", f"{st.kind} {st.figure}")
ok(hc.advance_cycle(st, 250_000) is None,
   "and the next tick does nothing while its seconds are unspent")

st.cycle_until_us = hc.mono_us() - 1          # as if the 3 s had passed
step = hc.advance_cycle(st, 250_000)
ok(step and step["group"] is None, "then the mic pause", str(step))
ok(st.figure == "hyperwaves",
   "which does not touch the pattern — nobody is on the stream to see it")
ok(set(hc.desired_modes(st).values()) == {0},
   "and every node is on its mic for it")

st.cycle_until_us = hc.mono_us() - 1
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "Flux Collective"
   and st.figure == "flux-collective", "then the B side", str(step))
st.cycle_until_us = hc.mono_us() - 1
hc.advance_cycle(st, 250_000)                 # pause
st.cycle_until_us = hc.mono_us() - 1
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "HYPERWAVES", "and round again", str(step))

print("\n[cycle] frozen by a mute and by an ident")
st.stream_on = False
st.cycle_until_us = hc.mono_us() - 1
ok(hc.advance_cycle(st, 250_000) is None,
   "a silent feed does not advance the cycle")
ok(st.cycle_until_us > hc.mono_us() - 1,
   "and the deadline is pushed out instead of falling behind",
   f"+{st.cycle_until_us - (hc.mono_us() - 1)} us of credit")
st.stream_on = True
st.timer_hold = {"id": 1, "preset": "IDENT", "targets": [],
                 "until_us": hc.mono_us() + 10_000_000, "pattern": {}}
st.cycle_until_us = hc.mono_us() - 1
ok(hc.advance_cycle(st, 250_000) is None,
   "nor does a cycle step land on top of an ident")
st.timer_hold = None

print("\n[cycle] a config change restarts the order")
st.cycle_until_us = hc.mono_us() + 60_000_000
st.cycles["logos"]["pairs"] = [["Flux Collective", "HYPERWAVES"]]
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "Flux Collective" and st.cycle_pos == 0,
   "swapping the pair starts from its A side rather than mid-rotation",
   str(step))
st.cycles["logos"]["enabled"] = False
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_step is None,
   "switching the cycle off clears the step, so the pause rule stops "
   "holding the rig on mic", str(st.cycle_step))

# --------------------------------------------------------------------- HTTP
print("\n[policy] over HTTP: the switches and the cycle persist")
import threading                                            # noqa: E402
import urllib.error                                         # noqa: E402
import urllib.request                                       # noqa: E402
from http.server import ThreadingHTTPServer                 # noqa: E402

st, cmds = fresh(), StubCmds()
set_live(st, "figure")
for s in st.slaves.values():
    s["mode"] = 1
_srv = ThreadingHTTPServer(("127.0.0.1", 0), hc.make_http_handler(st, cmds))
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_base = "http://127.0.0.1:%d" % _srv.server_address[1]


def post(path, obj):
    req = urllib.request.Request(_base + path, json.dumps(obj).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


code, body = post("/api/node", {"id": 121, "logos": False})
ok(code == 200 and body["flags"] == {"stream": True, "logos": False,
                                     "artists": True},
   "one key in the body moves one switch", json.dumps(body))
ok(cmds.modes() == {"10.0.0.2": "local"},
   "and the node is moved on the tap, not on the next tick", str(cmds.sent))
ok(hc.load_nodes()[121]["logos"] is False, "the switch is persisted")

code, body = post("/api/node", {"id": 121, "artists": False})
ok(body["flags"] == {"stream": True, "logos": False, "artists": False},
   "a second call does not resurrect the first switch", json.dumps(body))
code, _ = post("/api/node", {"id": 300})
ok(code == 400, "an id outside the protocol's range is refused")

code, body = post("/api/cycle", {"group": "logos", "enabled": True,
                                 "show_s": 45, "pause_s": 9,
                                 "pairs": [["HYPERWAVES", "Flux Collective"]]})
ok(code == 200 and body["cycle"]["show_s"] == 45
   and body["cycle"]["pause_s"] == 9,
   "the cycle is saved and echoed back clamped", json.dumps(body["cycle"]))
ok(hc.load_cycles()["logos"]["enabled"] is True, "and persisted")
ok(st.kind == "figure" and st.figure == "hyperwaves",
   "switching it on puts its A side on air without waiting for a tick",
   f"{st.kind} {st.figure}")
code, _ = post("/api/cycle", {"group": "nope"})
ok(code == 400, "an unknown group is refused")

snap = st.snapshot()
ok(snap["nodes"]["121"]["logos"] is False,
   "the state feed carries the switches for the page", json.dumps(snap["nodes"]))
ok(snap["cycle"] and snap["cycle"]["preset"] == "HYPERWAVES"
   and len(snap["cycle"]["seq"]) == 4,
   "and the running order it draws", json.dumps(snap["cycle"]))
_srv.shutdown()

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
