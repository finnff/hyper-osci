# UNO-Q status — board access, controller daemon & change log

_Last verified: 2026-07-22. Scope: the physical Arduino UNO-Q board, the deployed
controller daemon, and the (rejected) osci-render route. The controller app itself is
documented in [README.md](README.md)._

## Multicast audio: measured, rejected (2026-07-31)

The four-slave airtime worry — "28.8% duty for one slave, ~115% for four" — is
real, but the proposed fix (send one multicast packet instead of N unicasts)
makes it 12.8x worse. Measured, not reasoned; cheap to repeat with
`tools/mcast_probe.py`.

The slave was always ready for it. `net_rx.cpp` does
`udp_audio.listenMulticast(MCAST_GROUP, PORT_AUDIO)`, and
`handle_audio_packet()` filters on magic/version/type/sample_rate only — never
on destination address. So this needed **no firmware change**: aiming the
controller's `sendto` at 239.0.0.1 would have been the whole diff.

It is a catastrophic pessimisation. Saturating each path from the board (probe
at 1200 pkt/s; on-air counts from `iw dev wlan0 station dump` and the
`multicast TXQ` row of `iw dev wlan0 info`):

| path | ceiling | per packet | throughput |
|---|---|---|---|
| unicast | 1282 pkt/s | 780 us | 10.13 Mbit/s |
| multicast | 100 pkt/s | 9980 us | 0.79 Mbit/s |

Group-addressed frames go out at the lowest **basic rate** (~1 Mbit/s here),
not the negotiated per-station rate: 988 B at 1 Mbit/s plus preamble, DIFS and
backoff is the ~10 ms measured. The stream needs 200 pkt/s, so multicast cannot
carry **one** slave, never mind four. At 12.8x the airtime per packet it would
not break even until ~13 slaves.

It is not DTIM buffering — both ends have power save off (`esp_wifi_set_ps(
WIFI_PS_NONE)` on every wifi-up; the AP reports `Power save: off`). Raising the
basic rate is the only thing that could revive the idea, and NetworkManager's
wpa_supplicant AP mode exposes no `basic_rates` knob, so it would mean
replacing the AP stack with hostapd. Not worth it — but the airtime problem it
was meant to solve is real, so it needed measuring properly rather than
extrapolating from a saturated-link average.

**The four-slave load, measured directly** (one slave at 4x the packet rate, so
only airtime varies):

| offered | accepted | EAGAIN | on-air |
|---|---|---|---|
| 200/s | 100.0% | 0 | 1201 |
| 400/s | 88.6% | 274 | 2513 |
| 800/s | 85.8% | 680 | 4354 |
| 1000/s | 82.0% | 1078 | 4922 |

One slave at 200 pkt/s is clean — zero EAGAIN. The four-slave load of 800 pkt/s
loses ~14% locally before it ever reaches the air, and EAGAIN already shows up
at 400 pkt/s, so **four slaves does not fit on this 2.4 GHz / 20 MHz link**.
(Do not trust the "16% of the medium" you get from dividing 200 pkt/s by the
saturated 1282 pkt/s ceiling: the marginal cost per packet under saturation is
not the cost at low load, and the table above is what actually happens.)

The lever that would work is PHY rate, not packet count. The uplink already
negotiates HT — `rx bitrate: 21.7 MBit/s MCS 2 short GI` — while the downlink
sits near 10 Mbit/s, so there is headroom in rate control / band config that
costs no protocol change and no firmware change. Multicast is the wrong tool
regardless: group frames are unacknowledged and never retransmitted, so
adopting them trades unicast's retries away on a link that already loses
15-20% below the app layer (see "Residual: silent burst loss").

One trap when re-running the probe: aimed at a slave in forced **LOCAL** mode,
the slave's `rx` counter is not a delivery metric. Nothing drains the jitter
buffer in LOCAL, so it fills at ~512 ms and `jb.push()` starts refusing;
those losses land in `dropped` and never touched the air. Judge delivery from
the AP-side counters, or put the slave in NETWORK first.

### Two slaves: no — and SO_SNDBUF does not rescue it (2026-07-31)

Follow-on question: if four does not fit, does two? Measured the same way, one
slave at 4x rate, six rounds alternating so both loads see the same link phase:

| load | clean rounds | median local loss |
|---|---|---|
| 200 pkt/s (1 slave) | 5/6 | 0.0% |
| 400 pkt/s (2 slaves) | 0/6 | 21.0% |

One slave is clean in every round but one. Two slaves lost 10.8-38.4% in every
single round. **One slave is the honest maximum on this link today.**

The obvious rebuttal is that the drops are EAGAIN — a local queue overflow, not
air loss — so a bigger send buffer should ride through the documented ath10k
deaf-stall (~100-300 ms every ~1.44 s, `config.h`). At LEAD_US = 450 ms a
packet delayed 300 ms still beats its deadline, so this looked right, and it
contradicted the earlier decision to leave `SO_SNDBUF` alone. It was worth
testing. It is wrong, and the way it fails is instructive (`--sndbuf`, 400
pkt/s, mean of 3 rounds):

| sndbuf | EAGAIN | backlog at stop | drain |
|---|---|---|---|
| default | 1470 | 178 KiB | 3006 ms |
| 1 MB | 0 | 442 KiB | 1047 ms |
| 4 MB | **0** | **2979 KiB** | **10486 ms** |

(Backlog is `SIOCOUTQ` = `sk_wmem_alloc`, i.e. charged skb memory at ~2.3x the
988 B payload — read it as a size, not a packet count.)

A 4 MB buffer does reach zero EAGAIN — by swallowing ~3 MB it then needs
**10.5 seconds** to hand to the radio. Every packet in there arrives long past
its 450 ms deadline and the slave discards it on the `DEADLINE_SLACK_US` check.
The buffer does not move packets; it converts a *visible* local drop into an
*invisible* late arrival, and the tx-drop counter goes green while delivery
gets worse. Do not "fix" this with a buffer bump.

What the drain figures actually expose is that link capacity swings by more
than an order of magnitude. In a good phase it saturates at 1282 pkt/s; in the
bad phase above, the default-buffer run accepted only 530 of 2000 offered in
5 s and then spent another 3 s draining — under ~100 pkt/s all-in, which is
half of what a single slave needs. That instability — not airtime accounting,
not buffer sizing, not multicast — is what has to be fixed before a second
slave is worth building.

## Interval timers (2026-07-28)

*"Show preset **IDENT** on slaves 1+2 for 20 s every 5 minutes."* Up to 8
rules, persisted at `~/hype_timers.json`, edited from a new panel on the
dashboard. `POST /api/timer` takes `op=save|delete|toggle|fire` — `fire` is
the page's **▶ test** button, so a rule can be rehearsed at soundcheck
instead of waited out.

A rule fires on its own thread (`timer_loop`, 0.25 s tick) for the same
reason `persist_loop` has one: a font rebuild on the stream thread is an
audible gap on every scope at once. The preset goes on air, the targeted
slaves are switched to STREAM, and on expiry both the pattern and each
target's previous draw setting are restored. Targets are slave **ids**, not
ips, so a rule survives a DHCP lease.

Decisions worth remembering, because each one is a failure mode we chose
against:

- **Non-targets are left alone.** There is one streamed pattern for the whole
  rig, so a slave already on STREAM/HYBRID sees the ident too. Forcing every
  non-target to its local pattern for the duration would make "on slaves 1+2"
  literally true, but it is a much bigger intervention than the rule asked
  for. The panel says this out loud rather than pretending otherwise.
- **Touching the pattern panel ends a hold early** and keeps what the
  operator just set (`_takeover`). Silently reverting them 15 s later is
  worse than a rule missing one cycle. A bare `{"stream":…}` mute does not
  count as taking over.
- **A hold is never persisted.** `persist_loop` skips while one is up, so a
  controller killed mid-ident comes back drawing the *set* — the exact
  failure `~/hype_state.json` exists to prevent. `dirty` stays armed, so the
  restore is written on the next tick.
- **A fire is refused for 2.5 s after a release** (`HOLD_COOLDOWN_US`). A
  slave's draw mode is only known from its 1 Hz STATUS beacon; measured in
  the harness, right after a release the slave reports `local` while the
  controller still has `network`. A rule firing inside that window would
  record the forced mode as the "previous" one and strand the slave on
  STREAM for good.
