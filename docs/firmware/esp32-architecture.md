# ESP32-C3 Slave Firmware Architecture

**Source:** [`src/esp32-slave/`](../../src/esp32-slave/) · **Pins/constants:** [`include/config.h`](../../src/esp32-slave/include/config.h) · **Wire format:** [`include/protocol.h`](../../src/esp32-slave/include/protocol.h) / [protocol.md](../protocol.md)

## 1. Platform choice

PlatformIO + **pioarduino** (Arduino core 3.x on ESP-IDF 5.x). The Arduino layer is used only for convenience surfaces (WiFi/UDP, NVS `Preferences`, USB-CDC `Serial`, GPIO); everything timing-critical calls ESP-IDF drivers directly:

- **`i2s_std`** — I2S master TX, 48 kHz / 16-bit / stereo. No MCLK: the PCM5102A's SCK pin is grounded, its internal PLL derives clocks from BCK (32× fs).
- **`adc_continuous`** — DMA ADC over 3 channels (mic GPIO0, vbat GPIO1, pot GPIO3) at 24 kHz each (72 kHz total; the C3's DMA-ADC ceiling is ~83 kHz). The Arduino `analogContinuous` wrapper *averages* conversions per read and cannot stream audio — that's why the raw driver is used. One-shot `analogRead` is also off-limits once the continuous driver owns ADC1.

Considered and rejected for v1: **Zephyr** (immature C3 I2S-TX/ADC-DMA drivers, blob-based WiFi, small community for this combo — schedule risk), **pure ESP-IDF** (equally solid, more boilerplate; the hot paths are IDF-native already so a later port is cheap), **Arduino core 2.x / stock platformio espressif32** (frozen on IDF 4.4, lacks both drivers above).

## 2. Data flow

```
                        ┌────────────────────────── audio task (prio 10, 5 ms cadence) ─┐
                        │                                                               │
 WiFi ──▶ net_rx task ──▶ JitterBuffer ──▶ pull_block() ──┐                             │
 (UDP 5000/5001)        │  (8192 frames,   deadline       │                             │
      │                 │   60 ms target)  policy         ▼                             │
      ▼                 │                          mode_manager::fill_block ──▶ audio_out::write ──▶ I2S DMA ──▶ PCM5102A ──▶ scope
 timesync (SYNC         │                                 ▲                             │
 beacon → offset)       │                                 │                             │
                        │   mic ADC DMA ──▶ mic_in ring ──▶ renderer_local              │
                        │   (24 kHz)        (DC-block)     (X=mic, Y=Butterworth LPF)   │
                        └───────────────────────────────────────────────────────────────┘
```

The **audio task never blocks on anything except the I2S DMA write** — that write is the metronome: the DMA queue drains at exactly 48 kHz, so each `i2s_channel_write` of a 240-frame block returns when a 5 ms slot frees up. Everything upstream (network pull, local render, mixing) must complete well inside 5 ms (measured budget: DSP is ~0.3 ms; WiFi stack interrupts eat into the rest).

## 3. Tasks (single-core C3)

| Task | Priority | Cadence | Responsibilities |
|------|----------|---------|------------------|
| `audio` | 10 | 5 ms, I2S-paced | `fill_block` → `write`; also drains the ADC DMA pool (via `mic_in::read`) |
| `net_rx` | 5 | 1 ms poll | WiFi connect/reconnect state machine, UDP RX → jitter buffer, SYNC → timesync, CMD → mode_manager, 1 Hz STATUS TX |
| Arduino `loop` | 1 | 10 ms | buttons (debounce + long-press), LED patterns, battery policy, serial console |

WiFi's internal tasks run at higher priority than `audio` — that's fine and unavoidable; the I2S DMA (6 descriptors × 240 frames = 30 ms of queue) rides through WiFi bursts.

Concurrency contracts:
- `JitterBuffer` is strict SPSC (producer `net_rx`, consumer `audio`) with `portMUX` critical sections around index/deadline updates.
- `mic_in`'s sample ring is produced *and* consumed inside the audio task (no locking); the ADC DMA pool provides the elasticity.
- `timesync` guards its 64-bit offset with a `portMUX` (64-bit writes aren't atomic on RV32).

## 4. Mode state machine (`mode_manager`)

```
            short press: NETWORK → LOCAL → HYBRID → NETWORK
                     (also settable via JSON cmd / console)

 NETWORK: pull_block ok ────────────▶ play stream       (source = network)
          fail, stream live (<1 s) ─▶ concealment ladder (hold → center)
          fail, stream dead (≥1 s) ─▶ renderer_local     (source = local)
 LOCAL:   renderer_local always
 HYBRID:  stream + renderer_local × HYBRID_MIC_GAIN, saturating add
          (stream side falls back to full local render when dead)
```

There is no explicit "fallback state" to enter or leave — the decision is re-made per 5 ms block, so recovery is automatic the moment the buffer refills; the 1 s liveness threshold plus the jitter-buffer start gate provide the hysteresis that keeps imagery from flapping. This satisfies the v3.1 hard requirement: power the unit on with no controller anywhere and it's a standalone mic visualizer within ~6 s (5 s WiFi timeout + 1 s stream timeout).

## 5. Jitter buffer & clock discipline

- Packets carry a **playback deadline** (controller clock) for their first frame. `timesync` tracks the controller-clock offset from SYNC beacons (median of last 8, applied directly — protocol.md §4.2's slewing is a v2 refinement; flight time ignored — ~1–2 ms ≪ ±5 ms spec).
- **Push side** (net task): stale-on-arrival packets are dropped. Continuity is judged by **deadline against the buffer tail**, not seq numbers: a 1–20 ms hole (lost packets) is filled with last-value hold so the buffer stays contiguous; earlier-than-tail packets (dup/reorder) are dropped; >20 ms discontinuities (controller restart, outage) flush and re-anchor; `HYPE_FLAG_SYNC_PULSE` flushes unconditionally. The start gate opens at 60 ms depth. Stats and stream-liveness count *accepted* packets only.
- **Pop side** (audio task): if the head frame is >5 ms early → conceal this block (see below); if >5 ms late → skip whole blocks to catch up; on underrun → close the start gate and rebuffer to 60 ms. `reset()` racing a pop/push across tasks is made safe by an epoch counter that aborts the in-flight commit.
- **Concealment ladder** (NETWORK mode, protocol.md §6): on any block the network can't serve while the stream is still live (<1 s since last accepted packet) — hold the last played frame for ≤20 ms, ramp X/Y to center over ~10 ms, hold center. Only after `STREAM_TIMEOUT_MS` (1 s) does the source switch to the local mic renderer. This keeps a WiFi hiccup from flapping the scope between stream and mic imagery every 5 ms.
- The guarantee (protocol.md §5.2): each slave plays frames within ±5 ms of deadline, quantized to 5 ms blocks (transiently ~7 ms after a stall); **slave-to-slave skew** stays at sync error (≤1–2 ms) + at most one block, because all slaves run identical policies over the identical stream.

**Known v1 simplification:** no continuous drift servo (sample slip/insert). Each slave's I2S clock free-runs (±~0.3 % worst case, typically ≪ that); over minutes the deadline policy corrects by 5 ms block-skips rather than smoothly. If that's ever visible, the fix is a depth-servo that drops/duplicates 1 frame per N blocks — noted in `README.md` TODOs.

## 6. Local renderer

Faithful port of the proven `esp32c3SIGMADELTA` unit:
- X = mic, DC-blocked (1-pole HPF at ~2 Hz, replaces the old `-2048` centering).
- Y = 2-pole Butterworth low-pass (Q = 0.7071), cutoff mapped from the pot to **20–300 Hz**, recomputed each 5 ms block — same coefficients math, same musical behavior, but now running at 48 kHz on the 24 kHz→48 kHz linearly-interpolated mic signal, and output through a real 16-bit DAC instead of 8-bit sigma-delta.
- Test patterns for bring-up (BOOT button / `pat` console cmd): 100 Hz circle (scope calibration: perfect circle = both channels healthy), 3:2 Lissajous.

## 7. Failure & power behavior

| Condition | Behavior |
|-----------|----------|
| No WiFi at boot | 5 s timeout → LOCAL rendering (radio keeps retrying in background) |
| Stream stops >1 s | auto-fallback per block; NET LED 5 Hz blink |
| Controller clock never seen | stream still plays (depth-gated only, no deadline policy) — degraded but functional |
| vbat < 3.45 V | blue LED triple-blink warning |
| vbat < 3.30 V | radio off, forced LOCAL (sheds ~70 mA) |
| vbat < 3.05 V sustained 10 s | deep sleep (recovery = power cycle; C3 GPIO wake only exists on GPIO0–5 and MODE is on GPIO7 — accepted) |
| `adc_continuous`/I2S init failure | logged on console; unit keeps running the paths that did init |

## 8. Console & remote control

USB-CDC serial (115200): `help stat mode pat gain wifi id reboot` — `stat` dumps mode/source, WiFi/RSSI/IP, packet & underrun counters, buffer depth, sync offset, vbat, pot, heap. Same controls exist as JSON commands on UDP 5001 (`set_mode`, `set_gain`, `identify`, `reboot`) so the controller's web UI can drive everything.
