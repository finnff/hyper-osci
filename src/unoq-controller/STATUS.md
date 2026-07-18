# UNO-Q status — board access & osci-render build

_Last verified: 2026-07-18. Scope: the physical Arduino UNO-Q board and the osci-render
render engine. The controller app in this directory is still a stub — see [README.md](README.md)._

## Controller daemon (bring-up, 2026-07-18)

`tools/hype_controller.py` (deployed at `/home/arduino/hype_controller.py`) streams
test patterns to the slaves and serves the web control panel:

- **Web UI:** <http://10.42.0.128:8080> (USB tether) or <http://192.168.50.1:8080>
  (on `HYPEROSCI_AP`). Pattern/freq/amp controls, stream on/off, per-slave and
  all-slave mode toggles (network / mic / hybrid), identify, gain, reboot.
- **Runs as a systemd service**, starts on boot:
  `sudo systemctl {status,restart,stop} hyperosci-controller`, logs via
  `journalctl -u hyperosci-controller -f`. Unit file mirrored at
  `deploy/hyperosci-controller.service` in this directory.
- Predecessor `tools/hype_sender.py` is kept as the minimal protocol reference
  (don't run both — they'd fight over UDP :5002 and the slaves' jitter buffers).

## How to get on the board

- **SSH:** `ssh arduino@10.42.0.128` — password `arduino`. Host is `uno-q`, Debian 13 (trixie), **aarch64**, 4 cores / 3.6 GB RAM.
- **ADB:** `adb shell` also works (USB).
- **sudo:** passwordless (`arduino` has NOPASSWD); account password is still `arduino`.
- IP note: `192.168.2.170` is also availible but its from the current WiFi lease (profile `jdv`). so please use the USB reverse-tether path.

## What's compiled

**osci-render, open-source git version, built natively for aarch64** (v2.8.10.8, **free** variant / `OSCI_PREMIUM=0`). Source at `~/osci-render`, artifacts at:

```
~/osci-render/Builds/osci-render/LinuxMakefile/build/
  osci-render          35 MB standalone, ELF ARM aarch64  ← the working binary
  osci-render.vst3     aarch64 VST3 plugin
```

## What works

- The `osci-render` standalone is a valid aarch64 executable, all shared libs resolve, and it launches (verified headless under `xvfb-run`). The VST3 is also aarch64.
- Clean `make -j3 CONFIG=Release` build, 0 errors. DSP SIMD compiles to ARM NEON (via xsimd), so no x86 dependency.

## What does NOT work / caveats

- **The premium binary can't run here.** `~/osci-render-premium/osci-render` is x86-64 → "Exec format error" on aarch64. Dead end; use the built-from-source binary instead.
- **It's a GUI/OpenGL app — no display on the board.** It only ran above because `xvfb` gave it a virtual display. There is no headless "just stream the audio" mode yet. For the HYPEROSCI plan (UNO-Q streams X/Y audio to the ESP32 slaves) we still need either a headless audio-only render path or to host the VST3 — **this is the open question, not solved.**
- **Free variant only.** Premium features are gated behind `OSCI_PREMIUM=1` and may need premium assets not in the public repo — untried.
- Launching prints `ALSA … /dev/snd/seq … No such file` — harmless (no hardware MIDI sequencer), not a failure.

## Rebuilding

Not a plain `make` — the upstream README/CI recipe needed several aarch64 fixes (native Projucer from Debian `juce-tools`, JUCE 8.0.6 cloned locally, drop `-mavx`, remove the develop-only `juce_audio_processors_headless` module, patch 2 lines in `juce_sharedtexture`, build LuaJIT, `-j3` to avoid OOM). Full step-by-step is in Claude's project memory (`osci-render-aarch64-build`). `.orig` backups of the two edited files sit next to them in `~/osci-render`.

## Dashboard v3 + LOCAL-pattern crash investigation (2026-07-18, late evening)

**Dashboard redesigned around one question per slave — "what should it draw?"**
One `draw` row per card: `STREAM · HYBRID · mic · circle · lissajous · ramp ·
square`. STREAM/HYBRID play the page's streamed pattern; the rest are rendered
on the slave itself (pattern buttons send `set_pattern` + `set_mode local` in
one click). The controller now **only streams audio to slaves in
NETWORK/HYBRID mode** — a slave on a local pattern gets nothing (this was the
"why does it stream at a slave set to microphone?" confusion, and it was also
inflating the drop counter). STATUS grew a `local_pattern` byte (35 B payload,
protocol.md §3.4 updated) so the UI names the actual local pattern instead of
guessing "mic". `source` is smoothed over sub-150 ms conceal gaps so the
"stream lost" line stops flapping, and the UI shows "buffering (normal)" for
4 s after any switch.

**OPEN BUG — slave hard-wedges when rendering local circle/lissajous/ramp/
square** (mic pattern is fine, streamed playback is fine — 3 min full-scale
streamed circle soak clean). Death is a total chip freeze: console dead, no
panic, no WDT reboot, JTAG debug module can't examine the hart, USB-JTAG
eventually drops off the bus. Ruled out by experiment: stack overflow (8 KB +
HWM), my jb-flush change, stream gating, ADC pool overflow (drain added),
deep-sleep battery guard (dies in <2 s; guard needs 10 s + prints first),
output amplitude (streamed = same waveform, stable). Working theory: **supply
brownout from CPU load** — the old renderers burned 480 soft-float sinf/cosf
ROM calls per 5 ms block (C3 has no FPU) on top of active WiFi, through the
SuperMini's undecoded `S2LC` LDO, on a bench unit with a suspect ground
(vbat/pot/mic-bias readings all drift). Renderers rewritten to phasor-rotation
oscillators (2 sinf/cosf per block instead of 480; square is now trig-free) —
built, NOT yet flashed: the slave's USB is a phantom device until power-cycled.

**Next bench session:** ① power-cycle the slave (battery switch OFF before
touching USB!), ② reflash (`pio run -t upload`), ③ soak LOCAL+circle ≥3 min —
if it still wedges with the cheap renderers, measure 3V3 during local circle
(ANENG A9002) and re-seat the GPIO1/AGND grounds; the crash then is
electrical, not firmware. ④ `stat` now prints `astk` (audio stack headroom)
and the firmware exports `g_ckpt/g_iter` markers for JTAG forensics.

**RESOLVED same evening:** the slave's USB recovered after a JTAG reset (no
power-cycle needed); flashed the phasor-rotator build and re-ran the soaks:
LOCAL+circle 3¾ min clean (was: dead <10 s, every time), lissajous/ramp/square
40 s each clean, clean rejoin to NETWORK streaming afterwards. The 480
soft-float sinf/cosf calls per block were the trigger — with them gone the
wedge is unreproducible. Caveat for the PCB: the failure mode was a
supply-level freeze, so the underlying power margin is thin — keep the S2LC
LDO decode (Q) open, add bulk capacitance near the C3, and re-check the bench
unit's grounds (vbat still reads 6-10 mV noise, GPIO1 divider ground suspect).

