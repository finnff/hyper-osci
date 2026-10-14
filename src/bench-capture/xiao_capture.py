#!/usr/bin/env python3
"""Host side of the XIAO RA4M1 analog capture (docs/hardware/bench-capture.md §4.3).

Talks to src/bench-capture/xiao_adc over USB CDC, checks block continuity, saves the raw
14-bit codes to .npz and optionally renders an X/Y + time-strip PNG.

  xiao_capture.py --pins 2,3 --seconds 5 --png        # slave B on D2 (X) / D3 (Y)
  xiao_capture.py --blast 5                           # raw USB CDC throughput
  xiao_capture.py --cmd info --cmd "ledtest 40 0 0"   # text commands, replies printed
"""
import argparse
import glob
import struct
import sys
import time

import numpy as np
import serial

MAGIC = b"XADC"
HEADER = struct.Struct("<4sIHBBI")  # magic, seq, n_frames, n_ch, flags, dropped
FLAG_SCAN_OVERRUN = 0x01
FLAG_LATE_READ = 0x02
ADC_FULL_SCALE = 16384  # 14-bit
VREF = 3.3              # AVCC0, nominal


def code_to_vin(codes):
    """Nominal front-end inverse: V_pin = (V_in + 3.3)/3. Calibrate per channel for real numbers."""
    v_pin = codes.astype(np.float64) / ADC_FULL_SCALE * VREF
    return 3.0 * v_pin - VREF


def find_port():
    ports = glob.glob("/dev/serial/by-id/*XIAO_RA4M1*")
    if not ports:
        sys.exit("no XIAO RA4M1 under /dev/serial/by-id")
    return ports[0]


def read_line(ser, timeout=2.0):
    end = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < end:
        c = ser.read(1)
        if c == b"\n":
            return buf.decode(errors="replace").strip()
        buf += c
    raise TimeoutError(f"no line from device (got {buf!r})")


def quiesce(ser):
    """Stop any capture and drain until the line has been quiet for 0.3 s.

    Returns the device's "stopped ..." line (ISR cost stats), or "" if none came.
    """
    ser.write(b"stop\n")
    quiet_since = time.monotonic()
    deadline = quiet_since + 5.0
    tail = b""
    while time.monotonic() < deadline:
        c = ser.read(65536)
        if c:
            tail = (tail + c)[-256:]
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > 0.3:
            i = tail.rfind(b"stopped")
            return tail[i:].split(b"\n")[0].decode(errors="replace").strip() if i >= 0 else ""
    sys.exit("device keeps streaming after stop")


def command(ser, cmd):
    """Send one text command and print every reply line until the line goes quiet."""
    ser.write(cmd.encode() + b"\n")
    buf = b""
    quiet_since = time.monotonic()
    while time.monotonic() - quiet_since < 0.3:
        c = ser.read(4096)
        if c:
            buf += c
            quiet_since = time.monotonic()
    for ln in buf.decode(errors="replace").splitlines():
        print(f"{cmd} -> {ln.strip()}")


def blast(ser, seconds, load=False):
    ser.write(f"blast {seconds}{' load' if load else ''}\n".encode())
    t0 = time.monotonic()
    total = 0
    tail = b""
    while True:
        chunk = ser.read(65536)
        total += len(chunk)
        tail = (tail + chunk)[-200:]
        if b"blast_done" in tail and tail.endswith(b"\n"):
            break
        if time.monotonic() - t0 > seconds + 5:
            break
    dt = time.monotonic() - t0
    print(tail.decode(errors="replace").strip().splitlines()[-1])
    print(f"host: {total} bytes in {dt:.2f} s = {total / dt / 1e3:.0f} KB/s")


