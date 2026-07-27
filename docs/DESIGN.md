# HYPEROSCI — Canonical Design Decisions

**Date:** 2026-07-17
**Status:** LOCKED for slave v1 hardware/firmware. Changes to this file require updating every doc/code file that references it.
**Deadline context:** ~5 weeks (target: PCBs ordered, assembled, and firmware running by ~2026-08-21).

This file is the single source of truth. The research history lives in [docs/research/](research/). Where this file contradicts a research doc, **this file wins**.

---

## 1. System snapshot

4 battery-powered slave units, each driving one oscilloscope in X/Y mode. Each slave = **ESP32-C3 SuperMini + PCM5102A I2S DAC module + MAX4466 mic**, on a custom through-hole carrier PCB. A central controller (Arduino UNO-Q) generates osci-render X/Y audio and streams it over its own WiFi AP. Every slave falls back to standalone mic→scope visualization when the network is absent.

The UNO-Q controller has **arrived** (Debian 13 "trixie", aarch64) and doubles as the on-stage 2.4 GHz WiFi AP — **no laptop on stage**. Any "laptop streamer" stand-in language elsewhere refers to a Python test-streamer running on the UNO-Q itself, standing in for full osci-render integration.

```
UNO-Q (WiFi AP, 2.4 GHz) ──UDP──▶ 4× [ESP32-C3 ──I2S──▶ PCM5102A ──▶ scope X/Y]
                                        ▲
                                   MAX4466 mic (fallback + hybrid mix)
```

## 2. Corrections to earlier research docs (important)

1. **The AP must run on 2.4 GHz, not 5 GHz.** ESP32-C3 (and S3) are 2.4 GHz-only. The hostapd config in `research/UNO-Q_controller.md` (`hw_mode=a`, channel 36) will not work with the slaves. Use `hw_mode=g`, channel 1/6/11, and do a channel scan at the venue.
2. **Default transport is 4× UDP unicast, not multicast.** WiFi multicast is transmitted at the lowest basic rate with no retries and is often throttled/dropped by APs; unicast gets MCS rates + link-layer retries. With only 4 slaves at ~1.6 Mbps each, unicast is trivially affordable (~6.5 Mbps total). Slaves are transport-agnostic: they bind the audio port and also join the multicast group, so the controller can use either.
3. **Modem power-save must be OFF on slaves while streaming** (`WiFi.setSleep(false)`), otherwise DTIM buffering adds 100+ ms of jitter. This is the main reason network mode draws ~100 mA.

## 3. Per-slave bill of materials (modules)

| # | Module | Role | ~Price | Notes |
|---|--------|------|--------|-------|
| 1 | ESP32-C3 SuperMini | MCU, WiFi, I2S master | €3 | already owned ×4 |
| 2 | GY-PCM5102A (purple module) | 24-bit stereo DAC → scope X/Y | €2 | already ordered ×4 |
| 3 | MAX4466 electret mic module | fallback/hybrid audio input | €1.50 | already owned ×4 |
| 4 | TP4056 charger module (**USB-C variant**, blue PCB 17×27 mm, **with** DW01+FS8205 protection) | LiPo charging + protection | €0.50 | 1 A default charge kept (RPROG swap to 0.5 A optional); **mandate 5 V / 2 A wall chargers** so 1 A (~0.5C, full ~2.5–3 h) is safe |
| 5 | LiPo 3.7 V, **~2000 mAh** — reference part EEMB LP103454 (34×56×11 mm, ~40 g, pre-fitted JST); **sourced from Amazon as of 2026-07-27** | power | — | mounted off-board (loose in the enclosure, velcro/pocket); 1000 mAh is a smaller-cell reference only. A substituted cell must bring a **JST-PH 2.00 mm** lead, and its **polarity must be metered before first plug-in** — `B−` is not GND (pcb.md §1/§3.1) |
| 6 | Carrier PCB (this project) | ties it all together | ~€1–2/board | v1.1 laid out; THT except Q1 (SOT-23) + D2 (SMA) |

