# HYPEROSCI — Firmware Performance & Heat Analysis

**Date:** 2026-07-18 · **Scope:** ESP32-C3 slave firmware (`src/esp32-slave`) + controller
(`src/unoq-controller/tools/hype_controller.py`) · **Trigger:** "ESP runs hot even with a
heatsink; streaming performance drops a lot and seems inefficient."

Grounded in the firmware source, `docs/protocol.md`, `docs/DESIGN.md`,
`docs/hardware/power-budget.md`, and the **installed** `framework-arduinoespressif32-libs`
sdkconfig. Quantitative claims were adversarially cross-checked; where the first-pass numbers
were wrong they are corrected inline and flagged **[corrected]**.

---

## 1. Bottom line

Two independent problems, neither with the cause it first appears to have:

1. **"Runs hot"** is ~80% the **WiFi radio** (a locked design decision) and, *on the bench*,
   largely the **SuperMini's LDO burning USB volts as heat** — **not** the CPU. Firmware can
   trim a few °C and real battery-mA, but cannot make this chip "cool" while it streams.
   **The cheapest first action is to re-measure thermals on battery, not USB.**

2. **"Drops a lot"** is **not a bandwidth problem** (audio is only ~17–25 % airtime at 4 slaves).
   It is the **slave receive path silently discarding post-stall bursts** plus **controller-side
   pacing jitter** — and both are currently **invisible** on the dashboard because the lost
   packets are never counted.

---

## 2. What we send on the wire

| | Value | Source |
|---|---|---|
| Audio packet | **988 B** = 20 B `HypeHeader` + 8 B `HypeAudioPayload` + 960 B PCM (240 stereo int16 frames) | `protocol.h:36-43`, `config.h:28` |
| Rate per slave | **200 pkt/s** (5 ms blocks) → **1.58 Mbit/s** app layer | `hype_controller.py:42-49` |
| Fan-out | **Unicast, one identical copy to each of 4 slaves** (single shared `PatternGen`) | `hype_controller.py:587,638,642-644` |
| System total | ~6.8 Mbit/s on-air, ~812 pps | `protocol.md §8` |
| Airtime | **~16–18 % @ MCS7**, ~33 % @ MCS3 (collapse only at 6 Mbps basic rate) | `protocol.md §8` |
| SYNC | multicast `239.0.0.1:5001`, 20 B, 2/s | `hype_controller.py:600-604` |
| STATUS | unicast → controller, 55 B, 1/s per slave | `net_rx.cpp:144-180` |

**Key observation:** all four scopes receive the **identical** packet stream
(`protocol.md §4.3`, `§5.2`). The content today is **100 % parametric** — circle/lissajous/rose
synthesized from ~5 numbers in Python, streamed as PCM. `osci-render` (the arbitrary vector-audio
case that *genuinely* needs PCM streaming) is not yet integrated. So today we stream 1.58 Mbit/s
of audio to draw shapes the slaves could largely render themselves — see §6.

---

## 3. Why it runs hot

The felt heat is dominated by two things, only one of which is the C3, and neither is the CPU
optimizations one would reach for first.