def capture(ser, rate, pins, seconds):
    ser.write(b"info\n")
    print(read_line(ser))
    ser.write(f"cap {rate} {pins}\n".encode())
    ack = read_line(ser)
    print(ack)
    if not ack.startswith("ok"):
        sys.exit(1)

    buf = bytearray()
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        buf += ser.read(max(1, ser.in_waiting))
    host_s = time.monotonic() - t0
    print(quiesce(ser))

    blocks, seqs, flags, dropped = [], [], [], 0
    i = buf.find(MAGIC)
    skipped = i if i >= 0 else len(buf)
    n_ch = None
    while i >= 0 and i + HEADER.size <= len(buf):
        magic, seq, n_frames, n_ch, fl, dropped = HEADER.unpack_from(buf, i)
        size = HEADER.size + n_frames * n_ch * 2
        if i + size > len(buf):
            break
        blocks.append(np.frombuffer(buf, "<u2", n_frames * n_ch, i + HEADER.size).reshape(n_frames, n_ch))
        seqs.append(seq)
        flags.append(fl)
        nxt = i + size
        if buf[nxt:nxt + 4] != MAGIC and nxt + 4 <= len(buf):
            j = buf.find(MAGIC, nxt)
            skipped += (j - nxt) if j >= 0 else len(buf) - nxt
            nxt = j
        i = nxt

    if not blocks:
        sys.exit("no blocks received")
    seqs = np.array(seqs)
    gaps = int(np.sum(np.diff(seqs) - 1))
    fl = np.array(flags)
    overrun = int(np.count_nonzero(fl & FLAG_SCAN_OVERRUN))
    late = int(np.count_nonzero(fl & FLAG_LATE_READ))
    codes = np.concatenate(blocks)
    print(f"blocks={len(blocks)} seq {seqs[0]}..{seqs[-1]} gaps={gaps} device_dropped={dropped} "
          f"scan_overrun_blocks={overrun} late_read_blocks={late} junk_bytes={skipped}")
    print(f"frames={len(codes)} = {len(codes) / rate:.2f} s of signal in {host_s:.2f} s host time")
    vin = code_to_vin(codes)
    for k, p in enumerate(pins.split(",")):
        c = codes[:, k]
        v = vin[:, k]
        print(f"  D{p}: code min/mean/max {c.min()}/{c.mean():.0f}/{c.max()}  std {c.std():.1f}  "
              f"V_in(nominal) {v.min():+.3f} .. {v.max():+.3f}  mean {v.mean():+.3f}")
    return codes, seqs, gaps


def render(path, codes, rate, pins):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vin = code_to_vin(codes)
    names = [f"D{p}" for p in pins.split(",")]
    fig = plt.figure(figsize=(12, 6), facecolor="black")
    if vin.shape[1] >= 2:
        ax = fig.add_axes([0.02, 0.05, 0.42, 0.9], facecolor="black")
        n = min(len(vin), rate // 5)  # last 200 ms
        ax.plot(vin[-n:, 0], vin[-n:, 1], color="#40ff70", lw=0.6, alpha=0.6)
        ax.set_xlim(-3.3, 3.3)
        ax.set_ylim(-3.3, 3.3)
        ax.set_aspect("equal")
        ax.set_title(f"X={names[0]} Y={names[1]}, last {n / rate * 1e3:.0f} ms (nominal V)", color="w")
        ax.tick_params(colors="gray")
    ax2 = fig.add_axes([0.5, 0.1, 0.48, 0.8], facecolor="black")
    n = min(len(vin), rate // 20)  # 50 ms
    t = np.arange(n) / rate * 1e3
    for k, name in enumerate(names):
        ax2.plot(t, vin[:n, k], lw=0.8, label=name)
    ax2.set_xlabel("ms", color="gray")
    ax2.set_ylabel("V_in (nominal)", color="gray")
    ax2.tick_params(colors="gray")
    ax2.legend(facecolor="black", labelcolor="w")
    ax2.set_title("first 50 ms", color="w")
    fig.savefig(path, dpi=110, facecolor="black")
    print(f"png: {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--pins", default="2,3", help="XIAO pins, comma list (D0-D3, D5)")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default="/tmp/xiao_cap", help="output prefix (.npz, .png)")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--blast", type=int, metavar="SECONDS", help="USB throughput test instead of capture")
    ap.add_argument("--load", action="store_true",
                    help="with --blast: keep the 4 ch / 48 kSa/s sampling ISR running")
    ap.add_argument("--cmd", action="append", metavar="TEXT",
                    help="send a text command (info, led 2,3, ledtest r g b, wsprobe on, mon ...) "
                         "instead of capturing; repeatable")
    a = ap.parse_args()

    ser = serial.Serial(a.port or find_port(), 115200, timeout=0.2)
    quiesce(ser)
    if a.cmd:
        for c in a.cmd:
            command(ser, c)
        return
    if a.blast:
        blast(ser, a.blast, a.load)
        return
    codes, seqs, gaps = capture(ser, a.rate, a.pins, a.seconds)
    np.savez(a.out + ".npz", codes=codes, seqs=seqs, rate=a.rate, pins=a.pins)
    print(f"npz: {a.out}.npz")
    if a.png:
        render(a.out + ".png", codes, a.rate, a.pins)


if __name__ == "__main__":
    main()
