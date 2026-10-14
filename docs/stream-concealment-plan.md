# Stream gaps on the scope: burst-start cleanup and loop-fill concealment

**Status:** Phase 1 implemented and bench-run on 164 (2026-10-14, §3.6); scope check still
open. Phases 2–3 not started (rev 3).
- Measurements: `src/unoq-controller/STATUS.md`, "Transmission to one node (164)".
- Normative wire spec stays `protocol.md` + `protocol.h`; this file says what will change in them
  and why.
- Tracks `PLAN.md` risk 5 (WiFi jitter/loss).

**Phases:**
- **Phase 1 (A)** is built and deployed to 164 and the controller; **Phase 2 (B)** is ready to build.
- **Phase 3** is gated on data that Phase 2 starts collecting.
- The loss diagnosis (§6) runs alongside and blocks nothing.
- Unresolved questions are in §9.

## 1. What the audience sees

Two artefacts, with different causes and different fixes:

| | When | Cause | Visible as |
|---|---|---|---|
| **A. Burst-start centre dot** | every logo/name burst, deterministic | **pre-roll**: the slave joins a stream whose blocks are due 450 ms after they are sent, and the "too early" path runs the concealment ladder | ≤ 20 ms on a stale point, a 10 ms glide, then a **centre dot for ~0.42 s** before the name |
| **B. Mid-figure sparkle** | ~2–7 times/s during play (1–3.5 % packet loss on 164, same for logos and names) | lost packet → 5 ms hole filled with copies of the last point | beam **parks on one spot for 5 ms** (bright dot) and that 5 ms slice of the drawing is skipped: 25–35 % of one redraw at 50–70 Hz |

- **Not the cause:** the buffer running dry. Steady-state underruns were 0 and the controller's
  send queue lost nothing (`txfull`/`txerr` 0).
- **Not a fix:** a larger buffer. B is missing data, not late data.
- **Not the same event:** A and the one underrun per burst.
  - A comes from `pull_block` returning "too early" (`net_rx.cpp:378-381`), which counts no
    underrun.
  - `st_underruns` only moves on a failed `pop` (`net_rx.cpp:390-393`).
  - The underrun is a separate, still-unexplained event (§2.3).

## 2. Today's code paths

### 2.1 Lost packet (B)

`net_rx.cpp:117-138` judges continuity by deadline against the buffer tail:

- **1–20 ms past the tail:** `jb.push_hold(n)` appends `n` copies of the last pushed frame
  (`jitter_buffer.h:67-86`), then appends the packet.
- **More than 20 ms past:** `jb.reset()`, then rebuffer from this packet (60 ms start gate, then
  the pre-roll of §2.2).
  - **Invisible today:** after a reset `jb.started()` is false, so `pull_block` returns at
    `net_rx.cpp:374` before any `pop`, and **no counter moves**.
  - **Also invisible:** controller re-anchors (`hype_controller.py:4020-4024`) leave no trace on
    the slave.
  - "Underruns 0" therefore does **not** mean "no resets".
- **Earlier than the tail:** dropped as duplicate/reordered.

The fill happens at **push** time, ~450 ms ahead of the playhead, so everything a fill could copy
from is still in the ring.

### 2.2 Burst start (A): pre-roll, confirmed

- **The controller runs one continuous feed.** `epoch` is set once
  (`hype_controller.py:3945`, `epoch = start + LEAD_US`) and re-anchored only on > 20 ms drift.
  Every block is due 450 ms after it is rendered.
- **So the delay is built in.** A pattern switched at wall time *t* reaches the scope at
  *t* + 450 ms, however fast the node's mode flips. That is the design, not a defect.
- **What makes it visible** is what the slave draws while it waits:

```mermaid
sequenceDiagram
    participant C as Controller
    participant S as Slave 164
    C->>S: set_mode network (reconcile_modes, :912-938)
    Note over S: NETWORK, no recent audio → stream_active() false → mic
    S->>C: STATUS mode=1 (0–1 s later)
    Note over C: only now in targets (:4009-4010 filters on reported mode)
    C->>S: AUDIO, each block due 450 ms after send
    Note over S: stream_active() true, head too early → fill_conceal (mode_manager.cpp:171-174)
    Note over S: 20 ms hold on stale point → 10 ms ramp → centre dot ≈ 0.42 s
    Note over S: first block due → the name
```