- **`every_s` is clamped past `hold_s`.** A period inside the hold would
  re-fire before the restore ran and the show would never come back.
- **One hold at a time**, claimed under the same lock that reads it. A rule
  that comes due during another's hold keeps its due time and fires just
  after that one releases, once the cooldown has passed (~2.7 s).

Verified two ways. `tests/test_timers.py` (new, 32 checks) drives
`fire_timer`/`end_hold` and the scheduler with a stub `CmdSender` and
fabricated slaves — no UDP, so unlike `live_test.py` it is safe to run
against a live board. One section binds an HTTP handler on an ephemeral
loopback port, because the preset/rule coupling only exists in the handler.
End to end, a real controller on :8098 with `HYPE_*` in a temp dir and three
fake slaves beaconing real HYPE_STATUS from 127.0.0.2/.3/.4: the rule fires
and releases on schedule, only the targets are commanded, the pre-hold
pattern and modes come back, the takeover and cooldown behave, and
`~/hype_state.json` still reads `circle/123` while the ident is on air.
Dashboard driven in headless Chromium at 390 px and 1280 px — no overflow, no
control under 44 px on the phone, desktop unchanged.

### Review fixes (2026-07-31)

Reviewed against the board before merging. Three defects, each reproduced
first:

- **A refused fire cost the rule its turn.** `timer_loop` wrote the next fire
  time *before* calling `fire_timer` and ignored the result, so the rule
  queued behind another hold was refused by the post-release cooldown 250 ms
  later with its turn already spent — the one case the bullet above
  promises. Probed with a 3 s hold and a rule due 1.5 s into it: the second
  rule never went on air and its next fire was pushed 54.5 s out. The turn is
  now spent only on a fire that happened; a transient refusal retries just
  past the cooldown.
- **A hold fired into a muted stream.** `fire_timer` never read `stream_on`,
  so with STREAM off it still dragged its targets onto a dead stream: slaves
  that were drawing their own mic went to a drained jitter buffer and a dark
  scope for the whole hold. Muting for a changeover is exactly when a rule
  comes due. Refused now, and confirmed on the deployed daemon.
- **Deleting a preset left an armed rule.** The rule kept rendering ON with a
  live countdown, fired never and logged nothing. Referencing rules are
  paused, the operator is told, and a hold showing the deleted preset is
  released instead of left on the scopes.

Refusals were a bare `False`, so the dashboard's 409 guessed at three causes
in one string; `fire_timer` now returns the reason (or `None`) and the page
shows it. Three narrower races went with them: `end_hold` armed the cooldown
in a second lock acquisition (`timer_loop` landing in the gap defeats the
cooldown entirely), `_takeover` tested `timer_hold` unlocked before calling
an `end_hold` that is already atomic and already a no-op, and the timer
delete/toggle path tested `timer_hold` for `None` and then subscripted it in
a separate unlocked read — a 500 on the operator's tap if a hold expired in
between.

## Presets you can overwrite, phone UI cleanup (2026-07-28)

Two things, both in `PAGE`; no Python behaviour changed.

**Overwrite an existing preset.** `op=save` has always replaced a same-named
preset in place server-side, but the only way to reach that path was the
`+ save` prompt — you had to retype the name exactly and guess what
`sanitize_name()` would do to it, so in practice everyone made a second
preset. The page now tracks which preset it last applied or wrote
(`localStorage`, because the phone locks its screen mid-set and the reloaded
page must still know) and shows **`⟳ update "<name>"`** next to
**`+ save as…`**. The update button names its target and confirms; the
applied preset's chip is highlighted. Verified end to end against a real
controller on :8098 with `HYPE_PRESETS` pointed at a temp dir: applying a
preset then hitting update rewrote it in place (`circle`/90 Hz →
`rose` a=8/412 Hz on disk) with the list still 20 long and no duplicate name.

**UI cleanup, superseding two calls from the entry below.**

- `.seg` wrapping was the right fix but left the joined pill with square
  corners mid-row — row 1 ended blunt, row 2 started blunt, and it read as a
  rendering fault. The 7 draw options are now separate 4 px-gapped chips
  (also fewer mis-taps). The preset `[name][×]` pair keeps the joined pill
  under its own `.chip` class: two buttons, never wrapped, radii always right.