| Contributor | Dissipation | Actionable? |
|---|---|---|
| **WiFi RX chain, `WIFI_PS_NONE`** (`net_rx.cpp:194`) | **~84 mA ≈ 80 % of chip heat** (`power-budget.md:32`); ~330 mW on-die, but spread over the 5×5 QFN you *have* heatsinked (~13–20 °C rise) | **Locked.** PS_NONE is what defeats the ath10k DTIM unicast-buffering stall (~110 ms). Do **not** touch before the show. |
| **SuperMini LDO on bench USB** (5.0→3.3 V across ~130 mA rail) | **~220 mW** in an un-heatsinked SOT-23-5 (θJA ~200–250 °C/W → **~45–55 °C junction rise**). A C3 heatsink does nothing for it. | **2–4× less on battery** (4.2→3.3 V ≈ 117 mW fresh, 3.7→3.3 V ≈ 52 mW mid-discharge). |
| CPU @ 160 MHz (no `setCpuFrequencyMhz`) | 80 MHz saves **~5 mA [corrected]** — *not* 8–15 mA. C3 datasheet: modem-sleep is 20 mA@160 vs 15 mA@80 (most core current is frequency-independent), and `setCpuFrequencyMhz` does **no** voltage scaling, so compute *energy* is unchanged — the busy window just stretches. | Yes, safe, small |
| Discarded-mic soft-float, every block (§5.1) | ~8–10 % of one core ≈ ~2–4 mA | Yes, safe |
| Continuous 73.5 kHz ADC (`config.h:29`) | analog + DMA overhead even when the mic is unused | Medium (see §5) |

### Honest framing

- The C3 is a single-core RV32IMC with **no FPU** — every `float` op is libgcc soft-float. That
  makes the CPU findings below real *inefficiency*, but the total firmware-side thermal win is
  only **~10–15 mA and a few °C**. That helps **battery life and CPU headroom**, not "cool."
- **On the bench you are USB-powered, so the LDO drop is likely what your finger feels.**
  Re-measure at ~3.9 V on battery before spending effort chasing die temperature.
- A top-of-package heatsink couples poorly to the C3 die anyway (the thermal pad is on the
  package **bottom**; heat leaves through PCB copper). PCB copper / airflow beats a stick-on sink.
- The *only* lever that touches the 84 mA radio floor is **not streaming continuously** so the
  radio can power-save — i.e. the param-synth architecture in §6. High-risk, long-term.

---

## 4. Why streaming drops (ranked — the real causes)

Airtime is fine, so this is a **receive-path and timing** problem. In order of likely impact:

### 4.1 Silent lwIP mailbox overflow — *the invisible drop*
`CONFIG_LWIP_UDP_RECVMBOX_SIZE = 6` (confirmed in the installed SDK sdkconfig). After a
~300 ms AP "deaf" stall, the AP flushes ~60 queued frames as a microburst. The consumer
`net_task` runs at **priority 5** (`net_rx.cpp:288`), below the CPU-bound audio task at
priority 10 (`main.cpp:55`), polling with `vTaskDelay(1ms)` (`net_rx.cpp:278`). When more than
6 packets pile up between drains, **lwIP drops the excess in the tcpip thread, below the socket
layer**. Those drops are **never counted**: `st_rx_dropped` only increments for packets that
*arrive* (`net_rx.cpp` `handle_audio_packet`), and `CONFIG_LWIP_STATS` is off so `udp.drop`
doesn't even exist. Result: the dashboard shows **`rx/s` sagging with `drop/s ≈ 0`** — exactly
"drops a lot but nothing looks wrong."
**Fix:** move audio RX to **AsyncUDP** — its raw `udp_recv` callback captures the pbuf in the
tcpip thread into a 32-deep queue, **bypassing the 6-slot socket mailbox** and draining the
instant a packet lands. It also removes the WiFiUDP `malloc(1460)` + ~3 memcpys per packet
(§4.5). *(Confidence: medium — the mechanism and the =6 value are confirmed; that it is the
dominant drop source should be verified by instrumenting the burst.)*

### 4.2 Controller pacing jitter → all-scope reset
Any >20 ms drift re-anchors the epoch (`hype_controller.py:634`) with `flags=0`. The slave then
sees a >20 ms deadline jump and does a **full `jb.reset()`** (`net_rx.cpp:100-103`) → ~1 s
rebuffer **on all four scopes simultaneously**. The single-threaded `stream_loop` shares the
interpreter (GIL) with the `ThreadingHTTPServer` (`hype_controller.py:711-713`, polled at 1 Hz
per open browser) and runs pure-Python trig for 240 frames every 5 ms. On a Linux SBC, >20 ms
stalls under poll load are routine — a drop source **independent of the AP quirk**.
**Fix (controller):** run the HTTP server in a separate process, or pin the pacing loop.
**Fix (slave):** on a 20–200 ms *forward* delta, bounded `push_hold` to bridge the gap instead
of a full `jb.reset()` — turns a controller hiccup into a sub-frame hold, not a 1 s collapse.
*(Note: the slave detects this itself via the deadline-continuity check; the controller does
not send `HYPE_FLAG_SYNC_PULSE` on re-anchor.)*

