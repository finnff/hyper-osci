import os, tempfile, importlib.util
d = tempfile.mkdtemp()
os.environ["HYPE_STATE"] = os.path.join(d, "st.json")
os.environ["HYPE_PRESETS"] = os.path.join(d, "pr.json")
spec = importlib.util.spec_from_file_location("hc", "/tmp/hcstage/hype_controller.py")
hc = importlib.util.module_from_spec(spec); spec.loader.exec_module(hc)
n = 0
def ok(msg):
    global n; n += 1; print("  PASS", msg)

assert hc.load_live() is None; ok("no state file -> None, boots on defaults")
s = hc.State("circle")
assert (s.kind, s.text) == ("circle", "HYPEROSCI"); ok("first boot honours --pattern")

s.kind, s.text, s.font = "text", "Anémi", "gothic"
s.freq, s.rot_speed, s.amp, s.flip_y = 55.0, 0.25, 0.93, True
assert hc.save_live(s.live_snapshot()) is None
s2 = hc.State("circle")   # --pattern must LOSE to the saved file
got = (s2.kind, s2.text, s2.font, s2.freq, s2.rot_speed, s2.amp, s2.flip_y)
assert got == ("text", "Anémi", "gothic", 55.0, 0.25, 0.93, True), got
ok("round-trip: %r %s %g Hz amp %.2f rot %g flip_y=%s"
   % (s2.text, s2.font, s2.freq, s2.amp, s2.rot_speed, s2.flip_y))
assert s2.text_tbl and len(s2.text_tbl) > 100 and s2.text_rmax > 0
ok("table rebuilt at boot: %d pts rmax %.3f (accent survived)"
   % (len(s2.text_tbl), s2.text_rmax))
assert s2.stream_on is True; ok("stream_on always True on boot (deliberate)")

open(os.environ["HYPE_STATE"], "w").write(
    '{"kind":"text","text":"Liso","font":"gothiceng"}')   # font that never existed
s5 = hc.State("circle")
assert s5.font == "simplex" and s5.kind == "text" and s5.text == "Liso"
ok("unknown font in file -> falls back to simplex, keeps the text")

for bad in ['{ truncated', '[]', 'null', '', '{"kind":"nope","font":"../../etc/passwd",'
            '"freq":1e9,"amp":-5,"text":123,"a":999}']:
    open(os.environ["HYPE_STATE"], "w").write(bad)
    s3 = hc.State("circle")
    assert s3.kind in ("circle", "lissajous", "rose", "text")
    assert s3.font in hc.TEXT_FONTS and 1.0 <= s3.freq <= 2000.0
    assert 0.0 <= s3.amp <= 1.0 and 1 <= s3.ratio_a <= 9
    ok("garbage %-30r -> boots %s/%s %g Hz amp %.2f"
       % (bad[:30], s3.kind, s3.font, s3.freq, s3.amp))

os.chmod(d, 0o555)
e = hc.save_live({"kind": "circle"})
assert isinstance(e, OSError); ok("unwritable dir -> returns %s, no raise" % type(e).__name__)
os.chmod(d, 0o755)

open(os.environ["HYPE_STATE"], "w").write('{"kind":"text","text":"Liso","font":"simplex"}')
s4 = hc.State("circle")
assert s4.kind == "text" and s4.text == "Liso" and s4.freq == 100.0
ok("partial file (old build) -> missing fields fall back to defaults")
print("\n%d checks, ALL PASS" % n)
