# HYPEROSCI slave firmware (ESP32-C3 SuperMini)

Firmware for one slave unit: receives X/Y audio from the controller over WiFi
and drives a PCM5102A DAC into an oscilloscope in XY mode, with autonomous
mic-based fallback (the old proven Lissajous behavior).

Canonical references: [docs/DESIGN.md](../../docs/DESIGN.md) (decisions),
[docs/protocol.md](../../docs/protocol.md) (wire protocol),
[docs/hardware/wiring.md](../../docs/hardware/wiring.md) (hookup).

## Build & flash

Uses PlatformIO with the [pioarduino](https://github.com/pioarduino/platform-espressif32)
platform (Arduino core 3.x on ESP-IDF 5.x — required for the `i2s_std` and
`adc_continuous` drivers).

```sh
pio run                    # build
pio run -t upload          # flash over USB-C (power switch OFF — see wiring.md!)
pio device monitor         # 115200 baud, USB CDC
```

## Host test (jitter buffer)

`push_loop` (Phase 2 loop-fill concealment) is tested on the host, no ESP32
needed. `test/host/test_jitter_buffer.cpp` builds against a small FreeRTOS
shim (`test/host/shim/`):

```sh
cd src/esp32-slave
g++ -std=c++17 -Wall -Wextra -I test/host/shim -I include -I src \
    test/host/test_jitter_buffer.cpp -o /tmp/test_jb && /tmp/test_jb
```

## Module map

| File | Role |
|------|------|
| `include/config.h` | **canonical** pins/constants (mirrors DESIGN.md §4) |
| `include/protocol.h` | **canonical** wire format (shared with controller) |
| `src/main.cpp` | task setup and glue |
| `src/audio_out.*` | I2S master TX → PCM5102A (48 kHz/16-bit stereo, no MCLK) |
| `src/mic_in.*` | `adc_continuous` DMA: mic 24 kHz + vbat + pot; DC blocker |
| `src/renderer_local.*` | fallback renderer: X=mic, Y=Butterworth LPF (pot 20–300 Hz); circle/Lissajous/ramp/square test patterns (ramp = DC test, square = filter-ringing test) |
| `src/jitter_buffer.h` | SPSC frame ring with master-clock deadline tracking |
| `src/timesync.*` | SYNC-beacon offset tracking (median of 8) |
| `src/net_rx.*` | WiFi STA, UDP audio/ctrl RX, deadline policy, status TX |
| `src/mode_manager.*` | LOCAL/NETWORK/HYBRID state machine + auto-fallback, NVS settings, JSON commands |
| `src/ui.*` | buttons, LED patterns, battery policy, serial console |
| `test/host/` | host test for `push_loop` (jitter_buffer.h) + FreeRTOS shim |

## Task layout (single core)

| Task | Prio | Period | Job |
|------|------|--------|-----|
| `audio` | 10 | 5 ms (paced by I2S DMA) | pull block from active source → I2S |
| `net_rx` | 5 | 1 ms poll | WiFi mgmt, UDP RX → jitter buffer, 1 Hz status |
| Arduino `loop` | 1 | 10 ms | buttons, LEDs, battery, console |

## Serial console (USB-C, 115200)

`help` · `stat` · `mode <local|net|hybrid>` · `pat` (cycle test pattern) ·
`gain <0-100>` · `wifi <on|off>` · `id <0-255>` · `droptest <every> [run]`
(bench packet loss) · `fill <loop|hold>` (bench fill method) · `reboot`

## Status / TODO

- [x] Compiling skeleton with all modules and the full mode state machine
- [x] Packet-loss concealment (deadline-continuity buffer: ≤20 ms holes
      hold-filled; hold → ramp-to-center → 1 s mic fallback ladder)
- [ ] Bench bring-up: I2S output on a real scope (circle, then `pat` → ramp for
      the DC go/no-go test, square for filter ringing)
- [ ] Bench bring-up: mic path + pot filter (compare with old sigma-delta unit)
- [x] Network test against the UNO-Q controller (the laptop stand-in phase was
      skipped — the UNO-Q arrived in time and streams end-to-end since 2026-07-18)
- [ ] Clock-drift compensation (single-frame slip/insert per protocol.md §5.3)
      — v1 corrects in whole 5 ms blocks at the window edge; fine for the ±5 ms
      spec, revisit if scopes drift apart visibly over long sets
- [ ] Offset slewing in timesync (protocol.md §4.2 RECOMMENDED; v1 = direct median)
- [ ] WiFi credentials via console/NVS (currently compile-time defaults)