### 4.3 Blocking audio socket on the controller
The audio socket is created plain-blocking (`hype_controller.py:580`) while the status socket is
explicitly non-blocking (`:585`). During an AP stall the kernel TX queue backs up and a blocking
`sendto` to slave 1 **stalls the whole 4-slave fan-out loop** (`:642-646`), pushing pacing past
the 20 ms re-anchor threshold → §4.2 on every scope at once. `protocol.md:503` already mandates a
non-blocking socket.
**Fix:** `audio.setblocking(False)` — the existing `except OSError: pass` already swallows the
resulting `BlockingIOError`. One line.

### 4.4 Thin stall margin
`LEAD_US = 350 ms` (`hype_controller.py:50`) is the steady-state buffer depth, but the measured
stall is up to **300 ms** — only ~50 ms of slack before the buffer drains to empty (dot collapse).
The jitter buffer is actually **512 ms** (`JB_CAPACITY_FRAMES = 24576`, `config.h:47`), far larger,
so the lead can be raised for free. *(Latency is irrelevant for scope art — only slave-to-slave
sync matters, and a shared absolute deadline preserves that regardless of lead.)*
**Fix:** raise `LEAD_US` to **~450 ms** (drains to ~150 ms worst-case, refills toward 450, ~60 ms
ring headroom under the 512 ms cap). Also **update `docs/protocol.md §5.2` and its §11 constants
table** — they still say 8192 frames / 170 ms, stale vs the 24576 / 512 ms code.

### 4.5 WiFiUDP copy/alloc churn
Each audio packet through `WiFiUDP.parsePacket()`+`read()` does `malloc(1460)` + a `recvfrom`
copy + `new cbuf` + a second copy + `free` + a third copy on `read()` — **~3 memcpys and 2
heap alloc/free per packet, 200×/s** on a core with no FPU competing with the audio task.
Fixed for free by the AsyncUDP migration in §4.1 (its callback hands you a pointer straight into
the pbuf → one copy into the jitter buffer).

---

## 5. CPU / DSP inefficiency (the "seems inefficient" part)

Real wasted cycles on the FPU-less core. None of these is the drop cause, but they are the
concrete inefficiency and they free the headroom that de-risks the 80 MHz downclock.

### 5.1 Discarded-mic soft-float, every block — *confirmed, the biggest CPU win*
In NETWORK mode `fill_block` calls `mic_in::drain()` → `pump()` every 5 ms block, running the
per-sample soft-float DC-blocker on ~122 mic samples **plus** float IIRs on ~122 vbat + ~122 pot
samples — then `drain()` discards the whole mic ring (`mic_in.cpp:183`). The mic output is thrown
away and vbat/pot only need ~1 Hz freshness (they are oversampled ~120×).
**Cost:** ~8.3 % of one core @160 MHz (~16.7 % @80). **Fix:** in NETWORK/synth modes, drain the
ADC DMA into a scratch buffer *without* the mic float math (still drain — that's the documented
anti-wedge, `renderer_local.cpp:143-146`) and decimate vbat/pot to ~1-in-256. `mode_manager.cpp:131,136`,
`mic_in.cpp:75-110,181-184`.
*Aside: `power-budget.md:161` claims this channel is "decimated ≥1 s"; the code does no such thing
(`SLOW_ALPHA=0.01` at 24.5 kHz ≈ 4 ms). Fixing the decimation makes code and doc agree.*

