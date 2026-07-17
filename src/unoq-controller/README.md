# unoq-controller — HYPEROSCI controller app (stub)

**Status: nothing implemented yet.** This directory is a placeholder for the Debian app that runs on the Arduino UNO-Q (Qualcomm QRB2210). Read this so you know what it will become and what happens first instead.

## What this will be

Per [docs/research/UNO-Q_controller.md](../../docs/research/UNO-Q_controller.md), as corrected by [docs/DESIGN.md](../../docs/DESIGN.md) §2:

1. **WiFi access point** — hostapd + dnsmasq. SSID `HYPEROSCI_AP` / WPA2 `hyperosci2026`, controller at 192.168.4.1. **2.4 GHz, `hw_mode=g`, channel 1/6/11 — NOT the 5 GHz `hw_mode=a`/channel 36 config in the research doc.** The ESP32-C3 slaves are 2.4 GHz-only; that config would leave all four slaves unable to connect. Do a channel scan at the venue.
2. **Streamer** — implements [docs/protocol.md](../../docs/protocol.md) / [protocol.h](../esp32-slave/include/protocol.h): 988-byte `HYPE_AUDIO` packets (240 stereo frames, 48 kHz/16-bit, L=X R=Y) every 5 ms, **UDP unicast to each of the 4 slaves on port 5000** (not multicast — DESIGN §2), `HYPE_SYNC` beacon every 500 ms on port 5001, `HYPE_CMD` JSON commands on 5001, and a listener for 1 Hz slave status on port 5002.
3. **Renderer** — X/Y vector audio source. First: built-in test patterns (circle, Lissajous, SVG path playback). Later: osci-render integration (native ARM64 build, or a lightweight custom renderer if that fights back — see research doc §4).
4. **Web UI** — mobile-first control page at `http://192.168.4.1` (FastAPI + WebSocket): preset select, mode switching, per-slave status/override.

## What exists today

Nothing. Deliberately.

Per [docs/PLAN.md](../../docs/PLAN.md), week 2 delivers a **laptop Python streamer stand-in** first: the same streamer code, run from a laptop, used to bring up and soak-test 2- then 4-slave sync while the PCBs are being designed and fabbed. In week 3 it gets ported (mostly copied — both machines are Linux + Python) to the UNO-Q behind hostapd. The laptop streamer stays maintained afterwards as the performance-day fallback controller if the UNO-Q misbehaves.

The tool for flashing the UNO-Q Debian image (`arduino-flasher-cli`) lives in the repo root.

## Planned module layout

```
src/unoq-controller/
  hyperosci/
    streamer/        packetizer (HYPE_AUDIO), sync beacon (HYPE_SYNC),
                     command sender (HYPE_CMD), status listener + stats table
    renderer/        test patterns → SVG playback → osci-render bridge
    web/             FastAPI app, WebSocket, static mobile UI
    config.py        constants mirroring protocol.h / config.h (ports 5000/5001/5002,
                     48000 Hz, 240 frames — never redefine, mirror)
  system/
    hostapd.conf     2.4 GHz AP config (hw_mode=g)
    dnsmasq.conf     DHCP 192.168.4.2–.20
    *.service        systemd units (hyperosci-core, hyperosci-web, hyperosci-network)
  README.md          this file
```

Design notes for whoever starts this (probably future Finn):

- `protocol.h` is the canonical wire format; the Python side mirrors it with `struct` pack strings and must round-trip the 20-byte header + payloads byte-exact. All fields little-endian.
- Timestamps are the controller's monotonic clock in µs (`time.monotonic_ns() // 1000`); `HYPE_AUDIO.timestamp_us` is the playback deadline of the packet's first frame.
- Pace transmission from the audio clock (one packet per 5 ms per slave), not from `sleep()` drift.