Carrier-board discrete parts (full BOM in [hardware/pcb.md](hardware/pcb.md)): slide power switch (**SS12D00**, 6 mm handle — the 0.3 A make/break rating is accepted at 4 V, §5), mode button (6×6 mm tactile), 10 kΩ potentiometer (filter-cutoff; **RV097NS** 9 mm PCB-mount, B10K, 5-pin **mono with switch**, right-angle, metal shaft turned directly — no knob; the switch is unused, both ends on GND), 2× 3 mm LED + resistors, battery divider (2× 100 kΩ + 100 nF), 10 kΩ pull-up for GPIO2, bulk electrolytic (≥220 µF) on the battery rail, **2× RCA flying-lead output pad-pairs** (signal + ground; X = PCM5102A L, Y = PCM5102A R) driving the reused ~50 cm RCA cables — 100 Ω series resistors (R10/R11) feed the pads, female pin-header sockets for the SuperMini/DAC/TP4056 modules, and a **3-pin pigtail header (VCC / GND / OUT)** for the MAX4466 (on a ~10 cm cable, not a flat on-board footprint).

## 4. Canonical pin map (ESP32-C3 SuperMini)

Mirrors `src/esp32-slave/include/config.h`. **This table is law** — do not use different pins anywhere.

| GPIO | Function | Direction | Notes |
|------|----------|-----------|-------|
| 0 | `MIC_ADC` — MAX4466 OUT | in (ADC1_CH0) | mic biased at VCC/2 (same pin as the old proven unit) |
| 1 | `VBAT_ADC` — battery divider | in (ADC1_CH1) | 100k/100k ÷2, 100 nF to GND across bottom R |
| 2 | **unused** | — | strapping pin (must be high at reset) — leave NC, fit 10 kΩ pull-up to 3V3 on carrier |
| 3 | `POT_ADC` — 10 kΩ pot wiper | in (ADC1_CH3) | performance knob: local Y-filter cutoff 20–300 Hz (carried over from the old unit, same pin) |
| 4 | `I2S_BCK` → PCM5102A BCK | out | |
| 5 | `I2S_LRCK` → PCM5102A LCK | out | |
| 6 | `I2S_DOUT` → PCM5102A DIN | out | |
| 7 | `BTN_MODE` | in, pull-up | momentary to GND |
| 8 | onboard blue LED | out | **active-low**, strapping pin, heartbeat only |
| 9 | onboard BOOT button | in | strapping; readable after boot as secondary button |
| 10 | `LED_NET` (green) | out | see LED semantics §7 |
| 20 | `LED_MODE` (amber) | out | (UART0 RX repurposed — console is USB-C CDC) |
| 21 | UART0 TX | out | debug header, reserved (log output if USB CDC unavailable) |

Design rules applied: **no signals on strapping pins** (2, 8, 9); all three ADC1 inputs (mic, vbat, pot) on GPIO0/1/3; PCM5102A XSMT is not MCU-controlled in v1 (module bridge 3 soldered high — the DAC idles at 0 V for digital zero, so power-on pop is a non-issue for a scope).

**Continuity with the proven unit** (`~/Arduino/esp32c3SIGMADELTA`): mic stays on GPIO0, pot stays on GPIO3, and the local-mode Y filter reuses its exact 2-pole Butterworth (Q = 0.7071) with pot-mapped cutoff. What changes: output moves from 8-bit sigma-delta on GPIO1 to the PCM5102A (both X and Y now MCU-driven), and sampling moves from 20 kHz blocking `analogRead` to 24 kHz DMA.

## 5. PCM5102A module configuration (GY-PCM5102, purple)

Back-side solder bridges (must be soldered — modules often ship open):