**Evidence:**
- **On 164 today:** `src` reached 1 only 1.3–1.5 s after the pick. That is the 0–1 s STATUS lag
  plus the 450 ms lead. STATUS arrives once a second, so this number is only accurate to ±1 s.
- **In the code:** `pull_block` has only "play / don't play"; `fill_block` conceals whenever
  `stream_active()`.

### 2.3 One underrun per burst: refill (resolved by Phase 1)

- **Observed:** under +1 per burst, near the burst start, with `depth 0` and `src 0`, but only at
  1 Hz resolution.
- **Also observed:** after every burst, 164 reported `mode/src = 0,0` with a **non-zero** `depth`
  (13440, 10800, 6240, 4080, 1440 frames). LOCAL mode is buffering stream audio, because
  `handle_audio_packet` has no mode check.

**Candidate causes** (resolved 2026-10-14, §3.6: it was 1; 2 happens only on a lost command):
1. **Post-burst refill.**
   - `set_mode(LOCAL)` flushes (`mode_manager.cpp:118`), but the controller keeps sending until
     the next STATUS reports mode 0 (`:4009-4010`), and the buffer refills.
   - At the next NETWORK entry, `pull_block` skips the past-deadline blocks, plays the last one,
     then fails a `pop`.
   - This may not happen at all: if a new packet arrives first, the > 20 ms rule resets the
     buffer (`net_rx.cpp:131-134`).
2. **Burst-end drain.** The buffer empties while the slave is still in NETWORK, as the stream to
   it stops, and the next `pop` fails.

Phase 1 adds the logging that told these apart (§3.6).

## 3. Phase 1: clean burst start

### 3.1 Slave: a LOCAL slave never buffers stream audio

- **New gate.** `net_rx::set_accept_audio(bool)`.
  - `mode_manager::set_mode` calls it **before** the mode flips. Closing the gate also flushes
    (it replaced `net_rx::flush()`), in that order, so no packet can land after the flush.
  - On re-accept, also `jb.reset()` and clear `have_seq`. Otherwise the seq jump across a LOCAL
    stint under 5 s (< 1000 packets) counts as loss.
- **Initialise it in `init()` too.** `init()` sets `g_mode` directly (`mode_manager.cpp:84`).
  Boot-local keeps the radio off, so this is harmless today.
- **`handle_audio_packet`:** return right after the type check when not accepting, before the
  seq accounting and before the stats/liveness update. The rx/lost counters and
  `stream_active()` then describe only audio the slave would play.
- **Threading:** the flag is a `volatile bool`. It is written by the net task (CMD) or the loop
  task (console/button) and read by `async_udp`.
- **Status:** removed the per-burst underrun on the bench (§3.6).

### 3.2 Slave: pre-roll draws the local render, not the ladder

- **The difference to detect:** a fresh join (head far in the future) vs a mid-stream hiccup
  (head within a few ms). `pull_block`'s single bool cannot tell them apart; a state flag in
  `mode_manager` can.
- **`g_net_played`:**
  - set when `pull_block` succeeds;
  - cleared when the mode **actually changes** (`m != g_mode` in `set_mode`, not every call);
  - also cleared whenever `fill_block` sees `!stream_active()`. A Wi-Fi reconnect, unmuting after
    SILENT, or a controller restart (all > 1 s without audio) then gets a clean entry too.
- **`fill_block` NETWORK:**
  - `fill_conceal` only when `g_net_played`;
  - otherwise `renderer_local::render`, the same as the stream-lost branch.
  - The mic keeps drawing until the name's first block is due: a clean mic → name cut.
- **Scope: joins only.** A mid-name large-gap reset keeps today's ladder; Phase 2's counters
  show how often that happens.
- **Dashboard:** `active_source()` already reports 0 during this period.

### 3.3 Controller: stream targets follow the commanded mode

