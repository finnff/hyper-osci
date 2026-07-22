"""End-to-end against the RUNNING daemon: the eight names, then the crash."""
import json, math, subprocess, threading, time, urllib.request

B = "http://127.0.0.1:8080"
NAMES = ["HYPERWAVES", "Flux Collective", "Anémi", "Gan D", "Guild Navigator",
         "Eastern Distributor", "HyperLili", "Liso"]
fails = []


def post(path, obj):
    r = urllib.request.Request(B + path, json.dumps(obj).encode(),
                               {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=5))


def get(path):
    return json.load(urllib.request.urlopen(B + path, timeout=5))


def pid():
    return subprocess.run(["systemctl", "show", "hyperosci-controller",
                           "-p", "MainPID", "--value"],
                          capture_output=True, text=True).stdout.strip()


def ok(c, label, extra=""):
    print(("  PASS " if c else "  FAIL ") + label + (" — " + extra if extra else ""))
    if not c:
        fails.append(label)


p0 = pid()
print(f"daemon MainPID={p0}\n[live] the eight names through /api/pattern")
for nm in NAMES:
    post("/api/pattern", {"kind": "text", "text": nm, "font": "simplex",
                          "freq": 60, "amp": 0.8})
    s = get("/api/state")["pattern"]
    pv = get("/api/textpreview")["pts"]
    r = [math.hypot(x, y) for x, y in pv]
    ok(s["text"] == nm and s["kind"] == "text" and len(pv) > 300
       and max(r) - min(r) > 0.2,
       f"{nm!r}", f"echoed={s['text']!r} pts={len(pv)} r={min(r):.2f}..{max(r):.2f}")

print("\n[live] every font x the accented name")
for f in ["simplex", "duplex", "script", "gothic", "times", "italic"]:
    post("/api/pattern", {"text": "Anémi", "font": f})
    s = get("/api/state")["pattern"]
    ok(s["font"] == f and s["text"] == "Anémi", f"Anémi in {f}",
       f"tver={s['tver']}")

print("\n[live] §1.1 the crash that used to kill the daemon")
post("/api/pattern", {"kind": "text", "text": "W", "font": "times",
                      "amp": 1.0, "rot": 0.5, "freq": 90})
time.sleep(4)
ok(pid() == p0, "W/times amp=1.00 spin=0.5 for 4 s", f"MainPID still {pid()}")
post("/api/pattern", {"text": "LIVE|SET|2026", "font": "simplex"})
time.sleep(3)
ok(pid() == p0, "LIVE|SET|2026 amp=1.00 spin=0.5 for 3 s")
post("/api/pattern", {"text": "Eastern|Distributor", "font": "gothic",
                      "rot": 2.0})
time.sleep(3)
ok(pid() == p0, "Eastern|Distributor gothic amp=1.00 spin=2.0 for 3 s")

print("\n[live] §1.2 spin off returns to upright")
post("/api/pattern", {"rot": 0.0, "amp": 0.8})
time.sleep(0.5)
a = get("/api/state")["pattern"]
ok(a["rot"] == 0.0 and pid() == p0, "spin slider back to 0")

print("\n[live] §1.6 concurrent text/font POSTs")
errs = []


def spam(body, n):
    for _ in range(n):
        try:
            post("/api/pattern", body)
        except Exception as e:
            errs.append(str(e))


t1 = threading.Thread(target=spam, args=({"text": "HyperLili"}, 25))
t2 = threading.Thread(target=spam, args=({"font": "gothic"}, 25))
t1.start(); t2.start(); t1.join(); t2.join()
time.sleep(0.5)
s = get("/api/state")["pattern"]
ok(s["text"] == "HyperLili" and s["font"] == "gothic" and not errs,
   "50 interleaved rebuilds: both fields stick",
   f"text={s['text']!r} font={s['font']!r} http_errs={len(errs)}")
ok(pid() == p0, "daemon survived the concurrency burst")

print("\n[live] §1.3/1.4 presets")
pre = get("/api/state")["presets"]
post("/api/preset", {"op": "save", "name": "Anemi test"})
post("/api/pattern", {"kind": "circle"})
post("/api/preset", {"op": "load", "name": "Anemi test"})
s = get("/api/state")["pattern"]
ok(s["kind"] == "text" and s["text"] == "HyperLili" and s["font"] == "gothic",
   "save -> change -> load round-trips", f"{s['kind']} {s['text']!r} {s['font']}")
post("/api/preset", {"op": "delete", "name": "Anemi test"})
ok(get("/api/state")["presets"] == pre, "delete restores the original list",
   str(get("/api/state")["presets"]))

sl = get("/api/state")["slaves"]
print(f"\nslaves: {[(x['id'], x['ip'], x['source'], x['depth']) for x in sl]}")
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
