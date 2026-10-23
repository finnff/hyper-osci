# UNO-Q status — board access, controller daemon & change log

_Last verified: 2026-07-22. Scope: the physical Arduino UNO-Q board, the deployed
controller daemon, and the (rejected) osci-render route. The controller app itself is
documented in [README.md](README.md)._

## The deaf-stall, dissected: firmware absence cycle, not RF (2026-08-04)

Question that triggered this: would adding an antenna to the UNO-Q help the
1282→<100 pkt/s link oscillation? Answer below is measured, and it is no —
but the investigation found the stall's exact shape and cleared every host-side
suspect. Method: laptop Wi-Fi card (`wlp61s0`, power save off) joined
HYPEROSCI_AP as a known-good station; `mcast_probe.py` on the board as sender;
a seq-parsing receiver (arrival gaps + periodicity) on the laptop.

**The stall's true shape** (was "~100-300 ms every ~1.44 s" in `config.h`):
**three back-to-back ~103 ms deaf windows, ~1-5 ms on-channel between them,
then ~1.0 s clean — full period ~1.31 s.** ~310 ms of every 1310 ms off-air
= **24% of airtime gone, always**. The brief returns between windows flush a
sliver of the queue, which is why it reads as "100-300 ms" from a slave.

**Every host-side suspect cleared, one variable at a time** (same 400 pkt/s /
988 B probe, pattern byte-identical in each):
- Bluetooth (same WCN3990, shared antenna): `rfkill block` → identical stalls.
- Host/mac80211 scans: `iw event -t` on **both** ends during load → zero
  events. NM logged zero scans in 2 h. Laptop is not scanning either.
- P2P: `p2p_disabled 1` → identical. No NoA/Quiet IEs in beacons.
- HYPEROSCI userspace: controller + netwatch.timer stopped → identical.
- Load-dependence: 50/s ping on idle link → same ~200 ms RTT spikes. Always on.
- `survey dump` counters for other channels do not advance during the stalls,
  and the kernel's ath10k is built with `debug 0 debugfs 0 tracing 0`, so the
  firmware cannot be asked what it is doing. It looks exactly like an
  off-channel scan cycle (3 × ~100 ms dwells) scheduled inside the WCN3990
  firmware (`WLAN.HL.3.3.7.c2-00931`, 2023-10-14), invisible to nl80211.

**The link itself is clean and fast — when the radio is present.** To the
laptop station (AP sees it at −21 dBm): offered 1400 pkt/s for 20 s →
**delivered a steady 1139 pkt/s, zero tx retries, zero tx failed**, backlog
drained in 5 ms. Implied awake-capacity ≈ 1500 pkt/s, stable across every
round. **The 10x capacity oscillation does not exist on a strong-RF client** —
so it is not the UNO-Q's antenna, not co-channel interference at the AP's ear,
and not the firmware absence cycle (which only taxes a fixed 24%). What
oscillates is the **per-station link to the ESP32 slaves** (their RF + rate
control), which is where the 2026-07-18 rate-control-wedge writeup already
pointed.

**SO_SNDBUF, reframed — the 2026-07-31 rejection is about drain capacity, not
buffers.** Same 400 pkt/s probe to the laptop with `--sndbuf 1048576`:
**6000/6000 delivered, zero EAGAIN, max arrival gap 144 ms** (vs 207 ms of
gap and 5% loss with the 213 KB default), backlog drained in 0 ms — well
inside the 450 ms deadline lead. A ~1 MB buffer absorbs a full 310 ms absence
cluster and hands it to a link that can drain it. The 4 MB/10.5 s disaster
happened because the *slave* link's awake-capacity was ≈ the offered rate, so
the buffer could never drain. Rule: sndbuf sized to one absence cluster
(~300 ms × offered rate × ~2.3 truesize) is correct **iff** per-station
awake-capacity comfortably exceeds the offered rate. Fix the slave-side rate
collapse first; then the buffer is the ride-through for the firmware stall.

**Capacity arithmetic under the 24% tax:** N slaves at 200 pkt/s each need
awake-capacity ≥ N×200/0.76 ≈ N×263 pkt/s at the slaves' negotiated rates.
Good-phase slave links (1282 pkt/s saturated) fit 4 slaves; a wedged/bad-phase
link (<100 pkt/s) fits zero. Slave count is gated entirely on why the
slave-station MCS collapses, as the 2026-08-04 night table already showed.

**The cycle is written in 802.11 time units — but is NOT beacon-driven
(tested).** The measured constants are exactly 100 TU (102.4 ms) per window
and 1280 TU (1310.72 ms) per period — cluster spacing measured 1.306-1.310 s.
Hypothesis (research agent): TSF-locked, 3 beacon intervals deaf per 1280 TU.
Tested by setting `beacon_int 50` via `wpa_cli set_network` + AP restart
(verified live at 50 on both ends): stall shape **unchanged** — still
3 x ~103 ms / 1.31 s. So it is a fixed firmware timer whose constants happen
to be TU-quantized; the beacon interval is not a mitigation lever. Also ruled
out on-board: ath10k thermal quiet-mode duty cycling (a 100 ms-period
mechanism that would have fit perfectly — but `cooling_device` `cur_state=0`).
No published ath10k/WCN39xx report matches this signature. One unverified
lead for RF-front-end tuning (not the stall): `qcom,calibration-variant` is
empty and QMI `board_id 0xff` (unprogrammed), so the firmware loads a default
`board-2.bin` entry rather than a per-board antenna calibration — Arduino's
`qcm2290` BDF may or may not carry UNO-Q tuning.