- **Change:** extract `stream_targets(state)` from the loop at `:4009`. If a `mode_sent` command
  is pending, its mode wins over the last reported mode. `reconcile_modes` already pops the entry
  once the report agrees (`:926`).
- **Start:** the stream starts when the network command is sent, saving the 0–1 s STATUS lag.
- **End:** the stream stops when local is commanded.
  - The command and the audio share the AP's queue to that slave, so audio sent before the
    command arrives first and is then flushed.
  - No post-burst tail, even for unflashed slaves.
- **If a command is lost** (seen 1 in ~19 on the bench, §3.6):
  - *lost `network`*: no audio is accepted (gate closed); the 3 s resend starts the burst late.
  - *lost `local`*: the slave stays NETWORK while the stream to it has stopped. Its buffer
    drains (one underrun), the ladder shows ~0.55 s of centre dot, then the mic after
    `STREAM_TIMEOUT_MS`. Before 3.3 the same loss kept streaming the last pattern for up to 3 s.
    Fix candidates in §9.

### 3.4 On-air timing (consequence, decision deferred)

After 3.1–3.3, names air 450 ms after their step starts, because of the lead. What they lose
depends on what follows:

| sequence | first name | second name |
|---|---|---|
| A → mic pause | t+0.45 … t+5 (4.55 s) | — |
| A → B back to back (one stream) | t+0.45 … t+5.45 (full 5 s) | t+5.45 … t+10 (4.55 s) |

- **Only the step that ends on a flip to LOCAL loses 0.45 s:** the flip flushes the buffered
  tail.
- **Making it exact (optional):** switch the pattern `LEAD_US` before the step starts, and flip
  to LOCAL `LEAD_US` after it ends while that node's stream holds the step's pattern. That lives
  in the burst scheduler. It is out of Phase 1 unless 4.55 s reads as wrong on stage.

### 3.5 Phase 1 bench acceptance

- **Instrumentation** (stays in; it is how §2.3 got pinned):
  - the slave records mode changes, stream-up, first block played and each underrun /
    stream loss, with the ms since the mode change, in a 16-entry event ring;
  - the loop task prints them as `[ev <ms>] …` lines, **always** (a handful per burst; no
    `log on` needed), and `stat` shows the last pre-roll time.
  - Printing happens off the audio task, so I2S timing is untouched.
- **Flash 164, 10 bursts each of names and logos:**

| check | today | expected after |
|---|---|---|
| scope (phone video) at burst start | stale point → glide → centre dot ~0.42 s → name | mic → name, no centre dot |
| pre-roll time (serial) | not logged | ≈ 450–500 ms from mode entry |
| `depth` in LOCAL after a burst | 1440–13440 frames | 0 |
| underruns per burst (serial log) | 1 | the log says which §2.3 candidate it was; expect 0 if it was the refill |
| lost/s, drop/s in steady play | 1–3.5 %, 0 | unchanged |

- **`test_policy.py`:** `stream_targets` for pending-network, pending-local, settled and
  unknown-slave cases.

### 3.6 Bench result (2026-10-14, 164 on USB serial)

`/tmp/burst_probe.py`: turn 164's logos/artists on, play the artists pair 2 (trillosta → JEANS,
10 s) and the logo pair (8 s) from the API in turn, read the slave's `[ev]` lines, restore.

| | new slave, old controller (4 bursts) | new slave + new controller (5 bursts) |
|---|---|---|
| mode change → stream-up | 181–186 ms | 85–111 ms |
| stream-up → first block played | 456–576 ms | 575–596 ms |
| mode change → first block played | 637–762 ms | 671–692 ms |
| underruns | 0 | 1 — burst-end drain after a lost `local` command (journal shows it sent) |
| `depth` in LOCAL at the end | 0 | 0 |
| `drop` | 0 | 0 |

- **The per-burst underrun is gone** with only the slave gate in place: it was the post-burst
  refill (§2.3 candidate 1).
- **Stream-up → first play is ~580 ms, not the 450 ms lead.** Cause not pinned; [INFERENCE] a
  ~130 ms error in the slave's clock offset or the controller running ahead of its epoch.
