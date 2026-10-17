#!/usr/bin/env python3
"""Long-run bench of the slaves against the live show cycles (stream-concealment plan §4.5, "rig").

Run mode polls the controller's /api/state at 1 Hz into state.jsonl (every slave's counters
plus the controller's per-slave TX queue counters) and, when the XIAO is attached, records
each burst on one slave's RCA X/Y (xiao_capture.py) as burst_NNN.npz. Analyze mode folds that
into one row per burst and slave: loss, TX drops before the air, fill method, resets,
underruns, and (for the captured slave) parked-beam runs.

  soak.py run --minutes 60 --out /tmp/soak1            # XIAO D2/D3 on slave 164
  soak.py run --minutes 30 --node 121 --out /tmp/soak2
  soak.py analyze /tmp/soak1                           # table + summary.json

Stops early if any slave's battery reads below --vbat-stop mV (radio-off is 3300); readings
under 2000 mV are a floating sense line, ignored like the firmware does.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
COUNTERS = ("rx", "lost", "drop", "under", "loop_fills", "hold_fills", "gap_resets", "reanchor")
NODE_KEYS = COUNTERS + ("hist", "mode", "source", "rssi", "vbat_mv", "uptime", "depth", "ip")


def get_state(api):
    with urllib.request.urlopen(api + "/api/state", timeout=3) as r:
        return json.load(r)


def run(a):
    os.makedirs(a.out, exist_ok=True)
    xiao = bool(glob.glob("/dev/serial/by-id/*XIAO_RA4M1*")) and not a.no_capture
    log = open(os.path.join(a.out, "state.jsonl"), "a")
    end = time.time() + a.minutes * 60
    cap, n_burst, armed = None, 0, {}
    print(f"soak: node {a.node}, {a.minutes} min, capture={'on' if xiao else 'off'} -> {a.out}",
          flush=True)
    while time.time() < end:
        t = time.time()
        try:
            st = get_state(a.api)
        except OSError as e:
            log.write(json.dumps({"t": t, "err": str(e)}) + "\n")
            time.sleep(1)
            continue
        rec = {"t": t, "loop_frames": st.get("loop_frames"), "mic": st.get("mic"),
               "cycle": st.get("cycle"), "capture_node": a.node,
               "next_in": {g: c.get("next_in") for g, c in st["cycles"].items()},
               "nodes": {str(s["id"]): {k: s.get(k) for k in NODE_KEYS} for s in st["slaves"]},
               "tx": (st.get("net") or {}).get("tx")}
        log.write(json.dumps(rec) + "\n")
        log.flush()
        if cap is not None and cap.poll() is not None:
            cap = None
        # Arm a capture ~3 s before each burst: the tool needs ~1.5 s to start, and the
        # name airs LEAD (450 ms) after the step.
        for g, c in st["cycles"].items():
            ni = c.get("next_in")
            if xiao and cap is None and c.get("enabled") and ni is not None and ni <= 3 \
                    and armed.get(g) != c.get("phase"):
                armed[g] = c.get("phase")
                n_burst += 1
                prefix = os.path.join(a.out, f"burst_{n_burst:03d}_{g}")
                secs = (c.get("burst_s") or 10) + ni + 4
                with open(prefix + ".json", "w") as f:
                    json.dump({"group": g, "t_launch": t, "next_in": ni, "secs": secs}, f)
                cap = subprocess.Popen(
                    [sys.executable, os.path.join(HERE, "xiao_capture.py"), "--pins", a.pins,
                     "--seconds", str(secs), "--out", prefix],
                    stdout=open(prefix + ".log", "w"), stderr=subprocess.STDOUT)
            if ni is not None and ni > 10:
                armed.pop(g, None)
        low = [s for s in st["slaves"] if 2000 < (s.get("vbat_mv") or 0) < a.vbat_stop]
        if low:
            print(f"soak: slave {low[0]['id']} vbat {low[0]['vbat_mv']} mV < {a.vbat_stop}, "
                  "stopping", flush=True)
            break
        time.sleep(max(0.0, 1.0 - (time.time() - t)))
    if cap is not None:
        cap.wait()
    print("soak: done", flush=True)


def parked_runs(codes, rate, win_ms=4, tol=150, centre=(5550, 250)):
    """Stretches where both channels sit still for >= win_ms while the stream plays.

    Returns (stream_span_s, [(t_s, dur_ms, at_centre)]). "Plays" = 50 ms blocks with X
    std > 300 codes; the span is the first stretch of them lasting >= 1 s with no pause of
    1 s or more (a short cycle period puts the next burst's start in the same capture, and the
    mic between them is no part of this one). The span's first and last block are skipped
    (the mic -> name and name -> mic hand-offs sit near centre for a few ms).
    """
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view as sw
    c = codes.astype(np.int32)
    blk = rate // 20
    n = len(c) // blk
    act = np.flatnonzero(c[:n * blk, 0].reshape(n, blk).std(1) > 300)
    if not len(act):
        return (0.0, 0.0), []
    segs = np.split(act, np.flatnonzero(np.diff(act) >= 20) + 1)
    seg = next((s for s in segs if s[-1] - s[0] >= 20), segs[0])
    t0, t1 = seg[0] * blk, (seg[-1] + 1) * blk
    w = rate * win_ms // 1000
    flat = np.ones(len(c) - w + 1, bool)
    for ch in range(2):
        v = sw(c[:, ch], w)
        flat &= (v.max(1) - v.min(1)) < tol
    edges = np.flatnonzero(np.diff(np.r_[0, flat.astype(np.int8), 0]))
    runs = []
    for s, e in zip(edges[::2], edges[1::2]):
        if t0 + blk <= s and e + w <= t1 - blk:  # ignore the stream's own start/end
            x, y = c[s:e + w].mean(0)
            runs.append((s / rate, float((e - s + w) / rate * 1000),
                         bool(abs(x - centre[0]) < centre[1] and abs(y - centre[0]) < centre[1])))
    return (t0 / rate, t1 / rate), runs


def node_bursts(recs, nid):
    """NETWORK stints of one slave, from its own reported mode."""
    bursts, cur, prev = [], None, None
    for r in recs:
        n = r["nodes"].get(nid)
        if n is None or n.get("uptime") is None:
            continue
        if prev is not None and n["uptime"] < prev["nodes"][nid]["uptime"]:
            cur = None  # slave rebooted: counters restarted
        elif prev is not None and n["mode"] == 1 and cur is None:
            cur = {"start": prev}
        if cur is not None and r.get("cycle"):
            cur.setdefault("presets", set()).add(r["cycle"]["preset"])
        if cur is not None and n["mode"] == 0:
            cur["end"] = r
            bursts.append(cur)
            cur = None
        prev = r
    return bursts


def analyze(a):
    import numpy as np
    recs = [json.loads(l) for l in open(os.path.join(a.dir, "state.jsonl"))]
    for r in recs:  # one-node files (before 2026-10-17 evening): "node", always 164
        if r.get("node") and "nodes" not in r:
            r.update(nodes={"164": r["node"]}, capture_node=164)
    recs = [r for r in recs if r.get("nodes")]
    cap_node = str(recs[0]["capture_node"]) if recs else None
    caps = []
    for meta in sorted(glob.glob(os.path.join(a.dir, "burst_*.json"))):
        npz = meta[:-5] + ".npz"
        if os.path.exists(npz):
            caps.append((json.load(open(meta)), npz))
    rows = []
    for nid in sorted({k for r in recs for k in r["nodes"]}, key=int):
        for b in node_bursts(recs, nid):
            s, e = b["start"]["nodes"][nid], b["end"]["nodes"][nid]
            d = {k: (e[k] - s[k]) if e.get(k) is not None and s.get(k) is not None else None
                 for k in COUNTERS}
            hist = ([x - y for x, y in zip(e["hist"], s["hist"])]
                    if e.get("hist") and s.get("hist") else None)
            # Controller-side: datagrams the AP queue refused (EAGAIN), never on the air.
            ts, te = (b["start"].get("tx") or {}).get(e.get("ip")), \
                (b["end"].get("tx") or {}).get(e.get("ip"))
            tx_ok = te["ok"] - ts["ok"] if ts and te else None
            tx_full = te["full"] - ts["full"] if ts and te else None
            row = {"node": int(nid), "t": b["start"]["t"],
                   "dur_s": round(b["end"]["t"] - b["start"]["t"]),
                   "presets": sorted(b.get("presets", [])), "hist": hist,
                   "rssi": e.get("rssi"), "vbat_mv": e.get("vbat_mv"),
                   "tx_ok": tx_ok, "tx_full": tx_full, **d}
            sent = (d["rx"] or 0) + (d["lost"] or 0)
            row["loss_pct"] = round(100.0 * (d["lost"] or 0) / sent, 2) if sent else None
            row["txfull_pct"] = (round(100.0 * tx_full / (tx_ok + tx_full), 2)
                                 if tx_ok is not None and tx_ok + tx_full else None)
            # The capture armed ~3 s before this burst's start (the nearest such launch: at a
            # short cycle period the previous burst's capture is only ~14 s older).
            near = [c for c in caps if -2 < b["start"]["t"] - c[0]["t_launch"] < 10]
            cap = (min(near, key=lambda c: abs(b["start"]["t"] - c[0]["t_launch"] - 3))
                   if near and nid == cap_node else None)
            if cap is not None:
                z = np.load(cap[1])
                span, runs = parked_runs(z["codes"], int(z["rate"]))
                row.update(cap=os.path.basename(cap[1]), stream_s=round(span[1] - span[0], 2),
                           parked=len([r for r in runs if not r[2]]),
                           parked_ms=[round(r[1], 1) for r in runs if not r[2]],
                           centre=len([r for r in runs if r[2]]),
                           centre_ms=[round(r[1], 1) for r in runs if r[2]])
            rows.append(row)
    json.dump({"capture_node": cap_node, "bursts": rows},
              open(os.path.join(a.dir, "summary.json"), "w"), indent=1)
    hdr = ("node", "time", "s", "presets", "tx full%", "rx", "lost", "loss%", "loop", "hold",
           "reset", "under", "re-anc", "holes 1/2/3-4/5-30/>30", "parked", "centre", "rssi",
           "vbat")
    print(" | ".join(hdr))
    for r in sorted(rows, key=lambda r: (r["t"], r["node"])):
        print(" | ".join(str(x) for x in (
            r["node"], time.strftime("%H:%M:%S", time.localtime(r["t"])), r["dur_s"],
            "/".join(r["presets"]), r["txfull_pct"], r["rx"], r["lost"], r["loss_pct"],
            r["loop_fills"], r["hold_fills"], r["gap_resets"], r["under"], r["reanchor"],
            "/".join(map(str, r["hist"] or [])), r.get("parked", "-"),
            (f"{r['centre']} {r['centre_ms']}" if r.get("centre") else r.get("centre", "-")),
            r["rssi"], r["vbat_mv"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--minutes", type=float, default=60)
    r.add_argument("--out", required=True)
    r.add_argument("--api", default="http://10.42.0.127:8080")
    r.add_argument("--node", type=int, default=164)
    r.add_argument("--pins", default="2,3")
    r.add_argument("--no-capture", action="store_true")
    r.add_argument("--vbat-stop", type=int, default=3350)
    z = sub.add_parser("analyze")
    z.add_argument("dir")
    a = ap.parse_args()
    run(a) if a.cmd == "run" else analyze(a)


if __name__ == "__main__":
    main()
