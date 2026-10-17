# controller regression checks

Run **on the board** — the dev box has no Hershey fonts and a different Python,
so it is not a valid reference for either behaviour or timing.

```bash
adb push tests/*.py /tmp/ && adb shell "mkdir -p /tmp/hcstage && \
  cp /home/arduino/hype_controller.py /tmp/hcstage/ && \
  cp /tmp/test_fixes.py /tmp/test_policy.py /tmp/test_loop.py /tmp/hcstage/"
adb shell "cd /tmp/hcstage && python3 test_fixes.py"   # 6 bugs + the 8 show names
adb shell "cd /tmp && python3 test_persist.py"          # ~/hype_state.json round-trip + garbage
adb shell "cd /tmp/hcstage && python3 test_policy.py"   # node switches + the pair cycles
adb push src/esp32-slave/include/protocol.h /tmp/protocol.h
adb shell "cd /tmp/hcstage && HYPE_PROTOCOL_H=/tmp/protocol.h python3 test_loop.py"  # Phase 2 loop-fill field + settle window
adb shell "cp /usr/local/sbin/hyperosci-netwatch /tmp/nw_mod.py && \
           cd /tmp && python3 test_netwatch.py"         # watchdog trigger logic
adb shell "cd /tmp && python3 live_test.py"             # against the running daemon
adb shell "cd /tmp/hcstage && python3 test_figure.py"   # kind=figure loader + the show's artwork
```

`test_fixes.py`, `test_persist.py` and `test_policy.py` point
`HYPE_STATE`/`HYPE_PRESETS`/`HYPE_CYCLES` at a temp dir **before importing**
the controller. Keep it that way: `State()` restores the saved live pattern,
so without the override a test inherits whatever is currently on the scopes
(an assertion on a default field then fails for the wrong reason) and any
test that saves would write the show's real preset file.

`test_figure.py` overrides `HYPE_FIGURES` too, then re-points `hc.FIGURE_DIR`
at the real `~/hype_figures` for a final read-only pass over whatever artwork
is actually baked onto this board.

`test_policy.py` drives `desired_modes`/`advance_cycle`/`end_burst` directly
with a stub `CmdSender` and fabricated slaves, and binds its HTTP section to
port 0 — nothing on :5001/:5002 — so unlike `live_test.py` it is safe to run
while the daemon is serving a show.

`test_loop.py` checks the Phase 2 loop-fill decisions (the `loop_frames`
field, the settle window) plus one invariant that reaches into the slave:
it parses `HYPE_LOOP_FILL_MAX_MS` from `protocol.h` and asserts the
controller's `LOOP_FILL_CEIL_MS` stays >= it. The board has no repo, so
push `protocol.h` alongside first (above); if it is missing the invariant
FAILS rather than skips. Its last check reads the show's real set list,
`~/hype_presets.json` (override with `HYPE_SHOW_PRESETS`) and asserts every
preset keeps a usable loop; without the file that check is skipped.