| Bridge | Pin | Set to | Meaning |
|--------|-----|--------|---------|
| 1 | FLT | **L** | normal latency filter (try H = low-latency later; may reduce ringing on sharp vector edges) |
| 2 | DEMP | **L** | de-emphasis off |
| 3 | XSMT | **H** | un-muted. (MCU mute control was considered and rejected for v1 — every candidate pin is taken; the DAC's 0 V idle makes it unnecessary for a scope.) |
| 4 | FMT | **L** | I2S format |

Front: **SCK pin tied to GND** → DAC generates its internal clock via PLL from BCK (no MCLK needed from ESP32).

Key electrical facts (⚠️ VERIFY items covered in verification checklist §12):
- Output is **ground-centered, DC-coupled** (internal charge pump makes a negative rail). Full-scale ≈ 2.1 V RMS ≈ ±3 V peak — ideal for scope X/Y, no offset knobs needed.
- BCK ratio of 32× fs (16-bit stereo) is supported by the PLL.
- Module VIN feeds an onboard XC6206 3.3 V LDO; feeding VIN from the SuperMini's 3V3 rail works (dropout at ~20 mA is small), giving the DAC ~3.25 V at healthy battery. Caveat: below ~3.3–3.4 V battery the double-LDO cascade puts the DAC under its recommended minimums (CPVDD/DVDD 3.1 V, AVDD 3.0 V) — visuals may lose amplitude near end-of-charge; verify during the battery-rundown test.

## 6. Audio & signal parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Output sample rate | **48 000 Hz**, 16-bit stereo (L=X, R=Y) | fixed for v1, everywhere |
| Mic ADC | `adc_continuous`, 3 channels (mic + vbat + pot) at **24 kHz each** (72 kHz total, C3 DMA limit is ~83 kHz) | mic upsampled ×2 (linear interp) to 48 kHz; vbat/pot decimated in software |
| Local fallback pattern | X = DC-blocked mic, Y = 2-pole Butterworth LPF of mic, cutoff = pot (20–300 Hz) | exact filter from the proven old unit |
| I2S block size | 240 frames (5 ms) | matches network packet size |
| Jitter buffer | 24576-frame ring (**512 ms** capacity), start gate 60 ms; steady depth = controller lead (450 ms) | sized to ride the UNO-Q ath10k AP's ~300 ms radio stalls; latency is irrelevant for scope art — only slave-to-slave sync matters (updated 2026-07-18 from the original 8192/~170 ms/60 ms once the stalls were measured) |
| Sync accuracy target | ±5 ms (per v3.1 requirement) | beacon-based offset, no RTT compensation needed in v1 |

## 7. Modes, LEDs, buttons (UX spec)

**Modes:** `LOCAL` (mic only), `NETWORK` (stream only, auto-falls-back to local if stream lost >1 s), `HYBRID` (stream + mic mixed 50 %). Boot default: NETWORK with auto-fallback (i.e. power-on with no controller ⇒ behaves like the old mic units within ~6 s: 5 s WiFi timeout + 1 s stream timeout).

> **Open (not blocking anything):** HYBRID mixes each slave's *own* mic (`HYBRID_MIC_GAIN` 50 %,
> saturating add — see [firmware/esp32-architecture.md](firmware/esp32-architecture.md)). The
> alternative — one mic into the UNO-Q, mixed centrally into all four streams — is a possible v2.
> Per-slave local mixing is canon until Finn says otherwise.

**LED semantics:**

| LED | Pattern | Meaning |
|-----|---------|---------|
| NET (green) | off | WiFi radio off / not configured |
| | 1 Hz blink | connecting / connected but no stream |
| | solid | receiving audio stream |
| | 5 Hz blink | connected, stream lost (fallback active) |
| MODE (amber) | off | NETWORK mode |
| | solid | LOCAL mode |
| | 1 Hz blink | HYBRID mode |
| onboard blue | 1 s heartbeat (50 ms flash) | firmware alive |
| | 3 fast blinks / 2 s | battery low (< 3.45 V) |

