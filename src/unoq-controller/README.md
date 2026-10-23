# unoq-controller — HYPEROSCI controller app

**Status: implemented and running in production on the UNO-Q** as the systemd
service `hyperosci-controller` (deployed at `/home/arduino/hype_controller.py`).
Board access, build notes and the change log live in [STATUS.md](STATUS.md).

## What exists today

One file does everything: [`tools/hype_controller.py`](tools/hype_controller.py)
(pure Python 3 stdlib — no FastAPI, no numpy, no external deps). It is:

1. **Streamer** — implements [docs/protocol.md](../../docs/protocol.md) /
   [protocol.h](../esp32-slave/include/protocol.h): 988-byte `HYPE_AUDIO`
   packets (240 stereo frames, 48 kHz/16-bit, L=X R=Y) every 5 ms, UDP
   **unicast** to every discovered NETWORK/HYBRID slave on port 5000, deadline
   timestamps run `LEAD_US = 450 ms` ahead (rides the UNO-Q ath10k AP's
   ~300 ms radio stalls against the slave's 512 ms jitter buffer);
   `HYPE_SYNC` multicast beacon every 500 ms and `HYPE_CMD` JSON on 5001;
   1 Hz `HYPE_STATUS` listener + slave discovery on 5002 (accepts ≥ 55-byte
   payloads; parses `local_pattern` and `lost_packets` when present).
2. **Renderer** — `PatternGen`: circle / lissajous / rose, plus **text** in
   Hershey single-stroke vector fonts (6 faces from the `hershey-fonts-data`
   package, multi-line with per-line centering, amplitude-pulse and spin LFOs,
   X/Y mirror for scope deflection polarity). A `.jhf` file holds printable
   ASCII and nothing else, so accented letters are **composed**: NFD-decompose,
   draw the base glyph from the face, add the combining mark from `_MARKS` in
   the face's own units (14 marks — acute through ogonek); anything with no
   base at all folds to ASCII (`ß`→`ss`, curly quotes, dashes). `é` is a real
   `e` under a real acute, not a `?`. Text is prebuilt into a
   2000-point equal-arc-length table (constant beam speed — osci-render's
   algorithm, see
   [docs/text-rendering-findings.md](../../docs/text-rendering-findings.md));
   the 5 ms hot loop only walks the table. osci-render itself was evaluated
   and **not** integrated (no headless entry point — same doc).
   **SVG artwork** rides the same table under `kind="figure"`: a `.tbl` of
   2000 int16 XY pairs, baked off the board by
   [`tools/bake_svg.py`](tools/bake_svg.py) and dropped in `~/hype_figures/`
   (`HYPE_FIGURES`). The daemon only unpacks it — no XML parser on stage, per
   [§5 of the feature-port study](../../docs/osci-render-feature-port-feasibility.md).
   Board cost: 4.5 ms to load and cache a figure against 6.2 ms for a warm
   `render_text` rebuild, and the 5 ms block is 605 µs against text's 652 µs
   (both ~12% of budget — the hot loop cannot tell them apart). The baker
   traces centrelines, refuses a file with live `<text>` nodes instead of
   dropping the words, and reorders contours to cut retrace (the show's
   `hyperwaves` figure: 0.366 → 0.223 of beam travel).
3. **Web UI** — single embedded page (stdlib `http.server`, port 8080,
   no external assets so it works on the AP without internet):
   <http://192.168.50.1:8080> (on `HYPEROSCI_AP`) or <http://10.42.0.5:8080>
   (USB tether). Pattern/text/effect controls with live canvas preview,
   stream on/off, three per-node switches (streaming / logos / artists) that
   the controller turns into each slave's draw mode, the two pair cycles,
   identify/gain/reboot, per-second health rates (rx/drop/lost/under).
   When no slave is listed the panel distinguishes the two causes: if
   `/api/state`'s `net.egress` is false, nothing holds `192.168.50.1`, so the
   AP is down and no slave *can* appear — it says so in red rather than
   "no slaves discovered yet…". See [STATUS.md](STATUS.md).
4. **Persistence** — the live pattern is saved to `~/hype_state.json` (3 s
   debounced, on its own thread, atomic replace) and restored before the text
   table is built, so a reboot comes back drawing what was on the scopes
   rather than a default circle. It is validated by the same `clean_preset()`
   as a preset, so a corrupt file degrades to defaults instead of stopping
   the daemon. `stream_on` is deliberately not persisted — a rig that boots
   silent is worse than one that boots drawing.
5. **Presets** — up to 20 named snapshots of the whole streamed-pattern panel
   (artist names for stage changeovers), persisted at
   `~/hype_presets.json` across restarts. Every field is optional on load and
   filled from `PRESET_DEFAULTS`, so adding a field to a later build cannot
   drop a preset written by an earlier one; values are clamped and the font
   whitelisted in `clean_preset()`, on both the file and the load path.
   `op=save` overwrites a same-named preset in place, so the page offers both
   **`⟳ update "<name>"`** (rewrite the preset you last applied — the button
   names its target so there is no doubt what gets replaced) and
   **`+ save as…`** (a new name). Which preset is "current" is a client-side
   notion kept in `localStorage`; the controller has no session, and a phone
   that locked its screen still comes back able to update the right one.
6. **Pair cycles** — two periodic interruptions, `logos` and `artists`,
   persisted at `~/hype_cycles.json`. A cycle is *"every 5 minutes: name A
   for 5 s, an optional mic pause, name B — then the microphone until the
   next one"*. The rig's resting state is the mic (every node on its own
   audio); a burst borrows the stream and hands it straight back. Both run on
   `policy_loop`'s thread (a font rebuild on the stream thread is an audible
   gap on every scope).

   Five things worth knowing:
   - **There is one streamed pattern for the whole rig**, so the two cycles
     cannot both be on air. Their grids are phase-shifted apart
     (`cycle_phases`): logos every 2 min against artists every 1 min becomes
     34/154/274 against 0/60/120/180 instead of both landing on the minute.
     Periods that share no useful common multiple cannot always miss; when
     two do come due together the longer period wins, and a slot that falls
     inside another group's burst is **skipped, not queued** — two bursts
     back to back read as a fault on stage, not as a show.
   - **A burst obeys the per-node switches** like anything else: an artist
     name does not appear on a scope with `artists` off, and that node sits
     on its mic instead.
   - **Touching the pattern panel cuts a burst short**, keeping what the
     operator just set. Reverting them ten seconds later is worse than a
     cycle missing one burst. The grid is untouched, so the evening does not
     shift.
   - **A burst is never persisted** as the live pattern, and `op=save` is
     refused while one is on air — it would save the logo, not the artist.
   - **Picking a pair is not a config change.** Selecting another pair
     leaves the grid alone; only a running group's period or burst length, or
     a group going on or off, restages it (editing a group that is off does
     not). Picking one *on the show screen* (`"play": true`)
     plays it now as a burst, and that group's next one comes a full period
     later; with no cycle running there is no burst to play — the rig streams
     the panel — so its first name is loaded there instead.
   - **FAST FORWARD** (show screen, its own box above PREV/NEXT): **LOGOS
     NOW** / **ARTISTS NOW** play that group's *selected* pair now — the same
     `"play": true` without an `idx`, so nothing is re-selected (moving on
     to the next pair is NEXT). Each button shows when its group would come
     on its own.
   - **PULSE** (show screen, under SIZE/SPEED): **AS SET** streams each
     preset's own pulse; **ALL OFF** streams every logo and name at a steady
     size. Applied only to what is streamed, so presets and saves keep their
     pulse settings. Persisted in `~/hype_fx.json` (`HYPE_FX`).
