# controller regression checks

Run **on the board** — the dev box has no Hershey fonts and a different Python,
so it is not a valid reference for either behaviour or timing.

```bash
adb push tests/*.py /tmp/ && adb shell "mkdir -p /tmp/hcstage && \
  cp /home/arduino/hype_controller.py /tmp/hcstage/ && \
  cp /tmp/test_fixes.py /tmp/test_timers.py /tmp/hcstage/"
adb shell "cd /tmp/hcstage && python3 test_fixes.py"   # 6 bugs + the 8 show names
adb shell "cd /tmp && python3 test_persist.py"          # ~/hype_state.json round-trip + garbage
adb shell "cd /tmp/hcstage && python3 test_timers.py"   # interval-timer rules
adb shell "cp /usr/local/sbin/hyperosci-netwatch /tmp/nw_mod.py && \
           cd /tmp && python3 test_netwatch.py"         # watchdog trigger logic
adb shell "cd /tmp && python3 live_test.py"             # against the running daemon
```

`test_fixes.py`, `test_persist.py` and `test_timers.py` point
`HYPE_STATE`/`HYPE_PRESETS`/`HYPE_TIMERS` at a temp dir **before importing**
the controller. Keep it that way: `State()` restores the saved live pattern,
so without the override a test inherits whatever is currently on the scopes
(an assertion on a default field then fails for the wrong reason) and any
test that saves would write the show's real preset file.

`test_timers.py` drives `fire_timer`/`end_hold` directly with a stub
`CmdSender` and fabricated slaves — no sockets, no HTTP, nothing bound on
:5001/:5002 — so unlike `live_test.py` it is safe to run while the daemon is
serving a show.
