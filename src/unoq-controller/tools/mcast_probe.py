#!/usr/bin/env python3
"""Measure whether AUDIO can go out as multicast instead of N unicasts.

Sends real HYPE_AUDIO packets (988 B, the production size) at a chosen rate to
either a unicast slave or 239.0.0.1, and reports what the local send path did.
Pair it with the slave's own rx counter (/api/state) to get delivery.

Safe against a live rig: the samples are silence (centre-scale zeros) and the
first packet carries HYPE_FLAG_SYNC_PULSE, so the slave treats the seq jump as
an intentional resync and does not charge it to lost_packets. A slave in forced
LOCAL mode never reads the jitter buffer at all, so nothing is audible.
"""
import argparse
import array
import errno
import fcntl
import socket
import struct
import termios
import time


def outq(sock):
    """Bytes still sitting in this socket's send queue (SIOCOUTQ)."""
    buf = array.array("i", [0])
    fcntl.ioctl(sock.fileno(), termios.TIOCOUTQ, buf)
    return buf[0]


HYPE_MAGIC = 0x45505948
HYPE_VERSION = 1
HYPE_AUDIO = 1
HYPE_FLAG_SYNC_PULSE = 0x0001
PORT_AUDIO = 5000
HDR = struct.Struct("<IBBHIQ")
AUDIO_HDR = struct.Struct("<IHH")
SAMPLE_RATE = 48000
FRAMES = 240
PACKET_US = FRAMES * 1_000_000 // SAMPLE_RATE
LEAD_US = 450_000
TX_QUEUE_FULL = frozenset((errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS))


def mono_us():
    return time.monotonic_ns() // 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="slave IP or 239.0.0.1")
    ap.add_argument("--rate", type=int, default=200, help="packets/sec")
    ap.add_argument("--secs", type=float, default=10.0)
    ap.add_argument("--iface-ip", default="192.168.50.1")
    ap.add_argument("--sndbuf", type=int, default=0,
                    help="SO_SNDBUF bytes (0 = kernel default). Sizes the ride-"
                         "through for the ath10k deaf-stall: at LEAD_US=450 ms a "
                         "packet delayed 300 ms still beats its deadline.")
    a = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    if a.sndbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, a.sndbuf)
    eff = s.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
    is_mcast = a.dest.startswith(("239.", "224."))
    if is_mcast:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                     socket.inet_aton(a.iface_ip))
        # We are the AP host; nothing local needs to hear our own audio.
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)

    silence = b"\x00" * (FRAMES * 2 * 2)
    total = int(a.rate * a.secs)
    interval = 1.0 / a.rate
    ok = full = other = 0
    last_err = ""
    t0 = time.monotonic()
    for i in range(total):
        flags = HYPE_FLAG_SYNC_PULSE if i == 0 else 0
        pkt = (HDR.pack(HYPE_MAGIC, HYPE_VERSION, HYPE_AUDIO, flags, i,
                        mono_us() + LEAD_US) +
               AUDIO_HDR.pack(SAMPLE_RATE, FRAMES, 0) + silence)
        try:
            s.sendto(pkt, (a.dest, PORT_AUDIO))
            ok += 1
        except OSError as e:
            if e.errno in TX_QUEUE_FULL:
                full += 1
            else:
                other += 1
            last_err = errno.errorcode.get(e.errno, str(e.errno))
        nxt = t0 + (i + 1) * interval
        slack = nxt - time.monotonic()
        if slack > 0:
            time.sleep(slack)
    dur = time.monotonic() - t0

    # Backlog still owed to the radio when we stopped offering. This is the
    # number that decides whether a deep SO_SNDBUF is a ride-through or a lie:
    # anything still queued is delivered LATE, and the slave discards a packet
    # that misses its deadline. At LEAD_US = 450 ms, a drain under ~150 ms is
    # absorbed; seconds of drain means the link cannot carry the offered rate
    # and the buffer is only hiding the loss until the slave throws it away.
    backlog = outq(s)
    t_end = time.monotonic()
    while outq(s) > 0 and time.monotonic() - t_end < 20:
        time.sleep(0.005)
    drain = time.monotonic() - t_end

    att = ok + full + other
    print(f"dest={a.dest} {'MULTICAST' if is_mcast else 'unicast'} "
          f"target={a.rate}/s for {a.secs}s  sndbuf={eff}B")
    print(f"  wall            {dur:.2f}s  (achieved {att/dur:.0f} pkt/s)")
    print(f"  attempted       {att}")
    print(f"  accepted        {ok}   ({100*ok/max(att,1):.1f}%)")
    print(f"  buffer-full     {full} ({100*full/max(att,1):.1f}%)")
    print(f"  other errors    {other}" + (f"  last={last_err}" if last_err else ""))
    print(f"  payload         {att*988*8/dur/1e6:.2f} Mbit/s offered")
    print(f"  backlog at stop {backlog/1024:.0f} KiB charged "
          f"(sk_wmem_alloc: skb truesize, ~2.3x payload — not a packet count) "
          f"drained in {drain*1000:.0f} ms"
          + ("  <-- LATE, past the 450 ms lead" if drain > 0.45 else ""))


if __name__ == "__main__":
    main()