7. **Debug quick test** (SETUP > Debug, bottom of the page) — every logo,
   then every name in the two cycles' pairs, 2 s each, looping until switched
   off: a forced broadcast to every node, whatever its switches say, with the
   feed switched on. For setup and for checking each gateway draws. A red
   banner on both screens says it is running (with STOP). The cycles' grid
   is held while it runs, as a mute holds it; SILENT, a preset tap, a panel
   edit or a pair pick ends it, and preset saves are refused during it. Never
   persisted: a restart comes back to the normal show.

HTTP API: `GET /api/state`, `GET /api/shapepreview` (the live point table,
decimated — text and figure both); `POST /api/pattern`,
`POST /api/cmd` (per-slave HYPE_CMD), `POST /api/preset` (op=save|load|delete),
`POST /api/node {"id":121,"logos":false}`,
`POST /api/cycle {"group":"artists","every_s":300,"idx":1,...}` (add
`"play":true` to put the selected pair on the scopes now; the reply's
`played` / `loaded` says which way it went), `POST /api/qtest {"on":true}`
(the debug quick test; `GET /api/state` carries `qtest`: `null` or
`{preset, pos, n, step_s}`), `POST /api/fx {"pulse":false}` (global pulse
switch; `GET /api/state` carries `fx_pulse`).

