import importlib.util, io, json
spec = importlib.util.spec_from_file_location("nw", "/tmp/nw_mod.py")
nw = importlib.util.module_from_spec(spec); spec.loader.exec_module(nw)

def feed(payload, prev):
    class R:
        def __enter__(s): return io.StringIO(json.dumps(payload))
        def __exit__(s, *a): return False
    nw.urllib.request.urlopen = lambda *a, **k: R()
    return nw.audio_is_dead(prev)

def boom(*a, **k): raise OSError("refused")
N = [0]
def case(want, payload, prev, why):
    if payload is None:
        nw.urllib.request.urlopen = boom
        got, _ = nw.audio_is_dead(prev)
    else:
        got, _ = feed(payload, prev)
    assert got is want, f"{why}: expected {want}, got {got}"
    N[0] += 1; print(f"  PASS  bounce={str(got):5} {why}")

S = lambda **kw: dict({"mode": 1, "source": 1, "ip": "a", "rx": 10000}, **kw)
P = {"a": 4300}          # 5700 packets ago == one healthy 30 s tick

case(True,  {"stream_on": True, "slaves": [S(source=0, rx=4300)]}, P,
     "on mic AND rx frozen -> WEDGE")
case(True,  {"stream_on": True, "slaves": [S(source=0, rx=4350)]}, P,
     "on mic AND rx crawled 50 pkts -> WEDGE")
case(False, {"stream_on": True, "slaves": [S(source=0, rx=10000)]}, P,
     "on mic BUT rx +5700 -> normal loss flicker, DO NOT bounce")
case(False, {"stream_on": True, "slaves": [S(source=0, rx=4500)]}, P,
     "on mic, rx +200 (at the floor) -> do not bounce")
case(False, {"stream_on": True, "slaves": [S()]}, P, "healthy")
case(False, {"stream_on": True, "slaves": [S(source=0, rx=4300)]}, {},
     "no previous rx sample -> never bounce on first tick")
case(False, {"stream_on": True, "slaves": [S(source=0, rx=0)]}, P,
     "slave rebooted, rx reset to 0 -> negative delta, do not bounce")
case(False, {"stream_on": False, "slaves": [S(source=0, rx=4300)]}, P,
     "stream muted on purpose")
case(False, {"stream_on": True, "slaves": [S(mode=0, source=0, rx=4300)]}, P,
     "slave set to LOCAL on purpose")
case(False, {"stream_on": True, "slaves": [
        S(source=0, rx=4300), S(ip="b", source=1, rx=9000)]},
     {"a": 4300, "b": 3300}, "one slave still playing -> not the AP")
case(False, {"stream_on": True, "slaves": []}, P, "no slaves yet")
case(False, {}, P, "empty state doc")
case(False, None, P, "controller unreachable")
print(f"\n{N[0]} checks, ALL PASS")