**Hardware answer (datasheet ABX00162-ABX00173 + on-board):** the module is
**WCBN3536A = Qualcomm WCN3980** (driver probes it as `wcn3990 hw1.0`; wlan0
and hci0 share one MAC — one die, one antenna). It is a **shared Wi-Fi/BT PCB
trace antenna with no u.FL/MHF connector and no external-antenna provision**;
attaching one means cutting the trace at the feed and voids the FCC/ISED
modular certification. So the antenna question is closed twice over: not
physically available, and not the limiting factor (−117 dBm noise floor,
zero retries at −21 dBm). Related dead ends, recorded so they stay dead:
5 GHz AP (ch 40/44/48 are legal today) is moot — ESP32-C3 slaves are
2.4 GHz-only; threaded NAPI (upstream WCN3990 patch, +22-25% UDP RX, and this
board's softirqs are ~20x skewed onto CPU0) is not togglable here —
`echo 1 > /sys/class/net/wlan0/threaded` returns EOPNOTSUPP on this kernel
build, so it would need a driver rebuild.

State restored after the experiments: BT unblocked, `p2p_disabled 0`,
controller + netwatch restarted, egress re-bound, laptop disassociated (its
`HYPEROSCI_AP` NM profile kept, `autoconnect no` — reuse it for link tests).

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
  `GET /api/shapepreview` (refetched when `pattern.sver` bumps; renamed from
  `textpreview`/`tver` on 2026-08-21, when figures joined text on that table).
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

## Spin removed from the panel + SVG artwork on the scopes (2026-08-21)

Two changes, both live on the board:

- **Spin is gone from the dashboard.** The slider had been left at −1.0 rev/s
  with the live preset saying 0, which is how the words ended up crooked. The
  row is `display:none` and `disabled`; `rot` survives in the renderer, the
  preset field and `POST /api/pattern`, so re-enabling is one line. All eight
  presets were rewritten to `rot: 0.0` (backup:
  `~/hype_presets.json.prespin-20260821-141535`; the daemon's own `.bak`
  generation was left untouched).
- **`kind="figure"`: baked SVG artwork.** `tools/bake_svg.py` runs off the
  board and writes 2000 int16 XY pairs to `~/hype_figures/<name>.tbl`; the
  daemon unpacks and caches it on (mtime, size). No XML parser on stage —
  the route the feature-port study §5 recommended. The **HYPERWAVES** preset
  is now the wordmark artwork instead of Hershey text.

Measured on the board: block 605 µs figure / 652 µs text (5000 µs budget),
cold `load_figure` 4.45 ms against a 6.17 ms warm `render_text` rebuild, so a
figure switch is cheaper than a font switch and stays under the 20 ms
re-anchor threshold. The show's figure draws L = 13.63 with 0.223 of beam
travel as retrace (0.366 in document order — the baker reorders contours);
Hershey `HYPERWAVES` in duplex measures 17.97 total travel, so the artwork is
*less* beam travel than the text it replaced, i.e. no dimmer.

A preset naming artwork this board does not carry keeps its name, draws a
circle, says so in the dashboard and names it in the boot banner.

**Flux Collective** followed the same route the same day
(`figures/flux-collective.svg`, potrace-traced, 7 contours, retrace 0.294 →
0.221 after reordering, block 606 µs). Both figures peak at 81% full scale
because `rmax` normalisation scales each table to the same drawn radius —
two logos, matched brightness, no per-preset amp fiddling at the desk. The
preset keeps its old `text: "Flux\nCollective"` value even though `kind` is
now `figure`: if the `.tbl` ever goes missing, one field flip puts the words
back rather than a circle.

## Node switches + pair cycles; hybrid and blackout removed (2026-08-21)

**One authority for slave draw modes.** Before this the mode came from four
places — the slave sheet, "set all", a timer hold, the slave's own button —
and the only way to know what a scope would draw was to walk the rig.
`policy_loop` (0.25 s tick, own thread) now derives every mode from three
per-node switches and what is on air:

| | on air | node switch | mode |
|---|---|---|---|
| out of the show | anything | `stream` off | local (own mic) |
| a logo (`kind=figure`) | HYPERWAVES / Flux | `logos` off | local |
| a name (`kind=text`) | any artist preset | `artists` off | local |
| a cycle pause | — | — | local, every node |
| otherwise | — | — | network |

It is level-triggered against the 1 Hz STATUS beacon and re-sends no sooner
than `MODE_RESEND_US` (3 s), so a node that reboots, or whose own button was
pressed, is corrected without anything having to observe the event. Switches
live in `~/hype_nodes.json` keyed by **slave id**, settable while the rig is
powered off — which is when rigging actually happens.

Consequences, all deliberate:

- **`fire_timer` no longer touches modes.** It installs the preset; policy
  reads the hold's targets and spreads the rig. An ident therefore obeys the
  switches — an artist ident does not appear on a scope with `artists` off —
  and a release no longer restores a remembered mode, it re-derives one. The
  post-release cooldown stays, but for the honest reason (two stings back to
  back read as a fault), not the old mode-snapshot one.
- **HYBRID is gone from the UI.** A node found in hybrid (its own button still
  cycles into it) is drawn as `■HYB?` and pulled back.
- **BLACKOUT is gone.** It forced every slave to network and zeroed amp; with
  policy owning modes, the mode half fought the switches, and the amp half is
  the SIZE dial.

**Pair cycles.** Two of them, logos and artists, in `~/hype_cycles.json`:
A for `show_s`, an optional `pause_s` of mic, B, pause, round again. Artists
carries the three b2b line-ups and an index for tonight's. Both on = the logo
pair runs, then the artist pair; the page draws the exact step list the
controller will walk, so the order is never a guess. A cycle freezes (deadline
pushed out, not drifting) while the feed is SILENT or an ident is on air, and
restarts at its A side when the config changes. Both survive a reboot, and the
boot banner names the running cycle and every switched-off node.

Measured on the board: `test_policy.py` (46 checks) plus the existing four
suites all pass; a 5 s/3 s logo cycle stepped 4.9 s / 3.3 s on the wall clock
over five transitions.

**Cycles became periodic bursts; the idents are gone (same day).** The pair
cycles used to own the stream: A, pause, B, pause, round again, forever. What
the show actually wants is the **microphone** as the resting state — every
node on its own audio, which is what the room is there for — interrupted on a
period. A cycle is now *"every N minutes: A for a few seconds, an optional
mic pause, B, then straight back to the mic"*. `every_s` joins the config
(default 300 s, clamped past the burst + 10 s so the mic always gets the
stream back), `burst_steps` replaces `cycle_sequence`, and `state.cycle_mic`
is the single flag `desired_modes` reads.

That made the interval idents redundant — "show this every N minutes" is
exactly what a cycle now is — so the whole subsystem went: `~/hype_timers.json`,
`timer_loop`, `fire_timer`/`end_hold`, the hold cooldown, `POST /api/timer`,
the ident table and its slave-target picker, `test_timers.py`. A second
scheduler for the same job was one machine too many, and the picker let you
build a schedule out of any preset with no relation to the set list.
`end_burst` replaces `end_hold` and is what a hand on the panel calls.

The two cycles cannot both be on air (one stream), so their grids are
phase-shifted apart: `cycle_phases` puts the shorter period at 0 and gives
the other the offset whose closest approach is largest, scored on (smallest
gap, fewest collisions, latest first collision). Logos 10 s every 2 min with
artists 15 s every 1 min lands 34/154/274 against 0/60/120/180 — no
collisions, measured on the board. Periods that share no useful common
multiple cannot always miss; when two come due on the same tick the longer
period wins, and **a slot falling inside another group's burst is skipped,
not queued**. That last part was a real bug caught only by watching the live
daemon for 80 s: the skip logic sat inside the `run is None` branch, so a
logos slot missed at second 30 of an artists burst fired 300 ms after it
released — the queue the phases exist to prevent, reintroduced one branch
deeper. The grid steps regardless of what is on air now, and there is a test
for the mid-burst case, not just the same-tick one.

Cycle default is **5 s a side**, not 30. The offset is printed on the
settings card: a cycle quietly running 34 s off the grid looks like a bug the
first time someone times it with a phone.

## Dashboard: three defects that misreport the stage (2026-10-11)

Found by reading the page against the controller and driving it in headless
Chromium through `tools/uipreview.py`. **Deployed 2026-10-14** (`507436e`;
the previous build and the four `hype_*.json` files are kept in
`~/pre-pr1-20261014-102815/`). `test_policy`, `test_fixes`, `test_persist`
and `test_figure` pass on the board from `/tmp/hcstage`, and `live_test.py`
passes against the deployed daemon. Driven live with one slave (164, its
logos/artists switches turned on for the test):

- A show-screen pick with both cycles on plays at once: Anémi then HyperLili
  for 5 s each, 164 goes to network mode and receives the stream, then back
  to the mic with the panel's `Guild\nNavigator` handed back; the artists'
  next burst is a full period out.
- A settings-card pick (no `play`) moves neither grid; editing ARTIST
  SETTINGS with artists off leaves the logos' countdown where it was;
  switching artists back on restages both.
- A pick while SILENT is kept, plays nothing, and does not fire on unmute.
  A pick with artists off and logos on plays once and leaves the logos grid
  alone. With no cycle on, the pick loads its first name and 164 streams it.
- The dashboard reads MIC with the next burst and the panel line, then the
  burst's group and time left after a NEXT tap; the set list tags the pair on
  air, and "Guild Navigator" reads once.

Not exercised on the board: the AP-down banner (taking the AP down drops
the slave; checked in the `apdown` preview scenario). The show config was
restored afterwards from the backup above.

What changed:

- **NEXT / PREV / a set-list pair did nothing while the artist cycle ran —
  and pushed the names back.** The pick changed the pair and loaded its A
  side onto the panel, but between bursts every node is on its mic, so no
  scope showed it. Worse, the changed pair changed `advance_cycle`'s
  signature, which re-laid the grid: a burst due in 60 s moved to 300 s.
  The signature now covers only what the grid is built from (period and
  burst length of the groups that play, and which groups those are) — not
  the pair, and not a group that is off, so editing ARTIST SETTINGS while
  only the logos run no longer pushes the logos back. A burst whose pair is
  swapped under it still stops. `POST /api/cycle {"play": true}` plays the
  pair as a burst now and re-lays that group's grid from it (next one a full
  period later, the other group keeping its offset); with no cycle running it
  loads the first name instead, as before. The page sends one POST instead
  of two that could land in either order. 18 new checks in `test_policy.py`.
