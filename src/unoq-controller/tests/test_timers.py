#!/usr/bin/env python3
"""Regression checks for the interval timers.

No UDP and no board: State, fire_timer/end_hold and the scheduler are driven
directly with a stub CmdSender and fabricated slaves, so this is safe to run
while the daemon is live -- it never binds :5001/:5002 and never touches the
show's files. The last section does bind one HTTP socket, on an ephemeral
loopback port, because the preset/timer coupling only exists in the request
handler. Exit 0 = all pass.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("HC_DIR", HERE))
# Isolate from the live board BEFORE import, same reason as test_fixes.py:
# State() restores the saved live pattern, and a test must never write the
# show's own preset or timer file.
_TD = tempfile.mkdtemp(prefix="hctimers-")
os.environ["HYPE_STATE"] = os.path.join(_TD, "state.json")
os.environ["HYPE_PRESETS"] = os.path.join(_TD, "presets.json")
os.environ["HYPE_TIMERS"] = os.path.join(_TD, "timers.json")
import hype_controller as hc  # noqa: E402

FAIL = []


def ok(cond, label, extra=""):
    print(("  PASS " if cond else "  FAIL ") + label
          + (" — " + extra if extra else ""))
    if not cond:
        FAIL.append(label)


class StubCmds:
    """CmdSender's one method, recorded instead of sent."""

    def __init__(self):
        self.sent = []

    def send(self, ip, cmd_obj):
        self.sent.append((ip, cmd_obj))

    def modes(self):
        return {ip: c["mode"] for ip, c in self.sent
                if c.get("cmd") == "set_mode"}


def fresh(pattern="circle"):
    """A State with three slaves: 1 on network, 2 on local, 3 on hybrid."""
    st = hc.State(pattern)
    st.slaves = {
        "10.0.0.1": {"ip": "10.0.0.1", "id": 1, "mode": 1, "last_us": 0},
        "10.0.0.2": {"ip": "10.0.0.2", "id": 2, "mode": 0, "last_us": 0},
        "10.0.0.3": {"ip": "10.0.0.3", "id": 3, "mode": 2, "last_us": 0},
    }
    st.presets = [hc.clean_preset({"name": "IDENT", "kind": "rose", "a": 6,
                                   "freq": 222.0}),
                  hc.clean_preset({"name": "SHOW", "kind": "circle",
                                   "freq": 111.0})]
    return st


def rule(**kw):
    base = {"id": 1, "preset": "IDENT", "targets": [1, 2], "hold_s": 20,
            "every_s": 300}
    base.update(kw)
    return hc.clean_timer(base)


# ------------------------------------------------------------------- clamps
print("[timers] clean_timer: ranges, whitelists, missing fields")
t = hc.clean_timer({})
ok(t == {"id": 0, "preset": "", "enabled": True, "targets": [],
         "hold_s": 20, "every_s": 300},
   "an empty dict fills from the defaults", json.dumps(t))
ok(hc.clean_timer({"hold_s": 9999})["hold_s"] == 600, "hold clamps to 600 s")
ok(hc.clean_timer({"hold_s": 0})["hold_s"] == 1, "hold clamps up to 1 s")
# every <= hold would re-fire before the restore ran: the show would never
# come back. This is the one clamp that is a safety property, not tidiness.
c = hc.clean_timer({"hold_s": 90, "every_s": 10})
ok(c["every_s"] == 91, "a period inside the hold is pushed past it",
   f"hold={c['hold_s']} every={c['every_s']}")
ok(hc.clean_timer({"targets": [3, 1, 3, "2", None, 999]})["targets"]
   == [1, 2, 3], "targets deduped, sorted, non-numbers and >255 dropped")
ok(hc.clean_timer({"preset": "a/b<c>"})["preset"] == "abc",
   "preset name goes through the same charset filter as a preset")

# -------------------------------------------------------------------- fire
print("\n[timers] fire: the right slaves, the right restore data")
st, cmds = fresh(), StubCmds()
before = st.live_snapshot()
ok(hc.fire_timer(st, cmds, rule()) is None, "fires")
ok(st.kind == "rose" and st.ratio_a == 6, "the preset is on air",
   f"{st.kind} a={st.ratio_a}")
ok(cmds.modes() == {"10.0.0.1": "network", "10.0.0.2": "network"},
   "only the targets were switched, and to network", str(cmds.sent))
