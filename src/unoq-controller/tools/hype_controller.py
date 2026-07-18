#!/usr/bin/env python3
"""HYPEROSCI controller daemon: HYPE v1 streamer + web control panel.

Supersedes hype_sender.py (kept as a minimal protocol reference). Adds:
  - Embedded web UI (stdlib http.server, port 8080) — pattern / frequency /
    amplitude control, stream on/off, per-slave and all-slave mode toggles
    (network / local-mic / hybrid), identify, gain, reboot.
  - HYPE_CMD sender (JSON, unicast :5001) driving mode_manager::handle_command.

Streaming engine is inherited from hype_sender.py: SYNC multicast beacons,
deadline-stamped AUDIO unicast to every discovered slave, STATUS listener.

Usage (on the UNO-Q, as the AP host):
  python3 hype_controller.py [--iface-ip 192.168.50.1] [--http-port 8080]
Then browse http://10.42.0.128:8080 (USB tether) or http://192.168.50.1:8080.
"""

import argparse
import json
import math
import select
import socket
import struct
import sys
import threading
import time
from array import array
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HYPE_MAGIC = 0x45505948
HYPE_VERSION = 1
HYPE_AUDIO, HYPE_SYNC, HYPE_CMD, HYPE_STATUS = 1, 2, 3, 4

MCAST_GROUP = "239.0.0.1"
PORT_AUDIO, PORT_CTRL, PORT_STATUS = 5000, 5001, 5002

HDR = struct.Struct("<IBBHIQ")  # magic, ver, type, flags, seq, timestamp_us
AUDIO_HDR = struct.Struct("<IHH")  # sample_rate, frame_count, reserved
STATUS_PAYLOAD = struct.Struct("<6sBBBbHHIIIIi")

SAMPLE_RATE = 48000
FRAMES = 240  # 5 ms
PACKET_US = FRAMES * 1_000_000 // SAMPLE_RATE
LEAD_US = 80_000  # deadline lead: becomes the slaves' steady-state depth
SYNC_INTERVAL_US = 500_000

MODE_NAMES = {0: "local", 1: "network", 2: "hybrid"}


def mono_us():
    return time.monotonic_ns() // 1000


class State:
    """Shared between the stream loop and HTTP threads. Lock-protected."""

    def __init__(self, pattern):
        self.lock = threading.Lock()
        self.kind = pattern       # circle | lissajous | rose
        self.freq = 100.0         # base Hz
        self.amp = 0.8            # 0..1 of int16 headroom
        self.ratio_a = 3          # lissajous X multiplier / rose petal count
        self.ratio_b = 2          # lissajous Y multiplier
        self.stream_on = True
        self.slaves = {}          # ip -> dict(status fields + last_us)

    def pattern_params(self):
        with self.lock:
            return (self.kind, self.freq, self.amp * 32000.0,
                    self.ratio_a, self.ratio_b)

    def snapshot(self):
        now = mono_us()
        with self.lock:
            return {
                "pattern": {"kind": self.kind, "freq": self.freq,
                            "amp": self.amp, "a": self.ratio_a,
                            "b": self.ratio_b},
                "stream_on": self.stream_on,
                "slaves": [dict(s, age_ms=(now - s["last_us"]) // 1000)
                           for s in self.slaves.values()],
            }


class PatternGen:
    """Stateful stereo test-pattern generator (X = L, Y = R).

    Single base phase; ratios are multipliers so ratio changes stay
    phase-continuous (no scope-trace jump mid-stream).
    """

    def __init__(self):
        self.phase = 0.0

    def block(self, n, params):
        kind, freq, amp, a, b = params
        out = array("h", bytes(4 * n))  # n stereo frames, zeroed
        two_pi = 2.0 * math.pi
        step = two_pi * freq / SAMPLE_RATE
        p = self.phase
        sin = math.sin
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
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HYPEROSCI</title>
<style>
:root { --bg:#070b07; --panel:#0d140d; --line:#1d2b1d; --fg:#c9e8c9;
        --dim:#5f7a5f; --ph:#39ff14; --warn:#ffb347; --bad:#ff5252; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:var(--bg); color:var(--fg); font:14px/1.45 ui-monospace,
       "JetBrains Mono",Menlo,Consolas,monospace; padding:16px; }
h1 { font-size:18px; letter-spacing:.35em; color:var(--ph);
     text-shadow:0 0 12px rgba(57,255,20,.55); }
header { display:flex; align-items:center; gap:16px; flex-wrap:wrap;
         margin-bottom:14px; }
.panel { background:var(--panel); border:1px solid var(--line);
         border-radius:8px; padding:14px; }
#top { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:14px; }
#scope { width:260px; height:260px; background:#020402; border-radius:8px;
         border:1px solid var(--line); flex:none; }
