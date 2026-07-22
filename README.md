# HYPEROSCI

**4 battery-powered, WiFi-synchronized oscilloscopes drawing vector graphics in X/Y mode — for live performance.**

Each of the 4 slave units drives one analog oscilloscope with a stereo audio signal (L = X, R = Y). A central controller (Arduino UNO-Q running Debian) renders osci-render-style vectors as 48 kHz audio and streams it over its own WiFi access point. When the network is gone, every slave falls back within ~6 s to the proven standalone mode: its own microphone drives the scope as an audio-reactive Lissajous visualizer. No laptop on stage, no venue WiFi needed.

## Architecture

```
                     phone browser (web UI, http://192.168.50.1:8080)
                                      │
                                      ▼
        ┌─────────────────────────────────────────────┐
        │        Arduino UNO-Q  (Debian, QRB2210)     │
        │  renderer ─▶ streamer ─▶ WiFi AP (2.4 GHz)  │
        │        SSID HYPEROSCI_AP · 192.168.50.1     │
        └───────┬─────────┬──────────┬─────────┬──────┘
        UDP :5000 audio (unicast ×4) · :5001 sync/cmd · :5002 status
                │         │          │         │
                ▼         ▼          ▼         ▼
          ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
          │ slave 1 │ │ slave 2 │ │ slave 3 │ │ slave 4 │   ESP32-C3 SuperMini
          │         │ │         │ │         │ │         │   + PCM5102A I2S DAC
          │ mic ────┤ │ mic ────┤ │ mic ────┤ │ mic ────┤   + MAX4466 mic (fallback)
          └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   + LiPo/TP4056, custom THT carrier PCB
           X/Y ▼       X/Y ▼       X/Y ▼       X/Y ▼
          ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
          │ scope 1 │ │ scope 2 │ │ scope 3 │ │ scope 4 │   analog scopes in X/Y mode
          └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

Sync target ±5 ms via 500 ms beacons; the stream deliberately runs ~450 ms ahead of playback (512 ms slave jitter buffer) to ride out the UNO-Q radio's periodic ~300 ms stalls — latency is irrelevant for scope art, only slave-to-slave sync matters; stream loss > 1 s → automatic local-mic fallback.

## Documentation

**[docs/DESIGN.md](docs/DESIGN.md) is the single source of truth.** Where any other doc disagrees, DESIGN.md wins.

| Doc | What it is |
|-----|------------|
| [docs/DESIGN.md](docs/DESIGN.md) | Canonical decisions: pin map, modules, modes, power, network (LAW) |
| [docs/PLAN.md](docs/PLAN.md) | 5-week execution plan (Jul 17 → Aug 21), critical path, risks, shopping list |
| [docs/protocol.md](docs/protocol.md) | Normative wire protocol (with [src/esp32-slave/include/protocol.h](src/esp32-slave/include/protocol.h)) |
| [docs/hardware/wiring.md](docs/hardware/wiring.md) | Complete hookup guide + bring-up checklist (breadboard now, PCB later) |
| [docs/hardware/pcb.md](docs/hardware/pcb.md) | Carrier PCB: full BOM, power path, layout notes |
| [docs/hardware/power-budget.md](docs/hardware/power-budget.md) | Current draw, battery life, low-battery policy derivation |
| [docs/firmware/esp32-architecture.md](docs/firmware/esp32-architecture.md) | Slave firmware architecture (tasks, drivers, buffers) |
| [docs/osci-render-feature-port-feasibility.md](docs/osci-render-feature-port-feasibility.md) | What else is worth porting from osci-render (fonts, SVG, effects), measured on the board — and the 6 bugs found while looking, all now fixed (§1) |
| [FURTHER_CLARIFICATION_NEEDED.md](FURTHER_CLARIFICATION_NEEDED.md) | Open questions for Finn |
| [docs/research/](docs/research/) | Historical planning docs — superseded where they conflict with DESIGN.md |

Research index: [v3.md](docs/research/v3.md) (feasibility) · [v3.1-requirements.md](docs/research/v3.1-requirements.md) (confirmed requirements) · [v3.2-dual-dac.md](docs/research/v3.2-dual-dac.md) (DAC selection) · [UNO-Q_controller.md](docs/research/UNO-Q_controller.md) (controller architecture — note: its whole software stack is illustrative and was NOT built as described: no FastAPI/WebSocket/hostapd, and its 5 GHz AP config is wrong — see DESIGN.md §2 and src/unoq-controller/README.md for what exists) · [esp32-c3-vs-s3.md](docs/research/esp32-c3-vs-s3.md) · [arduino-uno-r4-option.md](docs/research/arduino-uno-r4-option.md) · [README-original.md](docs/research/README-original.md)

## Hardware summary

Per slave (modules socketed on a custom ~70 × 50 mm through-hole carrier PCB — see [docs/hardware/pcb.md](docs/hardware/pcb.md)):

| Module | Role |
|--------|------|
| ESP32-C3 SuperMini | MCU, WiFi, I2S master |
| GY-PCM5102A | 24-bit stereo I2S DAC → scope X/Y via 2× RCA pads (X = L, Y = R), ground-centered DC-coupled |
| MAX4466 | electret mic (on a ~10 cm pigtail) for fallback/hybrid mode |
| TP4056 (USB-C, with protection) + EEMB LP103454 LiPo 3.7 V 2000 mAh | power (~13 h NETWORK / ~30 h LOCAL) |

Scope output is 2× RCA flying-lead pads (X = DAC L, Y = DAC R, 100 Ω series) driving reused ~50 cm RCA cables into each scope's existing BNC→RCA adapter — no BNC/TRS on our board. Enclosure: 3D-printed case (4× M3 mounts), LiPo mounted off-PCB.

Controls per unit: power switch, mode button (NETWORK / LOCAL / HYBRID), filter-cutoff pot, 2 status LEDs. Controller: 1× Arduino UNO-Q (4 GB, Debian). Pin map and all constants: [src/esp32-slave/include/config.h](src/esp32-slave/include/config.h) — **canonical, do not improvise**.

## Status (2026-07-22)

- [x] Research + feasibility (docs/research/)
- [x] Design locked ([docs/DESIGN.md](docs/DESIGN.md))
- [x] Hardware modules owned/ordered (SuperMinis, DACs, mics, chargers, UNO-Q — arrived, Debian 13 trixie aarch64)
- [x] Slave firmware (`src/esp32-slave/`): full audio/network/mode stack — AsyncUDP audio RX, 512 ms jitter buffer + concealment, local mic/pattern renderers, console; unit #1 on breadboard streams end-to-end
- [x] UNO-Q controller app **deployed** (`hyperosci-controller` systemd service): streamer + web UI + patterns/Hershey-font text (accents composed from the face's own strokes)/effects/presets — [src/unoq-controller/README.md](src/unoq-controller/README.md). osci-render was built for aarch64 but deliberately **not** integrated (no headless entry point — [docs/text-rendering-findings.md](docs/text-rendering-findings.md))
- [x] Six controller bugs found by the feature-port study fixed and deployed 2026-07-22, including the one that had the daemon beaconing out of the USB tether instead of the AP — slave 121 now receives audio ([docs/…feasibility.md §1](docs/osci-render-feature-port-feasibility.md))
- [ ] Carrier PCB designed & ordered (order gate ~Aug 1 — see [docs/PLAN.md](docs/PLAN.md))
- [ ] 4-slave sync demo (slaves 2–4 not yet on breadboards; 3 built slaves need reflashing for the lost-packets counter)
- [ ] Assembly + venue rehearsal (show 2026-08-21)

## Repository layout

```
docs/                 all documentation (DESIGN.md = canon)
  research/           original v3 planning docs (historical)
  hardware/           wiring, power budget, PCB
  firmware/           firmware architecture
  protocol.md         normative wire protocol
  PLAN.md             5-week execution plan
src/esp32-slave/      PlatformIO project — slave firmware
src/unoq-controller/  UNO-Q controller app (streamer + renderer + web UI, deployed)
FURTHER_CLARIFICATION_NEEDED.md
```

The repo root also carries two gitignored third-party binaries: `arduino-flasher-cli` (UNO-Q Debian flasher) and `osci-render-premium-linux.zip` (x86-64 only — does not run on the aarch64 UNO-Q; kept for desktop reference. The controller renders natively instead, see [docs/text-rendering-findings.md](docs/text-rendering-findings.md)).

## Quickstart

Slave firmware build/flash instructions: **[src/esp32-slave/README.md](src/esp32-slave/README.md)** (PlatformIO + pioarduino platform, USB-C CDC console).

One rule worth repeating from DESIGN.md §9: **during breadboard bring-up, never plug in USB-C while the battery power switch is ON** — SuperMini clones tie VBUS to the 5 V pin (meter-confirmed 2026-07-18) and would back-feed the battery. Flash with the switch OFF. The carrier PCB's load-sharing power path removes this rule on final boards.