### 5.2 Per-sample float gain in the audio path — *operator-conditional [corrected]*
`audio_out::write` takes a memcpy fast-path only when `gain ≥ 0.999`, else does a soft-float
multiply on all 480 samples/block (`audio_out.cpp:80-88`).
**[corrected]** This is **not** "always-on": every default lands on the memcpy path (`g_gain`
defaults 1.0, NVS default 1.0, dashboard slider defaults to 100 and only sends `set_gain` on
drag). It activates only on units an operator trims below 100 % to match scope deflection —
common on a multi-scope bench, but opt-in.
**Fix (still worth it, trivial, safe):** store gain as Q15 and use
`scaled[i] = (int16_t)(((int32_t)frames[i] * g_q15) >> 15)`. Removes float from the prio-10
output path; <1 LSB difference. (For `g<0.999`, worst product stays inside int16 — no saturation.)

### 5.3 Oscillators call soft-float `sinf`/`cosf` per sample — *LOCAL/HYBRID only, but heavier than it looks*
`render_circle`/`render_lissajous`/`render_square` each evaluate 2 transcendentals per sample =
**480 `sinf`/`cosf` per block** (`renderer_local.cpp:79-80,111-113,126-127`).
**[corrected]** Soft-float `sinf`/`cosf` on the C3 is **~2000–2400 cyc/call** (Espressif's own
benchmark: `cosf` = 2377 cyc), *not* the ~300 first assumed. So `render_circle` ≈ 480 × ~2000 ≈
**~960k cyc/block vs the 800k budget @160 MHz** — these renderers are **brushing the real-time
budget at 160 MHz and would underrun at 80 MHz.** They apparently run today, so the true per-call
cost is likely lower than 2377 with `-O2`, but this is a **latent correctness flag**, not just
efficiency. `render_square` is the worst offender: it computes a full `sinf` **only to take its
sign** (`renderer_local.cpp:111,113`) — a `phase < PI ? +A : -A` compare gives a bit-identical
square wave for free.
**Important scope note:** these run in **LOCAL / HYBRID / ≥1 s-fallback only** — the steady
NETWORK streaming path never calls them (`mode_manager.cpp:124-136`). So a sine LUT is **~0
thermal benefit during the network show**; its value is (a) making LOCAL/HYBRID safe at 80 MHz,
and (b) enabling the param-synth mode in §6.
**Fix:** shared int16 sine LUT + uint32 phase accumulator (cos = quarter-table offset).

### 5.4 Minor
`update_lpf_coeffs` computes `cosf(w0)` four times where once suffices (`renderer_local.cpp:27-30`;
`b2==b0`, `b1==2*b0`). `render_mic`'s Butterworth is a per-sample soft-float biquad at 48 kHz —
Q15 would cut it, but it is MIC-mode only. No live double-`pump()` bug exists (verified), but it
is guarded only by the `if (pattern != MIC) drain()` check — fragile; a single-owner pump would
be more robust.

---

## 6. The strategic fork: stream parameters, not PCM

The whole hot, drop-prone pipeline exists to deliver **parametric** content the slaves could
synthesize locally. A `HYPE_PARAM` packet `{kind, freq, amp, ratio, phase_epoch}` sent on-change
+ a low-rate keepalive, with the slave computing phase from the **shared master clock** each
block, would:

- cut steady-state parametric traffic ~1000× (order-of-magnitude), **delete the 96 KiB jitter
  buffer and the 350 ms lead**, and make the ath10k stall **invisible** (a lost param packet is
  idempotent; the slave keeps drawing, phase-locked);
- let the radio run **power-save** → move NETWORK draw from ~120–135 mA toward LOCAL's ~50–60 mA
  → the real heat fix;
- make **per-scope-different art free** (each slave its own params at ~zero bandwidth).