#controls { flex:1; min-width:280px; display:flex; flex-direction:column;
            gap:12px; }
.row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.row label { width:70px; color:var(--dim); flex:none; }
input[type=range] { flex:1; min-width:120px; accent-color:var(--ph); }
.val { width:72px; text-align:right; color:var(--ph); flex:none; }
button, select, input[type=number] { background:#101b10; color:var(--fg);
  border:1px solid var(--line); border-radius:5px; padding:5px 12px;
  font:inherit; cursor:pointer; }
button:hover { border-color:var(--ph); }
button.on { background:var(--ph); color:#031003; border-color:var(--ph);
            font-weight:bold; }
button.danger:hover { border-color:var(--bad); color:var(--bad); }
.seg { display:flex; gap:0; }
.seg button { border-radius:0; }
.seg button:first-child { border-radius:5px 0 0 5px; }
.seg button:last-child { border-radius:0 5px 5px 0; }
#slaves { display:grid; gap:14px;
          grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); }
.card h2 { font-size:15px; color:var(--ph); margin-bottom:6px; }
.card h2 small { color:var(--dim); font-weight:normal; margin-left:8px; }
.stats { display:grid; grid-template-columns:repeat(3,1fr); gap:2px 12px;
         margin:8px 0; color:var(--dim); font-size:12.5px; }
.stats b { color:var(--fg); font-weight:normal; }
.badge { padding:1px 8px; border-radius:4px; border:1px solid var(--line);
         font-size:12px; }
.badge.net { color:var(--ph); border-color:var(--ph); }
.badge.mic { color:var(--warn); border-color:var(--warn); }
.stale { opacity:.35; }
#none { color:var(--dim); padding:24px; text-align:center; }
</style></head><body>
<header>
  <h1>HYPEROSCI</h1>
  <button id="stream" class="on" onclick="toggleStream()">STREAM ON</button>
  <span style="color:var(--dim)">all slaves:</span>
  <span class="seg">
    <button onclick="modeAll('network')">NETWORK</button>
    <button onclick="modeAll('local')">MIC</button>
    <button onclick="modeAll('hybrid')">HYBRID</button>
  </span>
</header>
<div id="top">
  <canvas id="scope" width="520" height="520"></canvas>
  <div id="controls" class="panel">
    <div class="row"><label>pattern</label>
      <span class="seg" id="kindseg">
        <button data-k="circle" onclick="setP({kind:'circle'})">circle</button>
        <button data-k="lissajous" onclick="setP({kind:'lissajous'})">lissajous</button>
        <button data-k="rose" onclick="setP({kind:'rose'})">rose</button>
      </span></div>
    <div class="row"><label>freq</label>
      <input type="range" id="freq" min="10" max="400" step="1"
             oninput="live()" onchange="setP({freq:+this.value})">
      <span class="val" id="freqv"></span></div>
    <div class="row"><label>amp</label>
      <input type="range" id="amp" min="0" max="100" step="1"
             oninput="live()" onchange="setP({amp:this.value/100})">
      <span class="val" id="ampv"></span></div>
    <div class="row" id="ratiorow"><label>ratio</label>
      <input type="number" id="ra" min="1" max="9" style="width:64px"
             onchange="setP({a:+this.value})"> :
      <input type="number" id="rb" min="1" max="9" style="width:64px"
             onchange="setP({b:+this.value})"></div>
  </div>
</div>
<div id="slaves" class="panel"><div id="none">no slaves discovered yet…</div></div>
<script>
"use strict";
let S = null, dirty = true;

function post(path, body) {
  return fetch(path, {method:"POST", body:JSON.stringify(body)})
    .then(() => poll());
}
const setP = p => post("/api/pattern", p);
const cmd = (ip, c) => post("/api/cmd", {ip:ip, cmd:c});
const modeAll = m => cmd("all", {cmd:"set_mode", mode:m});
function toggleStream() { post("/api/pattern", {stream: !S.stream_on}); }