**Buttons:**

| Action | Effect |
|--------|--------|
| MODE short press | cycle NETWORK → LOCAL → HYBRID |
| MODE long press (≥2 s) | toggle WiFi radio (battery save / force standalone) |
| MODE held at power-on | boot straight into LOCAL, WiFi off |
| BOOT short press (after boot) | cycle local test pattern (mic → circle → Lissajous → ramp → square; ramp = §12 DC test, square = §12 ringing test) |

**Pot (10 kΩ, GPIO3):** in LOCAL/HYBRID modes sets the Y-channel low-pass cutoff (20–300 Hz), exactly like the old unit. Ignored in pure NETWORK mode (reserved for a future streamed-effect parameter).

## 8. Network & protocol summary

Normative spec: [docs/protocol.md](protocol.md) + `src/esp32-slave/include/protocol.h`.

| Item | Value |
|------|-------|
| AP | SSID `HYPEROSCI_AP` / WPA2 `hyperosci2026`, **2.4 GHz** ch 6, controller at **192.168.50.1/24** (NetworkManager conn `hyperosci-ap`; 10.42.x/192.168.4.x collided with the USB tether) |
| Audio | UDP port **5000** (unicast to each slave; multicast 239.0.0.1 also joined) |
| Control + sync | UDP port **5001** (SYNC beacon every 500 ms from controller; JSON commands unicast) |
| Status | UDP port **5002** (each slave → controller, 1 Hz binary status) |
| Packet | 20-byte `HYPE` header + type-specific payload; audio = 240 stereo frames (988 B) |
| Clock | controller monotonic µs; slaves track offset from SYNC beacons (smoothed), packets carry playback deadline timestamps |
| Slave identity | derived from MAC (last octet), overridable via serial console |

## 9. Power architecture

```
LiPo ──▶ TP4056 module (charge via its own USB; DW01 protection) ──▶ OUT+
     OUT+ ──▶ power slide switch ──▶ SuperMini 5V pin ──▶ onboard LDO ──▶ 3V3 rail
     3V3 rail ──▶ PCM5102A VIN, MAX4466 VCC, LEDs, pull-ups
     LiPo+ ──▶ 100k/100k divider ──▶ GPIO1 (always connected, 21 µA)
```

**Breadboard/bring-up rule:** never have the power switch ON while the SuperMini USB-C is plugged in (most SuperMini clones tie VBUS straight to the 5 V pin — meter-confirmed on ours — so USB would back-feed the battery). Flash/debug on USB with switch OFF.

**On the carrier PCB the rule is *conditionally* retired, not deleted.** The load-sharing path is a P-FET + Schottky, but the two OR terms are not symmetric: the charger's VBUS is a real diode into the gate, while the SuperMini's VBUS **cannot** be — it *is* the load node, so a diode there self-biases the gate. That term is a voltage threshold on the load node instead (pcb.md §4.1). Design review (2026-07-26) found that threshold cannot reliably distinguish a sagging USB port from a charged cell, so **the rule stands unless the block is populated and passes the amended bench test**; a JP1-bridged board keeps it permanently. See [hardware/pcb.md](hardware/pcb.md) §4.

**Budget (estimates, to be measured in week 1):** the **selected cell is 2000 mAh** (EEMB LP103454) — plan around **~13 h NETWORK / ~30 h LOCAL**, comfortably clearing the ~10 h runtime target. The 1000 mAh column is retained as a smaller-cell reference only.

| Mode | Current @3.7 V | 1000 mAh (ref) | **2000 mAh (selected)** |
|------|----------------|----------|----------|
| NETWORK (WiFi RX, sleep off) | ~120–135 mA | ~6.5 h | **~13 h** |
| LOCAL (WiFi off) | ~50–60 mA | ~15 h | **~29–30 h** |
| Low-battery policy (firmware) | warn < 3.45 V, WiFi off < 3.30 V, deep sleep < 3.05 V | protects LiPo above DW01's 2.4 V cutoff | |