- **ON AIR named the panel's pattern while every scope drew its mic.** The
  `● MIC` indicator was computed and never shown. ON AIR now says MIC / MIC
  pause / FEED SILENT with the next burst, ghosts the canvas, and labels it
  as the panel's pattern; a burst shows its group and time left. The set
  list's ON AIR tag follows the same rule (it used to tag the pair holding
  the panel's preset, and any pair with an empty slot when nothing was
  loaded). An artist preset named after its own words now reads once, not
  "Anémi (Anémi)" on every burst.
- **AP down was only reported in SETUP > Diagnostics.** Now a page-level
  banner in both modes; on the show screen an outage otherwise looked like
  every slave being switched off.

Four smaller page defects found in the same review — the level −/+ sending
absolute gains, the invented battery estimate, the slave sheet freezing on
open, and the header/switch sizing — were fixed in `795e8d6` and backed out
of this batch to keep it to what changes the stage. `git revert a4b8564`
(the commit that backed them out; the branch is merged, not squashed, so
both hashes stay in history) brings them back.

`uipreview.py` caught up with the controller on the way: it read `stream_on`
where the page sends `stream` (SILENT never worked in the mock), its
`/api/cycle` re-armed the countdown on any edit (the bug above, mirrored),
and it gains a `panel` scenario (no cycle running).

## HYPERWAVES × Oracle Bones and the new line-up (2026-10-14)

Live on the board. Everything before it is in `~/pre-lineup-20261014-105048/`
(controller, the four `hype_*.json`, `hype_figures/`).

- **Oracle Bones replaces Flux Collective** as the second logo. The PNG the
  promoter sent went through `magick -threshold 50%` → `potrace -s -t 20` →
  `figures/oracle-bones.svg`, then `bake_svg.py`: 13 contours, L = 17.43
  drawn + 3.80 retrace, retrace 0.179 (0.267 in document order). The preset
  took Flux's slot, freq (70 Hz) and pulse, with `text: "Oracle\nBones"` as
  the fallback. `flux-collective.tbl` stays in `~/hype_figures/`, unused.
- **Artists, in order:** Nikos · trillosta b2b JEANS · Mama Snake b2b
  HyperLili · S]NE. Two of the four are solo sets: the pair's second slot is
  "— none —", which the cycle already played as a single name. The page
  showed it as "Nikos b2b —"; the set list, the NEXT hint and the boot
  banner now leave the empty slot out.
- **`S]NE` needed `[` `]` in preset names.** `sanitize_name` stripped the
  bracket, so the preset and the pair slot both became `SNE`. Brackets are
  inert in the `onclick` strings the whitelist protects. New check in
  `test_policy.py`; it fails on the old sanitizer.
- Fonts, picked from a contact sheet of every face rendered on the board:
  Nikos duplex, trillosta script, JEANS simplex, `Mama\nSnake` simplex,
  S]NE duplex; HyperLili stays italic. Freqs are 55–60 Hz. The panel's own
  pattern starts on Nikos, and the artist cycle is on pair 1. Cycle timings
  and node switches are unchanged.

Checked on the board: `test_fixes` (now renders the new eight names in
every face), `test_policy`, `test_persist` and `test_figure` pass from
`/tmp/hcstage`. With 164's logos/artists switches turned on for the test,
each of the four artist entries and the logo pair was played from the show
screen. 164 switched to the network and showed the names in order, then
went back to the mic. Oracle Bones aired as `figure oracle-bones`, not the
circle fallback. The switches were turned back off afterwards.

**Transmission to one node (164, RSSI −35…−40), same day.** `live_test.py`
passes with the new eight names, then the slave's own status packets were
sampled at 5 Hz:

| preset | rx/s | lost/s | drop | under | tx ok/s, full, err |
|---|---|---|---|---|---|
| HYPERWAVES | 190.2 | 6.0 (3.1%) | 0 | 0 | 198.3, 0, 0 |
| Oracle Bones | 193.4 | 2.8 (1.4%) | 0 | 0 | 198.3, 0, 0 |
| Nikos | 195.4 | 2.2 (1.1%) | 0 | 0 | 198.2, 0, 0 |
| trillosta | 193.4 | 4.7 (2.4%) | 0 | 0 | 198.5, 0, 0 |
| JEANS | 193.2 | 4.8 (2.4%) | 0 | 0 | 198.3, 0, 0 |
| Mama Snake | 194.3 | 3.9 (2.0%) | 0 | 0 | 198.3, 0, 0 |
| HyperLili | 193.1 | 5.1 (2.6%) | 0 | 0 | 198.5, 0, 0 |
| S]NE | 193.5 | 7.1 (3.5%) | 0 | 0 | 198.2, 0, 0 |

The table is from 11 s of steady streaming per preset, played from the
panel. rx + lost ≈ 198 of 200, nothing went stale, the buffer never fell
below 12240 frames, and the UNO-Q's own queue lost nothing. The loss is the
same 1–3.5% for figures and text, which matches the residual
air/AP-side loss noted in the lwIP-socket entry; it arrives as small gaps
that hold-fill conceals. Played as cycle bursts, every name aired in order.
Each burst logged one underrun as the stream started from an empty buffer
(depth 0, start gate), then none.
(Corrected below: that underrun was the post-burst refill, not the start
gate.)

## Clean burst start — stream-concealment plan Phase 1 (2026-10-14)

Plan and reasoning: `docs/stream-concealment-plan.md` §3. Slave 164 is
flashed over USB; 100 and 121 still run the old firmware, which works
unchanged against the new controller.

- **Controller:** `stream_targets()` — while a `set_mode` is unconfirmed,
  the commanded mode decides whether a slave gets audio, not its last 1 Hz
  beacon. A burst's stream starts when the node is told to play and stops
  when it is told to go local. New checks in `test_policy.py`.
- **Slave:** a LOCAL slave discards AUDIO before any accounting
  (`net_rx::set_accept_audio`, which replaced `flush()`), and a join draws
  the mic until its first block is due instead of ~0.42 s of centre dot
  (`g_net_played`). Joins, underruns and stream loss print as `[ev <ms>]`
  lines on the serial console; `stat` shows the last pre-roll.

Bench, 164 on USB serial, bursts played from `/api/cycle`:

| | old controller (4 bursts) | new controller (5 bursts) |
|---|---|---|
| mode change → first block played | 637–762 ms | 671–692 ms |
| underruns | 0 (was 1 per burst) | 1, from a lost `local` command |
| LOCAL depth after a burst | 0 (was 1440–13440) | 0 |

The lost command is in the journal as sent; 164 stayed on the network with
the stream stopped, drained, and showed ~0.55 s of centre dot before the
mic. A shorter resend is the open fix (plan §9). Not yet looked at on a
scope. Deployed `b8afdd63…`; backup in `~/pre-phase1-20261014-142936/`.

