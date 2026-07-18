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