- The full-width square preview pushed the entire pattern panel below the
  fold. Capped at `min(100%,38vh)` and centred, it stays square (mandatory —
  the canvas is 520² and a non-square box shears every figure) while the
  controls stay on screen. `#top` also switches to `flex-direction:column` on
  phones: once the preview is narrower than the viewport the controls panel
  tries to share its flex line, which put the page back to 504 px of
  sideways scroll.
- Touch targets: every button/select/number input gets `min-height:44px` and
  ranges 44 px of height under 640 px. 52 controls were 32 px tall.
- `post()` now surfaces the controller's `{err}` instead of swallowing it —
  `+ save as` at the 20-preset cap used to look exactly like a save that
  worked. Confirmed: the 21st save raises `max 20 presets` in the page.

Verified in headless Chromium at 320/390 px (`scrollWidth == innerWidth`,
zero overflowing elements, no button under 44 px) and 1280 px (unchanged:
260 px scope, `#top` in a row, 32 px buttons, 3-column slave grid, 4-column
stats). Extracted `<script>` passes `node --check`; `py_compile` clean.
`test_fixes.py` and `test_persist.py` produce byte-identical output before
and after the change (both still fail on this dev box for the documented
reason — no Hershey fonts here). Needs the usual scp + restart to reach the
board.

## Dashboard renders on portrait phones (2026-07-28)

On a 390 px phone the page scrolled sideways to 550 px: the 7-button `draw`
segments (519 px, `.seg` didn't wrap) and the `#slaves` grid's 360 px minimum
track both overflowed, and the preview canvas sat fixed at 260 px. CSS-only
fix in `PAGE` (no JS/markup change): `.seg` gets `flex-wrap:wrap`, the slave
grid minimum becomes `min(360px,100%)`, and a `max-width:640px` media query
makes `#scope` a full-width square (`aspect-ratio:1/1`), drops `#controls`'
min-width, loosens `.stats` to 3 columns and un-nowraps the help-table
headers. Verified in headless Chromium at 320/390/1280 px with injected
state: zero overflowing elements, `scrollWidth == innerWidth` on both phone
widths; desktop unchanged (260 px scope, multi-column cards — where the seg
row previously overflowed ~400 px cards too, now it wraps). Extracted
`<script>` still passes `node --check`. Needs the usual scp + restart to
reach the board.

## Controller daemon (bring-up, 2026-07-18)

`tools/hype_controller.py` (deployed at `/home/arduino/hype_controller.py`) streams
test patterns to the slaves and serves the web control panel:

- **Web UI:** <http://192.168.50.1:8080> (on `HYPEROSCI_AP` — the address that
  never moves, use this one) or <http://10.42.0.5:8080> (USB tether).
  Pattern/freq/amp controls, stream on/off, per-slave and
  all-slave mode toggles (network / mic / hybrid), identify, gain, reboot.
- **Runs as a systemd service**, starts on boot:
  `sudo systemctl {status,restart,stop} hyperosci-controller`, logs via
  `journalctl -u hyperosci-controller -f`. Unit file mirrored at
  `deploy/hyperosci-controller.service` in this directory.