Details/derivation: [hardware/power-budget.md](hardware/power-budget.md).

## 10. Firmware stack

- **PlatformIO + pioarduino platform (Arduino core 3.x / ESP-IDF 5.x)**, board `esp32-c3-devkitm-1`, USB CDC console.
- **Why not Zephyr / pure ESP-IDF:** Zephyr's ESP32-C3 port lacks mature I2S-TX and continuous-DMA-ADC drivers and its WiFi path (Espressif HAL blobs) is far less field-proven — unacceptable risk on a ~5-week deadline. Pure ESP-IDF would be equally solid but adds boilerplate; since all hot-path code here already calls IDF drivers directly, migrating later is cheap. PlatformIO (not Arduino IDE) for pinned versions and reproducible builds.
- Native IDF drivers used directly where the Arduino wrapper is inadequate: `i2s_std` (TX master, 48 kHz/16-bit/stereo, no MCLK) and `adc_continuous` (mic+vbat DMA). The Arduino `analogContinuous` wrapper *averages* conversions and is unusable for audio — do not use it.
- FreeRTOS layout (single core): high-prio audio task (pulls 240-frame blocks from active source, pushes to I2S DMA — the blocking I2S write paces the loop), network task (UDP RX → jitter buffer, status TX), Arduino `loop()` for UI/console at 100 Hz.
- Source: `src/esp32-slave/` — see [firmware/esp32-architecture.md](firmware/esp32-architecture.md).

## 11. Mechanical / size

Target carrier PCB: **70 × 50 mm**, 2-layer, through-hole **except Q1 (SOT-23) and D2 (SMA)**, both on enlarged hand-solder pads. Modules are socketed on female headers — **except the TP4056**, whose pads are not on a 2.54 grid and take **six** machined single pins: four on the output row, plus two 21.65 mm away at the USB-C-end corner pads, without which the jack sits on a cantilever (pcb.md §2/§5). Module footprints (✅ measured 2026-07-18, pin positions from photogrammetry 2026-07-26):

| Module | Approx. size | Mounting |
|--------|--------------|----------|
| ESP32-C3 SuperMini | 22.5 × 18 mm | 2× 1×8 header, 2.54 mm |
| GY-PCM5102A | **32.0 × 17.4 mm** (photogrammetry 2026-07-26; calipers said ~31.8 × 17) | 1×6 I2S header (SCK BCK DIN LCK GND VIN) on a short edge + **1×9 analog/config header** on the long edge (⊥); analog out on **LROUT / ROUT / AGND** (X = LROUT, Y = ROUT) |
| MAX4466 | ~20 × 13 mm | on a ~10 cm 3-pin pigtail (VCC GND OUT), exits the enclosure aimed at the PA so its gain trimpot stays screwdriver-reachable — not flat on the carrier |
| TP4056 (USB-C variant) | blue PCB **26.9 × 17.3 mm** measured, **25.2 mm** once the two depanelization nubs are filed off (USB-C jack overhangs **1.4 mm**) | 4 pads (B+/B−/OUT+/OUT−) at 0 / 3.526 / 10.960 / 14.066 mm, **plus the two `IN+`/`IN−` corner pads 21.65 mm away as a second mount row** — **six machined single pins, not a 2.54 header**; **USB-C** charge-port cutout on the board edge |

**Scope connection (resolved):** no BNC or TRS on the carrier. The board exposes **2× RCA flying-lead solder pad-pairs** (signal + ground): X = PCM5102A L, Y = PCM5102A R, each fed through its 100 Ω series resistor (R10/R11). Signal chain: board RCA pad → reused ~50 cm RCA cable (RCA male, salvaged from the old sigma-delta units) → BNC→RCA adapter already fitted on each scope → scope CH. This frees ~25 mm of board edge and ~€2/board vs the old BNC plan.