- **Scope not checked yet.** The mic-not-dot pre-roll follows from the code path
  (`g_net_played` false → `renderer_local::render`); the phone-video row of §3.5 is still open.
- Pending command → stream: `mode change → stream-up` halved; the remaining ~100 ms is
  between the slave's mode change and its first accepted audio, cause not pinned.

## 4. Phase 2: loop-fill concealment

Every pattern the controller streams repeats exactly at 1/`freq`:

- **Text and figures** walk the table at `freq` redraws/s (`hype_controller.py:1325`).
- **Circle, Lissajous and rose** advance `p` by `2π·freq/48000` per sample (`:1358`).
- **Lissajous `a`/`b` and the rose petal count are integers 1–9** on both entry paths: the API at
  `:3611-3613` and `clean_preset` at `:162-163`. So all three repeat every 2π.

The samples a lost packet carried were therefore already sent one loop earlier. The slave fills
the hole by copying from one loop back in its own ring, in place of the parked point.

### 4.1 Wire format

`HypeAudioPayload.reserved` (u16, offset 26) becomes **`loop_frames`**:

- the pattern's repeat length in **whole frames at 48 kHz**;
- **0 = no usable loop** → today's last-value hold.

| freq | loop_frames | rounding |
|---|---|---|
| 50 Hz | 960 | exact |
| 55 Hz | 873 | +0.27 |
| 60 Hz | 800 | exact |
| 70 Hz | 686 | +0.29 |

- **Range:** a u16 covers down to 0.73 Hz; the ring bound (§4.3) is the real limit.
- **Precision:** the weakness of whole frames is at **high** freq, where §4.2 rounding drift
  rules it out.
- **Senders:**
  - Only `hype_controller.py` writes the field.
  - `hype_sender.py:154` and `mcast_probe.py:83` keep sending 0 (hold); they are test tools.
- **Compatibility:** current slaves ignore the field (`protocol.md` §9). No version bump.

| controller | slave | result |
|---|---|---|
| old (sends 0) | new | hold, as today |
| new | old (ignores) | hold, as today |
| new | new | loop-fill |

### 4.2 Controller: what goes in the field

A pure helper (unit-tested in `test_policy.py` style) owns the decision. It is called once per
packet in the stream loop.

Definitions:
- `P = 48000 / freq` (exact loop length);
- `L = round(P)` (what goes on the wire);
- `F` = the controller's fill ceiling in frames (`LOOP_FILL_CEIL`, below).

**Send `L` only when all of these hold; otherwise send 0:**
1. **No spin:** `rot_speed == 0`. Spin breaks periodicity. The slider is disabled, but presets
   and `/api/pattern` can still set it.
2. **Integer ratios** (always true today, kept as a guard).
3. **Ring bound:** `1 ≤ L ≤ LOOP_MAX_FRAMES` = 8192 (≈ 5.9 Hz).
4. **Rounding drift:** a fill copies forward loop by loop, so the rounding error accumulates.
   Require `ceil(F / L) · |L − P| ≤ 4` frames.

   | freq | drift at F = 150 ms (7200 frames) | result |
   |---|---|---|
   | 55 Hz | ≈ 2.5 frames | passes |
   | 70 Hz | ≈ 3.1 frames | passes |
   | 50 / 60 Hz | 0 | passes |
   | 700 Hz (68.57 frames) | ≈ 45 frames, two-thirds of a loop | rejected |

5. **Pulse:** the size change across one loop is `pulse_depth · π · pulse_rate / freq`. Require
   it ≤ 5 %.
   - The sliders allow 10 Hz at 100 % depth, which gives 57 % at 55 Hz.
   - The eight show presets give 0.0–2.6 % (HyperLili 0.25 at 2 Hz / 60 Hz).
   - Over a whole hole the step is `depth · π · rate · (hole + loop) / 48000`. For a 2-packet
     hole that is 4.2 % at HyperLili and ≤ 2.9 % for the other seven. Longer fills are a Phase 3
     input.