**But this is a parallel prototype, not a pre-show change**, because:
- The slave does **not** have a drop-in synth today: there is **no `rose` renderer** in the enum
  (`renderer_local.h:14` = MIC/CIRCLE/LISSAJOUS/RAMP/SQUARE), and the local oscillators
  **free-run their phase** (`renderer_local.cpp:50-51,81`) — they are *not* mutually clock-locked,
  so synth mode needs new clock-anchored phase code.
- `osci-render` (arbitrary vector audio) **cannot** be parameterized and still needs the PCM path.
  The right end-state is a **hybrid**: param-synth for figures, PCM only for osci-render.
- For the PCM path, **keep 48 kHz / 16-bit stereo** — it is the *floor* for smooth scope vectors,
  not a knob to turn down (lower rate = visible polygon chords + flicker). 12-bit wire packing
  saves ~24 % but only on a path that should carry osci-render; solve bandwidth with synth instead.

---

## 7. Prioritized action plan (5 weeks to 2026-08-21: robust + cool + low-risk)

**Tier 1 — one-liners, do now (kills the visible drops):**
1. `LEAD_US` 350 → **450 ms** (controller). 3× stall margin, JB has room, zero cost. §4.4
2. `audio.setblocking(False)` on the controller audio socket. §4.3
3. Move the HTTP server off the pacing thread/process (or pin the pacing loop). §4.2

**Tier 2 — cheap & safe (the real inefficiency + headroom):**
4. NETWORK-mode ADC drain **without** the soft-float DC-blocker; decimate vbat/pot ~1/256 (keep
   draining the DMA). Frees ~8–10 % core. §5.1
5. **Q15 integer gain** in `audio_out`. §5.2
6. `setCpuFrequencyMhz(80)` **after** 4–5 land; DMM-measure 160 vs 80, watch `astk`/underruns.
   Expect **~5 mA**. §3

**Tier 3 — measure-gated:**
7. If underruns persist: **AsyncUDP** for audio RX (bypasses the 6-slot mailbox; the invisible-drop
   fix; also removes the 3 copies + malloc/packet). §4.1, §4.5
8. Slave: soften >20 ms *forward* delta from `jb.reset()` → bounded `push_hold`. §4.2

**Tier 4 — prototype in parallel, NOT before the show:**
9. Sine LUT + phase accumulator (fixes `render_square`'s over-budget soft-float; makes LOCAL/HYBRID
   safe at 80 MHz). §5.3
10. Clock-anchored param-synth mode + add `rose` to the slave — true fix for both symptoms, real
    new code, osci-render still needs PCM. §6

**Also:** add an **uncounted-loss / `seq_gaps` line to the dashboard** so silent mailbox drops
(§4.1) stop being invisible — you cannot tune what you cannot see.

### Guardrails — do NOT do before the show
- Re-enable WiFi power save → reintroduces the ~110 ms ath10k DTIM stall.
- Switch audio to multicast → basic-rate = *worse* airtime + no ACK/retry (`protocol.md §8` is right).
- Shrink the 512 ms jitter buffer → sized for the ath10k quirk; latency is irrelevant here.
- Enable `esp_pm` / auto light-sleep → fights `PS_NONE` and I2S DMA timing.

---

## 8. Method note

Derived from a direct read of the firmware + docs + the installed SDK, then expanded and
**adversarially verified** by an independent multi-agent pass (4 analysis lenses, 25 skeptic
verdicts, 1 completeness critic). 22 of 25 verdicts were "partial" — the *mechanisms* held but
several first-pass **numbers were over-claimed and are corrected here**: the 80 MHz saving
(~5 mA, not 8–15), the soft-float trig cost (~2000 cyc/call, not ~300 → renderers near the RT
budget), and the gain float path (operator-conditional, not always-on). Nothing was refuted;
the load-bearing facts — radio-dominated heat, bench-USB LDO, `recvmbox=6` silent drops,
controller pacing jitter, thin lead margin — all stood.