**Enclosure:** 3D-printed case; the carrier keeps its 4× M3 mounting holes and its JST-PH battery socket. The 2000 mAh LiPo (34 × 56 × 11 mm) rides **off-board**, loose in the enclosure (velcro/pocket), since it is large relative to the 70 × 50 mm carrier.

**Panel controls:** power = **SS12D00 slide switch, 6 mm handle** (ordered 2026-07-27). Its 0.3 A
rating is a make/break figure at 50 VDC and is accepted at the ~4 V this switch interrupts —
the earlier "1 A-rated part required, for the ~0.35 A WiFi TX peak" call compared a peak
current against an arc-energy rating; see [hardware/pcb.md](hardware/pcb.md) §5. Filter-cutoff pot = **RV097NS** 9 mm PCB-mount (B10K, **5-pin mono *with switch*, right-angle**, body 27.3 × 9.5 × 11.3 mm); its **metal shaft is turned directly — no knob**, so the enclosure needs only a shaft hole (resolved 2026-07-18). The pot's **mounting surface sits on the board's south edge** and its M7×0.75 bushing + 15 mm shaft protrude through the enclosure wall — the panel nut is structural, since a bare shaft turned by hand puts torque into a part with no bracket lugs (geometry corrected 2026-07-27, [hardware/pcb.md](hardware/pcb.md) §5).

## 12. Verification checklist

*(Status 2026-07-26: the mechanical items are closed — see `hardware/measurements.md`. What is left is bench-electrical and moves to the assembled carrier; per `hardware/pcb.md` §8.2 none of it gates the fab order, which the board survives either way thanks to JP1.)*

- [ ] PCM5102A output truly passes DC on the actual purple modules (drive a slow ramp, watch scope) — datasheet says ground-centered/DC-capable. *Measured 2026-07-18:* the module carries a ~470 Ω "471" output filter but is ground-centered (no DC-blocking cap) — confirm the ramp passes DC and decide R10/R11 = 0 Ω vs 100 Ω.
- [ ] PCM5102A PLL locks at 32× fs BCK (16-bit stereo, SCK grounded) — verify tone output.
- [ ] Filter ringing: sharp X/Y steps (square Lissajous) — compare FLT=L vs FLT=H visually on a scope.
- [ ] SuperMini onboard LDO type & dropout (probe 3V3 during WiFi TX burst at VBAT=3.5 V; check brownout).
- [ ] `adc_continuous` stable at 72 kHz total across 3 channels on C3/Arduino core 3.x.
- [ ] Measured currents per mode vs §9 table.
- [ ] DAC output amplitude/offset during battery rundown to 3.05 V (double-LDO undervoltage check, §5 caveat).
- [x] Measure all four module outlines/pin positions before PCB layout — **done 2026-07-18 (calipers) and superseded 2026-07-26 by photogrammetry** (`hw/pin_locs` → `measured.py`); the two numbers calipers guessed at were both wrong and both were wrong in the v1.0 board.
- [ ] WiFi robustness: 4 unicast streams from hostapd AP, RSSI at 10 m, packet-loss stats from slave status packets.

## 13. Repository layout

```
docs/            all documentation (this file = canon)
  research/      original v3 planning docs (historical, superseded where conflicting)
  hardware/      wiring, power, PCB
  firmware/      firmware architecture
  protocol.md    normative wire protocol
  PLAN.md        ~5-week execution plan
src/esp32-slave/     PlatformIO project (slave firmware)
src/unoq-controller/ UNO-Q controller app (deployed — see its README.md)
hw/carrier/          KiCad carrier PCB, generated by tools/*.py (see layout-notes.md)
```

Open questions used to live in a root `FURTHER_CLARIFICATION_NEEDED.md`; every PCB- and
order-blocking one closed by 2026-07-18, so the file was folded into the specs it fed —
hardware questions into [hardware/pcb.md](hardware/pcb.md) §10, and the HYBRID mic-mix
question into §7's mode table above.