function fmtUp(s) {
  if (s >= 3600) return (s/3600).toFixed(1) + "h";
  if (s >= 60) return Math.floor(s/60) + "m" + (s%60) + "s";
  return s + "s";
}
const fmtN = n => n >= 1e6 ? (n/1e6).toFixed(1)+"M"
              : n >= 1e3 ? (n/1e3).toFixed(1)+"k" : ""+n;

function live() {  // slider preview before the change commits
  document.getElementById("freqv").textContent =
      document.getElementById("freq").value + " Hz";
  document.getElementById("ampv").textContent =
      document.getElementById("amp").value + " %";
  drawScope(+document.getElementById("freq").value);
}

function drawScope() {
  if (!S) return;
  const c = document.getElementById("scope"), g = c.getContext("2d");
  const p = S.pattern, w = c.width, h = c.height;
  const amp = (+document.getElementById("amp").value / 100) * w * 0.44;
  g.fillStyle = "#020402"; g.fillRect(0, 0, w, h);
  g.strokeStyle = "#0e1a0e"; g.lineWidth = 1;         // graticule
  for (let i = 1; i < 8; i++) {
    g.beginPath(); g.moveTo(i*w/8, 0); g.lineTo(i*w/8, h); g.stroke();
    g.beginPath(); g.moveTo(0, i*h/8); g.lineTo(w, i*h/8); g.stroke();
  }
  g.strokeStyle = "#39ff14"; g.lineWidth = 2;
  g.shadowColor = "#39ff14"; g.shadowBlur = 8;
  g.beginPath();
  const N = 1200, k = p.kind, a = p.a, b = p.b, hp = Math.PI/2;
  for (let i = 0; i <= N; i++) {
    const t = 2*Math.PI*i/N;
    let x, y;
    if (k === "lissajous") { x = Math.sin(a*t + hp); y = Math.sin(b*t); }
    else if (k === "rose") { const r = Math.sin(a*t);
                             x = r*Math.cos(t); y = r*Math.sin(t); }
    else { x = Math.cos(t); y = Math.sin(t); }
    const px = w/2 + amp*x, py = h/2 - amp*y;
    i ? g.lineTo(px, py) : g.moveTo(px, py);
  }
  g.stroke(); g.shadowBlur = 0;
}

function card(s) {
  const src = s.source ? '<span class="badge net">NET</span>'
                       : '<span class="badge mic">MIC</span>';
  const seg = ["network","local","hybrid"].map((m, i) =>
    `<button class="${s.mode===i?'on':''}"
       onclick="cmd('${s.ip}',{cmd:'set_mode',mode:'${m}'})">` +
    ["NETWORK","MIC","HYBRID"][i] + "</button>").join("");
  return `<div class="card ${s.age_ms>3000?'stale':''}" style="padding:4px 0">
    <h2>SLAVE ${s.id} ${src}<small>${s.ip} · ${s.mac}</small></h2>
    <div class="stats">
      <span>rssi <b>${s.rssi} dBm</b></span>
      <span>vbat <b>${s.vbat_mv} mV</b></span>
      <span>up <b>${fmtUp(s.uptime)}</b></span>
      <span>buf <b>${s.depth}f</b></span>
      <span>rx <b>${fmtN(s.rx)}</b></span>
      <span>drop <b>${fmtN(s.drop)}</b></span>
      <span>under <b>${fmtN(s.under)}</b></span>
      <span>age <b>${(s.age_ms/1000).toFixed(1)}s</b></span>
    </div>
    <div class="row">
      <span class="seg">${seg}</span>
      <button onclick="cmd('${s.ip}',{cmd:'identify'})">ID</button>
      <label style="width:auto;color:var(--dim)">gain</label>
      <input type="range" min="0" max="100" value="100" style="width:90px"
        onchange="cmd('${s.ip}',{cmd:'set_gain',gain:this.value/100})">
      <button class="danger"
        onclick="if(confirm('reboot slave ${s.id}?'))
                 cmd('${s.ip}',{cmd:'reboot'})">reboot</button>
    </div></div>`;
}

