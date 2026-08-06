#!/usr/bin/env python3
"""Mock UI preview server — test the dashboard without hardware.

    python3 uipreview.py                 # healthy rig
    python3 uipreview.py --scenario fail # one slave failing
    python3 uipreview.py --port 8081

Then open http://127.0.0.1:8080/.

The state below mirrors what the real controller publishes, field for field:
`lost` and `uptime` are integers (packets, seconds), `net.tx` is keyed by
slave IP, and `font` is a name from TEXT_FONTS. A mock that drifts from the
board is worse than no mock -- it renders a page that cannot exist.

POSTs are answered the way the controller answers them (unknown op -> 400,
save during a hold -> 409, rename rewrites the timers that pointed at the old
name) and they mutate this process's state, so the set-list and timer paths
can be exercised end to end.
"""
import argparse
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from hype_controller import PAGE, PRESETS_MAX

PRESETS = ["HYPEROSCI", "thanks claude", "Stoom", "HYPERWAVES",
           "Flux Collective", "Anémi", "Guild Navigator"]


def slave(ip, sid, **kw):
    s = {"ip": ip, "mac": f"8c:d0:b2:a9:08:{sid:02x}", "id": sid,
         "mode": 1, "source": 1, "rssi": -57, "vbat_mv": 3980,
         "depth": 21600, "rx": 2657034, "drop": 2993, "under": 34,
         "lost": 80756, "uptime": 13896, "offs": -2147483648,
         "lpat": "mic", "last_us": 0, "age_ms": 300}
    s.update(kw)
    return s


def base_state():
    return {
        "pattern": {"kind": "text", "freq": 50.0, "amp": 0.9, "a": 3, "b": 2,
                    "text": "HYPERWAVES", "font": "duplex",
                    "pulse_rate": 1.2, "pulse_depth": 0.0, "rot": -0.01,
                    "flip_x": False, "flip_y": False, "tver": 373},
        "presets": list(PRESETS),
        "stream_on": True,
        "slaves": [
            slave("192.168.50.228", 121),
            slave("192.168.50.229", 122, mode=2, source=0, rssi=-66,
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
        "timers": [{"id": 2, "preset": "Stoom", "enabled": True,
                    "targets": [], "hold_s": 5, "every_s": 60,
                    "next_in": 43}],
        "hold": None,
    }


SCENARIOS = {
    "ok": "healthy four-slave rig",
    "fail": "slave 123 dropping packets on a weak link",
    "dark": "feed muted — nothing is being streamed",
    "apdown": "the AP is down, no interface holds the address",
    "hold": "a timer hold is on air",
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
    elif name == "hold":
        st["hold"] = {"id": 2, "preset": "Stoom", "left_s": 3}
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
        elif self.path.startswith("/api/textpreview"):
            pts = [[i / 32 - 1, 0.5 * (1 if (i // 8) % 2 else -1)]
                   for i in range(64)]
            self._json({"ver": st["pattern"]["tver"], "pts": pts})
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
                 "/api/timer": self._timer, "/api/cmd": self._cmd}.get(self.path)
        if route is None:
            return self._json({"err": "not found"}, 404)
        return route(st, body)

    def _pattern(self, st, body):
        for k in ("kind", "freq", "amp", "a", "b", "text", "font",
                  "pulse_rate", "pulse_depth", "rot", "flip_x", "flip_y"):
            if k in body:
                if body[k] is None:
                    return self._json({"err": f"{k} must be a number"}, 400)
                st["pattern"][k] = body[k]
        if "text" in body or "font" in body:
            st["pattern"]["tver"] += 1
        if "stream_on" in body:
            st["stream_on"] = bool(body["stream_on"])
        return self._json({"ok": True})

    def _preset(self, st, body):
        op, name = body.get("op"), (body.get("name") or "").strip()
        if op not in ("save", "load", "delete", "move", "rename") or not name:
            return self._json({"err": "need op + name"}, 400)
        names = st["presets"]
        if op == "save":
            if st["hold"] is not None:
                return self._json({"err": "cannot save — timer hold active"},
                                  409)
            if name not in names:
                if len(names) >= PRESETS_MAX:
                    return self._json({"err": f"max {PRESETS_MAX} presets"},
                                      400)
                names.append(name)
            return self._json({"ok": True})
        if name not in names:
            return self._json({"err": "no such preset"}, 404)
        if op == "load":
            return self._json({"ok": True})
        if op == "delete":
            names.remove(name)
            paused = [t for t in st["timers"]
                      if t["preset"] == name and t["enabled"]]
            for t in paused:
                t["enabled"] = False
            return self._json({"ok": True, "note": None if not paused else
                               f"{len(paused)} timer(s) paused — they "
                               f"showed \"{name}\""})
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
        for t in st["timers"]:
            if t["preset"] == name:
                t["preset"] = new_name
        return self._json({"ok": True})

    def _timer(self, st, body):
        op = body.get("op")
        if op not in ("save", "delete", "toggle", "fire"):
            return self._json({"err": "bad op"}, 400)
        if op == "save":
            if body.get("preset") not in st["presets"]:
                return self._json({"err": "no such preset"}, 404)
            every = max(10, int(body.get("every_s", 300)))
            hold = max(1, int(body.get("hold_s", 15)))
            tid = int(body.get("id") or 0)
            t = {"id": tid or (max([x["id"] for x in st["timers"]] or [0]) + 1),
                 "preset": body["preset"], "enabled": True,
                 "targets": list(body.get("targets") or []),
                 "hold_s": hold, "every_s": every, "next_in": every}
            st["timers"] = [x for x in st["timers"] if x["id"] != t["id"]]
            st["timers"].append(t)
            # W8: echo the clamped values, so a page that asked for 2 s and
            # got the 10 s floor can show what is actually running.
            return self._json({"ok": True, "id": t["id"],
                               "every_s": every, "hold_s": hold})
        try:
            tid = int(body.get("id", 0))
        except (TypeError, ValueError):
            return self._json({"err": "need id"}, 400)
        t = next((x for x in st["timers"] if x["id"] == tid), None)
        if t is None:
            return self._json({"err": "no such timer"}, 404)
        if op == "delete":
            st["timers"].remove(t)
        elif op == "toggle":
            t["enabled"] = not t["enabled"]
            t["next_in"] = t["every_s"] if t["enabled"] else None
        else:  # fire
            if not st["stream_on"]:
                return self._json({"err": "not now — feed is muted"}, 409)
            st["hold"] = {"id": t["id"], "preset": t["preset"],
                          "left_s": t["hold_s"]}
            t["next_in"] = t["every_s"]   # F12: a hand-fired hold costs a turn
        return self._json({"ok": True})

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
        st["hold"] = None   # a hand on the panel ends any hold (F11)
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