## Phase 1b — fast mode resend, forward-only re-anchor (2026-10-14)

After review of Phase 1 (plan §3.7–3.8). Controller deployed (md5
`8d4449da…`, backup `~/pre-phase1b-20261014-125806/`); 164 reflashed.

- **`MODE_RESEND_US` 3 s → 200 ms** (every policy tick), and the slave
  answers each CMD with a STATUS. The 3 s in the node-switch entry above is
  superseded. On the bench every transition went out once.
- **Re-anchor forward only, above `REANCHOR_US` = 100 ms, dropping the
  backlog.** The journal from show night (Aug 21–22) had five re-anchors of
  26–347 ms, each followed within 50 ms by a −20…−23 ms one: every scope reset
  (~0.45 s of centre dot), then the catch-up burst overshot backward. Stalls
  ≤ 100 ms are now caught up with their original deadlines; 4 of those 5
  would not have been seen. `hype_sender.py` has the same rule at half its
  80 ms lead.
- **Slave:** an underrun now shows the mic, not the centre dot (SILENT
  mid-burst checked on the serial log); new `reset` event and `resets=`
  counter; the console no longer blocks when nobody reads the USB port.

Found with the new logging: **every join** to 164 starts late and loses a
piece. The first packet arrives with 262–336 ms of its 450 ms lead left, then
a 90–180 ms run of packets is lost and the slave rebuffers, all within the
pre-roll, so the scope shows the mic throughout and the name comes up ~130 ms
late (first play 575–605 ms after stream-up). No controller re-anchor fired.
The cause is open (plan §9).

## Phase 2 — loop-fill concealment (2026-10-17)

Plan §4. Controller deployed (md5 `8297831b…`, backup
`~/hype_controller.py.pre-phase2`); 164 flashed over USB. 100 and 121 still
run the old firmware: they ignore `loop_frames` and keep the hold, and their
59 B STATUS leaves the dashboard fill line empty.

- **Controller:** each AUDIO packet carries `loop_frames` (the pattern's
  repeat length, e.g. 686 at 70 Hz), or 0 with spin on, with too much pulse
  or rounding drift, and for `L + 150 ms` after any pattern change.
  `/api/state` has `loop_frames`; each slave carries `loop_fills`,
  `hold_fills`, `gap_resets`, `reanchor` and `hist`. The Diagnostics panel
  shows a `fill loop … · hold … · holes …` line per slave.
- **Slave:** holes up to 20 ms are copied from one loop back instead of
  parking the beam. New console commands `droptest`, `fill loop|hold`, and
  `[fill]` lines under `log on`.

Bench, 164 on USB with the cycles off (restored afterwards; `hype_cycles.json`
and `hype_state.json` byte-identical to before):

| check | setup | result |
|---|---|---|
| loop fills | `droptest 20`, `fill loop`, 10 s | 173 loop / 0 hold, 0 resets, 0 underruns |
| A/B | same, `fill hold` | 0 loop / 168 hold |
| settle window | `droptest 2`, load Mama Snake → HyperLili → JEANS | 17 hold fills after each change (≈ 170 ms, `L + F` = 167–168 ms), then loop |
| no-loop patterns | spin 0.5, then pulse 10 Hz / 100 % | field 0; 0 loop / 122 and 110 hold |
| rig | all eight presets, 8 s each, natural loss | field 686–960; 52–77 loop / 0 hold each, 0 resets; loss 3.2–4.8 % |

`since0` in the `[fill]` lines is wall time since the last 0 packet, and
packets arrive in bursts, so a first loop fill can read `since0=0ms`; the run
length of hold fills after a change is the measure.

XIAO capture of 164's RCA X/Y (164 on battery, one USB board at a time),
looking for a parked beam: both channels flat within 150 codes for ≥ 4 ms
while streaming.
- **Before the flash** (`/tmp/p2_before.npz`, 4.5 s of Oracle Bones, hold):
  13 parked runs of 5.0, 10.0 and 15.0 ms, i.e. 1–3 lost packets held,
  with the tolerance at 250.
- **After** (`/tmp/p2_after.npz`, 7.7 s of the artists burst, natural loss):
  none at 150. At 250 there is one 5.2 ms run near centre that doesn't
  repeat at any loop lag. Over the whole 10 s burst 164 counted 71 loop
  fills, 0 hold fills and 1 reset.

Not a controlled A/B: different presets and loss in each window. `fill hold`
vs `fill loop` on the same burst needs the console and the XIAO together.

### One-hour soak, 164 on battery (2026-10-17, 15:20–16:20 CEST)

`src/bench-capture/soak.py` against the normal show cycles: a logos burst
(HYPERWAVES → Oracle Bones, 8 s) and an artists burst (Mama Snake →
HyperLili, 10 s) alternating every minute. 164 alone on the stream (100 and
121 are switched out of both groups). Every burst was recorded on the XIAO:
60 captures, 0 sequence gaps. Data in `/tmp/soak1` (`state.jsonl`,
`summary.json`, `burst_*.npz`).

| | logos (30) | artists (30) | all 60 |
|---|---|---|---|
| loss | 6.17 % (3.9–10.6 per burst) | 4.80 % (3.4–7.5) | 5.41 % (5837 of 107 910) |
| loop fills | 1284 | 1931 | 3215 |
| hold fills | 92 | 26 | 118 (3.5 %) |
| resets | 34 | 9 | 43 |
| underruns | 1 | 0 | 1 |
| re-anchors | 0 | 0 | 0 |

- **Holes by size** (missing packets): 2523 × 1, 554 × 2, 256 × 3–4,
  21 × 5–30, 22 × > 30. The 43 resets are exactly the 21 + 22 holes over
  20 ms.
- **Parked beam on the capture**: 13 runs of 4.0–5.5 ms in 60 bursts (about
  510 s of stream), every one in a burst that also counted hold fills. The
  4.5 s pre-flash capture had 6 at the same threshold.
- **Where the 118 holds come from** `[INFERENCE]`: fills the slave can't
  loop. That is the first few packets after a reset (fewer than one loop
  buffered, about 3–4 packets) and the ~167 ms settle window after the
  mid-burst name change. Logos has about one reset per burst (the join reset
  of the Phase 1b entry) against 0.3 for artists, and 3.5× the holds.
  Telling the two causes apart needs `log on` over USB.
- **The one underrun (15:51:13, Oracle Bones)**: 168 lost (10.6 %), two
  holes over 30 packets. Stream 2.35 s, then about 0.45 s on the mic, then
  the rest of the name. That is a deaf-stall longer than the 450 ms lead.
  The mic is quiet on the bench, so on the scope it was a near-centre dot,
  28 short still runs on the capture.
- **Controller**: no re-anchor or TX-drop lines in the journal; 130
  `set_mode` for 120 transitions.
- **Node**: RSSI −23…−26 dBm; battery 3558 → 3536 mV over the hour.

### 100 and 121 flashed (2026-10-17)

Both over USB, battery switch OFF, from `5037692`. After boot each joined
(100: 192.168.50.202, RSSI −26; 121: 192.168.50.228, RSSI −57 at the bench
spot), `stat` shows the `fill: mode=loop` line and the controller parses the
95 B STATUS (counters 0, no stream yet). vbat on USB read 28 / 10 mV, the
usual USB artefact (below 2000 mV the battery policy ignores it); check
again on battery. 100 dropped off USB once, 10 s after its first plug-in:
seating, not the deep-sleep rule (it read 5008 mV on the re-plug and stayed
up).

### Three-node stress soak (2026-10-17, 19:04–19:35 CEST)

