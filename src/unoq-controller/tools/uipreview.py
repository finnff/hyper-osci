#!/usr/bin/env python3
"""Mock UI preview server — test the dashboard without hardware.

    python3 uipreview.py                  # healthy rig
    python3 uipreview.py --scenario fail  # one slave failing
    python3 uipreview.py --port 8081

Then open http://127.0.0.1:8080/.

The state below mirrors what the real controller publishes, field for field:
`lost` and `uptime` are integers (packets, seconds), `net.tx` is keyed by
slave IP, `font` is a name from TEXT_FONTS, and every group in `cycles`
carries the burst plan, countdown and phase that snapshot() adds. A mock that
drifts from the board is worse than no mock -- it renders a page that cannot
exist.

POSTs are answered the way the controller answers them (unknown op -> 400,
save during a burst -> 409, delete clears the name out of the pairs that
named it) and they mutate this process's state, so the set-list, node-switch
and cycle paths can be exercised end to end.
"""
import argparse
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from hype_controller import PAGE, PRESETS_MAX, CYCLE_GROUPS

# The show as it stands, so the seeded pairs below name presets that exist.
PRESETS = ["HYPERWAVES", "Flux Collective", "Anémi", "Gan D",
           "Guild Navigator", "Eastern Distributor", "HyperLili", "Liso"]


def slave(ip, sid, **kw):
    s = {"ip": ip, "mac": f"8c:d0:b2:a9:08:{sid:02x}", "id": sid,
         "mode": 1, "source": 1, "rssi": -57, "vbat_mv": 3980,
         "depth": 21600, "rx": 2657034, "drop": 2993, "under": 34,
         "lost": 80756, "uptime": 13896, "offs": -2147483648,
         "lpat": "mic", "last_us": 0, "age_ms": 300}
    s.update(kw)
    return s


def burst_seq(c, group):
    """The steps one burst plays — the mock's copy of burst_steps()."""
    pair = c["pairs"][min(c["idx"], len(c["pairs"]) - 1)] if c["pairs"] else []
    names = [n for n in pair if n in PRESETS]
    seq = []
    for i, n in enumerate(names):
        if i and c["pause_s"]:
            seq.append({"group": "", "preset": "", "secs": c["pause_s"]})
        seq.append({"group": group, "preset": n, "secs": c["show_s"]})
    return seq


def cycle(group, **kw):
    """One group's published config. Phases are the real ones the controller
    picks for these two periods: logos moves, the shorter artists grid holds
    0. Hard-coding them keeps the mock honest without importing the search."""
    c = {"enabled": True, "show_s": 10, "pause_s": 0, "every_s": 120,
         "idx": 0, "pairs": [], "phase": 0}
    c.update(kw)
    c["running"] = False
    c["burst_s"] = 2 * c["show_s"] + c["pause_s"]
    c["seq"] = burst_seq(c, group)
    return c


def base_state():
    return {
        "pattern": {"kind": "figure", "freq": 50.0, "amp": 0.9, "a": 3, "b": 2,
                    "text": "HYPERWAVES", "font": "duplex",
                    "figure": "hyperwaves",
                    "pulse_rate": 1.2, "pulse_depth": 0.0, "rot": 0.0,
                    "flip_x": False, "flip_y": False, "sver": 373},
        "figures": ["hyperwaves", "flux-collective"],
        "presets": list(PRESETS),
        "stream_on": True,
        "slaves": [
            slave("192.168.50.228", 121),
            slave("192.168.50.229", 122, mode=0, source=0, rssi=-66,
                  vbat_mv=3820, lpat="circle", age_ms=900),
            slave("192.168.50.230", 123, rssi=-61, vbat_mv=4050, age_ms=150),
            slave("192.168.50.231", 124, rssi=-70, vbat_mv=3610, age_ms=450),
        ],
        "net": {"iface": "192.168.50.1", "egress": True,
                "tx": {"192.168.50.228": {"ok": 2269115, "full": 2959,
                                          "err": 0, "pct": 0.0},
                       "192.168.50.229": {"ok": 2260000, "full": 3100,
                                          "err": 2, "pct": 0.1},
                       "192.168.50.230": {"ok": 2270000, "full": 2800,
                                          "err": 0, "pct": 0.0},
                       "192.168.50.231": {"ok": 2255000, "full": 3300,
                                          "err": 1, "pct": 0.1}}},
        # One node out of the logos, so the tile switches have something to
        # draw that is not the default.
        "nodes": {"122": {"stream": True, "logos": False, "artists": True}},
        "cycles": {
            "logos": cycle("logos", show_s=10, every_s=120, phase=34,
                           next_in=78,
                           pairs=[["HYPERWAVES", "Flux Collective"]]),
            "artists": cycle("artists", show_s=15, every_s=60, phase=0,
                             next_in=41,
                             pairs=[["Anémi", "HyperLili"],
                                    ["Eastern Distributor", "Gan D"],
                                    ["Guild Navigator", "Liso"]]),
        },
        # The resting state of the rig: every node on its own audio, waiting
        # for the next burst.
        "mic": True,
        "cycle": None,
    }