**Settle window after any change.**
- **Why the controller must decide:**
  - The slave cannot see pattern changes: the controller never sets SYNC_PULSE (`:4027` sends
    flags 0).
  - The artists mostly share 55–60 Hz, so the loop length often doesn't change either.
- **Rule:** send **0 for `L + F` frames** after any change of the `pattern_params()` tuple
  (`:1231`): kind, freq, amp, a, b, table *identity*, rmax, pulse, rot, flips.
- **Why that long:** a packet arriving after a hole of up to `F` copies from up to `L + F` frames
  before itself. That source must post-date the change.
- **What it covers:** preset loads, cycle steps, SIZE, freq drags, text/font edits and flips.

**One fill limit, set by the controller.**
- `LOOP_FILL_CEIL` = 150 ms from Phase 2 onward, even though Phase 2 slaves fill at most 20 ms.
- **Why:** the controller is a single deploy over the tether, while each slave needs USB. If a
  slave that fills up to *X* ms ever met a controller whose settle window assumed less, a hole
  near a pattern change would be filled with the old pattern.
- **Cost:** a bit more hold time after each change.
- **Invariant:** `LOOP_FILL_CEIL` (controller) ≥ `HYPE_LOOP_FILL_MAX_MS` (`protocol.h`, the slave
  limit, 20 in Phase 2). A controller test parses `protocol.h` and asserts it.
- **If the limit ever goes up:** the controller is deployed first.

**Cost:** one tuple compare plus a few float ops per 5 ms block.

### 4.3 Slave: `JitterBuffer::push_loop(count, loop)`

- **Commit protocol:** the same as `push_hold`. Snapshot epoch/tail under `mux_`, copy, then
  commit only if the epoch is unchanged.
- **Copy forward one frame at a time** from `(tail + CAP − loop + i) % CAP` to `(tail + i) % CAP`.
  - **Index:** `CAP` = 24576 is not a power of two, so `(tail − loop + i) % CAP` underflows
    uint32 to the wrong index.
  - **Overlap:** when the hole is longer than a loop (60 Hz: 800 frames vs a 4-packet hole of
    960), the copy reads frames it wrote earlier in the same call, like LZ77.
    `memcpy`/`memmove` would read stale ring contents.
- **`pushed_since_reset_` is a `JitterBuffer` member:**
  - zeroed inside `reset()`'s critical section;
  - incremented inside the **same** critical section that commits the tail, for `push`,
    `push_hold` and `push_loop`.
  - `reset()` is reachable from another task (`set_mode` → `flush`). A counter held anywhere
    else could race a flush and report a stale count, and the fill would copy invalid data.
- **Validity, checked before choosing loop over hold:**
  - `loop != 0`;
  - `pushed_since_reset_ ≥ loop`: data from before a reset belongs to an older stream;
  - `loop + count ≤ CAP − 1`: the source never overlaps this fill's own write window.
- **After the fill:** `last_l_/last_r_` = the last filled frame, so a later hold continues from the
  right point.
- **Caller:** in `net_rx.cpp:122-126`, use `loop_frames` from the packet *after* the hole. It
  describes the content around the hole, and §4.2 zeroes it whenever the source would be stale.

### 4.4 Observability and bench tooling

**Counters.** Phase 3 needs these, and right now resets are invisible (§2.1).

| counter | counts |
|---|---|
| `loop_fills`, `hold_fills` | fill events, by method |
| `gap_resets` | > limit gaps that reset the buffer |
| `reanchor_gaps` | deadline jumps with **contiguous** seq: a controller re-anchor, nothing lost |
| gap-size histogram | holes of 1, 2, 3–4, 5–30, > 30 packets |

- **Where they show:** in `stat` (`ui.cpp:192`), and appended to STATUS after `lost_packets` as
  nine u32: payload 39 → 75 B, datagram 59 → 95 B.
- **Controller:** parses them length-tolerantly, like `lost`. The dashboard shows a fill line per
  slave, and `/api/state` carries the histogram.

**Console commands** (RAM only):
- **`droptest <every> [run]`:** drop `run` consecutive AUDIO packets every `every`, *before* the
  seq accounting, so they count as real loss. `droptest 0` turns it off.