All three slaves on battery with every switch on; XIAO on 121. Both cycles
set to `every_s` 28 (show: 120), which the scheduler phased as artists 0–10 s,
logos 15–23 s: about 5 s of mic between bursts, 4× the show's burst rate.
`soak.py run --minutes 30 --node 121 --out /tmp/soak2`, plus a 7 s
`iw station dump` + ping log on the UNO-Q. Cycles were restored afterwards
(`hype_cycles/nodes/state.json` byte-identical to the backups).

**The AP was wedged when the run started.** That began when 100 and 164
rejoined at 16:58 UTC, on the 120 s show cycles, about 6 min before the soak.
Netwatch bounced `hyperosci-ap` at 17:09:28 UTC, 4.5 min into the soak. 66
captures, 0 sequence gaps, but only every other burst has one: captures run
~17 s and bursts start every ~14 s.

| per node, bursts | wedged, 19 each | after the bounce, 107 each |
|---|---|---|
| loss | 88–95 % (per burst 33–99) | 6.4 / 6.8 / 7.0 % (100 / 121 / 164; per burst 0.2–13) |
| refused at the controller (EAGAIN) | 91 / 37 / 93 % | 3.5 / 3.3 / 3.7 % |
| ping RTT avg (max) | 205 (521) / 686 (1272) / 133 (489) ms | 50 / 47 / 52 (≤ 141) ms |
| underruns | 8 / 18 / 14 | 0 |
| resets | 587 / 2 / 221 | 582 / 591 / 625 |
| loop / hold fills | — | 2871 / 359, 4129 / 436, 2919 / 377 |

- **Three nodes fit in a good phase**: 6.7 % loss across 321 node-bursts,
  about half of it refused by the AP queue before the air, 0 underruns, 0
  re-anchors. The wedged phase matches the 2026-07-18 writeup: station rates
  of 2–6 Mbit/s for 100 and 164, RTT up to 1.3 s.
- **Resets are the visible cost at three nodes**: 5.6 per burst per node,
  against 0.7 for one node. Holes after the bounce (all nodes): 8373 × 1,
  1943 × 2, 775 × 3–4, **1702 × 5–30**, 96 × > 30. So 94.7 % of resets are
  holes of 25–150 ms.
- **What a reset costs on the scope** (121): **~0.4 s of centre dot per
  mid-stream reset**, 12.3 % of stream time per burst (median, max 30 %).
  Corrected in "Review fixes and Phase 3" below. The figure first written
  here (near-centre still runs of 4–8 ms, 39 ms per reset) came from
  `parked_runs`, whose 150-code stillness tolerance is about the capture's
  noise floor, so it caught the edges of each dot and missed the rest. The
  plan's ~0.45 s was right.
- **Netwatch cannot see a wedge on the show cycles.** `audio_is_dead`
  (`deploy/hyperosci-netwatch:120-129`) only counts slaves in NETWORK. A
  30 s tick that lands in mic time returns "not dead", which zeroes the
  strikes and keeps no rx sample. A bounce needs three consecutive ticks
  inside bursts, with the 8–10 s bursts every 60 s. This run's denser cycle
  is what let it fire. On the show cycles the 18:58 CEST (16:58 UTC) wedge
  would have stayed `[INFERENCE]`. Fixed the same evening (next entry). In the old check,
  121 flickering onto the stream also reset the strikes on every in-burst
  tick.
- **Battery readings**: 100 read 5756 mV for the whole run (impossible for a
  LiPo, so its sense is broken and its low-battery cutout can't act). 121 sat
  at 3678 mV. 164 read 3886 → 4000 mV.

### Netwatch: wedge check that works on the show cycles (2026-10-17)

`deploy/hyperosci-netwatch` (deployed, backup `/tmp/hyperosci-netwatch.pre-txcheck`
on the UNO-Q), `tests/test_netwatch.py` 29 checks:

- **A check is now wedge / healthy / no evidence.** No slave on the stream
  (mic time between bursts, muted, controller unreachable, first sample) is
  no evidence: strikes stand and the last in-burst rx sample is kept.
  Before, it counted as healthy and zeroed both.
- **Second signal: the controller's per-slave AP-queue refusals**
  (`/api/state` `net.tx` ok/full). Any slave with ≥ 1000 sends (1500 since
  the second review) since its
  last sample and ≥ 50 % refused strikes, even if another slave is still
  playing and wherever the check lands: the counters only move while
  streaming. Healthy three-node runs: 3.5 % (worst burst ~10 %). The wedge:
  91–93 %.
- **Replay of the stress soak's 1 Hz state at 30 s ticks, every tick phase**:
  first strike on the first wedged tick (98 % refused to 164). The bounce
  would have come ~75 s in, not 4.5 min. One strike just after the real
  bounce (that window still held wedged sends), then none in the 25
  healthy minutes.
- **On the 120 s show cycles**: a burst every ~60 s, so three wedged bursts
  ≈ 2–3 min to the bounce (test "wedge on 120 s cycles").
- **Only slaves that are reporting count** (amended after the second stress
  run). Sends to a slave that has gone quiet are refused as well: 164,
  switched off for a minute at 18:04 UTC, drew two strikes, "AP refused
  68 % / 73 % of sends". A slave whose STATUS is ≥ 5 s old (2 s since the
  review fixes below, 3 s since the second review), or that is marked
  `gone`, now gets a fresh tx sample
  instead of a verdict. In the wedge nobody was ever marked. Without this, a
  node dying on stage would leave strikes standing.

### Second three-node stress soak (2026-10-17, 19:47–20:17 CEST)

Same setup as the first (28 s cycles, all three on battery, XIAO on 121),
new netwatch deployed. `/tmp/soak3`. Cycles restored byte-identical. 164 was
switched off by hand around 20:04. All bursts 20:03:30–20:05:40 are left
out (23 node-bursts). Captures now alternate groups: 19 logos, 21 artists.

| node | bursts | loss | refused at controller | loop / hold fills | resets | underruns |
|---|---|---|---|---|---|---|
| 100 | 118 | 5.96 % (1.7–12.5) | 2.62 % | 3604 / 444 | 513 | 0 |
| 121 | 118 | 6.24 % (3.1–10.5) | 2.41 % | 5043 / 455 | 525 | 0 |
| 164 | 118 | 7.58 % (3.1–12.3) | 2.76 % | 5535 / 585 | 555 | 1 |

- **No wedge in 30 min.** Loss stayed 6.3–6.9 % in every 5-minute window,
  refusals 2.0–3.4 %. No netwatch strikes outside the outage, 0 re-anchors.
  The underrun is one 164 burst at 20:01:49 with a > 30-packet hole.
- **Holes**: 11787 × 1, 2775 × 2, 1104 × 3–4, 1494 × 5–30, 99 × > 30. That
  is 4.5 resets per burst per node, 93.8 % of them 25–150 ms holes. It
  confirms the first run's Phase 3 numbers.
- **On the scope (121)**: ~0.4 s of centre dot per mid-stream reset, 15.9 %
  of stream time per burst (median, max 31 %), corrected as above (first
  written as 31–37 ms per reset from `parked_runs`). 16 + 24 short parked
  runs ≤ 5.9 ms in the 19 logos and 21 artists bursts.
- **Side effect of the outage, worth fixing**: while 164 was gone, 100 and
  121 lost 41–90 % for ~30 s (bursts 20:04:09–20:04:37). The controller
  kept streaming to 164: its `ok` count still rose for ~10 s after it went
  quiet, then 98 % refused, until the 30 s delete. `[INFERENCE]` The AP
  queued those packets for an absent station and the shared airtime and
  queue starved the other two. One node dying on stage would cost every
  other scope ~30 s of heavy loss.

### Controller stops streaming to a silent slave after 3 s (2026-10-17)

