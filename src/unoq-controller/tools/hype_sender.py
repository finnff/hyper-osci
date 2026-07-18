#!/usr/bin/env python3
"""Minimal HYPEROSCI controller-side sender for bring-up (W1/W2).

Implements the HYPE v1 wire protocol (src/esp32-slave/include/protocol.h):
  - SYNC beacons, multicast 239.0.0.1:5001 every 500 ms (discovery bootstrap
    + timesync). Slaves that hear a beacon start unicasting STATUS back.
  - STATUS listener on :5002 — prints each slave's health once a second and
    learns slave IPs (no config needed).
  - AUDIO unicast :5000 to every discovered slave: 240-frame / 5 ms packets,
    deadline-stamped LEAD_US ahead of the master clock, test-pattern content.

Usage (on the UNO-Q, as the AP host):
  python3 hype_sender.py [--iface-ip 192.168.50.1] [--pattern circle|lissajous]
"""

import argparse
import math
import select
import socket
import struct
import sys
import time
from array import array

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


class PatternGen:
    """Stateful stereo test-pattern generator (X = L, Y = R)."""

    def __init__(self, kind):
        self.kind = kind
        self.phase_a = 0.0
        self.phase_b = 0.0

    def block(self, n):
        out = array("h", bytes(4 * n))  # n stereo frames, zeroed
        two_pi = 2.0 * math.pi
        if self.kind == "lissajous":
            fa, fb, amp = 150.0, 100.0, 26000.0
        else:  # circle
            fa, fb, amp = 100.0, 100.0, 26000.0
        step_a = two_pi * fa / SAMPLE_RATE
        step_b = two_pi * fb / SAMPLE_RATE
        pa, pb = self.phase_a, self.phase_b
        off = 0.0 if self.kind == "lissajous" else math.pi / 2.0
        for i in range(n):
            out[2 * i] = int(amp * math.sin(pa + off))  # X (cos for circle)
            out[2 * i + 1] = int(amp * math.sin(pb))    # Y
            pa = (pa + step_a) % two_pi
            pb = (pb + step_b) % two_pi
        self.phase_a, self.phase_b = pa, pb
        return out.tobytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface-ip", default="192.168.50.1",
                    help="local IP of the AP interface (multicast egress)")
    ap.add_argument("--pattern", default="circle",
                    choices=["circle", "lissajous"])
    args = ap.parse_args()

    def make_ctrl():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                         socket.inet_aton(args.iface_ip))
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

    gen = PatternGen(args.pattern)
    slaves = {}  # ip -> last_status_mono_us
    seq_sync = 0
    seq_audio = 0
    frames_sent = 0

    start = mono_us()
    epoch = start + LEAD_US  # deadline of audio frame 0
    next_audio = start
    next_sync = start

    print(f"[sender] pattern={args.pattern} iface={args.iface_ip} "
          f"lead={LEAD_US/1000:.0f}ms", flush=True)

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

        # Send every due packet (catch-up burst after scheduler stalls, capped
        # so a long stall can't wedge the loop).
        burst = 0
        while now >= next_audio and burst < 10:
            # Keep deadlines glued to real time: ideally each packet's
            # deadline is LEAD_US ahead of its send time. If pacing drifted
            # (Python jitter accumulates), re-anchor the epoch — the slave
            # sees one >20 ms discontinuity and cleanly rebuffers, instead of
            # deadlines sliding into the stale-drop past forever.
            ideal = epoch + frames_sent * 1_000_000 // SAMPLE_RATE
            drift = (now + LEAD_US) - ideal
            if abs(drift) > 20_000:
                epoch += drift
                print(f"[re-anchor] drift={drift/1000:.1f}ms", flush=True)
            deadline = epoch + frames_sent * 1_000_000 // SAMPLE_RATE
            payload = gen.block(FRAMES)
            pkt = (HDR.pack(HYPE_MAGIC, HYPE_VERSION, HYPE_AUDIO, 0,
                            seq_audio, deadline) +
                   AUDIO_HDR.pack(SAMPLE_RATE, FRAMES, 0) + payload)
            for ip in slaves:
                try:
                    audio.sendto(pkt, (ip, PORT_AUDIO))
                except OSError:
                    pass  # iface mid-bounce: drop; slave conceals/rebuffers
            seq_audio += 1
            frames_sent += FRAMES
            next_audio += PACKET_US
            burst += 1
            now = mono_us()
        if now - next_audio > 200_000:  # hopelessly behind: resync pacing
            next_audio = now

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
                    if src_ip not in slaves:
                        print(f"[discovered] slave id={sid} at {src_ip} "
                              f"mac={mac.hex(':')}", flush=True)
                    slaves[src_ip] = mono_us()
                    print(f"[status {src_ip}] id={sid} "
                          f"mode={MODE_NAMES.get(mode, mode)} "
                          f"src={'net' if source else 'local'} rssi={rssi} "
                          f"depth={depth} rx={rx} drop={dropped} "
                          f"under={underruns} up={uptime}s "
                          f"offs={offset}us", flush=True)

        # Forget slaves silent for > 5 s so we stop streaming into the void.
        for ip in [ip for ip, t in slaves.items() if now - t > 5_000_000]:
            print(f"[lost] {ip}", flush=True)
            del slaves[ip]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
