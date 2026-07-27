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
3. **Web UI** — single embedded page (stdlib `http.server`, port 8080,
   no external assets so it works on the AP without internet):
   <http://192.168.50.1:8080> (on `HYPEROSCI_AP`) or <http://10.42.0.5:8080>
   (USB tether). Pattern/text/effect controls with live canvas preview,
   stream on/off, per-slave draw mode (stream/hybrid/mic/local patterns),
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

HTTP API: `GET /api/state`, `GET /api/textpreview`; `POST /api/pattern`,
`POST /api/cmd` (per-slave HYPE_CMD), `POST /api/preset` (op=save|load|delete).

`deploy/hyperosci-netwatch` (+ `.service`/`.timer`) is a separate root-owned
watchdog that every 30 s re-activates `hyperosci-ap` if nothing holds
192.168.50.1, and bounces it if no slave has *received a packet* for three
consecutive checks. It is not part of this process on purpose — the stream
loop must never fork. See [STATUS.md](STATUS.md) for why "every slave is on
its mic" alone is not a safe trigger.

`tools/hype_sender.py` is the original minimal streamer, kept only as a
protocol reference — never run both (they fight over port 5002).

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
  not `sleep()` drift; pacing drift > 20 ms re-anchors the epoch once,
  cleanly.
- The embedded web page (`PAGE`) is a **plain** triple-quoted Python string:
  a `\n` typed into its JavaScript becomes a real newline in the served
  source (this broke the dashboard once). Write `\\n` and syntax-check with
  `node --check` on the extracted `<script>` after editing.