---

## Perf Tier 1+2 landed + NEW show-day risk: ath10k AP rate-control wedge (2026-07-18, night)

Implemented the "small cheap" tier of PERFORMANCE_HEAT_ANALYSIS.md (§7 items 1-5;
commits d192b3a controller, d0e23ae firmware):

1. `LEAD_US` 350 → **450 ms** — verified: slave settles at `depth=21600` frames (450 ms).
2. `audio.setblocking(False)` on the controller audio socket.
3. Q15 integer gain in `audio_out::write` (no float left in the audio-output path).
4. `update_lpf_coeffs` computes `cosf(w0)` once (was 4×).
5. `mic_in::pump(mic_math)` — light drain when the mic output is discarded: DMA pool
   still fully drained (anti-wedge), soft-float DC-blocker skipped, vbat/pot/bias IIRs
   decimated 1-in-256 (~1 s freshness). Frees ~8-10% of the core. MIC/HYBRID unchanged.

§7 item 6 (80 MHz downclock) deliberately NOT done — small win, wants its own soak.

### The wedge (found while verifying #1; NOT caused by it — A/B-proven)

Symptom: slave stuck on mic fallback, dashboard `rx/s` near zero, slave `stat` showed
`drop` climbing ~100/s with `rx` frozen, `gaps` huge. Diagnosis: **ping from the UNO-Q
showed 0% loss but RTT 465-1690 ms under streaming load** (idle: 2 ms). The ath10k AP's
per-station TX rate control had wedged below the stream's 1.58 Mbit/s → a standing ~1 s
TX queue → every audio packet arrived far past its deadline → 100% stale-dropped by the
slave, and the qdisc tail-dropped ~40% besides. Self-sustaining as long as the stream
keeps feeding the queue.

Remedy ladder (tested in order): controller restart ✗, slave reboot ✗ (fresh assoc did
NOT reset the AP's rate state), **AP bounce ✓**:

    sudo nmcli c down hyperosci-ap && sleep 3 && sudo nmcli c up hyperosci-ap
    sudo systemctl restart hyperosci-controller   # its multicast SYNC socket dies with the iface

**Show runbook:** if all scopes fall back to mic and `rx/s` is ~0 while WiFi shows
connected — bounce the AP, then restart the controller. ~15 s total. (SSH via USB
tether survives the bounce; the AP bounce is invisible to nothing except the slaves,
which rejoin in ~5 s.)

### Residual (measured tonight, pre-existing): silent burst loss

With everything healthy: rx ≈ 155-175 pkt/s of 200 sent, `drop=0`, `underrun=0` —
i.e. **15-20% of packets are lost below the app layer** (ath10k deaf-stall flush
microbursts overflowing lwIP's 6-slot UDP recvmbox, analysis §4.1), forcing a
jb rebuffer (~1-2 s of mic) every ~10 s. This is the measurement §7 Tier 3 was
gated on → **AsyncUDP audio RX is now warranted** for a W2 work item. RF was worse
tonight than during yesterday's clean 44 s soak; re-check loss rate in daytime too.
