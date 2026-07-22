# controller regression checks

Run **on the board** — the dev box has no Hershey fonts and a different Python,
so it is not a valid reference for either behaviour or timing.

```bash
adb push tests/*.py /tmp/ && adb shell "mkdir -p /tmp/hcstage && \
  cp /home/arduino/hype_controller.py /tmp/hcstage/ && cp /tmp/test_fixes.py /tmp/hcstage/"
adb shell "cd /tmp/hcstage && python3 test_fixes.py"   # 6 bugs + the 8 show names
adb shell "cd /tmp && python3 test_persist.py"          # ~/hype_state.json round-trip + garbage
adb shell "cp /usr/local/sbin/hyperosci-netwatch /tmp/nw_mod.py && \
           cd /tmp && python3 test_netwatch.py"         # watchdog trigger logic
adb shell "cd /tmp && python3 live_test.py"             # against the running daemon
```

`test_fixes.py` and `test_persist.py` point `HYPE_STATE`/`HYPE_PRESETS` at a
temp dir **before importing** the controller. Keep it that way: `State()`
restores the saved live pattern, so without the override a test inherits
whatever is currently on the scopes (an assertion on a default field then
fails for the wrong reason) and any test that saves would write the show's
real preset file.