ok(st.timer_hold["modes"] == {"10.0.0.1": 1, "10.0.0.2": 0},
   "each target's previous draw setting was recorded",
   str(st.timer_hold["modes"]))
ok(st.timer_hold["pattern"] == before, "the pre-hold pattern was recorded")

print("\n[timers] one hold at a time")
ok(hc.fire_timer(st, cmds, rule(id=2)) == hc.FIRE_HOLDING,
   "a second rule cannot start on top of the first")
ok(st.timer_hold["id"] == 1, "and the first one's restore data survives")

# ------------------------------------------------------------------ release
print("\n[timers] release: modes and pattern both go back")
cmds.sent.clear()
hc.end_hold(st, cmds)
ok(st.timer_hold is None, "the hold is cleared")
ok(cmds.modes() == {"10.0.0.1": "network", "10.0.0.2": "local"},
   "every target back to the setting it had — including the local one",
   str(cmds.sent))
ok(st.live_snapshot() == before, "the pre-hold pattern is back")

print("\n[timers] the operator taking over keeps their pattern")
st, cmds = fresh(), StubCmds()
hc.fire_timer(st, cmds, rule())
st.kind, st.freq = "lissajous", 333.0        # as if /api/pattern had landed
cmds.sent.clear()
hc.end_hold(st, cmds, restore_pattern=False)
ok(st.kind == "lissajous" and st.freq == 333.0,
   "their pattern is NOT reverted out from under them", st.kind)
ok(cmds.modes() == {"10.0.0.1": "network", "10.0.0.2": "local"},
   "but the slaves are still put back", str(cmds.sent))

# ----------------------------------------------------------------- cooldown
# A slave's mode is only known from its 1 Hz STATUS beacon, so just after a
# release the controller still believes a slave it has just put back on local
# is on network. Firing inside that window would record the forced mode as
# the "previous" one and strand the slave on network for good.
print("\n[timers] the post-release cooldown")
ok(hc.fire_timer(st, cmds, rule(id=3)) == hc.FIRE_COOLDOWN,
   "a fire straight after a release is refused")
st.hold_cooldown_us = 0                      # as if the cooldown had elapsed
ok(hc.fire_timer(st, cmds, rule(id=3)) is None, "and allowed once it passes")

# ------------------------------------------------------------------ targets
print("\n[timers] targeting")
st, cmds = fresh(), StubCmds()
hc.fire_timer(st, cmds, rule(targets=[]))
ok(sorted(cmds.modes()) == ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
   "no targets means every slave", str(sorted(cmds.modes())))
st, cmds = fresh(), StubCmds()
hc.fire_timer(st, cmds, rule(targets=[7]))
ok(cmds.sent == [] and st.timer_hold is not None,
   "a rule for a slave that is not here still holds, but commands nobody",
   str(cmds.sent))
st, cmds = fresh(), StubCmds()
ok(hc.fire_timer(st, cmds, rule(preset="GONE")) == hc.FIRE_NO_PRESET,
   "a rule whose preset was deleted does not fire")
ok(st.timer_hold is None, "and leaves no half-claimed hold")

# --------------------------------------------------------------- persistence
print("\n[timers] a hold must not become the persisted live pattern")
st, cmds = fresh(), StubCmds()
hc.save_live(st.live_snapshot())
hc.fire_timer(st, cmds, rule())
# persist_loop's guard, inline: this is the check that keeps a controller
# killed mid-hold from coming back drawing the ident instead of the show.
ok(st.timer_hold is not None and st.dirty,
   "the hold does mark the state dirty (the restore needs to be written)")
saved = hc.load_live()
ok(saved["kind"] == "circle",
   "but ~/hype_state.json still holds the show", saved["kind"])

print("\n[timers] the file round-trips")
hc.save_timers([rule(), rule(id=2, preset="SHOW", targets=[], hold_s=5,
                             every_s=60)])
back = hc.load_timers()
ok(len(back) == 2 and back[0]["targets"] == [1, 2]
   and back[1]["preset"] == "SHOW" and back[1]["every_s"] == 60,
   "two rules survive a save/load", json.dumps(back))
with open(os.environ["HYPE_TIMERS"], "w") as f:
    json.dump([{"preset": "IDENT"}, {"no": "preset"}, "junk", 7], f)