SCENARIOS = {
    "ok": "healthy four-slave rig, both cycles waiting on the mic",
    "fail": "slave 123 dropping packets on a weak link",
    "dark": "feed muted — nothing is being streamed",
    "apdown": "the AP is down, no interface holds the address",
    "burst": "a logo burst is on air",
    "gone": "slave 124 has stopped answering (the F8 tombstone)",
    "down": "the controller itself does not answer",
}


def apply_scenario(st, name):
    if name == "fail":
        s = st["slaves"][2]
        s.update(drop=42000, under=150, rssi=-94, vbat_mv=3400,
                 mode=0, source=0, lpat="square", age_ms=2400)
    elif name == "dark":
        st["stream_on"] = False
    elif name == "apdown":
        st["net"]["egress"] = False
        for s in st["slaves"]:
            s["mode"], s["source"] = 0, 0
    elif name == "burst":
        st["cycles"]["logos"]["running"] = True
        st["cycle"] = {"group": "logos", "preset": "HYPERWAVES", "left_s": 6}
        st["mic"] = False
    elif name == "gone":
        # Marked lost at 5 s, still listed until 30 s: the tile has to say so
        # rather than silently vanishing mid-set.
        s = st["slaves"][3]
        s.update(age_ms=12000, gone=True)
    return st


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        st = self.server.state
        if self.server.scenario == "down" and self.path != "/":
            # Not a 500: the controller being unreachable means no answer at
            # all, which is the case the page's stale banner exists for.
            self.close_connection = True
            return
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            self._json(st)
        elif self.path.startswith("/api/shapepreview"):
            pts = [[i / 32 - 1, 0.5 * (1 if (i // 8) % 2 else -1)]
                   for i in range(64)]
            self._json({"ver": st["pattern"]["sver"], "pts": pts})
        else:
            self._json({"err": "not found"}, 404)

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        st = self.server.state
        if self.server.scenario == "down":
            self.close_connection = True
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n)) if n else {}
        except ValueError:
            return self._json({"err": "bad json"}, 400)
        route = {"/api/pattern": self._pattern, "/api/preset": self._preset,
                 "/api/node": self._node, "/api/cycle": self._cycle,
                 "/api/cmd": self._cmd}.get(self.path)
        if route is None:
            return self._json({"err": "not found"}, 404)
        return route(st, body)

    def _pattern(self, st, body):
        for k in ("kind", "freq", "amp", "a", "b", "text", "font", "figure",
                  "pulse_rate", "pulse_depth", "rot", "flip_x", "flip_y"):
            if k in body:
                if body[k] is None:
                    return self._json({"err": f"{k} must be a number"}, 400)
                st["pattern"][k] = body[k]
        if {"text", "font", "figure", "kind"} & set(body):
            st["pattern"]["sver"] += 1
        if "stream_on" in body:
            st["stream_on"] = bool(body["stream_on"])
        self._takeover(st)
        return self._json({"ok": True})

    def _takeover(self, st):
        """A hand on the panel cuts the burst on air short (end_burst)."""
        st["cycle"] = None
        st["mic"] = any(c["enabled"] and c["seq"]
                        for c in st["cycles"].values())
        for c in st["cycles"].values():
            c["running"] = False

    def _preset(self, st, body):
        op, name = body.get("op"), (body.get("name") or "").strip()
        if op not in ("save", "load", "delete", "move", "rename") or not name:
            return self._json({"err": "need op + name"}, 400)
        names = st["presets"]
        if op == "save":
            if st["cycle"] is not None:
                return self._json(
                    {"err": "cannot save — a cycle burst is on air"}, 409)
            if name not in names:
                if len(names) >= PRESETS_MAX:
                    return self._json({"err": f"max {PRESETS_MAX} presets"},
                                      400)
                names.append(name)
            return self._json({"ok": True})
        if name not in names:
            return self._json({"err": "no such preset"}, 404)
        if op == "load":
            self._takeover(st)
            return self._json({"ok": True})
        if op == "delete":
            names.remove(name)
            # A pair naming a preset that is gone plays half a burst, so the
            # controller clears the slot and says how many it touched.
            gone = []
            for g, c in st["cycles"].items():
                for pair in c["pairs"]:
                    for i, n in enumerate(pair):
                        if n == name:
                            pair[i] = ""
                            gone.append(g)
                c["seq"] = burst_seq(c, g)
            return self._json({"ok": True, "note": None if not gone else
                               f"cleared from {len(gone)} "
                               f"{'pair' if len(gone) == 1 else 'pairs'}"})
        if op == "move":
            try:
                idx = int(body.get("index", -1))
            except (TypeError, ValueError):
                return self._json({"err": "need index"}, 400)
            names.remove(name)
            names.insert(max(0, min(idx, len(names))), name)
            return self._json({"ok": True})
        new_name = (body.get("new_name") or "").strip()   # rename
        if not new_name:
            return self._json({"err": "need new_name"}, 400)
        if new_name in names:
            return self._json({"err": "name already used"}, 409)
        names[names.index(name)] = new_name
        for g, c in st["cycles"].items():
            for pair in c["pairs"]:
                for i, n in enumerate(pair):
                    if n == name:
                        pair[i] = new_name
            c["seq"] = burst_seq(c, g)
        return self._json({"ok": True})

    def _node(self, st, body):
        """Flip one node's switches. Only the keys present move."""
        try:
            sid = int(body.get("id"))
        except (TypeError, ValueError):
            return self._json({"err": "need a slave id"}, 400)
        if not 0 <= sid <= 255:
            return self._json({"err": "id out of range"}, 400)
        f = dict(st["nodes"].get(str(sid))
                 or {"stream": True, "logos": True, "artists": True})
        for k in ("stream", "logos", "artists"):
            if k in body:
                f[k] = bool(body[k])
        st["nodes"][str(sid)] = f
        return self._json({"ok": True, "id": sid, "flags": f})

    def _cycle(self, st, body):
        group = body.get("group")
        if group not in CYCLE_GROUPS:
            return self._json({"err": "need group logos|artists"}, 400)
        c = st["cycles"][group]
        for k in ("enabled", "show_s", "pause_s", "every_s", "idx", "pairs"):
            if k in body:
                c[k] = body[k]
        c["show_s"] = min(600, max(3, int(c["show_s"])))
        c["pause_s"] = min(600, max(0, int(c["pause_s"])))
        c["burst_s"] = 2 * c["show_s"] + c["pause_s"]
        # A period inside the burst would come due before the pair finished,
        # and the mic would never get the stream back.
        c["every_s"] = max(min(86400, max(10, int(c["every_s"]))),
                           c["burst_s"] + 10)
        c["idx"] = min(max(0, int(c["idx"])), max(0, len(c["pairs"]) - 1))
        c["seq"] = burst_seq(c, group)
        # Editing anything re-arms a full period out, so a slider nudge at
        # 4m59s does not fire as you let go.
        c["next_in"] = c["every_s"] if c["enabled"] and c["seq"] else None
        self._takeover(st)
        return self._json({"ok": True, "group": group, "cycle": c})

    def _cmd(self, st, body):
        ip, c = body.get("ip"), body.get("cmd") or {}
        s = next((x for x in st["slaves"] if x["ip"] == ip), None)
        if s is None:
            return self._json({"err": "no such slave"}, 404)
        if c.get("cmd") == "set_mode":
            s["mode"] = {"local": 0, "network": 1, "hybrid": 2}.get(
                c.get("mode"), s["mode"])
            s["source"] = 0 if s["mode"] == 0 else 1
        elif c.get("cmd") == "set_pattern":
            s["lpat"] = c.get("pattern", s["lpat"])
        self._takeover(st)
        return self._json({"ok": True})


def main():
    p = argparse.ArgumentParser(
        description="Mock UI preview server — the dashboard without hardware",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="scenarios:\n" + "\n".join(
            f"  {k:<8} {v}" for k, v in SCENARIOS.items()))
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="ok")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()

    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    srv.state = apply_scenario(base_state(), args.scenario)
    srv.scenario = args.scenario
    print(f"UI preview http://127.0.0.1:{args.port}  "
          f"[{args.scenario}: {SCENARIOS[args.scenario]}]")
    print("Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
