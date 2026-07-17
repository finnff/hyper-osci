# HYPEROSCI - Distributed Oscilloscope Rendering System

**A synchronized multi-oscilloscope visual system for live performance**

---

## Project Overview

HYPEROSCI connects 4 oscilloscopes to display synchronized osci-render visuals during live performances, with audio-reactive capabilities.

### Key Features
- 4 synchronized oscilloscope outputs
- Standalone operation (no laptop required)
- Mobile web control interface
- Audio-reactive visualizations
- Fallback mode for reliability

---

## System Architecture

```
                        ┌─────────────────┐
                        │   Mobile Phone  │
                        │   (Web Control) │
                        └────────┬────────┘
                                 │ WiFi
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ARDUINO UNO-Q (Controller)                        │
│                                                                      │
│  - Runs osci-render (Debian Linux)                                  │
│  - WiFi Access Point                                                │
│  - Web API + UI                                                     │
│  - Audio input (USB mic)                                            │
└─────────────────────────────────────────────────────────────────────┘
                                 │ WiFi (UDP Multicast)
          ┌──────────┬───────────┼───────────┬──────────┐
          ▼          ▼           ▼           ▼          ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Slave 1  │ │ Slave 2  │ │ Slave 3  │ │ Slave 4  │
    │ ESP32-C3 │ │ ESP32-C3 │ │ ESP32-C3 │ │ ESP32-C3 │
    │ PCM5102A │ │ PCM5102A │ │ PCM5102A │ │ PCM5102A │
    └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
         │            │            │            │
         ▼            ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  Scope 1 │ │  Scope 2 │ │  Scope 3 │ │  Scope 4 │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [v3.md](v3.md) | Original research and feasibility analysis |
| [v3.1-requirements.md](v3.1-requirements.md) | Confirmed requirements and constraints |
| [v3.2-dual-dac.md](v3.2-dual-dac.md) | PCM5102A DAC solution details |
| [UNO-Q_controller.md](UNO-Q_controller.md) | UNO-Q controller software architecture |
| [esp32-c3-vs-s3.md](esp32-c3-vs-s3.md) | MCU comparison for battery life |

---

## Hardware

### Controller
- **Arduino UNO-Q 4GB** (€65)
  - Qualcomm QRB2210 (4× Cortex-A53 @ 2.0 GHz)
  - 4GB RAM, Debian Linux
  - Built-in WiFi 5 (802.11ac)
  - STM32U585 coprocessor

### Slaves (×4)
- **ESP32-C3 SuperMini** (~€3 each)
- **PCM5102A I2S DAC** (~€2 each)
- **MAX4466 Microphone** (for fallback mode)
- **LiPo Battery** + TP4056 charger

### Total Cost
- New purchases: ~€100-120
- Already have: Mics, batteries, scopes

---

## Timeline

| Phase | Weeks | Description |
|-------|-------|-------------|
| 1 | 1-8 | ESP32-C3 + PCM5102A slaves (core system) |
| 2 | 9-14 | UNO-Q controller integration |
| 3 | 15-16 | Testing and venue rehearsal |

**Deadline:** End of May 2026

---

## Operating Modes

1. **Network Mode** - Receive osci-render visuals from UNO-Q
2. **Local Mic Mode** - Fallback: direct mic → scope visualization
3. **Hybrid Mode** - Network visuals + local mic mixed

---

## Quick Start (Future)

1. Power on UNO-Q controller
2. Power on 4 slave units
3. Connect phone to `HYPEROSCI_AP` WiFi
4. Open `http://192.168.4.1` in browser
5. Select preset and enjoy!

---

## Status

- [x] Research complete
- [x] Hardware selected
- [x] PCM5102A DAC modules ordered
- [x] Arduino UNO-Q ordered
- [ ] ESP32-C3 firmware development
- [ ] UNO-Q software development
- [ ] PCB design
- [ ] Final assembly
- [ ] Venue testing