function render() {
  const p = S.pattern;
  document.getElementById("stream").textContent =
      S.stream_on ? "STREAM ON" : "STREAM OFF";
  document.getElementById("stream").className = S.stream_on ? "on" : "";
  for (const b of document.querySelectorAll("#kindseg button"))
    b.className = b.dataset.k === p.kind ? "on" : "";
  document.getElementById("ratiorow").style.visibility =
      p.kind === "circle" ? "hidden" : "visible";
  // Don't fight the user mid-drag: only sync widgets nobody is touching.
  const act = document.activeElement;
  const sync = (id, v) => { const e = document.getElementById(id);
                            if (e !== act) e.value = v; };
  sync("freq", p.freq); sync("amp", Math.round(p.amp*100));
  sync("ra", p.a); sync("rb", p.b);
  live();
  const div = document.getElementById("slaves");
  if (!S.slaves.length) {
    div.innerHTML = '<div id="none">no slaves discovered yet…</div>';
  } else if (!(act && div.contains(act))) {  // keep gain sliders draggable
    div.innerHTML = S.slaves.sort((a,b)=>a.id-b.id).map(card).join("");
  }
}

async function poll() {
  try { S = await (await fetch("/api/state")).json(); render(); }
  catch (e) { /* controller restarting; retry next tick */ }
}
poll(); setInterval(poll, 1000);
</script></body></html>
"""


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
            else:
                self._json({"err": "not found"}, 404)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n)) if n else {}
            except (ValueError, json.JSONDecodeError):
                return self._json({"err": "bad json"}, 400)

            if self.path == "/api/pattern":
                with state.lock:
                    if body.get("kind") in ("circle", "lissajous", "rose"):
                        state.kind = body["kind"]
                    if "freq" in body:
                        state.freq = min(2000.0, max(1.0, float(body["freq"])))
                    if "amp" in body:
                        state.amp = min(1.0, max(0.0, float(body["amp"])))
                    if "a" in body:
                        state.ratio_a = min(9, max(1, int(body["a"])))
                    if "b" in body:
                        state.ratio_b = min(9, max(1, int(body["b"])))
                    if "stream" in body:
                        state.stream_on = bool(body["stream"])
                self._json({"ok": True})
            elif self.path == "/api/cmd":
                ip, cmd_obj = body.get("ip"), body.get("cmd")
                if not ip or not isinstance(cmd_obj, dict):
                    return self._json({"err": "need ip + cmd"}, 400)
                cmds.send(ip, cmd_obj)
                self._json({"ok": True})
            else:
                self._json({"err": "not found"}, 404)

    return Handler


# ---------------------------------------------------------------------------
# Streaming engine (from hype_sender.py, driven by State)
# ---------------------------------------------------------------------------

def stream_loop(state, iface_ip):
    def make_ctrl():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                         socket.inet_aton(iface_ip))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        except OSError:
            pass  # iface may be mid-bounce; retried on next failure
        return s

    ctrl = make_ctrl()
    audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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

    while True:
        now = mono_us()

        if now >= next_sync:
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
            seq_sync += 1
            next_sync += SYNC_INTERVAL_US

        with state.lock:
            streaming = state.stream_on
            targets = list(state.slaves.keys())

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
                    except OSError:
                        pass  # iface mid-bounce: drop; slave conceals
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
                    with state.lock:
                        if src_ip not in state.slaves:
                            print(f"[discovered] slave id={sid} at {src_ip} "
                                  f"mac={mac.hex(':')}", flush=True)
                        state.slaves[src_ip] = {
                            "ip": src_ip, "mac": mac.hex(":"), "id": sid,
                            "mode": mode, "source": source, "rssi": rssi,
                            "vbat_mv": vbat, "depth": depth, "rx": rx,
                            "drop": dropped, "under": underruns,
                            "uptime": uptime, "offs": offset,
                            "last_us": mono_us(),
                        }

        # Forget slaves silent for > 5 s so we stop streaming into the void.
        with state.lock:
            for ip in [ip for ip, s in state.slaves.items()
                       if now - s["last_us"] > 5_000_000]:
                print(f"[lost] {ip}", flush=True)
                del state.slaves[ip]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface-ip", default="192.168.50.1",
                    help="local IP of the AP interface (multicast egress)")
    ap.add_argument("--pattern", default="circle",
                    choices=["circle", "lissajous", "rose"])
    ap.add_argument("--http-port", type=int, default=8080)
    args = ap.parse_args()

    state = State(args.pattern)
    cmds = CmdSender(state)

    httpd = ThreadingHTTPServer(("0.0.0.0", args.http_port),
                                make_http_handler(state, cmds))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print(f"[controller] web on :{args.http_port}, iface={args.iface_ip}, "
          f"lead={LEAD_US/1000:.0f}ms", flush=True)
    stream_loop(state, args.iface_ip)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
