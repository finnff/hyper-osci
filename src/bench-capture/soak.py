#!/usr/bin/env python3
"""Long-run bench of one slave against the live show cycles (stream-concealment plan §4.5, "rig").

Run mode polls the controller's /api/state at 1 Hz into state.jsonl and, when the XIAO is
attached, records each burst on the slave's RCA X/Y (xiao_capture.py) as burst_NNN.npz.
Analyze mode folds that into one row per burst: loss, fill method, resets, underruns, and
parked-beam runs found in the capture.

  soak.py run --minutes 60 --out /tmp/soak1            # 164, XIAO D2/D3
  soak.py analyze /tmp/soak1                           # table + summary.json

Stops early if the slave's battery reads below --vbat-stop mV (radio-off is 3300).
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
        node = next((s for s in st["slaves"] if s["id"] == a.node), None)
        rec = {"t": t, "loop_frames": st.get("loop_frames"), "mic": st.get("mic"),
               "cycle": st.get("cycle"),
               "next_in": {g: c.get("next_in") for g, c in st["cycles"].items()},
               "node": None if node is None else
               {k: node.get(k) for k in COUNTERS + ("hist", "mode", "source", "rssi",
                                                    "vbat_mv", "uptime", "depth")}}
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
        if node is not None and node.get("vbat_mv") and node["vbat_mv"] < a.vbat_stop:
            print(f"soak: vbat {node['vbat_mv']} mV < {a.vbat_stop}, stopping", flush=True)
            break
        time.sleep(max(0.0, 1.0 - (time.time() - t)))
    if cap is not None:
        cap.wait()
    print("soak: done", flush=True)


def parked_runs(codes, rate, win_ms=4, tol=150, centre=(5550, 250)):
    """Stretches where both channels sit still for >= win_ms while the stream plays.

    Returns (stream_span_s, [(t_s, dur_ms, at_centre)]). "Plays" = 50 ms blocks with X
    std > 300 codes, between the first and last such block; the first and last block are
    skipped (the mic -> name and name -> mic hand-offs sit near centre for a few ms).
    """
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view as sw
    c = codes.astype(np.int32)
    blk = rate // 20
    n = len(c) // blk
    act = np.flatnonzero(c[:n * blk, 0].reshape(n, blk).std(1) > 300)
    if not len(act):
        return (0.0, 0.0), []
    t0, t1 = act[0] * blk, (act[-1] + 1) * blk
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


def analyze(a):
    import numpy as np
    recs = [json.loads(l) for l in open(os.path.join(a.dir, "state.jsonl"))]
    recs = [r for r in recs if r.get("node")]
    # Bursts from the slave's own mode: NETWORK stints, counters diffed across each.
    bursts, cur = [], None
    for prev, r in zip(recs, recs[1:]):
        if r["node"]["uptime"] < prev["node"]["uptime"]:
            cur = None  # slave rebooted: counters restarted
            continue
        if r["node"]["mode"] == 1 and cur is None:
            cur = {"start": prev, "preset": None}
        if cur is not None and r.get("cycle"):
            cur["preset"] = cur["preset"] or r["cycle"]["preset"]
            cur.setdefault("presets", set()).add(r["cycle"]["preset"])
        if cur is not None and r["node"]["mode"] == 0:
            cur["end"] = r
            bursts.append(cur)
            cur = None
    caps = []
    for meta in sorted(glob.glob(os.path.join(a.dir, "burst_*.json"))):
        npz = meta[:-5] + ".npz"
        if os.path.exists(npz):
            caps.append((json.load(open(meta)), npz))
    rows = []
    for b in bursts:
        s, e = b["start"]["node"], b["end"]["node"]
        d = {k: (e[k] - s[k]) if e.get(k) is not None and s.get(k) is not None else None
             for k in COUNTERS}
        hist = ([x - y for x, y in zip(e["hist"], s["hist"])]
                if e.get("hist") and s.get("hist") else None)
        row = {"t": b["start"]["t"], "dur_s": round(b["end"]["t"] - b["start"]["t"]),
               "presets": sorted(b.get("presets", [])), "hist": hist,
               "rssi": e.get("rssi"), "vbat_mv": e.get("vbat_mv"), **d}
        sent = (d["rx"] or 0) + (d["lost"] or 0)
        row["loss_pct"] = round(100.0 * (d["lost"] or 0) / sent, 2) if sent else None
        # The capture launched shortly before this burst's start.
        cap = next((c for c in caps if -15 < b["start"]["t"] - c[0]["t_launch"] < 15), None)
        if cap is not None:
            z = np.load(cap[1])
            span, runs = parked_runs(z["codes"], int(z["rate"]))
            row.update(cap=os.path.basename(cap[1]), stream_s=round(span[1] - span[0], 2),
                       parked=len([r for r in runs if not r[2]]),
                       parked_ms=[round(r[1], 1) for r in runs if not r[2]],
                       centre=len([r for r in runs if r[2]]),
                       centre_ms=[round(r[1], 1) for r in runs if r[2]])
        rows.append(row)
    json.dump({"bursts": rows}, open(os.path.join(a.dir, "summary.json"), "w"), indent=1)
    hdr = ("time", "s", "presets", "rx", "lost", "loss%", "loop", "hold", "reset", "under",
           "re-anc", "holes 1/2/3-4/5-30/>30", "parked", "centre", "rssi", "vbat")
    print(" | ".join(hdr))
    for r in rows:
        print(" | ".join(str(x) for x in (
            time.strftime("%H:%M:%S", time.localtime(r["t"])), r["dur_s"],
            "/".join(r["presets"]), r["rx"], r["lost"], r["loss_pct"], r["loop_fills"],
            r["hold_fills"], r["gap_resets"], r["under"], r["reanchor"],
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