`stream_targets(state, now)` leaves out any slave whose last STATUS is
older than `STREAM_STALE_US` = 3 s, whatever its mode or a pending command.
Its next STATUS puts it back. The journal logs `[stream] <ip> paused: no
STATUS for 3 s` and `resumed`; past the 30 s delete the rejoin shows as
`[discovered]` instead. Why 3 s and not 2: STATUS is 1 Hz, and at ~5 % loss
two lost in a row would cut healthy nodes several times a night, each a ~1 s
hole. `test_policy.py` has 4 new cases. Deployed, md5 `4a55584c…`, backup
`~/hype_controller.py.pre-stale`.

**Verification** (`/tmp/soak4`, 20:25–20:37 CEST, 28 s cycles, 42 bursts per
node): 100 was switched off by hand at ~20:28:40 and back on at ~20:29:40.

- `[stream] 192.168.50.202 paused` came 3 s after 100's last STATUS. 2 s
  later it was marked lost, deleted 20:29:10, rediscovered 20:29:43.
- **121 and 164 while 100 was off** (4 bursts each): loss 3.8–7.6 %, AP
  refusals 0.6–1.7 %, 0 underruns. Last time the same event cost them
  41–90 % for ~30 s. With two nodes the refusals fell below the three-node
  4.6–7.9 % before and after.
- **No false cuts**: no `paused` line for 121 or 164 in 12 min. STATUS age
  sampled at 1 Hz never went over 1.02 s (2088 samples, 0 over 1.5 s).
- **Netwatch**: 0 strikes, so the quiet-slave rule held live.
- Whole run, outage bursts out: loss 6.8 / 7.5 / 8.6 %, refusals 4.6–5.0 %
  (100 / 121 / 164), 0 underruns, 0 re-anchors. Refusals ran about twice the
  previous run's 2.4–2.8 % (per-burst median 4.7 vs 2.0 %). `[INFERENCE]`
  This is link variation, not the change: the change adds one set
  comprehension over three slaves per loop pass, and the two-node stretch
  in the middle of the same run was at ~1 %. A longer run would settle it.

### Review fixes and Phase 3 (2026-10-17)

An outside review of the `stream-concealment` work raised eight findings.
Checking finding 2 turned up a bigger, mismeasured cost.

**Each mid-stream reset costs ~0.4 s of centre dot, not ~39 ms.** `soak.py`
now has a `dark` column: stills of ≥ 50 ms inside the stream (25 ms blocks,
X std ≤ 300 codes). On 121:

| run | nodes | captured bursts | dark gaps / resets | dark time per burst, median (max) |
|---|---|---|---|---|
| soak1 (1 h) | 1 | 60 | 1 / 43 | 0 % (6.8 %) |
| first stress soak | 3 | 65 | 205 / 302 | 12.3 % (30.3 %) |
| second stress soak | 3 | 42 | 136 / 197 | 15.9 % (30.9 %) |
| stale-slave check | 3 | 24 | 107 / 167 | 25.9 % (30.5 %) |

- The second stress soak leaves out its outage burst (154 resets, no stream
  to measure).
- Gaps last 375–425 ms. Their count per burst tracks the resets (correlation
  0.83–0.94).
- At 1 ms resolution each gap is the ladder: 20 ms parked on the last point,
  a 10 ms linear ramp, then centre.
- One node's resets were join holes in the pre-roll, which is already on the
  mic, so they were invisible.
- **Why `parked_runs` missed them:** its 150-code stillness tolerance is
  about the capture's noise floor (~150 codes over 1 ms). It caught the edges
  of each dot and missed the rest.

**The findings:**

| # | finding | verdict | done |
|---|---|---|---|
| 1 | an underrun leaves `pushed_since_reset_` saturated, so `push_loop` can copy pre-stall audio | real | `push()` onto an empty buffer zeroes it (`jitter_buffer.h`); host test case 7 fails before, passes after |
| 2 | a flush leaves `g_net_played` set, so the refill wait runs the ladder | real, the gaps above | the audio task watches `net_rx::flush_count()` and pre-rolls on the mic; Phase 3 removes most flushes |
| 3 | `g_join_ms` stale on HYBRID → NETWORK; no STREAM_UP | real, logs only | `set_mode` stamps the join; the played branch logs STREAM_UP |
| 4 | loop marker updated before the drop checks | real, `stat`/log only | updated after accept; the fill log reads this packet's field |
| 5 | `loop_frames_for` rejects non-integer ratios for every kind | can't happen: API and `clean_preset` both `int()` the ratios | none |
| 6 | `stale_now` duplicates the `stream_targets` test | real, tidiness | `stream_stale(s, now)` used by both |
| 7 | `rx_check` lacks the quiet-slave filter | real, show-end only: one healthy slave forces "healthy", and between bursts `tx_seen` clears strikes | `fresh_ips()` filters both checks |
| 8 | `TX_FRESH_MS` 5 s > the 3 s stream cut | real, narrow | 2 s |

- Finding 1 is reachable: `net_rx.cpp` pushes a packet onto an empty buffer
  without the delta check. Packets and blocks are both 240 frames, so an
  underrun always leaves the depth at exactly 0.

**Phase 3 (plan §5):**
- `HYPE_LOOP_FILL_MAX_MS` 20 → 150 ms. That equals the controller's
  `LOOP_FILL_CEIL`, so the controller needs no change.
- Hold stays ≤ 20 ms (`HOLD_FILL_MAX_MS`, `config.h`). A hole over 20 ms
  with no usable loop still flushes.
- The pulse step over a long fill is accepted. At HyperLili it is ≤ 26 %.

**Deployed:**
- Controller md5 `b6605350…`, backup `~/hype_controller.py.pre-review`. All
  six board test files pass.
- Netwatch md5 `6e196613…`, backup `/tmp/hyperosci-netwatch.pre-review`
  (tmpfs). `test_netwatch.py` has 33 checks; the old script fails the new 2.5 s
  case.
- Firmware flashed over USB with the battery switch OFF, in the order 164,
  100, 121.
- When 164 was plugged in, the XIAO's `/dev/serial/by-id` entry disappeared.
  This is more evidence for the open USB-coexistence item.

**Bench, 164 on USB, steady Mama Snake, `droptest 200 30` (one 150 ms hole
per second):**
- Every 30-packet hole was loop-filled (`[fill] loop 7200f 30pkt`).
- 4 resets in 20 s, all from holes over 150 ms (31–33 packets: natural loss
  next to the drop).
- With `fill hold`: 20 resets in 15 s, including a 75 ms hole. This confirms
  the hold cap.
- After every reset, `first-play` came ~445 ms later: the flush clears
  `g_net_played`, so the wait is drawn as pre-roll.

**Three-node soak on the new firmware** (21:53–22:05 CEST, 28 s cycles,
XIAO on 121, all on battery, `/tmp/soak5`):

| node | bursts | loss | refused | resets per burst (before) | loop / hold fills | holes 5–30 / > 30 |
|---|---|---|---|---|---|---|
| 100 | 49 | 5.97 % | 4.33 % | 0.55 (7.69) | 1069 / 44 | 288 / 17 |
| 121 | 50 | 6.17 % | 3.99 % | 0.34 (7.83) | 1410 / 111 | 307 / 9 |
| 164 | 49 | 8.40 % | 4.13 % | 0.65 (7.52) | 2226 / 109 | 309 / 21 |

- "Before" is the stale-slave check run, outage left out. Its loss and hole
  sizes were the same.
- Dark time per burst on 121: median 0 %, max 4.3 %. That is 4 gaps of
  300–325 ms in 20 bursts, now on the mic.
- 0 underruns, 0 re-anchors.
- No `[stream] paused` lines during the soak. The only ones were from the
  flashing.