- Predecessor `tools/hype_sender.py` is kept as the minimal protocol reference
  (don't run both — they'd fight over UDP :5002 and the slaves' jitter buffers).

## How to get on the board

- **SSH:** `ssh arduino@10.42.0.5` — password `arduino`. Host is `uno-q`, Debian 13 (trixie), **aarch64**, 4 cores / 3.6 GB RAM.
- **ADB:** `adb shell` also works (USB).
- **sudo:** passwordless (`arduino` has NOPASSWD); account password is still `arduino`.
- **Tether addressing (fixed 2026-07-22):** `usb-tether` was pure DHCP off the
  laptop's shared 10.42.0.0/24, whose pool is `10.42.0.10–254` — so the board's
  address wandered (`.128` one day, `.127` the next) and every bookmark rotted.
  It now carries a **static `10.42.0.5` in addition to** the DHCP lease
  (`nmcli c mod usb-tether +ipv4.addresses 10.42.0.5/24`, method still `auto`).
  `.5` is below the pool so it can never collide, and keeping DHCP means the
  board still works when tethered to a different host.
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

## The AP did not start on boot — "no slaves discovered yet…" (fixed 2026-07-22)

**Symptom:** after rebooting board + slaves, the dashboard sat on
`no slaves discovered yet…` forever. Everything else looked healthy.

**Cause:** `hyperosci-ap` had `connection.autoconnect: no`, so NetworkManager
never brought it up at boot. `wlan0` stayed `disconnected` (NM even rotated its
MAC for scanning), nothing held `192.168.50.1`, and no slave could associate.
The controller was blameless and was in fact reporting it correctly the whole
time — `journalctl -u hyperosci-controller` showed
`[net] multicast egress 192.168.50.1: UNAVAILABLE` — but nobody reads the
journal mid-show, and the dashboard's empty-list message looked identical to
"the slaves are switched off".

**Fix, three parts:**

```bash
sudo nmcli c mod hyperosci-ap connection.autoconnect yes \
     connection.autoconnect-priority 10 connection.autoconnect-retries 0
```

`autoconnect-retries 0` means *retry forever* — the AP comes back on its own
after a radio wedge instead of giving up after NM's default 4 tries.

Second, the dashboard now says which of the two it is: `/api/state` carries
`net: {iface, egress}`, and when `egress` is false the slaves panel shows
**"Wi-Fi AP is DOWN"** in red with the `nmcli c up` command, instead of the
ambiguous "no slaves discovered yet…". Slaves are evicted after 5 s of silence,
so this covers a mid-show AP loss as well as a cold boot.

Third, nothing needs restarting to recover: `bind_egress()` (the §1.5 fix)
re-applies `IP_MULTICAST_IF` every 500 ms beacon. Observed live — the daemon had
been running 90 minutes with the AP down and bound itself the moment the AP
appeared.

**Verified by an actual reboot**, no manual steps: service start 18:55:28 →
`egress bound` 18:55:40 → `[discovered] slave id=121` 18:55:46. **18 s cold boot
to streaming.** The 7 `UNAVAILABLE` lines in between are the expected
boot-order window, and they stop on their own.

## Unattended operation: pattern persistence + netwatch (2026-07-22)

Goal: the rig comes back from a power cut **on its own**, with no laptop and
nothing to SSH into.

**The live pattern now persists** to `~/hype_state.json`. It is restored in
`State.__init__` *before* the text table is built, so the first 5 ms block is
already correct — no flash of a default circle on stage. `--pattern` now only
decides the very first boot, before a state file exists (and it can finally
name `text`).

- Written by `persist_loop`, a 3 s debounced writer on **its own thread**:
  `os.replace` on the eMMC can block for tens of ms, which on the stream
  thread would be several missed blocks. Measured: **120 POSTs → 4 disk
  writes**, so dragging a slider does not chew the flash.
- Every field goes through the same `clean_preset()` as a preset, so a
  truncated, hand-edited or older-build file can never stop the daemon
  booting. Verified against `{ truncated`, `[]`, `null`, `''`, an unknown font
  name and `{"freq":1e9,"amp":-5,"a":999}` — all boot clamped and sane.
- **`stream_on` is deliberately NOT persisted.** The failure modes are not
  symmetric: a rig that boots silent after a power blip, because someone muted
  it during setup, is far worse than one that boots drawing when you wanted
  quiet.

**`hyperosci-netwatch`** (`deploy/`, installed at `/usr/local/sbin/`, oneshot
service + 30 s timer, runs as root because there is no one to answer a polkit
prompt) covers the two faults that take the rig off the air and that you
cannot fix from the phone, because both break the phone's own path in:

1. `192.168.50.1` is not on `wlan0` → `nmcli c up hyperosci-ap`.
2. The documented ath10k rate-control wedge → `nmcli c down/up`, after 3
   consecutive bad checks, with a 300 s cooldown.

It is a separate process on purpose: the controller paces 48 kHz audio and
must never fork.

**The subtle part — why "all slaves on mic" is NOT sufficient to act on.**
Measured on 2026-07-22: with ~6.5 % air loss the slave's `source` field
flickers to 0 **~50 times in 4 minutes** while the audio is completely fine
(`under` did not move once in 30 minutes; jitter buffer sawtooths 450 ms →
140 ms and refills, never empties). Striking on `source` alone would have
bounced a healthy AP mid-show. So a strike also requires the slave's **`rx`
counter to have stopped advancing** (< 200 packets in a ~30 s tick against
~5700 healthy) — that is the actual wedge signature and it does not flicker.
A *negative* delta means the slave rebooted and zeroed its counter, which is
exactly when it must be left alone. 13 unit tests in
`scratchpad/test_netwatch.py`; live soak = 240 s, 50 flickers, **0 strikes**.

**Presets now keep one generation of undo.** `save_presets()` copies the
current file to `~/hype_presets.json.bak` before every write. Every path into
that function is destructive — a delete, or anything that empties the
in-memory list, overwrites the file with `[]` and leaves nothing to recover
from. Restore with:

```bash
cp ~/hype_presets.json.bak ~/hype_presets.json
sudo systemctl restart hyperosci-controller
```

One generation covers a single bad write, **not** a run of deletes: delete
five presets one at a time and the backup holds only the fourth. The `.bak` is
never refreshed from an empty or `[]` file, so a clobber cannot shadow a good
backup.

**2.4 GHz channel moved 6 → 11.** A scan found **7 other APs sitting on
channel 6** (Odido 77, Cookies&Cream 69, Dragonsreach 64, Ziggo8922244 64,
Casa JoSe 60, TMNL-9AF9D1 57, Ziggo9878968 55) against 3 on channel 11.
Measured better but not dramatically: loss 14.6 → 13.0 pkt/s. RSSI read 9 dB
lower on 11, so re-scan and re-pick **at the venue** rather than trusting this:

```bash
sudo nmcli -f SSID,CHAN,SIGNAL d wifi list --rescan yes   # look at CHAN
sudo nmcli c mod hyperosci-ap 802-11-wireless.channel 6   # or 1 / 11
sudo nmcli c down hyperosci-ap && sudo nmcli c up hyperosci-ap
```

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

Implemented the "small cheap" tier of [performance-heat-analysis.md](../../docs/performance-heat-analysis.md) (§7 items 1-5;
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

**The controller restart that used to follow is not needed** (re-verified
2026-07-31, bounced with the daemon left running). `bind_egress()` handles it:
`[net] sync send failed (Errno 101); recreating socket` → `egress UNAVAILABLE`
→ `bound` 4 s later → slave rediscovered 2 s after that, no intervention. The
line is kept out of the ladder because restarting costs a rebuffer on every
scope for nothing.

**Give it time before you judge it.** Measured 60 s after a bounce the rig
looks *worse* than before — 72% ENOBUFS, slave rx 15/s, buffer at 0 — because
the station is still reassociating and rate control has not ramped. Measured
again at ~5 min: 0% ENOBUFS, rx 210/s, buffer full at 450 ms, `source=network`
in 100% of samples. Do not stack a second remedy on top of the first inside
that window; `hyperosci-netwatch`'s 300 s cooldown (§ above) exists for the
same reason.

**Show runbook:** if all scopes fall back to mic and `rx/s` is ~0 while WiFi shows
connected — bounce the AP and wait. ~5 min to full recovery, most of it ramp.
(SSH via USB tether survives the bounce; the AP bounce is invisible to nothing
except the slaves, which rejoin in ~5 s.)

### Residual (measured tonight, pre-existing): silent burst loss

With everything healthy: rx ≈ 155-175 pkt/s of 200 sent, `drop=0`, `underrun=0` —
i.e. **15-20% of packets are lost below the app layer** (ath10k deaf-stall flush
microbursts overflowing lwIP's 6-slot UDP recvmbox, analysis §4.1), forcing a
jb rebuffer (~1-2 s of mic) every ~10 s. This is the measurement §7 Tier 3 was
gated on → **AsyncUDP audio RX is now warranted** for a W2 work item. RF was worse
tonight than during yesterday's clean 44 s soak; re-check loss rate in daytime too.

## AsyncUDP audio RX landed — silent loss fixed & now measurable (2026-07-19, night)

Commits: `db67afe` (slave: AsyncUDP audio RX + `lost_packets` on the wire, STATUS
55→59 B, protocol.md updated), `420882c` (controller: parse + dashboard `lost` /
`lost/s`). Deployed to the UNO-Q and flashed on the bench slave.

What changed: the audio socket no longer goes through WiFiUDP's BSD-socket path
(6-slot lwIP recvmbox, the silent-drop bottleneck) — a raw lwIP callback feeds
AsyncUDP's 32-deep queue with tcpip backpressure, and the handler pushes straight
into the jitter buffer from the `async_udp` task (bumped to prio 8). New
`lost_packets` counter (seq-gap inferred, SYNC_PULSE-aware) finally counts loss
the slave never saw arrive; on serial `stat` as `lost=` and on the dashboard.

Verified on hardware (same bad-RF night as the wedge writeup):
- **rx/s 190–198 of 200** (was 155–175); rx+lost ≈ 200 — full accounting.
- 60 s soak: `source=1` throughout — **the ~10 s mic-dip flap is gone**;
  drop/s=0, under/s=0, depth pinned at 450 ms.
- Ping-flood stress (14 s, ~200×1 kB/s extra): no flap, no underruns, depth
  dipped to 300 ms and recovered. Pre-fix this burst profile forced rebuffers.
- WiFi bounce (`wifi off`/`on` on console): stream back in <5 s (AsyncUDP
  re-listen + IGMP re-join in `on_wifi_up`).
- Residual lost/s ≈ 3–10 (~2.5%) in tonight's RF is **upstream** (air/AP-side),
  arrives as 1–2-packet gaps, and is concealed by hold-fill without rebuffering.
  Re-measure in daytime RF; watch `lost/s` on the dashboard at the venue.

Rollout note: STATUS length changed 55→59 B, both directions compatible — the
3 unflashed slaves show `lost –` on the dashboard until reflashed; flash them
before the show so per-slave RF loss is visible from FOH.

## Text rendering landed — Hershey fonts + pulse/spin effects (2026-07-18)

Outcome of the osci-render investigation (docs/text-rendering-findings.md): wrapping
osci-render headlessly is not realistic (no headless entry point); instead its
algorithm (glyph strokes → normalized path → constant arc-length traversal →
LFO-modulated point transforms) is reimplemented in `hype_controller.py` as
pattern `kind="text"`.

- **Fonts:** Hershey single-stroke vector fonts, `apt install hershey-fonts-data`
  (installed on the board, `/usr/share/hershey-fonts/*.jhf`). Six faces exposed:
  simplex, duplex, script, gothic, times, italic. `python3-freetype` also
  installed for a future arbitrary-TTF outline route.
- **Controls (dashboard + `/api/pattern`):** `text` (`|` = newline, printable
  ASCII, ≤80 chars), `font`, `pulse_depth`/`pulse_rate` (amplitude LFO,
  osci-render's Scale+sine-LFO equivalent), `rot` (rev/s spin), plus the existing
  freq (redraws/s — 40–80 looks best) and amp. Canvas preview via
  `GET /api/textpreview` (refetched when `pattern.tver` bumps).
- **Hot path:** text/font changes rebuild a 2000-point equal-arc-length table
  outside the state lock; the per-block loop is a table walk + rotate/scale
  (124 µs vs 94 µs for circle per 5 ms block on the dev box). Verified on
  hardware 2026-07-18 night: 30 s soak while streaming pulsing text — rx/s
  187–201, drop/s 0, under/s 0, source locked, zero re-anchors/tracebacks
  through live text/font rebuilds.
- No protocol or firmware changes — slaves just play the XY PCM.
- **Added 2026-07-19:** flip ⇋X/⇵Y mirror toggles (scope deflection polarity —
  bench scope needed flip_y), true multi-line text (textarea, Enter = new line),
  and **presets**: up to 20 named snapshots of the whole streamed-pattern panel
  (`POST /api/preset` op=save|load|delete), persisted at
  `/home/arduino/hype_presets.json` across restarts — made for pre-saving
  artist names and switching in one tap between stage acts.