- **`fill loop|hold`:** force either method, for A/B on the same scope.
- **`log on`:** also prints one line per fill event (method, size, ms since the last pattern
  change seen as a 0 field). §4.5 can then check the settle window without luck.

**Host test (prerequisite: a build path).** The slave has only the `esp32c3-supermini` env and
no test infrastructure.
- **Build:** `src/esp32-slave/test/host/test_jitter_buffer.cpp`, built with one documented `g++`
  command against a small shim for `portMUX_TYPE`/`portENTER_CRITICAL`.
- **The race needs a hook:** the shim's critical-section macro calls a test hook once. A
  single-threaded test can't hit a reset racing a fill any other way.
- **Cases:**
  - hole longer than a loop (the LZ77 overlap);
  - ring wrap with `tail < loop` (the index formula);
  - `pushed_since_reset_ < loop` → hold;
  - reset injected between snapshot and commit → no commit, counter 0;
  - the `loop + count` bound.
- **If the shim proves awkward:** fall back to on-board verification, via `droptest`, the
  counters and the scope. That is the acceptance below either way.

### 4.5 Phase 2 bench acceptance

| check | setup | expected |
|---|---|---|
| dot gone | bench scope, `droptest 20` (5 %), `fill loop` | no dots, no missing slices; `loop_fills` climbs, `hold_fills` ≈ 0 |
| A/B | same, `fill hold` | dots back; `hold_fills` climbs |
| settle window | `droptest 2`, then preset changes | the per-event log shows hold only within `L + F` of each change, loop everywhere else |
| no-loop patterns | `rot ≠ 0`, or pulse 10 Hz / 100 % | field 0, hold only |
| rig | 164, all eight presets, normal loss | same counters; lost/s unchanged (this conceals, it does not cut loss) |

## 5. Phase 3: raise the reset limit (gated)

- **Today:** any gap over 20 ms resets the buffer, followed by ~0.45 s of the ladder for
  mid-stream gaps. Hold-fill made 20 ms the right limit, because a longer parked dot is worse
  than a reset. Loop-fill removes that reason.
- **Not decided yet; it waits for data:**
  - **Is it needed?** It needs the `gap_resets` count and the histogram from Phase 2, collected
    at the **show's node count**.
  - **Why the node count matters:** the one-node numbers here don't carry over. On 2026-08-09,
    with three nodes, the controller lost 1–29 % of packets before they reached the air, in bursts
    timed with the deaf-stall clusters. Holes at the show could be far longer than at one node.
  - **Which limit?** Read it off the histogram.
  - **A deaf window delays packets; it doesn't lose them.** Its length (~103 ms, STATUS
    2026-08-04) is not by itself an argument for any limit.
- **Constraints when it is set:**
  - `HYPE_LOOP_FILL_MAX_MS` ≤ the controller's `LOOP_FILL_CEIL` (§4.2). Raise the controller
    first if it must go above 150 ms.
  - **Pulse step over a long fill:** at HyperLili, a 150 ms fill ends with up to a 26 % size step
    at the steepest point of the pulse. Cap the fill length by pulse rate, or accept that one step
    as better than a reset.
  - **Re-anchors:** with seq contiguous, nothing is missing, so loop-fill **inserts** drawing.
    The next packet resumes out of phase by (drift mod loop). That is one visible jump, much
    better than a 0.45 s reset. `reanchor_gaps` counts them.
- **Acceptance:** `droptest <every> <run>` sized from the histogram's worst bucket → `gap_resets`
  stays 0, and the figure stays intact on the scope.

## 6. Alongside: where is the loss?

This blocks no phase; it decides whether the loss itself can be cut.

- **AP counters can't answer it.**
  - On 2026-10-14, `iw dev wlan0 station dump` (`/sbin/iw`, not on the ssh PATH) showed
    `tx retries 0, tx failed 0` for 164 over 49,166 sent packets, while 164 counted ~2.5 % lost.
  - **[INFERENCE]** ath10k does not populate these counters.
  - `hostapd_cli` is not installed.
