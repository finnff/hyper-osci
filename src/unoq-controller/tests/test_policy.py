#!/usr/bin/env python3
"""Regression checks for the per-node switches and the pair cycles.

Run ON THE BOARD (see tests/README.md): it imports hype_controller, which
parses Hershey fonts at import time and those only exist there.

What this is defending, in one sentence each:
  - a node switched out of a group draws its own mic while that group is on
    air, and nothing else can quietly put it back;
  - the microphone is the resting state, and a burst is a periodic
    interruption that hands the stream straight back;
  - the two grids are offset so their bursts miss, and a slot that lands
    inside the other group's burst is skipped rather than queued behind it;
  - a pair naming a preset that is not in the set list plays nothing instead
    of stalling the burst on a name nobody can see.
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

os.environ["HYPE_NODES"] = os.path.join(_TD, "nodes.json")
os.environ["HYPE_CYCLES"] = os.path.join(_TD, "cycles.json")
os.environ["HYPE_FX"] = os.path.join(_TD, "fx.json")
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

print("\n[policy] the mic is the normal state, not a gap")
st = fresh()
set_live(st, "figure")
st.cycle_mic = True
ok(set(hc.desired_modes(st).values()) == {0},
   "between bursts, and during a pause, every node is on its own mic",
   str(hc.desired_modes(st)))
st.cycle_mic = False
ok(set(hc.desired_modes(st).values()) == {1},
   "with no cycle running at all the panel's pattern streams as before",
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

print("\n[policy] the stream follows the command, not the beacon")
st, cmds = fresh(), StubCmds()
set_live(st, "figure")
T0 = 0                                      # the fixtures' last_us: just heard from
ok(hc.stream_targets(st, T0) == [],
   "three slaves reporting local, nothing commanded: no audio goes out",
   str(hc.stream_targets(st, T0)))
hc.reconcile_modes(st, cmds)
ok(sorted(hc.stream_targets(st, T0)) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
   "told to play: the stream starts now, before their beacons agree",
   str(hc.stream_targets(st, T0)))
for s in st.slaves.values():
    s["mode"] = 1
hc.reconcile_modes(st, cmds)                # settles: pending records drop
st.cycle_mic = True                         # burst over, back to the mics
hc.reconcile_modes(st, cmds)
ok(hc.stream_targets(st, T0) == [],
   "told to go local: the stream stops now, though they still report network",
   str(hc.stream_targets(st, T0)))
for s in st.slaves.values():
    s["mode"] = 0
hc.reconcile_modes(st, cmds)
ok(hc.stream_targets(st, T0) == [] and st.mode_sent == {},
   "and stays stopped once their beacons agree", str(st.mode_sent))
st.slaves["10.0.0.9"] = dict(st.slaves["10.0.0.1"], id=9, mode=2)
ok(hc.stream_targets(st, T0) == ["10.0.0.9"],
   "a slave nobody has commanded streams by what it reports (hybrid)",
   str(hc.stream_targets(st, T0)))

print("\n[policy] a slave that stops reporting stops getting audio")
st, cmds = fresh(), StubCmds()
set_live(st, "figure")
for s in st.slaves.values():
    s["mode"], s["last_us"] = 1, 10_000_000
now = 10_000_000 + hc.STREAM_STALE_US
ok(len(hc.stream_targets(st, now)) == 3,
   "three beacons late is still on the air (two lost in a row is normal)")
st.slaves["10.0.0.3"]["last_us"] = 10_000_000 - 1
ok(sorted(hc.stream_targets(st, now)) == ["10.0.0.1", "10.0.0.2"],
   "one past it: that node is left out, the other two keep their stream",
   str(hc.stream_targets(st, now)))
st.slaves["10.0.0.3"]["mode"] = 0          # last heard on its mic
hc.reconcile_modes(st, cmds)
ok(cmds.modes().get("10.0.0.3") == "network"
   and "10.0.0.3" not in hc.stream_targets(st, now),
   "a pending network command does not put a silent node back on the air",
   str(cmds.sent))
st.slaves["10.0.0.3"]["last_us"] = now
ok(sorted(hc.stream_targets(st, now)) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
   "its next STATUS puts it straight back")

# ------------------------------------------------------------------ cycles
print("\n[cycle] clamps and the seeded config")
c = hc.clean_cycle({}, "artists")
ok(c["enabled"] is False and c["show_s"] == 5 and c["pause_s"] == 0,
   "an empty config fills from the defaults (5 s a side), and starts OFF",
   json.dumps(c))
ok(hc.clean_cycle({"show_s": 9999}, "logos")["show_s"] == 600,
   "show clamps to 600 s")
ok(hc.clean_cycle({"show_s": 0}, "logos")["show_s"] == 3,
   "and up to 3 s — a one-second logo is a flicker, not a logo")
ok(hc.clean_cycle({}, "logos")["every_s"] == 300,
   "the period defaults to five minutes")
ok(hc.clean_cycle({"every_s": 20, "show_s": 30, "pause_s": 5},
                  "logos")["every_s"] == 75,
   "a period inside the burst is pushed past it — the mic must get the "
   "stream back", str(hc.clean_cycle({"every_s": 20, "show_s": 30,
                                      "pause_s": 5}, "logos")))
ok(hc.clean_cycle({"idx": 9, "pairs": [["A", "B"]]}, "logos")["idx"] == 0,
   "an index past the end of the pair list is clamped")
ok(hc.clean_cycle({"pairs": [["A", ""], ["", ""], "junk"]}, "logos")["pairs"]
   == [["A", ""]],
   "an empty pair and a junk entry are dropped, a half pair is kept")

print("\n[cycle] what one burst plays")
cyc = hc.load_cycles()
cyc["logos"].update(enabled=True, show_s=20, pause_s=5, every_s=120,
                    pairs=[["HYPERWAVES", "Flux Collective"]], idx=0)
names = ["HYPERWAVES", "Flux Collective", "Anemi", "HyperLili"]
seq = hc.burst_steps(cyc["logos"], "logos", names)
ok([(s["group"], s["preset"], s["secs"]) for s in seq]
   == [("logos", "HYPERWAVES", 20), (None, "", 5),
       ("logos", "Flux Collective", 20)],
   "A, mic pause, B — and no trailing pause: the mic follows anyway",
   str(seq))
ok(hc.cycle_burst_s(cyc["logos"]) == 45,
   "the burst holds the stream for 20 + 5 + 20")
cyc["logos"]["pause_s"] = 0
ok(len(hc.burst_steps(cyc["logos"], "logos", names)) == 2,
   "no pause seconds means no pause step")
cyc["logos"]["idx"] = 0
cyc["logos"]["pairs"] = [["Gone", "Missing"]]
ok(hc.burst_steps(cyc["logos"], "logos", names) == [],
   "a pair naming presets that are not in the set list plays nothing")
cyc["logos"]["pairs"] = [["HYPERWAVES", "Flux Collective"]]
cyc["logos"]["enabled"] = False
ok(hc.burst_steps(cyc["logos"], "logos", names) == [],
   "and a group that is switched off plays nothing")
cyc["artists"].update(enabled=True, show_s=5, pause_s=3, idx=0,
                      pairs=hc.clean_cycle({"pairs": [["S]NE", ""]]},
                                           "artists")["pairs"])
ok([(s["group"], s["preset"]) for s in
    hc.burst_steps(cyc["artists"], "artists", names + ["S]NE"])]
   == [("artists", "S]NE")],
   "a solo set keeps its bracketed name and plays alone, no trailing pause",
   str(cyc["artists"]["pairs"]))

print("\n[cycle] the two grids are offset so their bursts miss")
cyc = hc.load_cycles()
cyc["logos"].update(enabled=True, show_s=5, pause_s=0, every_s=120,
                    pairs=[["HYPERWAVES", "Flux Collective"]])
cyc["artists"].update(enabled=True, show_s=7, pause_s=1, every_s=60)
ph = hc.cycle_phases(cyc)


def _collide(cyc, ph):
    """(closest approach, overlapping bursts) between the two grids."""
    return hc._placement_score(
        hc._fire_windows(ph["logos"], cyc["logos"]["every_s"],
                         hc.cycle_burst_s(cyc["logos"])),
        hc._fire_windows(ph["artists"], cyc["artists"]["every_s"],
                         hc.cycle_burst_s(cyc["artists"])))


gap, hits, _ = _collide(cyc, ph)
ok(hits == 0 and gap > 0,
   "unphased both would land on 0, 120, 240 — phased they never touch",
   f"phases={ph}, closest approach {gap}s")
ok(ph["artists"] == 0 and 0 < ph["logos"] < 120,
   "the shorter period holds phase 0 and the longer one moves around it",
   str(ph))
cyc["logos"]["enabled"] = False
ok(hc.cycle_phases(cyc)["artists"] == 0,
   "one cycle on its own needs no offset")
# 97 s against 60 s share no useful multiple: some collision is unavoidable,
# but the placement must still beat both starting together.
cyc["logos"].update(enabled=True, every_s=97, show_s=8, pause_s=0)
_, hits_phased, _ = _collide(cyc, hc.cycle_phases(cyc))
_, hits_zero, _ = _collide(cyc, {"logos": 0, "artists": 0})
# _placement_score reports -collisions, so bigger is better.
ok(hits_phased > hits_zero,
   "periods that cannot always miss still miss far more often",
   f"{-hits_phased} collisions phased vs {-hits_zero} at phase 0")

# ------------------------------------------------------------ cycle clock
print("\n[cycle] a burst, then the mic until the next one")
st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=2, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]], idx=0)
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_mic,
   "nothing fires on the first tick: the grid starts a full period out, so "
   "a restart does not fire into a rig someone is still cabling up")
ok(st.cycle_next["logos"] > hc.mono_us(), "and the burst is armed",
   str(st.cycle_next))

st.cycle_next["logos"] = hc.mono_us() - 1     # as if the period had elapsed
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "HYPERWAVES" and st.kind == "figure"
   and st.figure == "hyperwaves",
   "when it comes due the A side takes the stream", f"{st.kind} {st.figure}")
ok(not st.cycle_mic and set(hc.desired_modes(st).values()) == {1},
   "and the rig comes off the mic for it")
ok(hc.advance_cycle(st, 250_000) is None,
   "the next tick does nothing while its seconds are unspent")
ok(st.cycle_next["logos"] > hc.mono_us(),
   "the grid has already stepped to the next slot", str(st.cycle_next))

st.cycle_run["until_us"] = hc.mono_us() - 1
step = hc.advance_cycle(st, 250_000)
ok(step and step["group"] is None, "then the mic pause", str(step))
ok(st.figure == "hyperwaves",
   "which does not touch the pattern — nobody is on the stream to see it")
ok(set(hc.desired_modes(st).values()) == {0},
   "and every node is on its mic for it")

st.cycle_run["until_us"] = hc.mono_us() - 1
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "Flux Collective"
   and st.figure == "flux-collective", "then the B side", str(step))
st.cycle_run["until_us"] = hc.mono_us() - 1
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None,
   "and the burst ends there")
ok(st.cycle_mic and set(hc.desired_modes(st).values()) == {0},
   "the rig is back on the microphone until the next period", str(st.kind))
ok(st.kind == "circle",
   "with the panel's own pattern handed back underneath", st.kind)

print("\n[cycle] one stream: a burst due inside another one is skipped")
st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
st.cycles["artists"].update(enabled=True, show_s=3, pause_s=0, every_s=90,
                            pairs=[["Anemi", "HyperLili"]])
hc.advance_cycle(st, 250_000)                 # arm both
st.cycle_next["logos"] = st.cycle_next["artists"] = hc.mono_us() - 1
step = hc.advance_cycle(st, 250_000)
ok(step and step["group"] == "artists",
   "due together, the longer period wins — it is the one seen least often",
   str(step))
ok(st.cycle_next["logos"] > hc.mono_us(),
   "and the loser's grid still steps, so it waits for its own next slot, "
   "rather than queueing a second burst back to back", str(st.cycle_next))

st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
st.cycles["artists"].update(enabled=True, show_s=3, pause_s=0, every_s=90,
                            pairs=[["Anemi", "HyperLili"]])
hc.advance_cycle(st, 250_000)
st.cycle_next["artists"] = hc.mono_us() - 1
hc.advance_cycle(st, 250_000)                 # artists takes the stream
st.cycle_next["logos"] = hc.mono_us() - 1     # logos comes due mid-burst
ok(hc.advance_cycle(st, 250_000) is None
   and st.cycle_run["group"] == "artists",
   "a slot that falls inside another group's burst does not interrupt it")
ok(st.cycle_next["logos"] > hc.mono_us(),
   "and it is skipped, not queued — the release must not be followed "
   "straight away by a second burst", str(st.cycle_next))
hc.end_burst(st)
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_mic,
   "the mic gets the stream back instead", str(st.cycle_run))

print("\n[cycle] frozen by a mute")
st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
hc.advance_cycle(st, 250_000)
st.stream_on = False
st.cycle_next["logos"] = hc.mono_us() - 1
ok(hc.advance_cycle(st, 250_000) is None,
   "a silent feed does not start a burst")
ok(st.cycle_next["logos"] > hc.mono_us() - 1,
   "and the grid is pushed out instead of falling behind",
   f"+{st.cycle_next['logos'] - (hc.mono_us() - 1)} us of credit")
st.stream_on = True

print("\n[cycle] a config change restages both grids")
st.cycle_next["logos"] = hc.mono_us() - 1
hc.advance_cycle(st, 250_000)                 # burst on air
ok(st.cycle_run is not None, "a burst is running")
st.cycles["logos"]["every_s"] = 120
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None,
   "editing the period ends the burst on air rather than half-playing it")
ok(st.cycle_next["logos"] > hc.mono_us() + 100_000_000,
   "and re-arms a full period out, so tweaking a slider at 4m59s does not "
   "fire as you let go", str(st.cycle_next))
st.cycles["logos"]["enabled"] = False
ok(hc.advance_cycle(st, 250_000) is None and not st.cycle_mic,
   "switching the cycle off hands the stream back to the panel",
   str(st.cycle_mic))

print("\n[cycle] a hand on the panel cuts a burst short")
st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
hc.advance_cycle(st, 250_000)
st.cycle_next["logos"] = hc.mono_us() - 1
hc.advance_cycle(st, 250_000)
armed = st.cycle_next["logos"]
hc.end_burst(st, restore_pattern=False)
ok(st.cycle_run is None and st.cycle_step is None and st.cycle_mic,
   "the burst is off the air")
ok(st.figure == "hyperwaves",
   "what the operator just set stays up — no silent revert", st.figure)
ok(st.cycle_next["logos"] == armed,
   "and the grid is untouched: cutting one burst does not shift the evening")

print("\n[cycle] editing a group that is off leaves the running grid alone")
st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=120,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
st.cycles["artists"].update(enabled=False, pairs=[["Anemi", "HyperLili"]])
hc.advance_cycle(st, 250_000)
st.cycle_next["logos"] = hc.mono_us() + 60_000_000
armed = st.cycle_next["logos"]
st.cycles["artists"].update(show_s=9, pause_s=4, every_s=600)
ok(hc.advance_cycle(st, 250_000) is None
   and st.cycle_next["logos"] == armed,
   "ARTIST SETTINGS edited while only the logos run does not push the "
   "logos a period out",
   f"{(st.cycle_next['logos'] - hc.mono_us()) / 1e6:.0f}s to go")
st.cycles["artists"]["enabled"] = True
hc.advance_cycle(st, 250_000)
ok(st.cycle_next["logos"] != armed and "artists" in st.cycle_next,
   "switching the group on still restages both grids around it",
   str(st.cycle_next))

print("\n[cycle] picking another pair leaves the grid where it is")
st = fresh()
st.cycles["artists"].update(enabled=True, show_s=3, pause_s=0, every_s=300,
                            pairs=[["Anemi", "HyperLili"],
                                   ["HyperLili", "Anemi"]], idx=0)
hc.advance_cycle(st, 250_000)
st.cycle_next["artists"] = hc.mono_us() + 60_000_000   # a minute to go
armed = st.cycle_next["artists"]
st.cycles["artists"]["idx"] = 1
ok(hc.advance_cycle(st, 250_000) is None
   and st.cycle_next["artists"] == armed,
   "a pair picked a minute before the burst is still a minute away — it "
   "used to restage the grid and push the names five minutes out",
   f"{(st.cycle_next['artists'] - hc.mono_us()) / 1e6:.0f}s to go")

print("\n[cycle] a pair played on demand")
st.cycle_play = "artists"
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "HyperLili" and st.text == "HyperLili",
   "the picked pair's first name takes the stream on the next tick",
   str(step))
ok(st.cycle_play is None, "the request is consumed, not left armed")
ok(not st.cycle_mic and set(hc.desired_modes(st).values()) == {1},
   "and the rig comes off the mic to show it")
ok(st.cycle_next["artists"] > hc.mono_us() + 290_000_000,
   "the grid restarts from this burst, so the scheduled one does not follow "
   "straight after it",
   f"{(st.cycle_next['artists'] - hc.mono_us()) / 1e6:.0f}s to go")
st.cycles["artists"]["idx"] = 0
st.cycle_play = "artists"
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "Anemi"
   and st.cycle_run["pattern"]["kind"] == "circle",
   "a second pick replaces the burst on air, and still hands back the "
   "panel's own pattern rather than the first pick's name",
   st.cycle_run["pattern"]["kind"])
st.cycle_run["until_us"] = hc.mono_us() - 1
hc.advance_cycle(st, 250_000)
st.cycle_run["until_us"] = hc.mono_us() - 1
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None
   and st.kind == "circle" and st.cycle_mic,
   "and when it ends the rig is back on the mic with the panel restored",
   st.kind)

st.cycle_play = "artists"
hc.advance_cycle(st, 250_000)
st.cycles["artists"]["idx"] = 1                # picked from the settings card
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None
   and st.kind == "circle",
   "a burst whose pair is swapped under it stops, rather than finishing on "
   "a name the card no longer shows", st.kind)

st.stream_on = False
st.cycle_play = "artists"
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None
   and st.cycle_play is None,
   "a pick while the feed is SILENT is dropped, not queued")
st.stream_on = True
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None,
   "so unmuting does not fire a burst nobody asked for just now")

st = fresh()
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
st.cycles["artists"].update(enabled=False, pairs=[["Anemi", "HyperLili"]])
hc.advance_cycle(st, 250_000)
logo_next = st.cycle_next["logos"]
st.cycle_play = "artists"
step = hc.advance_cycle(st, 250_000)
ok(step and step["preset"] == "Anemi" and "artists" not in st.cycle_next,
   "a group with no period of its own still plays once on demand, and gets "
   "no grid out of it", str(st.cycle_next))
ok(st.cycle_next["logos"] == logo_next,
   "the running group's grid is left alone by a one-off")

print("\n[qtest] the debug quick test: every name, every node, then back")
st = fresh()
st.node_flags[121] = {"stream": True, "logos": False, "artists": True}
st.node_flags[164] = {"stream": False, "logos": True, "artists": True}
st.cycles["logos"].update(enabled=True, show_s=3, pause_s=0, every_s=60,
                          pairs=[["HYPERWAVES", "Flux Collective"]])
st.cycles["artists"].update(pairs=[["Anemi", "HyperLili"], ["Gone", ""],
                                   ["HyperLili", ""]])
hc.advance_cycle(st, 250_000)
st.cycle_next["logos"] = hc.mono_us() - 1
hc.advance_cycle(st, 250_000)
ok(st.cycle_run is not None, "(a logo burst is on air)")
st.stream_on = False
ok(hc.start_qtest(st) and st.cycle_run is None and st.cycle_step is None,
   "starting it cuts the burst on air short")
ok(st.stream_on, "and switches the feed on: a test nobody sees tests nothing")
ok(not hc.start_qtest(st), "a second start is a no-op")
seen = [st.qtest["preset"]]
for _ in range(4):
    st.qtest["until_us"] = hc.mono_us() - 1
    hc.advance_qtest(st)
    seen.append(st.qtest["preset"])
ok(seen == ["HYPERWAVES", "Flux Collective", "Anemi", "HyperLili",
            "HYPERWAVES"],
   "logos, then names, each once, a slot naming no preset skipped, looping",
   str(seen))
ok(st.figure == "hyperwaves" and st.qtest["n"] == 4,
   "and each step installs that preset", f"{st.figure} {st.qtest}")
ok(hc.advance_qtest(st) is None, "nothing moves before its 2 s are spent")
ok(set(hc.desired_modes(st).values()) == {1},
   "a forced broadcast: switches off for logos or streaming do not apply",
   str(hc.desired_modes(st)))
armed = st.cycle_next["logos"]
ok(hc.advance_cycle(st, 250_000) is None and st.cycle_run is None
   and st.cycle_next["logos"] == armed + 250_000,
   "the cycles hold their grid while it runs, as a mute holds it")
st.cycle_play = "artists"
hc.advance_cycle(st, 250_000)
ok(st.cycle_play is None and st.cycle_run is None,
   "and a pair pick queued meanwhile is dropped, not fired later")
ok(st.snapshot()["qtest"]["preset"] == "HYPERWAVES",
   "the state feed says what the test shows", json.dumps(st.snapshot()["qtest"]))
ok(hc.stop_qtest(st) and st.qtest is None and st.kind == "circle",
   "stopping hands back the panel's own pattern from before the burst",
   st.kind)
ok(st.cycle_mic and set(hc.desired_modes(st).values()) == {0},
   "and the rig is back on the mic, switches obeyed again",
   str(hc.desired_modes(st)))
ok(not hc.stop_qtest(st) and st.snapshot()["qtest"] is None,
   "a second stop is a no-op")

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
                                 "show_s": 45, "pause_s": 9, "every_s": 600,
                                 "pairs": [["HYPERWAVES", "Flux Collective"]]})
ok(code == 200 and body["cycle"]["show_s"] == 45
   and body["cycle"]["pause_s"] == 9 and body["cycle"]["every_s"] == 600,
   "the cycle is saved and echoed back clamped", json.dumps(body["cycle"]))
ok(hc.load_cycles()["logos"]["enabled"] is True, "and persisted")
ok(st.cycle_run is None and st.cycle_mic,
   "switching it on does not barge onto the stream — the rig goes to the "
   "mic and the first burst lands a period later", str(st.cycle_run))
ok(st.cycle_next["logos"] > hc.mono_us(), "with the grid armed",
   str(st.cycle_next))
code, _ = post("/api/cycle", {"group": "nope"})
ok(code == 400, "an unknown group is refused")

snap = st.snapshot()
ok(snap["nodes"]["121"]["logos"] is False,
   "the state feed carries the switches for the page", json.dumps(snap["nodes"]))
ok(snap["mic"] is True and snap["cycle"] is None,
   "the feed says the rig is on the mic between bursts", json.dumps(snap["mic"]))
ok(len(snap["cycles"]["logos"]["seq"]) == 3
   and snap["cycles"]["logos"]["next_in"] is not None
   and snap["cycles"]["logos"]["burst_s"] == 99,
   "and each group carries its own burst plan and countdown",
   json.dumps(snap["cycles"]["logos"]))

print("\n[policy] over HTTP: a pair picked on the show screen")
code, body = post("/api/cycle", {"group": "artists", "idx": 0, "play": True,
                                 "pairs": [["Anemi", "HyperLili"]]})
ok(code == 200 and body["played"] is True and body["loaded"] is None
   and st.cycle_run and st.cycle_run["group"] == "artists",
   "with a cycle running the rig rests on the mic, so the pair plays as a "
   "burst — loading it onto the panel would show nothing", json.dumps(body))
post("/api/cycle", {"group": "logos", "enabled": False})
code, body = post("/api/cycle", {"group": "artists", "idx": 0, "play": True})
ok(code == 200 and body["loaded"] == "Anemi" and not body["played"]
   and st.text == "Anemi" and st.cycle_run is None,
   "with no cycle running the panel streams, so the first name is loaded "
   "there and stays up", json.dumps(body))
post("/api/cycle", {"group": "artists", "enabled": True})
st.stream_on = False
code, body = post("/api/cycle", {"group": "artists", "idx": 0, "play": True})
ok(code == 200 and not body["played"] and "SILENT" in (body["note"] or "")
   and st.cycle_run is None,
   "while the feed is SILENT the pick is kept but nothing plays, and the "
   "reply says why", json.dumps(body))
st.stream_on = True
code, body = post("/api/cycle", {"group": "artists", "play": True,
                                 "pairs": [["Gone", "Missing"]]})
ok(code == 200 and not body["played"] and body["note"],
   "a pair naming no preset plays nothing and says so", json.dumps(body))

print("\n[qtest] over HTTP: on, off, and what ends it")
cmds.sent.clear()
for s in st.slaves.values():
    s["mode"] = 0
code, body = post("/api/qtest", {"on": True})
ok(code == 200 and body["on"] and st.qtest["preset"] == "HYPERWAVES",
   "on puts the first logo up", json.dumps(body))
ok(set(cmds.modes().values()) == {"network"} and len(cmds.modes()) == 3,
   "and every node is sent to the stream on the tap", str(cmds.sent))
code, body = post("/api/preset", {"op": "save", "name": "HYPERWAVES"})
ok(code == 409, "a preset save is refused: it would save the test name")
code, _ = post("/api/pattern", {"stream": False})
ok(st.qtest is None and not st.stream_on,
   "SILENT is the kill switch: it ends the test and stays silent")
post("/api/qtest", {"on": True})
code, _ = post("/api/pattern", {"amp": 0.5})
ok(st.qtest is None and st.figure == "hyperwaves" and st.amp == 0.5,
   "a hand on the panel ends it and what the operator set stays up")
code, body = post("/api/qtest", {"on": False})
ok(code == 200 and body["on"] is False, "off when it is not running is fine")
code, _ = post("/api/qtest", {})
ok(code == 400, "a body without on is refused")

print("\n[fx] over HTTP: the global pulse switch")
st.pulse_depth, st.pulse_rate = 0.3, 1.2
ok(st.pattern_params()[8] == 0.3 and st.snapshot()["fx_pulse"] is True,
   "by default each pattern streams with its own pulse")
code, body = post("/api/fx", {"pulse": False})
ok(code == 200 and body["pulse"] is False and st.pattern_params()[8] == 0.0,
   "pulse off streams a steady size", json.dumps(body))
ok(st.live_snapshot()["pulse_depth"] == 0.3,
   "without touching the pattern's own pulse — saves keep it")
ok(fresh().fx_pulse is False and fresh().pattern_params()[8] == 0.0,
   "it is persisted, so a restart mid-show stays steady")
code, body = post("/api/fx", {"pulse": True})
ok(code == 200 and st.pattern_params()[8] == 0.3
   and hc.load_fx_pulse() is True,
   "pulse on gives each pattern its own pulse back")
code, _ = post("/api/fx", {"pulse": "no"})
ok(code == 400 and st.fx_pulse is True, "a non-bool body is refused")
_srv.shutdown()

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
