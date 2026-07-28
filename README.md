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
| [docs/performance-heat-analysis.md](docs/performance-heat-analysis.md) | Why the slave runs hot and why streaming dropped (2026-07-18), with the prioritized fix plan |
| [docs/hardware/pcb-review-findings.md](docs/hardware/pcb-review-findings.md) | 2026-07-26 adversarial design review of the carrier's §4 power path (findings + verdicts) |
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

Scope output is 2× RCA flying-lead pads (X = DAC L, Y = DAC R, series R fitted as 0 Ω links — the DAC module already carries ~470 Ω of its own) driving reused ~50 cm RCA cables into each scope's existing BNC→RCA adapter — no BNC/TRS on our board. Enclosure: 3D-printed case (4× M3 mounts), LiPo mounted off-PCB.

Controls per unit: power switch, mode button (NETWORK / LOCAL / HYBRID), filter-cutoff pot, 2 status LEDs. Controller: 1× Arduino UNO-Q (4 GB, Debian). Pin map and all constants: [src/esp32-slave/include/config.h](src/esp32-slave/include/config.h) — **canonical, do not improvise**.

## Status (2026-07-28)

- [x] Research + feasibility (docs/research/)
- [x] Design locked ([docs/DESIGN.md](docs/DESIGN.md))
- [x] Hardware modules owned/ordered (SuperMinis, DACs, mics, chargers, UNO-Q — arrived, Debian 13 trixie aarch64)
- [x] Slave firmware (`src/esp32-slave/`): full audio/network/mode stack — AsyncUDP audio RX, 512 ms jitter buffer + concealment, local mic/pattern renderers, console; unit #1 on breadboard streams end-to-end
- [x] UNO-Q controller app **deployed** (`hyperosci-controller` systemd service): streamer + web UI + patterns/Hershey-font text (accents composed from the face's own strokes)/effects/presets — [src/unoq-controller/README.md](src/unoq-controller/README.md). osci-render was built for aarch64 but deliberately **not** integrated (no headless entry point — [docs/text-rendering-findings.md](docs/text-rendering-findings.md))
- [x] Six controller bugs found by the feature-port study fixed and deployed 2026-07-22, including the one that had the daemon beaconing out of the USB tether instead of the AP — slave 121 now receives audio ([docs/…feasibility.md §1](docs/osci-render-feature-port-feasibility.md))
- [x] Boots unattended 2026-07-22: `hyperosci-ap` now autoconnects (it never did — that, not the controller, was the real "no slaves discovered yet…"), the USB tether holds a fixed `10.42.0.5` alongside DHCP, and the dashboard names the AP-down case instead of showing an empty list. Cold boot to streaming: 18 s ([src/unoq-controller/STATUS.md](src/unoq-controller/STATUS.md))
- [x] Survives a power cut with no laptop 2026-07-22: the live pattern persists to `~/hype_state.json` and is restored before the first audio block, presets keep one generation of undo, and a root `hyperosci-netwatch` timer re-ups the AP (and bounces it on the documented ath10k wedge). Regression checks in [src/unoq-controller/tests/](src/unoq-controller/tests/) — run them **on the board**
- [x] Carrier PCB designed & **ordered 2026-07-28** — JLCPCB, qty 10, DHL Express (DDP), $45.42 landed, arriving **Aug 5–7** (order gate was ~Aug 1; met three days early — see [docs/PLAN.md](docs/PLAN.md)). ⏳ Production is paused until the production file is confirmed by email. **Routed and gate-clean as of 2026-07-28** — the pre-fab DFM pass moved copper and killed router seed 11; a 16-variant re-sweep found exactly one clean result (**seed 33**, now the default) and the board is 1343 segments / 48 GND vias / 0 unconnected / 0 DRC violations, with the width tables regenerated from the routed copper. **Nothing gates the plot any more:** SW1 was the last item and it closed 2026-07-28 off the SS12D00 drawing — 2.5 mm pitch (neither value the footprint was drawn for, and 0.05 mm off the centre of the slot's window), body 8.5 × 3.7 mm on the pin centreline, no layout change. **The fab package is plotted and committed** to [`hw/carrier/fab/`](hw/carrier/fab/); `tools/plot_fab.py` regenerates it and refuses to write the zip unless the board still passes. Detail: [`hw/carrier/layout-notes.md`](hw/carrier/layout-notes.md)
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
hw/carrier/           KiCad carrier PCB — generated end-to-end by tools/*.py
```

The repo root also carries two gitignored third-party binaries: `arduino-flasher-cli` (UNO-Q Debian flasher) and `osci-render-premium-linux.zip` (x86-64 only — does not run on the aarch64 UNO-Q; kept for desktop reference. The controller renders natively instead, see [docs/text-rendering-findings.md](docs/text-rendering-findings.md)).

## Quickstart

Slave firmware build/flash instructions: **[src/esp32-slave/README.md](src/esp32-slave/README.md)** (PlatformIO + pioarduino platform, USB-C CDC console).

One rule worth repeating from DESIGN.md §9: **during breadboard bring-up, never plug in USB-C while the battery power switch is ON** — SuperMini clones tie VBUS to the 5 V pin (meter-confirmed 2026-07-18) and would back-feed the battery. Flash with the switch OFF. The carrier PCB's load-sharing power path is *intended* to remove this rule, but design review (2026-07-26) found the SuperMini-side detection unreliable against a sagging laptop port — so treat the rule as permanent until `docs/hardware/pcb.md` §4.4 passes on an assembled board.
