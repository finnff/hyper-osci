import importlib.util, io, json, os, tempfile
spec = importlib.util.spec_from_file_location("nw", "/tmp/nw_mod.py")
nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)

N = [0]
def ok(cond, why):
    assert cond, why
    N[0] += 1; print(f"  PASS  {why}")

def serve(payload):
    class R:
        def __enter__(s): return io.StringIO(json.dumps(payload))
        def __exit__(s, *a): return False
    def boom(*a, **k): raise OSError("refused")
    nw.urllib.request.urlopen = boom if payload is None else (lambda *a, **k: R())

def check(payload, prev_rx, prev_tx=None):
    serve(payload)
    return nw.check(prev_rx, prev_tx or {})[0]

S = lambda **kw: dict({"mode": 1, "source": 1, "ip": "a", "rx": 10000}, **kw)
P = {"a": 4300}          # 5700 packets ago == one healthy 30 s tick
on = lambda *sl: {"stream_on": True, "slaves": list(sl)}

print("-- rx: the slaves' own counters")
ok(check(on(S(source=0, rx=4300)), P) is True, "on mic AND rx frozen -> WEDGE")
ok(check(on(S(source=0, rx=4350)), P) is True, "on mic AND rx crawled 50 pkts -> WEDGE")
ok(check(on(S(source=0, rx=10000)), P) is False,
   "on mic BUT rx +5700 -> normal loss flicker, healthy")
ok(check(on(S(source=0, rx=4500)), P) is False, "on mic, rx +200 (at the floor) -> healthy")
ok(check(on(S()), P) is False, "playing -> healthy")
ok(check(on(S(source=0, rx=4300)), {}) is None,
   "no previous rx sample -> no evidence, never strike on first sight")
ok(check(on(S(source=0, rx=0)), P) is False,
   "slave rebooted, rx reset to 0 -> negative delta, leave it alone")
ok(check(on(S(source=0, rx=4300), S(ip="b", source=1, rx=9000)),
         {"a": 4300, "b": 3300}) is False, "one slave still playing -> not the AP (rx alone)")

print("\n-- nothing on the stream is no evidence, not health")
ok(check({"stream_on": False, "slaves": [S(source=0, rx=4300)]}, P) is None,
   "stream muted on purpose")
ok(check(on(S(mode=0, source=0, rx=4300)), P) is None,
   "every slave LOCAL (mic time between show bursts)")
ok(check(on(), P) is None, "no slaves yet")
ok(check({}, P) is None, "empty state doc")
ok(check(None, P) is None, "controller unreachable")
serve(on(S(mode=0, rx=9999)))
ok(nw.check(P, {})[2] == P, "mic-time check keeps the last in-burst rx sample")

print("\n-- tx: the controller's per-slave AP-queue refusals")
T = lambda ok_, full: {"ok": ok_, "full": full, "err": 0, "pct": 0.0}
local = lambda tx: {"stream_on": True, "slaves": [S(mode=0)], "net": {"tx": tx}}
ok(check(local({"a": T(1144, 1456)}), P, {"a": [1000, 0]}) is True,
   "91% of 1600 sends refused -> WEDGE, even checked in mic time")
ok(check(on(S(rx=9000), S(ip="b", source=0, rx=3000)) | {"net": {"tx": {
         "a": T(2600, 50), "b": T(1100, 1500)}}},
         {"a": 7000, "b": 2950}, {"a": [1000, 0], "b": [1000, 0]}) is True,
   "one slave at 94% refused while another plays -> WEDGE (the 2026-10-17 case)")
ok(check(local({"a": T(2544, 56)}), P, {"a": [1000, 0]}) is False,
   "3.5% of 1600 refused -> healthy")
ok(check(local({"a": T(1520, 500)}), P, {"a": [1000, 0]}) is False,
   "49% of 1020 refused -> under the line, healthy")
ok(check(local({"a": T(1300, 400)}), P, {"a": [1000, 0]}) is None,
   "only 700 sends since the sample -> not judged yet")
serve(local({"a": T(1300, 400)}))
ok(nw.check(P, {"a": [1000, 0]})[3] == {"a": [1000, 0]},
   "... and the old sample is kept so the next burst adds up")