- 0 netwatch strikes.
- Cycles restored: `hype_cycles/nodes/state.json` are byte-identical to the
  `p3.bak` backups.
- Still open: the phone-video scope checks of Phases 1, 2 and 3.

### Second review of the fixes (2026-10-17)

A code-only recheck of `163ee2c` raised 12 points. Two were fixed:

- **A held dot could become a loop source.** `push_hold` counted its frames
  in `pushed_since_reset_`, so a later loop-fill could copy them for its
  whole hole (≤ 150 ms). The packet after a hold is always pushed real, so at
  least min(loop, 240) frames of each copied loop were real. At a 960-frame
  loop after a 720-frame hold, ~75 % of the copied loop was dot. The review's
  fix (leave hold frames out of the count) would not stop the copy, because
  the frames stay in the ring. `push_hold` now **zeroes** the counter: no fill
  until a full loop of real audio follows a hold. Host test case 8 fails
  before the fix and passes after (8/8).
- **Netwatch 2 s window too tight for a wedge.** A slave outside the window
  gets a fresh tx sample, so two lost STATUS in a wedge (age 2–3 s) threw its
  sends away and strikes could not build. `TX_FRESH_MS` is now 3 s, the
  controller's stream cut. A departed slave is refused for up to 3 s at
  200 pkt/s, which could reach 600/1000 = 60 % against the old `TX_MIN`, so
  `TX_MIN` is now 1500 (≤ 40 %). An 8 s burst (~1600 sends) is still judged
  whole. `test_netwatch.py`: 36 checks pass; the previous script fails.
  Deployed md5 `bbeecd44…`, backup `~/hyperosci-netwatch.pre-3s`.

Not fixed, with reasons:

- **Clearing loop history on every underrun:** frames left after an underrun
  keep the timeline. The next packet takes the normal hole check against the
  old tail, so the ring before it is the same stream. Clearing would block a
  valid fill.
- **Non-integer `a`/`b` disabling loop-fill:** can't happen. `clean_preset`
  (preset load, live load, apply) and `/api/pattern` all make them integers.
- **The two counter races** (a reset during a pop counts as an underrun; a
  mode-change flush during a fill counts as a gap reset): they can only add
  counts, and soak5 had 0 underruns. (Corrected later: the soaks do switch
  modes, ~294 per soak, and soak7 had 0 underruns as well. The races are
  real but rare; see "Third review".)
- **Flushing a 20–150 ms hole with no loop:** 47 of soak5's 76 resets were
  holes over 150 ms. The rest trade ≤ 150 ms of dot for ~0.4 s of mic, the
  choice Phase 3 already made. Revisit after logging when `loop_frames` is 0.
- **`dark_gaps` X-only:** a false gap needs a figure narrower than ~300
  codes in X; none of the show presets is.

Firmware flashed to 100, 121 and 164 over USB, battery switch OFF
(2026-10-17 23:47 – 2026-10-18 00:08 CEST).

**Verification soak** (00:20–00:32 CEST, 28 s cycles, XIAO on 121, all on
battery, `/tmp/soak6`) against soak5 (before the hold fix):

| preset pair | bursts | resets (soak5) | resets > 150 ms hole (soak5) | hold fills (soak5) |
|---|---|---|---|---|
| HYPERWAVES / Oracle Bones | 75 | 104 (59) | 41 (31) | 417 (240) |
| Mama Snake / HyperLili | 75 | 24 (17) | 18 (16) | 63 (24) |

- Per node, resets per burst rose to 0.90 / 0.78 / 0.88 (100 / 121 / 164),
  from 0.55 / 0.34 / 0.65. 121's loss was unchanged (6.42 % vs 6.17 %).
- The extra resets are almost all holes ≤ 150 ms on the logos, where holds
  are frequent: 63 vs 28. `[INFERENCE]` After a hold, loop-fill now waits a
  full loop of real audio, so in that window a 20–150 ms hole resets instead
  of filling, and a ≤ 20 ms hole holds again, which restarts the wait.
- Dark time on 121: 4 gaps in 17 bursts, median 0 %, max 5.8 % (soak5: 4 in
  20, max 4.3 %).
- 0 underruns, 0 re-anchors, 0 netwatch strikes. The only stream cut was
  164's at 00:18:59, when it came off USB before the soak.
- Cycles restored: `cmp` identical to the `v2.bak` backups.
- **Net: the hold fix costs more than it saves.** It removes a partial dot
  of ≤ 20 ms per copied loop for ≤ 150 ms. In exchange, about one hole per
  logos burst becomes a ~0.4 s reset to the mic.
- **Reverted** (2026-10-18): `jitter_buffer.h`, host test case 8 and the
  plan/architecture wording are back to `163ee2c`. The host test passes 7/7,
  and the build is 1025021 B, the same as `163ee2c`. The netwatch change
  stays. Reflashed over USB with the battery switch OFF: 164 at 00:36,
  121 at 00:57, 100 at 01:31 CEST. Each came back with `fill: mode=loop`
  and 0 resets.

**Final soak on the reverted firmware** (01:37–01:49 CEST, 28 s cycles,
XIAO on 121, all on battery, `/tmp/soak7`):

| node | bursts | loss | refused | resets per burst (soak5 / soak6) | loop / hold fills |
|---|---|---|---|---|---|
| 100 | 49 | 7.21 % | 4.61 % | 0.57 (0.55 / 0.90) | 1451 / 81 |
| 121 | 49 | 6.47 % | 4.27 % | 0.27 (0.34 / 0.78) | 1439 / 99 |
| 164 | 49 | 7.80 % | 4.43 % | 0.43 (0.65 / 0.88) | 2151 / 140 |

- Logo resets from holes ≤ 150 ms: 25 (soak5 28, soak6 63). Artists: 3.
  This confirms the hold fix caused soak6's extra resets.
- Dark time on 121: 1 gap (300 ms) in 17 bursts; median 0 %, max 3.9 %.
- 0 underruns, 0 re-anchors, 0 netwatch log lines, no stream cuts.
- Cycles restored: `cmp` identical to the `v3.bak` backups.

### Third review: netwatch strikes split per signal (2026-10-18)

A recheck of `163ee2c..HEAD` plus a simulation of the show schedule at the
real check interval (31–35 s), two slaves wedged at 91 % refused and a third
still playing, as on 2026-10-17. Bursts from wedge start to bounce:

| setup | median | p90 | max |
|---|---|---|---|
| `TX_MIN` 1000, one strike count | 3.1 | 4.6 | 5.4 |
| `TX_MIN` 1500, one strike count | 3.9 | 8.3 | 11.2 |
| `TX_MIN` 1500, separate tx / rx counts | 3.3 | 4.2 | 4.5 |

- **Cause:** the playing third slave makes `rx_check` read healthy on nearly
  every check. With one shared count, any check that landed before a burst
  had `TX_MIN` sends to judge wiped the tx strikes. Raising `TX_MIN` to 1500
  doubled how often that happened (147 → 302 clears in the simulation).
  Sends from a short healthy burst carried into a wedged one also dilute the
  first verdict, but that adds less than one burst.
- **Fix:** `strikes` is now `{"tx": n, "rx": n}`. Each signal clears only on
  its own healthy verdict; a tx window that was not judged leaves tx strikes
  standing. Either count reaching 3 bounces. An old single-int state file
  loads as 0 / 0. With a check interval locked at exactly 30 s, some show
  phases never bounced under either `TX_MIN` (78 of 400); with the split,
  none.