back = hc.load_timers()
ok(len(back) == 1 and back[0]["hold_s"] == 20,
   "junk entries are dropped and the survivor is filled from the defaults",
   json.dumps(back))

# ---------------------------------------------------------------------- mute
# A hold drags its targets onto the stream. Muted, that is not an
# interruption but a dark scope for the whole hold -- and the slaves it takes
# were drawing their own mic input a moment earlier.
print("\n[timers] a muted stream refuses the hold")
st, cmds = fresh(), StubCmds()
st.stream_on = False
ok(hc.fire_timer(st, cmds, rule()) == hc.FIRE_MUTED,
   "STREAM off refuses the fire")
ok(st.timer_hold is None and cmds.sent == [],
   "no hold claimed and no slave dragged off its own mic", str(cmds.sent))
st.stream_on = True
ok(hc.fire_timer(st, cmds, rule()) is None, "and fires once it is back on")

# ----------------------------------------------------------------- scheduler
# The bug this guards: timer_loop used to write the next fire time BEFORE
# calling fire_timer and ignore the result, so a rule that came due behind
# another rule's hold was refused by the post-release cooldown 250 ms later
# and had already lost its turn. It then waited a whole period -- the one
# case timer_loop's docstring promises to handle.
print("\n[timers] a rule queued behind a hold still gets its turn")
hc.HOLD_COOLDOWN_US = 500_000      # the mechanism, not the 2.5 s wall clock
st, cmds = fresh(), StubCmds()
st.timers = [rule(id=1, preset="IDENT", hold_s=1, every_s=30),
             rule(id=2, preset="SHOW", hold_s=1, every_s=30)]
_now = hc.mono_us()
st.timer_next = {1: _now, 2: _now + 600_000}   # 2 comes due inside 1's hold
threading.Thread(target=hc.timer_loop, args=(st, cmds), daemon=True).start()
on_air, _t0 = [], time.monotonic()
while time.monotonic() - _t0 < 6.0:
    _h = st.timer_hold
    if _h and (not on_air or on_air[-1] != _h["preset"]):
        on_air.append(_h["preset"])
    if len(on_air) == 2:
        break
    time.sleep(0.02)
ok(on_air == ["IDENT", "SHOW"],
   "the queued rule fires after the release instead of losing a period",
   str(on_air))
hc.HOLD_COOLDOWN_US = 2_500_000
# timer_loop has no stop flag and the thread is a daemon, so empty its rule
# list: otherwise it keeps firing into the sections below and its [timer]
# lines read as if they belonged to the test that is printing.
with st.lock:
    st.timers = []
time.sleep(1.2)                     # let the hold in flight release quietly

# ------------------------------------------------- preset delete vs. its rule
# Only the request handler couples the two, so this section talks HTTP to a
# handler bound on 127.0.0.1:0 -- no fixed port, nothing the daemon uses.
print("\n[timers] deleting a preset does not leave an armed rule behind")
st, cmds = fresh(), StubCmds()
st.timers = [rule(id=1, preset="IDENT"), rule(id=2, preset="SHOW")]
_srv = ThreadingHTTPServer(("127.0.0.1", 0), hc.make_http_handler(st, cmds))
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_base = "http://127.0.0.1:%d" % _srv.server_address[1]


def post(path, obj):
    req = urllib.request.Request(_base + path, json.dumps(obj).encode(),
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


_code, _body = post("/api/preset", {"op": "delete", "name": "IDENT"})
ok(_code == 200 and [t["enabled"] for t in st.timers] == [False, True],
   "the rule that showed it is paused; the other is untouched",
   str([(t["preset"], t["enabled"]) for t in st.timers]))
ok(_body.get("note") and "IDENT" in _body["note"],
   "and the operator is told rather than left with a dead countdown",
   str(_body.get("note")))
ok(hc.load_timers()[0]["enabled"] is False, "the pause is persisted")

print("\n[timers] a refused fire says which reason, not all three")
_code, _body = post("/api/timer", {"op": "fire", "id": 2})
ok(_code == 200 and st.timer_hold is not None, "a good rule fires on demand")
_code, _body = post("/api/timer", {"op": "fire", "id": 2})
ok(_code == 409 and hc.FIRE_HOLDING in _body.get("err", ""),
   "and a second tap names the hold that is in the way", str(_body))
_srv.shutdown()

print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
sys.exit(1 if FAIL else 0)
