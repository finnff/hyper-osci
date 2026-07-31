#!/usr/bin/env python3
"""Regression checks for the TX buffer-full accounting.

The counter exists for one situation: packets dying in this box's own radio
queue instead of in the air. A healthy rig exercises none of that -- it
reports 0.0% forever, which a counter hardwired to zero also does. So the
interesting cases are driven here directly through tx_rollup(), including the
43%-local-loss night that motivated the whole thing.

No UDP, no board, no daemon files touched. Exit 0 = all pass.
"""
import errno
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("HC_DIR", HERE))
# Isolate before import, same reason as test_timers.py: State() restores the
# live pattern and must never write the show's own files.
_TD = tempfile.mkdtemp(prefix="hctx-")
os.environ["HYPE_STATE"] = os.path.join(_TD, "state.json")
os.environ["HYPE_PRESETS"] = os.path.join(_TD, "presets.json")
os.environ["HYPE_TIMERS"] = os.path.join(_TD, "timers.json")
import hype_controller as hc  # noqa: E402

FAIL, RAN = [], []
A, B = "192.168.50.228", "192.168.50.229"


def ok(cond, label, extra=""):
    RAN.append(label)
    print(("  PASS " if cond else "  FAIL ") + label
          + (" — " + extra if extra else ""))
    if not cond:
        FAIL.append(label)


print("\n-- a healthy link publishes zero, and says so per slave")
prev = {}
pub, worst = hc.tx_rollup({A: 200}, {}, {}, prev)
ok(pub[A]["pct"] == 0.0 and worst == 0.0, "200 clean sends -> 0.0%")
ok(pub[A]["ok"] == 200 and pub[A]["full"] == 0,
   "clean window still reports the cumulative total", str(pub[A]))

print("\n-- the night that started this: 43% dying before the air")
# 200 packets/s attempted, 86 refused by a full send buffer.
prev = {}
pub, worst = hc.tx_rollup({A: 114}, {A: 86}, {}, prev)
ok(pub[A]["pct"] == 43.0, "86 ENOBUFS of 200 -> 43.0%", str(pub[A]["pct"]))
ok(worst == 43.0, "worst tracks the pct (drives the journal line)")
ok(pub[A]["full"] == 86, "buffer-full counted, not folded into ok")

print("\n-- pct is windowed, not lifetime: recovery must show as recovery")
# Next second is clean. The cumulative total stays 86; the rate must fall.
pub, worst = hc.tx_rollup({A: 314}, {A: 86}, {}, prev)
ok(pub[A]["pct"] == 0.0, "a clean second after a bad one -> 0.0%",
   str(pub[A]["pct"]))
ok(pub[A]["full"] == 86, "...while the lifetime total still remembers 86")

print("\n-- a second bad window is measured on its own, not cumulatively")
pub, worst = hc.tx_rollup({A: 414}, {A: 186}, {}, prev)
ok(pub[A]["pct"] == 50.0, "100 ENOBUFS of the next 200 -> 50.0%",
   str(pub[A]["pct"]))

print("\n-- an idle publish must not repeat the last window's number")
pub, worst = hc.tx_rollup({A: 414}, {A: 186}, {}, prev)
ok(pub[A]["pct"] == 0.0, "no traffic since last publish -> 0.0%, not 50.0",
   str(pub[A]["pct"]))
ok(worst == 0.0, "worst falls back too (no stale journal warning)")

print("\n-- STREAM off for a while, then on: no bogus catch-up spike")
# The publish runs outside `if streaming`, so tx_prev keeps tracking frozen
# totals. Resume must be measured from the pause, not from before it.
paused = dict(prev)
for _ in range(5):                      # five seconds of nothing sent
    hc.tx_rollup({A: 414}, {A: 186}, {}, paused)
pub, worst = hc.tx_rollup({A: 614}, {A: 186}, {}, paused)
ok(pub[A]["pct"] == 0.0, "200 clean packets after a 5 s pause -> 0.0%",
   str(pub[A]["pct"]))

print("\n-- ENOBUFS and other errnos are separate buckets")
prev2 = {}
pub, _ = hc.tx_rollup({A: 100}, {A: 50}, {A: 50}, prev2)
ok(pub[A]["full"] == 50 and pub[A]["err"] == 50,
   "a buffer-full and an ENETUNREACH do not get mixed")
ok(pub[A]["pct"] == 50.0, "both still count as 'never reached the air'")
print("\n-- the errno taxonomy the live rig actually produces")
# The socket is non-blocking, so a full send buffer is EAGAIN. Counting only
# ENOBUFS would have read 0.0% through a window that really lost 85.6%.
ok(errno.EAGAIN in hc.TX_QUEUE_FULL,
   "EAGAIN counts as buffer-full — this is the one the live rig raises")
ok(errno.EWOULDBLOCK in hc.TX_QUEUE_FULL, "EWOULDBLOCK too (same value here)")
ok(errno.ENOBUFS in hc.TX_QUEUE_FULL,
   "ENOBUFS too — blocking sockets and full device queues still report it")
ok(errno.ENETUNREACH not in hc.TX_QUEUE_FULL,
   "ENETUNREACH is NOT buffer-full: that one is the AP going away")
ok(errno.EHOSTUNREACH not in hc.TX_QUEUE_FULL, "nor EHOSTUNREACH")


print("\n-- per-slave attribution: which link is under pressure")
prev3 = {}
pub, worst = hc.tx_rollup({A: 200, B: 100}, {B: 100}, {}, prev3)
ok(pub[A]["pct"] == 0.0 and pub[B]["pct"] == 50.0,
   "one bad slave does not smear onto the healthy one",
   "A=%s B=%s" % (pub[A]["pct"], pub[B]["pct"]))
ok(worst == 50.0, "worst is the max across slaves, not the mean")

print("\n-- a slave that has only ever failed does not crash the rollup")
pub, _ = hc.tx_rollup({}, {"10.0.0.9": 7}, {}, {})
ok(pub["10.0.0.9"]["pct"] == 100.0,
   "no successful send ever -> 100%, no KeyError", str(pub["10.0.0.9"]))

print("\n-- nothing sent at all: no division by zero")
pub, worst = hc.tx_rollup({}, {}, {}, {})
ok(pub == {} and worst == 0.0, "empty counters -> empty publish")

print("\n-- it reaches the dashboard: /api/state carries net.tx")
st = hc.State("circle")
st.net_tx = {A: {"ok": 1, "full": 2, "err": 0, "pct": 66.7}}
snap = st.snapshot()
ok(snap["net"].get("tx", {}).get(A, {}).get("pct") == 66.7,
   "snapshot() exposes net.tx")
ok(json.loads(json.dumps(snap))["net"]["tx"][A]["full"] == 2,
   "and it survives JSON round-trip to the page")

print("\n%d checks, %d failed" % (len(RAN), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