Per-node switches and the pair cycles (2026-08-21): every node carries
`stream` / `logos` / `artists` in `~/hype_nodes.json` (keyed by slave id, so
they can be set with the rig powered off), and `policy_loop` is the **only**
writer of slave draw modes — it turns those switches plus what is on air into
`network` or `local`, level-triggered against the STATUS beacon, so a node
that reboots or has its own button pressed is pulled back within a beacon.
HYBRID is no longer reachable, and the blackout button and the interval
idents are gone: "show this every N minutes" is now what a cycle *is*, so a
second scheduler for the same job was one machine too many. Both cycles
freeze while the feed is SILENT.

`deploy/hyperosci-netwatch` (+ `.service`/`.timer`) is a separate root-owned
watchdog that every 30 s re-activates `hyperosci-ap` if nothing holds
192.168.50.1, and bounces it if no slave has *received a packet* for three
consecutive checks. It is not part of this process on purpose — the stream
loop must never fork. See [STATUS.md](STATUS.md) for why "every slave is on
its mic" alone is not a safe trigger.

`tools/hype_sender.py` is the original minimal streamer, kept only as a
protocol reference — never run both (they fight over port 5002).

`tools/bake_svg.py` is the offline SVG baker — it runs on the laptop, imports
`hype_controller.normalize_resample` so a baked figure and a rendered word are
normalised by identical arithmetic, and writes the `.tbl` that ships to
`~/hype_figures/`. Sources live in `figures/`.

## Deviations from the original plan (kept deliberately)

- **AP** is a NetworkManager connection `hyperosci-ap` (SSID `HYPEROSCI_AP`,
  2.4 GHz **channel 11**, autoconnect on with unlimited retries, controller at
  **192.168.50.1/24**) — not hostapd+dnsmasq and not 192.168.4.1 (10.42.x and
  .4.x collided with the USB tether; see STATUS.md). Channel was 6 until
  2026-07-22; re-pick it at the venue, STATUS.md has the one-liner.
- **Single-file app**, not the `hyperosci/{streamer,renderer,web}` package
  layout once sketched here — at this size the one file is easier to deploy
  (scp + restart) and to keep in lockstep with the firmware.
- The "laptop streamer stand-in" phase was skipped — the UNO-Q arrived in
  time and bring-up happened directly on it. Any Linux laptop can still run
  `hype_controller.py` unchanged as the show-day fallback controller.

## Design notes (still canonical)

- `protocol.h` is the canonical wire format; the Python side mirrors it with
  `struct` pack strings and must round-trip byte-exact. All fields
  little-endian. Never redefine constants — mirror them.
- Timestamps are the controller's monotonic clock in µs
  (`time.monotonic_ns() // 1000`); `HYPE_AUDIO.timestamp_us` is the playback
  deadline of the packet's first frame.
- Transmission is paced from the audio clock with deadline catch-up bursts,
  not `sleep()` drift. A stall up to `REANCHOR_US` (100 ms) is caught up
  with the original deadlines; a longer one re-anchors the epoch forward
  once and drops the backlog.
- The embedded web page (`PAGE`) is a **plain** triple-quoted Python string:
  a `\n` typed into its JavaScript becomes a real newline in the served
  source (this broke the dashboard once). Write `\\n` and syntax-check with
  `node --check` on the extracted `<script>` after editing.