serve(local({"a": T(50, 900)}))
d, _, _, tx = nw.check(P, {"a": [1000, 0]})
ok(d is None and tx == {"a": [50, 900]},
   "ok counter went backwards (controller restarted) -> new baseline, no verdict")

print("\n-- a slave that went quiet is not the AP (2026-10-17, 164 switched off)")
gone = lambda **kw: {"stream_on": True, "net": {"tx": {"a": T(1144, 1456)}},
                     "slaves": [S(mode=0, **kw)]}
serve(gone(age_ms=26000, gone=True))
d, _, _, tx = nw.check(P, {"a": [1000, 0]})
ok(d is None and tx == {"a": [1144, 1456]},
   "91% refused to a slave marked gone -> no verdict, fresh sample")
serve(gone(age_ms=6000))
ok(nw.check(P, {"a": [1000, 0]})[0] is None, "silent 6 s, not yet marked -> no verdict")
serve(gone(age_ms=2500))
ok(nw.check(P, {"a": [1000, 0]})[0] is None,
   "tx: silent 2.5 s, still being sent to (3 s cut) -> no verdict")
ok(check(on(S(source=0, rx=4300, age_ms=2500)), P) is None,
   "rx: NETWORK slave silent 2.5 s, frozen on mic -> no evidence, not a wedge")
ok(check(on(S(source=0, rx=4300, age_ms=20000, gone=True)), P) is None,
   "rx: switched off showing the mic (show end) -> no evidence, not a wedge")
ok(check(on(S(source=0, rx=4300, age_ms=20000, gone=True),
            S(ip="b", source=0, rx=3000)), {"a": 4300, "b": 2950}) is True,
   "rx: the reporting slave alone still decides -> WEDGE")
ok(check(local({"a": T(2600, 1456)}), P, {"a": [1144, 1456]}) is False,
   "back and reporting: only the sends since its return are judged -> healthy")
ok(check(on(S(rx=9000), S(ip="b", source=0, rx=3000)) | {"net": {"tx": {}}},
         {"a": 7000, "b": 2950}, {}) is False,
   "slave missing from net.tx entirely -> rx decides")

print("\n-- main(): strikes across the show's burst schedule")
nw.STATE = os.path.join(tempfile.mkdtemp(), "state")
nw.ap_is_up = lambda: True
clock = [1000.0]
nw.uptime_s = lambda: clock[0]
bounces = []
nw.ap_bounce = lambda: bounces.append(clock[0]) or True
logs = []
nw.log = logs.append

def schedule(ticks):
    """ticks: list of (in_burst, ok_per_slave, full_per_slave, rx_step)."""
    if os.path.exists(nw.STATE):
        os.remove(nw.STATE)
    bounces.clear(); logs.clear()
    ok_, full, rx = 0, 0, 0
    for burst, d_ok, d_full, d_rx in ticks:
        ok_, full, rx = ok_ + d_ok, full + d_full, rx + d_rx
        clock[0] += 30
        serve({"stream_on": True,
               "slaves": [S(mode=1 if burst else 0, source=0, rx=rx)],
               "net": {"tx": {"a": T(ok_, full)}}})
        nw.main()
    return list(bounces)

MIC = (False, 0, 0, 0)
WEDGED = (True, 150, 1450, 30)          # one 8 s burst, 91% refused, 30 packets in
HEALTHY = (True, 1545, 55, 1500)
ok(schedule([HEALTHY, MIC] + [WEDGED, MIC] * 3) == [clock[0] - 30],
   "wedge on 120 s cycles: mic ticks between bursts don't reset -> bounce on the 3rd wedged burst")
ok(schedule([HEALTHY, MIC] + [WEDGED, MIC] * 2 + [HEALTHY, MIC] + [WEDGED, MIC] * 2) == [],
   "two wedged bursts, a healthy one, two more -> strikes reset, no bounce")
ok(schedule([HEALTHY, MIC] * 20) == [], "a healthy evening -> never bounces")
ok(schedule([MIC] * 10) == [], "nobody on the stream for 5 min -> never bounces")
print(f"\n{N[0]} checks, ALL PASS")