- `test_netwatch.py`: 39 checks pass on the board. New: the wedge-while-
  another-plays schedule (fails on the previous script), the matching
  "a judged healthy window clears it", and rx with a slave silent 2.5 s,
  which counts inside the 3 s window (a switch-off can draw one stray rx
  strike, ~10 % of the time; one strike cannot bounce).
- Comments: a clean departure doesn't strike, but a link degrading while its
  STATUS still arrives can. The 3 s window rides out one lost STATUS and
  usually two (two is ~3.02 s, so jitter decides).
- **Deployed** 2026-10-18 17:26 UTC, md5 `859d7184…`, backup
  `~/hyperosci-netwatch.pre-split`. A manual run exited cleanly and wrote
  `"strikes": {"tx": 0, "rx": 0}`. Controller only, no reflash.
- Firmware points for the next batch (no reflash for these alone): cap a
  hold or loop-fill so the real packet still fits (needs > ~487 ms depth,
  0 drops in soak4–7); read depth and tail deadline under one lock; have
  `pull_block` return IDLE, not UNDERRUN, when `pop` fails because of a
  reset. Copying from 2 loops back to skip a held dot waits on a counter of
  fills that copy held frames.

## Artist order follows the poster (2026-10-23)

- **Artists, in order:** S]NE · Nikos · Mama Snake b2b HyperLili · JEANS b2b
  trillosta (23/02, 02/03, 03/05, 05/07). JEANS now plays before trillosta.
- Set live through the API: `/api/cycle` pairs with `idx` 0 (S]NE, the pair
  it was already on), and `/api/preset` `move` to put the panel's preset list
  in the same order after the two logos. Persisted to `~/hype_cycles.json`
  and the presets file; timings and switches unchanged.
- `CYCLE_SEED` and `tools/uipreview.py` follow. Controller md5 `a4b50e22…`
  copied without a restart (the only change is the seed), backup
  `~/hype_controller.py.pre-order`. Board at `10.42.0.128` on the tether
  this time.
- **Fonts changed:** S]NE duplex → times, Nikos duplex → gothic, Mama Snake
  simplex → duplex, JEANS simplex → italic, trillosta script → gothic;
  HyperLili stays italic. Freqs are unchanged. Set live with load → font →
  save on `/api/preset`, each in a gap between bursts. Backup
  `~/hype_presets.json.pre-fonts-20261023`. Checked against the August prod
  presets (`~/pre-lineup-20261014-105048/hype_presets.json`) with the same
  renderer: Eastern Distributor in gothic at 55 Hz ran fine with ink 20.4,
  beam step 7.8 % of the line height, 138 pen lifts and a line 15 % of the
  screen. Every new pick is inside all four (worst: Mama Snake duplex ink
  18.5, trillosta gothic step 4.5 %, 54 lifts). Inside the Mama Snake →
  HyperLili pair the brightness drops about 2.3× (ink 18.5 → 8.0).

## Debug quick test (2026-10-23)

SETUP > Debug (last section) has a **debug: quick-test** OFF/ON toggle:
the logos' and artists' pair names in turn (logos first, each name once,
empty slots and names with no preset skipped), 2 s each, looping. A forced
broadcast: `desired_modes` puts every node on the stream whatever its
S/L/A switches say, and starting it switches the feed on. A red banner with
STOP sits at the top of both screens while it runs, and ON AIR says
`QUICK TEST · i of n`. For setup and checking the gateways.

- **Additive only.** New `qtest_names` / `advance_qtest` / `start_qtest` /
  `stop_qtest`, `State.qtest`, a `qtest` key in `/api/state`, and
  `POST /api/qtest {"on": bool}`. Existing paths gain early exits only:
  `advance_cycle` holds both grids (like a mute) and drops a pair pick while
  it runs, `persist_loop` does not save a test name as the live pattern, and
  `op=save` is refused with 409. `apply_preset` gains an optional `only_if`
  guard, so a step whose font is still parsing when STOP lands cannot draw
  over the restored pattern.
- **What ends it:** STOP or OFF hands back the panel's own pattern (from
  before any burst it cut). SILENT ends it too, as the kill switch. A preset
  tap or a panel edit ends it, keeping what the operator set, the same as for
  a burst. A pair picked on the show screen ends it, then plays as normal.
  It is not persisted.
- **Tests:** 21 new checks in `test_policy.py`. `test_fixes`, `test_policy`,
  `test_loop`, `test_figure` and `test_persist` pass on the board.
  `uipreview.py` mocks `/api/qtest` and has a `qtest` scenario.
- Deployed and restarted. Controller md5 `a8ceba7c…`, backup
  `~/hype_controller.py.pre-qtest`. Live smoke with no slaves joined: the 8
  names came round in order every ~2.1 s, with the cycles' countdowns held
  (logos 168 s, artists 108 s, unchanged through the test). A save during the
  test got 409. STOP and SILENT both put the panel's Mama Snake / duplex
  back. Node mode switching was not exercised (no slaves on the AP); the
  test suite covers it.

## Fast forward buttons (2026-10-23)

The show screen has a **FAST FORWARD** box above PREV/NEXT with **▶▶ LOGOS
NOW** and **▶▶ ARTISTS NOW**. Each button shows that group's "due in …"
countdown, or "on air" / "cycle off". It is for when a set ends early and
the room should see the names now rather than at the next slot.

- **Page only.** It sends the existing `POST /api/cycle {"group": g,
  "play": true}` without an `idx`, so the **selected pair plays now** and
  stays selected. NEXT is still the way to move on to the next pair. The
  controller then re-lays both grids from that moment: the group pressed is
  next due a full period out, and the other group keeps its offset from it.
  SILENT refuses with the usual toast.
- **Tests:** `test_fixes`, `test_policy`, `test_figure`, `test_loop` and
  `test_persist` pass on the board. Checked in `uipreview.py` (layout, and
  the request body has no `idx`).
- Deployed and restarted. Controller md5 `6c30108e…`, backup
  `~/hype_controller.py.pre-ff`.
- Live smoke:
  - Before: logos due in 169 s, artists in 109 s.
  - ARTISTS NOW: Mama Snake → HyperLili played at once. Logos then due in
    58 s, artists in 118 s.
  - LOGOS NOW: HYPERWAVES → Oracle Bones played at once. Logos then due in
    118 s, artists in 58 s.
  - Neither press changed the selected pair (artists idx 2, logos idx 0).

## Global pulse switch (2026-10-23)

The show screen has a **PULSE** row under SIZE/SPEED. **AS SET** gives each
preset its own pulse. **ALL OFF** streams every logo and name at a steady
size, as JEANS (depth 0) already ran.

- **Where it acts:** only in `State.pattern_params`, which is what the
  streamer reads each block. It passes `pulse_depth` 0 while `fx_pulse` is
  False. Presets, the panel, saves, `~/hype_state.json` and the cycles never
  see it, so AS SET gives each name its own pulse back. Spin is not touched
  (every preset is at 0 anyway).
- **Interface:** `POST /api/fx {"pulse": bool}` (400 for anything that is
  not a bool). `/api/state` gains `fx_pulse`. The switch is persisted in
  `~/hype_fx.json` (`HYPE_FX`), so a restart mid-show does not bring the
  pulses back. A missing or bad file means pulse on.
- **Tests:** 6 new checks in `test_policy.py`, which now isolates `HYPE_FX`.
  All five suites pass on the board, and none of them wrote the real
  `~/hype_fx.json`. `uipreview.py` mocks `/api/fx`.
- Deployed. Controller md5 `770a943a…`, backup
  `~/hype_controller.py.pre-fx`.
- **Live:** OFF gave `fx_pulse` false while the panel kept its own
  pulse_depth (0.15), and the file read `{"pulse": false}`. It was still off
  after a restart. Switched back on, and left on AS SET.