- **Air vs ESP32:** use the 2026-08-04 laptop station rig (`wlp61s0`, NM profile `HYPEROSCI_AP`,
  autoconnect off). The laptop runs a seq-numbered receiver for `mcast_probe.py` while the
  controller streams to 164.
  - Loss at the same moments on both → AP/air.
  - Loss on 164 only → the ESP32 receive path.
- **If it's the ESP32:** larger Wi-Fi RX buffers via pioarduino `custom_sdkconfig`. The real risk
  is replacing the prebuilt Arduino libs everything was validated on, more than the effort.

## 7. Considered and not chosen

| Option | Why not |
|---|---|
| Straight line across the hole | A line drawn over 5 ms dwells long, so it is bright. It cuts through the logo, which is no better than the dot. |
| XOR parity (1 per N) | Needs interleaving for back-to-back losses. Needs overwrite-in-place (a late repair is dropped as a duplicate at `net_rx.cpp:127`). Must be a new packet type, or old slaves draw the parity bytes. +1/N airtime on a link the N × 263 pkt/s rule already limits. Its exact repair buys nothing for periodic content. |
| NACK / resend | The request travels the same stalled link. Round trips hit 0.5–1.7 s during the rate-control wedge. |
| Bigger buffer / longer lead | The holes are lost data, not late data. |
| Multicast audio | Measured and rejected (STATUS 2026-07-31): no link-layer retries. |

## 8. Docs and code to update as each phase lands

- **`protocol.h`:**
  - `loop_frames`;
  - the STATUS counters and new size asserts;
  - `HYPE_LOOP_FILL_MAX_MS`.
- **`protocol.md`:**
  - §3.1: `reserved` → `loop_frames`; whole frames, 0 = hold, which senders set it.
  - §3.4: 95 B datagram, still accept ≥ 55; the new fields.
  - §5.2: the gap row chooses loop-fill vs hold.
  - §6: the ladder applies only after a block has played; pre-roll draws the local render.
  - §7.2 diagram.
  - §11 constants.
  - **SYNC_PULSE:** §5.4 says the controller sets it on preset changes, and §10 (`:508`) says to
    set it on packet 0 of every new epoch. The controller does neither. Rewrite both to say it is
    **not** sent, and that a re-anchor must **not** set it: setting it would turn every re-anchor
    back into a reset.
- **`firmware/esp32-architecture.md`:** push-side and pop-side bullets.
- **`ui.cpp` help text:** `droptest`, `fill`, the extra `log on` lines.
- **`src/unoq-controller/STATUS.md`:** one entry per deployed phase, with before/after counters.
- **Flashing:** every slave needs USB. Unflashed slaves keep working (§4.1); only 3.3 helps them.

## 9. Open items

| Item | Status | How it closes |
|---|---|---|
| Cause of the one underrun per burst (§2.3) | **closed**: post-burst refill; fixed by the LOCAL gate (§3.6) | — |
| Lost `local` command → ~0.55 s centre dot (§3.3) | seen 1 in ~19 commands | your call: resend unconfirmed `set_mode` every ~250 ms instead of 3 s (≤ 4 extra packets per transition), or keep streaming to a node until it confirms `local` (old behaviour: shows the last pattern instead of the dot) |
| Stream-up → first play ≈ 580 ms vs 450 ms lead (§3.6) | measured, unexplained | compare `stat` sync offset against a controller-side timestamp |
| Scope check of Phase 1 pre-roll (§3.5) | open | phone video of a burst start |
| Air vs ESP32 loss (§6) | open | laptop comparison |
| STATUS 2026-08-04 "zero tx retries, zero tx failed" | rests on counters ath10k likely leaves at 0; the 1139 pkt/s receiver-side result stands | mark that line "not populated by ath10k", not "clean link", once §6 confirms |
| Phase 3 limit | needs Phase 2 histogram at show node count | §5 |
| Exact on-air timing (§3.4) | deferred | decide after seeing Phase 1 on stage |
| Move off the onboard AP to a router? | **decided 2026-10-14: no.** The show stays on the UNO-Q's onboard AP (`hyperosci-ap`); loss work targets that AP and the ESP32 | closed |
