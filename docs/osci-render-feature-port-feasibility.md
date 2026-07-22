# Findings: porting osci-render features — custom fonts, SVG, and the effect catalogue

*Investigated 2026-07-22, and revised the same day against the first board-side CPython timings
ever taken on this rig — the board was unreachable throughout the study itself. Question: osci-render has ~30 effects, arbitrary system fonts and
SVG import. Which of those can we port into `hype_controller.py`, and which cost more than
they are worth? Companion to [text-rendering-findings.md](text-rendering-findings.md), which
covered the same ground for text only and whose conclusions still hold.*

**Method:** full osci-render source (incl. the `osci_render_core` submodule) read directly;
findings adversarially verified by independent agents that re-read the primary sources.
Corrections from that pass are folded in and contested items are marked. See §11.

**Revised 2026-07-22, second pass — the board is measured now.** The first pass was written
with the board unreachable: every A53 figure in it was a dev-box measurement times an assumed
4–7× scaling factor. The board is up (QRB2210, 4× A53, max 2016 MHz, Python 3.13.5) and **that
scaling model is refuted.** The dev box runs Python 3.14.6, whose tier-2 optimiser constant-folds
work the board actually does — dev-box `math.sin` timed at 7.3 ns/op net of the loop floor,
*below* an empty loop iteration, against 208.7 ns on the board — and the implied factor lands
anywhere between 1.34× and 28.6× depending on which line you pick. Dev-box numbers are dropped
or restated in board terms, never averaged with board ones. Every µs and ms figure below is a
board figure unless marked an estimate.

---

## 0. Bottom line

**All three requests are feasible, and the effect catalogue is much cheaper for us than it is
for osci-render** — because of an architectural accident in our favour (§2). The catalogue is
1549 lines of C++ *total*; individual effects are 3–15 lines of point math (`Scale` is
literally `return input * Point(x,y,z)`).

But the study turned up something more urgent than any feature. **Both boxes below are now
fixed and deployed (2026-07-22) — they are kept in the past tense they were found in, because the diagnosis is the useful part.**

> **The shipping controller has a reachable crash, and it is live on the daemon running right
> now.** Streamed text + spin + amp above `32767/(32000·rmax)` — `rmax` being the largest radius
> in the point table — raises `OverflowError` inside `stream_loop`, which has no exception
> handler: the daemon dies and systemd restarts it 3 s later at CLI defaults, with every live
> setting gone. Re-reproduced **on the board**, against the deployed
> `/home/arduino/hype_controller.py` (md5 `6e4393d8…`, byte-identical to the repo copy):
> `"LIVE|SET|2026"` / simplex / spin 0.5 / amp 1.00 dies at block 13 (**65 ms**), and the exact
> threshold for that string is **amp > 0.859**; a bare `"W"` in the same face dies above
> **amp 0.824**. `"HYPEROSCI"` (rmax 0.907) is safe at any amp, and so is the `"Stoom|Afblazen"`
> loaded on the board today — which is exactly why this has never been seen. See §1.

> **And the daemon is mis-aimed right now:** it booted before its own AP existed, its
> `IP_MULTICAST_IF` failed and was swallowed, and it has been beaconing out of the default route
> ever since — silently, with a healthy-looking log. Bringing the AP up afterwards does not heal
> it. No slave can hear it until the daemon is restarted. See §1.5.

Ranked answer to the three asks:

| Ask | Verdict | Cheapest good answer |
|---|---|---|
| **Custom fonts** | easy — but reorder the priorities, and *accents came first* | The 32 installed `.jhf` files are only **25 distinct faces**: 19 unexposed, and **~6 of those Latin-readable**. Two of the six, `cursive` and `scripts`, measure a **0.196 retrace fraction against 0.340–0.512 for every face shipping today** (§4). Two dict entries plus their `<option>`s. TTF works too (§4) — 7.65 ms/rebuild, the cost class already in use — but hollow outlines. **What actually shipped first was neither: a `.jhf` file is printable ASCII only, so `Anémi` was rendering as `An?mi` — byte-identically, verified. Accents are now composed from the face's own strokes (§10 item 0).** |
| **SVG import** | moderate — bake it offline | Parse on the laptop, ship a point table. Board-side delta ≈ 40 lines and no parser that can fail on stage (§5). |
| **More effects** | easy — most are free | ~18 of the 26 in your screenshot bake into the point table at zero hot-path cost (§6). The bake itself is not free — **10.9 ms affine / 13.3 ms swirl** on the board, 1.5–2× a warm gothic `render_text` rebuild (6.9 ms) — so it belongs behind the rebuild mutex. |

The real ceiling is not CPU. A text block measures **523 µs against a 5000 µs budget (10.5%)**
on the board, and that cost is independent of table length and of `freq`; the most expensive
per-sample warp measured — live swirl, four `math` calls per sample — still only reaches
818 µs (16%). The ceiling is **48000/freq drawn points per redraw** (§3), now measured exactly:
534 distinct table entries at the live 90 Hz, a 3.75× decimation of the 2000-point table. That
is physics. The one software limit that binds is the GIL, and the cliff is at **two** threads:
one competing CPU-bound thread never crossed the 20 ms re-anchor threshold in four 2 s runs,
two always did (§9).

---

## 1. Six bugs found on the way (fix these regardless of any feature)

> **ALL SIX FIXED AND DEPLOYED 2026-07-22.** `/home/arduino/hype_controller.py` is md5
> `296d398e…`, byte-identical to the repo copy, and `hyperosci-controller` is running it. The
> section below is kept as the record of what was wrong and why — read the diagnosis, not the
> mitigations. Verification: 20 automated checks on the board (`test_fixes.py`) plus 20 against
> the live daemon over HTTP (`live_test.py`), all passing. Before/after on the same board, same
> minute: `W`/times at amp 1.00 + spin 0.5 raises `OverflowError` at block 12 (60 ms) on the old
> module and runs 400 blocks clean on the new one. Cost: **+2.1 µs on a 574 µs block** with spin
> on, **−1.4 µs** with spin off — inside run-to-run noise, on a 5000 µs budget. §1.5 was the one
> that mattered on the night: the restart logged `[net] multicast egress 192.168.50.1: bound`
> and then `[discovered] slave id=121 at 192.168.50.228` — **first audio the rig has ever
> actually delivered.** All four saved presets survived the restart (§1.4).
>
> Every `:NNN` line reference below points at the **pre-fix** file, kept on the board as
> `/home/arduino/hype_controller.py.pre-bugfix.bak`.

The first four were reproduced against `src/unoq-controller/tools/hype_controller.py` as
committed (script: `verify_bugs.py`, output pasted into the session log). The deployed
`/home/arduino/hype_controller.py` is **byte-identical** to it — md5
`6e4393d8520ae188abad81dbf585531d` — so all of them are in the daemon running now, not just in a
source tree (§1.1 is reachable today; §1.3 and §1.4 stay latent until the font list or
`PRESET_FIELDS` changes). §1.1 and §1.2 were additionally re-run **on the board**:
`LIVE|SET|2026` + spin at amp 1.00 overflows on block 13, and after 37 spin blocks `rot` holds
2.3248 rad straight through a block at `rot_speed = 0`. §1.5 and §1.6 were found by re-reading
the source against the measured numbers, and §1.5 is observable on the board right now.

### 1.1 P0 — `OverflowError` kills the daemon (text + spin + amp)

`render_text` normalises by the **bounding-box span** (`:176-177`, `k = 1.8/span`), which bounds
`|x|` and `|y|` at 0.9 *each* but lets the *radius* exceed 1. With `rot_speed = 0` the transform
never mixes the axes, so nothing overflows at any amp — which is why this is invisible today.
Once spin is on, `int(x*axx - y*axy)` at `:293` can exceed int16:

| text / font | rmax | max safe amp | spin off | spin 0.5 rev/s |
|---|---|---|---|---|
| `HYPEROSCI` simplex | 0.907 | 1.13 | ok at amp 1.00 | ok at amp 1.00 |
| `Stoom\|Afblazen` gothic (live on the board today) | 0.971 | 1.05 | ok | ok at amp 1.00 |
| `H` simplex | 1.082 | 0.947 | ok | **dies at amp 1.00** (90 ms) |
| `LIVE\|SET\|2026` simplex | 1.192 | **0.859** | ok | **dies at amp 1.00 (65 ms = block 13), 0.90 (135 ms)** |
| `W` simplex | 1.243 | **0.824** | ok | **dies at amp 0.85** (165 ms) |

The threshold is exactly `amp > 32767/(32000·rmax)` — `:232` scales amp by 32000, `:293` stores
the product in an `array("h")`. The first two ceilings are unreachable (the amp slider stops at
1.00), which is why nobody has seen this.

rmax is set by the shape of the rendered block: 0.9 for a wide figure, climbing toward the box
diagonal `0.9·√2 = 1.27` as it approaches square. Changing face is not an escape — all 32
installed Hershey faces put `HYPEROSCI` between 0.899 and 0.917 — but the face is not neutral
either once the string is short, so read the column as per-string *and* per-face: `H` is 1.082
in simplex and **1.241 in times**, dropping its ceiling from 0.947 to 0.825. The dangerous
inputs are single letters and short multi-line blocks, and a bare `W` is a plausible thing to
type: its ceiling across the six shipping faces runs 0.823–0.873.

`stream_loop` has no `try/except` around `gen.block()` (`:1044`) and `main()` catches only
`KeyboardInterrupt` (`:1133-1137`), so the exception ends the process. `Restart=always` /
`RestartSec=3` brings it back at CLI defaults — circle, 100 Hz, `HYPEROSCI` — with the live
look gone. It does *not* latch into `failed`, and it also does not flap: `main()` can only start at
`circle`, `lissajous` or `rose` (`--pattern`, `:1116-1117`, applied at `:1121`), and nothing
restores the live pattern at boot — `load_presets()` fills `state.presets` (`:226`) but no code
path ever applies one. So the daemon comes back exactly once, at the `State` defaults
(`:212-214`), and stays up; `--pattern` cannot even select `text`, so it can never restart into
the crashing state. That is worse on stage than a restart loop, not better: one traceback in a
journal nobody is reading at showtime, and then the artwork is a circle until someone re-picks
the preset on the phone.

**Show-day mitigation, no code:** keep amp ≤ 80%. Confirmed on the board: `LIVE|SET|2026`
survives amp 0.85 and 0.80 with spin at 0.5 rev/s, and the tightest ceiling across every string
and face measured is a bare `W` at 0.824 (rmax 1.243). 80% is in fact safe for *any* string, not
just the ones tested: `k = 1.8/span` (`:176-177`) bounds `|x|` and `|y|` at 0.9 each, so rmax can
never exceed 0.9√2 = 1.273 and the worst possible sample is 0.80 × 32000 × 1.273 = 32583, inside
int16. Note the cap is not conditional on spin being on — see §1.2.
**Cheapest exact fix:** store the table's rmax at build time and cap once per block —
`ae = min(ae, 32767.0/rmax)` right after `:282`. Zero per-sample cost — on the board the
per-block transform is unmeasurable: turning pulse and spin on moves a `block(240)` from 523.0
to 523.8 µs against a 5000 µs budget, and spin alone measures 524.6, so the ordering is noise.
`flip` folds into the same four floats (`:284-287`), which makes one more `min()` per block
free. No figure changes size.
Do **not** "normalise by max radius" instead: it shrinks a lone `W` by 28% (0.9/1.243 = 0.72×, from the measured rmax) and silently
changes what every saved preset's amp value means.

**Fixed** as described: `table_rmax()` stores the table's largest radius at build time and
`block()` does `ae = min(ae, 32767.0/rmax)` once per block, immediately after the pulse LFO.
The bound is exact — `|x·cosθ − y·sinθ| ≤ hypot(x, y)` for any θ and either flip — so no string
can reach int16 at any amp, and nothing that was already safe shrinks by a sample: `HYPEROSCI`
(rmax 0.907) gets a ceiling of 36113 against a requested 32000, so the `min()` is not binding
and the peak stays 28800. `W`/times (rmax 1.243) clamps to 26368 and still peaks at 32756 — the
full int16 range, just not past it.

### 1.2 P1 — `PatternGen.rot` is never reset

`self.rot` (`:268`, advanced at `:301-302`) keeps its accumulated angle when `rot_speed`
returns to 0. Verified on the board: after 37 blocks of spin `rot` = 2.3248 rad (133°), and one further block
with `rot_speed = 0` leaves it at 2.3248 — it never returns to zero. Frozen at that angle the
transform still mixes the axes, so a *static* rotated word crashes identically with spin
switched off — reproduced against the module, not re-run on the board. So "turn spin off" is not a
reliable escape from 1.1.

**Fixed:** `rot_speed == 0` now sets `self.rot = 0.0` and takes the cheap branch. Spin off
means upright — before this there was no control anywhere in the UI that could straighten a
word once the slider had been touched. Verified on the board: 37 blocks of spin, then one
block at 0, and the output is byte-identical to a generator that never spun.

### 1.3 P1 — the preset load path does not validate `font`

`/api/pattern` gates the font (`:855`, `if body.get("font") in TEXT_FONTS`). The preset loader
does not: `:934` calls `render_text(p["text"], p["font"])` and `:950` writes `state.font`
unvalidated. `render_text` swallows the `KeyError` (`:155-158`) and returns `None`; `block()`
then falls through to a plain circle (`:276`, `:304`). Verified — the emitted samples are
`[25600, 0, 25597, 335, …]`, a constant-radius circle, while `/api/state` still reports
`kind="text"` and the dashboard's playing line still names the artist.

On stage that is unbreakable in the dark: the scopes draw a circle and the phone insists
otherwise. Unreachable today (the six font keys come from a package dependency) — but **any**
change to the font list makes it live, which is precisely what §4 proposes.

**Fixed** in the same commit as §1.4, as demanded: `clean_preset()` whitelists `font` against
`TEXT_FONTS` and `kind` against the four pattern names, and clamps every numeric field — on the
file-load path and the `op=load` path both.

### 1.4 P1 — adding one `PRESET_FIELDS` entry deletes every saved preset

`load_presets()` filters with `all(k in p for k in PRESET_FIELDS)` (`:91`). Verified: a valid
preset survives today and is dropped the moment one field is appended — and the next
`save_presets()` rewrites `~/hype_presets.json` from the filtered list (`:918-919`),
**permanently deleting the prepared artist names**, silently, with no log line. On the board
today that is four saved presets — `HYPEROSCI`, `thanks claude`, `I   LOVE`, `Stoom` — each
holding exactly the twelve current fields and nothing more, so all four go on the first save
after the daemon restarts with the new field.

Every feature in this document adds a preset field. Fix the filter to require only `name` +
`kind` and read the rest with `p.get(k, default)` — *in the same commit* that converts the
loader's bare subscripts at `:934-950`, or you trade silent data loss for a `KeyError`.

**Fixed** exactly that way. `PRESET_FIELDS` is gone; `PRESET_DEFAULTS` replaces it,
`load_presets()` requires only a non-empty `name`, and every other field is read through
`clean_preset()` with a default. Adding a field in a future build is now a no-op for old files.
Verified against a file holding one complete preset, one missing nine fields, one naming a font
that does not exist and one full of type-wrong junk: all four load, all four survive a re-save,
and the junk one comes back clamped to `circle` at 100 Hz. The four real presets on the board
survived the deploy.

### 1.5 P0 — SYNC multicast egress silently misroutes when the daemon starts before the AP

`make_ctrl()` (`:971-979`) sets `IP_MULTICAST_IF` to the fixed startup `iface_ip` inside a
`try` whose `except OSError: pass` (`:977-978`) swallows the failure. If the AP interface does
not exist yet the `setsockopt` raises `EADDRNOTAVAIL` — confirmed on the board, errno 99 for any
unassigned address — so the socket is returned with **no multicast egress set at all** and
falls back to the kernel default route. `make_ctrl()` is called once at `:981` and re-called
only from the `except OSError` around `ctrl.sendto()` (`:1017`) — i.e. only while sends are
*failing*. The moment a default route makes `sendto()` succeed, nothing ever re-tries the
binding again.

Observed live, and still true as this was written: the daemon booted with `iface=192.168.50.1`
before `hyperosci-ap` existed, logged 35 × `[net] sync send failed ([Errno 101] Network is
unreachable); recreating socket`, then went quiet as soon as a default route appeared. The AP
has since been brought up — `wlan0` now holds 192.168.50.1 and a client is associated — and the
daemon is **still** beaconing out of the USB tether: `ip route get 239.7.7.7` resolves to
`dev usb0`, and `systemctl show -p MainPID -p NRestarts` gives 658 / 0, so the socket from
00:57 is the one still in use. The log looks healthy. No slave can hear a sync beacon, and the
dashboard shows `slaves: []` with the AP up and working.

This is P0 because "debug over USB tether first, bring the AP up second" is the obvious
bring-up order and it silently produces a rig that never syncs.

**Show-day mitigation, no code:** AP up *first*, daemon second; restart the daemon any time the
AP comes up after it.
**Cheapest fix:** re-apply `IP_MULTICAST_IF` on a slow timer or per beacon — the beacon path is
500 ms, not hot, and the `setsockopt` is microseconds — and log the egress interface whenever it
changes, so a healthy log stops meaning "quiet".

**Fixed:** `IP_MULTICAST_IF` moved out of socket construction into `bind_egress()`, re-applied
on **every** 500 ms beacon, with a log line on each transition. The daemon now self-heals
whenever the AP appears, in either boot order, and a socket recreated by the send-failure path
is re-aimed on its next beacon. One note for future debugging: `ip route get 239.0.0.1` still
answers `dev usb0` even when this is working — the socket option overrides the route lookup, so
the route table was a *symptom* of the broken state, never the authority. The authority is the
`[net] multicast egress … bound` line.

### 1.6 P1 — concurrent `/api/pattern` rebuilds: lost updates, and the two-thread GIL stall

The handler snapshots `text` and `font` under the lock (`:847-848`), runs `render_text()`
*outside* it (`:857` — deliberate, and right, so a font parse cannot stall pacing), then writes
**both** fields back unconditionally (`:886`). Two overlapping POSTs — two phones, or one
double-tap — fail two ways. (a) Lost update: a request that set only `text` writes back its
stale snapshot of `font` too, silently reverting a concurrent font change, and vice versa. No
error, no log; the operator's edit flickers back. The window is the rebuild itself — 3.3 ms to
36.4 ms measured on the board (§4, §9) — comfortably inside phone-tap latency. (b) The stall:
two concurrent rebuilds are exactly two competing CPU-bound threads on top of `stream_loop`,
which is the regime the board measures at **342–625 ms** worst block gap (§9) — a re-anchor and
~450 ms of stationary centre dot on all four scopes, from two ordinary UI actions.

**Fix:** one global rebuild mutex held across snapshot → render → write-back (§10), and write
back only the fields the request actually set. The mutex is the same one §9 shows is sufficient
on its own.

**Fixed:** `State.rebuild_lock`, taken only when a request actually carries `text` or `font` so
slider POSTs do not queue behind a font parse, and held across snapshot → render → write-back.
Lock order is `rebuild_lock` before `state.lock`, never the reverse. The write-back is
field-by-field as well, so even a future writer outside the mutex cannot lose the other field,
and `op=load` takes the same mutex — a preset tap and a font change are two rebuilds like any
other pair. Verified with 50 interleaved `text`/`font` POSTs against the live daemon: both
fields stick, no HTTP errors, no re-anchor. `sys.setswitchinterval()` was **not** touched — §9
refuted it.

---

## 2. Why the effect catalogue is cheaper for us than for osci-render

osci-render evaluates each effect **per audio sample**, on the stream of points coming out of
its arc-length traversal (`osci_SimpleEffect.h`, `ShapeVoice.cpp:238-458`). A warp therefore
permanently destroys constant beam speed — the thing arc-length traversal exists to guarantee.

We do not have a stream. We have a **precomputed equal-arc-length table** (`render_text`
`:181-199`) and a hot loop that walks it. That means a static warp can be applied to the
polylines *before* the resample and then **re-resampled**, so uniform brightness is restored:

```
strokes → normalise (:176-179) → [WARP HERE] → equal-arc resample (:181-199) → table
```

Consequences, all measured:

- A baked warp costs **zero** on the hot path — structurally, because the walk reads `ttbl[int(pos)]`
  (`:292`) and does the same work whatever the entries hold, and the board confirms the walk is
  insensitive even to table *size*: `block(240)` measures 525.8 / 526.8 / 526.2 µs against 2000- /
  4000- / 8000-entry tables. The bake itself is not cheap, though: warp + re-resample of the
  2000-point table is **10.9 ms affine, 13.3 ms swirl** on the board — 1.6–1.9× a warm `gothic`
  rebuild (6.91 ms) and 3–4× a warm `simplex` one (3.29 ms), under the 20 ms re-anchor threshold but
  not by much. It runs in the HTTP thread (`:857`, `:934`) and must sit behind the same rebuild
  mutex as `render_text` (§1.6).
- The whole *existing* effect stack (pulse + spin + flip) is **free** — measured on the board, text + spin + pulse costs 523.8 µs/block against 523.0 µs
  for static text, and text + spin alone 524.6 µs: the deltas are run-to-run noise — because `axx/axy/ayx/ayy` (`:284-287`) is an affine chain folded into
  four floats **once per block**. That is the same trick osci-render uses for parameter
  smoothing (`osci_Effect.cpp:44-46`, commented *"not per sample!"*). Scale, Translate,
  Rotate and Skew all fold into it for ~free.
- Effects that osci-render needs a ring buffer for are table arithmetic for us. `Trace` and
  `Dash` need a 1-second `Point` ring plus a synthesised frame-sync channel over there
  (`DashedLineEffect.h`, `ShapeVoice.cpp:281-282`); here they are a restriction of the walk
  range, because our `pos` **is** the frame phase.

Where per-sample work is genuinely needed, budget it from two board-measured rules of thumb:
**~25 µs/block per simple per-sample float op** (~105 ns/sample, marginal) and **~100 µs/block
per per-sample transcendental** (~420 ns/sample). Dispatch dominates, but it does not level
everything — a `math.sin()` is 3–4× a float op, not equal to it. Reference points, all
`block(240)` on the board against a text-branch replica (baseline 453 µs):

| per-sample addition | Δ µs/block | ns/sample |
|---|---|---|
| 1 float op | +30 | +127 |
| 4 float ops | +96 | +402 |
| one `math.sin()` | +101 | +419 |
| full 2×2 rotation | +104 | +434 |
| full swirl (`hypot`, `atan2`, `cos`, `sin`) | +365 | +1519 |

Against a **5000 µs block budget** and a 523 µs text baseline, none of that binds. The most
expensive per-sample warp measured is a live swirl — four libm calls a sample — and it adds
**+365 µs**; the replica carrying it totals **818 µs, 16% of budget**. A 20%-of-period ceiling
(1000 µs) leaves 477 µs above the text baseline: about 18 extra simple per-sample ops, or 4
transcendentals, or one full live swirl with margin. **CPU is not what limits the effect
catalogue — §3 is.**

---

## 3. The hard ceiling: 48000/freq points per redraw

Independent of CPU, code and money. The walk advances `tstep = m*freq/48000` entries per
sample (`:281`) and reads `ttbl[int(pos)]` (`:292`), so **one redraw visits `48000/freq`
distinct table entries**, whatever `m` is. Board-confirmed by walking the shipping 2001-entry
table entry by entry: 1601 distinct entries at 30 Hz, 961 at 50, 534 at 90 (the live setting),
480 at 100, 240 at 200, 121 at 400 — the formula exactly, or one entry over where the walk
lands on the wrap endpoint:

| freq | drawn points/redraw | decimation of the 2001-entry table | |
|---|---|---|---|
| 24 Hz | 2000 | 1.00× | 2000 of 2001 entries — and it flickers |
| 40 Hz | 1200 | 1.67× | bottom of the band `STATUS.md:193` calls best |
| 80 Hz | 600 | 3.34× | top of that band |
| 90 Hz | 534 | 3.75× | the live setting on the board when it was measured |
| 100 Hz | 480 | 4.17× | the code default (`:213`) — what a restart returns to |
| 400 Hz | 121 | 16.54× | slider max |

90, 100 and 400 Hz were counted on the board; the other three follow from the same walk. The
odd +1 at 90 and 400 Hz is the wrap entry (`:296-297`), not a break in `48000/freq`.

So `TEXT_TABLE_POINTS = 2000` is 1.7–3.3× oversampled across the 40–80 Hz band that looks best,
3.75× at the 90 Hz the board is set to today, and **a bigger table buys nothing above ~1200
points** — all 2000 entries are visited only below ~24 Hz. Detail trades directly against
flicker, and there is no software escape. This is the number that limits SVG artwork (§5), not
CPU: on the board `block(240)` costs ~525 µs of a 5000 µs budget and does not move with table
length (2000 / 4000 / 8000 entries → 525.8 / 526.8 / 526.2 µs) or with `freq`.

The related quantity is drawn arc length `L` in the ±1 box — for text it is `render_text`'s own
`total` (`:181-182`), which sums within-stroke segments only, so pen-up jumps cost nothing.
Trace brightness goes as `1/L` at fixed freq. `L` and contour count are **geometry, not timing**,
so nothing in this table depends on which machine ran it — and the board reproduces every text
row below exactly. Text rows measured through the shipping `render_text`; the SVG row comes from
the offline parser:

| figure | L | contours | pen-up jumps | jump fraction |
|---|---|---|---|---|
| `HYPEROSCI` simplex | 5.00 | 18 | 17 | 0.434 |
| `HYPEROSCI` cursive (**not exposed**) | 6.67 | 11 | 10 | **0.196** |
| `HYPEROSCI` script | 9.36 | 30 | 28 | 0.340 |
| `HYPEROSCI` duplex | 9.55 | 36 | 34 | 0.410 |
| typical SVG asset (median of 99) | 10.58 | — | — | — |
| `HYPEROSCI` times | 12.75 | 125 | 91 | 0.512 |
| `ART\|SHOW\|2026` simplex | 13.61 | 21 | 20 | 0.438 |
| `HYPEROSCI` gothic | 15.98 | 131 | 124 | 0.453 |
| `Stoom\|Afblazen` gothic — **live on the board today** | 20.54 | 99 | 94 | 0.469 |

*`contours` counts polylines; `pen-up jumps` counts table steps longer than 4× the median — the
moves the beam visibly retraces — and jump fraction is the share of total beam travel spent on
them. Jumps run below contours because a join can be coincident (no move at all) or shorter than
the threshold, and because the wrap from the last table entry back to the first goes uncounted, so
both retrace columns understate slightly.*

**The rig is already set well above the SVG median** — measured, not inferred. The pattern live on
the board is `Stoom|Afblazen`, gothic, two lines at 90 Hz: L = **20.54** over 99 contours, **1.9×
the SVG median**, using the multi-line controls that shipped in `a641176`. (L is the ±1-box arc
length, so that ratio is amp-independent; the board sat at amp 0.4 with `stream_on` false, so this
is the pattern the operator configured, not a trace anyone watched.) Typical artwork is half that
arc length. That materially raises the odds SVG looks fine, and it is still the single most
reassuring number in the study.

---

## 4. Custom fonts

### What osci-render actually does

`TextParser.cpp:71-73` calls JUCE's `GlyphArrangement::addFittedText` + `createPath()` — i.e. it
asks the system font engine for **filled glyph outlines** and traces their boundary. There is no
stroke path anywhere in osci-render. Fonts come only from `Font::findAllTypefaceNames()`
(`TxtComponent.h:30`) — **installed system families, no TTF/OTF file loading at all**. The free
build is capped at 2 lines (`maximumLines = 2`); multi-line layout, markdown and bold/italic are
premium (`TextParser.cpp:17-70`).

So "custom fonts" in osci-render means *hollow outline letters in an installed system family* —
which our own [text-rendering-findings.md:100-101](text-rendering-findings.md) already predicted
and accepted.

### Three routes, cheapest first

**Tier 0 — expose the Hershey faces we already have. ~15 lines, best value in the area.**
`hershey-fonts-data` ships **32 `.jhf` files**; `TEXT_FONTS` (`:66-73`) exposes **6**. All 32 were
run through the *unmodified* `_jhf_glyphs`/`render_text` **on the board**: every one parses, every
one has 95 glyphs covering ASCII 32–126. But hashing the rendered point table collapses them:
rendering one common test string through all 32 gives only **25 distinct figures**, and 6 of those
are already exposed — so **19 new faces, not 26**. Six groups render byte-identically:
`futural`≡`meteorology`≡`rowmans`, `futuram`≡`rowmand`, `gothiceng`≡`gothgbt`, `gothgrt`≡`gothicger`,
`gothicita`≡`gothitt`, `rowmant`≡`timesrb`. And only **about 6 of the 19 are new *and*
Latin-readable** — `cursive`, `scripts`, `gothicger`, `gothicita`, `timesr`, `timesib`; the rest are
Greek (`greek`/`greeks`/`greekc`/`timesg`), Cyrillic (`cyrilc_1`/`cyrillic`), kana (`japanese`) or
symbol sets (`astrology`/`music`/`symbolic`/`mathlow`/`mathupp`/`markers`). Noted in passing: the
face we expose as `times` is `timesrb`, which renders identically to `rowmant` — Hershey **Roman
Triplex**, not Times Bold.

Standout: **`cursive`** and **`scripts`** — which render identically on uppercase Latin. Measured on
the board, each spends only **19.6% of beam travel on pen-up retrace, against 0.340–0.512 across the
six faces shipping today** (simplex 0.434, duplex 0.410, script 0.340, gothic 0.453, times 0.512):
joined scripts lift the pen far less. L = 6.67 over 11 contours, and genuinely lovely. Cost: one
dict entry plus an `<option>` tag each.
*Caveat:* this trips §1.3 the moment keys are renamed, and a 25-item picker is a **worse** live
control surface than a 6-item one (even the Latin-readable subset is 12) — the `<select>` at
`:474-475` commits on change to all four scopes with no preview. Curate `cursive` and `scripts`
first — the retrace measurement makes that choice for us — and bind them to presets.

**Tier 1 — Inkscape's single-line SVG fonts. Best quality-per-risk, zero installs.**
Inkscape's Hershey Text extension ships **18 single-stroke faces** as SVG 1.1 `<font>` files
(9 `EMS*` under OFL 1.1, 9 `Hershey*` under the Hershey licence), 216 glyphs each including
Latin-1, 1.21 MB total. A ~45-line stdlib `ElementTree` + M/L/C walker parses all 18 **on the board**, but the cold parse
measures **27.5–130 ms** per face (EMSOsmotron 27.5, EMSReadability 49.4, HersheyGothEnglish 124–130).
It flattens all 216 glyphs eagerly, exactly as `_jhf_glyphs` (`:106-146`) flattens all 95, so it is
one-off per face and cacheable in `_glyph_cache` unchanged — but the heavy faces cost ~3.5× the
slowest `.jhf` face on the board (`astrology`, 36.4 ms cold). Parse at startup, or behind the rebuild
mutex. `EMSReadability` measures L=7.47 over **9 contours** at a **0.268 jump fraction**, against
simplex's 18 contours at 0.434 — half the contours, a third less retrace, and it looks like a modern
grotesque rather than a 1967 plotter font. `EMSNixish` is the retrace pick of the set at **0.173**. **Do not `apt install inkscape`** (345 MiB / 177
packages) — copy the 18 files.

**Tier 2 — the literal ask: arbitrary TTF/OTF.** `python3-freetype` **2.5.1-1**, bound against libfreetype **2.13.3**, is confirmed installed on the
board (`dpkg -l`, matching `STATUS.md:188`), so this is code risk only, not install risk. `_ttf_glyphs()` is
~25–45 lines: `face.load_char(ch, FT_LOAD_NO_SCALE|FT_LOAD_NO_HINTING)` then
`outline.decompose(move_to=, line_to=, conic_to=, cubic_to=)`, returning the same
`(left, right, [polyline,…])` shape `_jhf_glyphs` returns. Everything from `:171` down is reused
verbatim; the hot loop needs **zero** edits. Measured on the board, all on the same 9-glyph `HYPEROSCI`: `Face()` open **196 µs**, quadratic
flatten at 6 steps/curve **2.09 ms** (loading the 9 outlines is ~0.6 ms of that), and the **full
pipeline — flatten + normalise + equal-arc resample to 2000 — 7.65 ms** (12 contours, 568 raw
points). The shipping Hershey path measures **3.29 ms** warm for simplex and **6.91 ms** warm for
`gothic`, whose one-off face parse adds ~7.4 ms on top (15.57 ms cold against 8.15 ms warm on a
longer string) — so a TTF rebuild sits *between* the warm and the cold cost of a face already used
on stage. **TTF outline text is not a new cost class; it is the one already in use.**
Stock faces available with no download: **53 TTF/OTF files in 22 families** on the board — URW
base35 (incl. Z003 chancery), DejaVu Sans/Serif/Sans Mono, Quicksand in three weights, Noto
Mono/Sans Mono, Symbola. Two are useless for a wordmark and were confirmed so: `NotoColorEmoji` is
bitmap-only (FreeType will not open it at a scalable size) and `DroidSansFallbackFull` is a CJK
fallback whose `A` and `a` both map to glyph 0. `StandardSymbolsPS` and `D050000L` open fine but map
ASCII to Greek and to Zapf dingbats — which may be a feature.

Three honest caveats:

- **Lazy per-glyph caching is worth doing, but it is an optimisation, not a safety requirement.**
  Eager is already the shipping architecture: `_jhf_glyphs` (`:106-146`) parses every glyph of a face
  on first use and caches per file, and the heaviest *exposed* face measured cold is `gothic` at
  15.6 ms — the Tier 0 faces above would push that to 36.4 ms (`astrology`). None of it stalls the
  stream unless it monopolises the GIL, and pure Python does not: on the board, one competing
  CPU-bound thread produced a 7.4–7.9 ms worst block gap and **zero** crossings of the 20 ms
  re-anchor threshold in 4 of 4 runs. What *is* mandatory is the rebuild mutex (§1.6) — two
  concurrent rebuilds put the block gap in the 342–625 ms regime.
- **No kerning, and it fails silently.** FreeType reads only the legacy `kern` table; modern
  kerning is GPOS and `python3-uharfbuzz` is not in trixie. Cosmetic for short names.
- **You get the outline of your brand font, not your brand font.** Hollow double-stroke
  letters, counters as separate loops. Not a defect — it is what osci-render does.

The dimness objection does *not* distinguish outlines, and the board confirms the figure unchanged:
DejaVu Sans measures **L=9.79 over 12 contours**, between the `duplex` (9.55) and `times` (12.75)
faces already shipping. On retrace it is *better* than anything we expose — **0.179 of beam travel is
pen-up**, against simplex's 0.434 and times' 0.512 — because a closed outline lifts the pen once per
loop, not once per stroke. `Z003` chancery is lower still: L=11.98 at **0.145**. So `cursive` keeps a
narrow edge on contour count (11 vs 12) but *not* on pen-up travel: the outlines win that metric,
by drawing more ink rather than by lifting less.

**If a specific single-stroke brand wordmark is the real requirement**, no library delivers it —
`potrace`/`autotrace` produce outlines, not centrelines. Hand-trace the skeleton in Inkscape once
(~20–40 min), bake it, and no controller code changes at all.

> Licence note for anything *baked and committed*: `fonts-urw-base35` is AGPL-3 with an exception
> covering only PostScript/PDF embedding. Live on-board rendering is fine; for a committed data
> file use **DejaVu (Bitstream Vera) or Quicksand (OFL-1.1)** — the clean-licence families actually
> installed. Liberation is **not on the board at all**; bake from it off-board if you want it, but it
> is not a board-side fallback.

---

## 5. SVG import

### What osci-render does

`SvgParser.cpp:5-16`: JUCE's XML SVG parser → `Drawable::getOutlineAsPath()` → `pathToShapes`.
Two policies worth *not* copying: it takes `isStrokeVisible() ? strokePath : path`
(`juce_DrawableShape.cpp:185`), so a stroked line is traced as the **outline of its own stroke**
— two parallel passes plus end caps; and `DrawableComposite::getOutlineAsPath` honours neither
`clip-path` nor `display:none`, so clipped-away geometry still gets drawn. Taking centrelines
instead is strictly better for us and is one line of policy.

`pathToShapes` does not flatten beziers — it emits `Line`/`Quadratic`/`Cubic` objects evaluated
per sample later, and its "arc length" for curves is an octagonal approximation of the **chord**
(`osci_CubicBezierCurve.cpp:41-50`), so curved segments are ~14% under-budgeted and traced
non-uniformly. Our resampler is already better.

### Where the cost is (measured on the board)

Parsing is cheap at the sizes we can actually draw, and stdlib is the only option: `lxml`, `numpy`
and `svgelements` are all absent from the board (checked directly), so the front-end stays ~230
lines of `xml.etree` + a `d`-grammar regex + a transform stack + adaptive de Casteljau. Board cost:
`ET.fromstring` runs at **~0.1 ms per kB** of SVG (1.34 ms at 15 kB / 300 paths), and the flatten →
normalise → equal-arc resample tail measures **7.65 ms** for a 12-contour / 568-point figure — of
which ~5.2 ms is the normalise + resample from `:171` down that `render_text` already runs, reused
verbatim. An icon-sized asset therefore lands at **~9 ms**, and not much under 4 ms whatever you
feed it, because the resample emits 2000 entries regardless of how simple the figure is. Against
`render_text`'s **3.29 ms** warm simplex and **6.91 ms** warm gothic, that is the same cost class as
a font rebuild, not a new one. (Every part was timed on the board; the end-to-end figure is their
sum, not a measurement.) Keep `svgelements` laptop-side if you use it at all — use it as a parser
only, never `Path.point(t)`.

The expat mechanism is real but the board defuses it. `ET.parse` feeds C expat in 64 KiB chunks
and **expat never releases the GIL**, so a large document is one uninterruptible hold — but at the
measured board rate (~4.5 µs/path) even an 8000-element document is ~36 ms, and a single hold of
that size only bites when a second CPU-bound thread is already running. That is the same two-thread
GIL cliff the rebuild mutex (§1.6) exists to close, not an independent hazard. Behind the mutex,
board-side parsing is simply fine.

Parse cost, then, is not the reason to bake. The reasons are:

**Recommendation — bake on the laptop, not the board.** Same parser runs off-board; the
controller does `array('h').frombytes()`. Measured: a baked int16 table is 7.8 kB and loads in
**0.016 ms** (JSON: 39 kB / 0.74 ms). Board-side delta ≈ 40 lines against ~230, and it deletes
the entire class of stage failures — no XML parser, no entity-bomb cap, no recursion cap, no
upload endpoint. It also buys offline luxuries the board could never afford, like nearest-neighbour
contour reordering (measured 54–59% retrace reduction on real icons).

### What actually makes SVG hard, and it is not software

- **73.1% of shapes in 400 real SVGs are fill-only** (14.9% stroke-only, 11.9% both). A beam
  traces the boundary of every filled region: hollow shapes, counters as separate loops, shared
  borders drawn twice. Artwork must be authored as strokes with `fill:none`, or reduced to a
  silhouette. No parser fixes this.
- **`<text>` is silently dropped** by every route (it is not geometry until converted).
  *Path → Object to Path* in Inkscape is a mandatory, documented step, and the baker should shout
  when it sees a `<text>` node.
- **Point budget** (§3, now measured exactly): at 90 Hz the walk visits **534 distinct table
  entries per redraw** — 3.75× decimation of the 2000-point table — so artwork detail beyond that is
  invisible whatever the table holds. **Contour count is not the binding limit.** `gothic` measures
  131 contours at a 0.453 jump fraction, between `simplex` 0.434 and `times` 0.512, and `gothic` is
  what the board's live pattern is set to. Budget on **retrace fraction**, not contour count: hold at
  or under the ~0.45 the shipping faces measure, which the offline baker can compute and improve
  (nearest-neighbour reordering measured 54–59% less retrace). This supersedes §9's "use contour
  count" metric. `L` remains the brightness target — near the ~10.6 SVG median, ~13 at the top.

If a web upload ever ships: `do_POST` (`:835-840`) reads `Content-Length` unbounded and
`json.loads` unconditionally, and the server binds `0.0.0.0:8080` (`:1124`) with no auth on any
route. The AP is WPA2-PSK (so the threat model is "anyone with the passphrase", not the audience),
but a pre-staged directory + `adb push` avoids the whole class. Free hardening either way: bind
`192.168.50.1`.

---

## 6. The effect catalogue

**Premium is not a barrier.** `osci-render.jucer:8` sets `OSCI_PREMIUM=1`, so the *default build
is the premium build*, and the source for all ten "premium" effects is present under GPLv3.
`TRADEMARKS.md` restricts only the name and logos. (The alphabetical interleaving in your
screenshot confirms a premium build — the sort pushes premium last only when `!OSCI_PREMIUM`,
`EffectTypeGridComponent.cpp:41-46`.) **Trace's grey means already-in-chain**, not locked
(`:130-135`); it is a free effect (`PluginProcessor.cpp:64`).

**God Ray is not a shader** — settled from source. `GodRayEffect.h` is 13 lines of per-sample
`return input * scale` with `scale` from a biased `rand()`, registered in `toggleableEffects`
(`PluginProcessor.cpp:78`). No effect in the picker is a shader; the picker is fed only from
`toggleableEffects`, while shader parameters sit in a separate list that
`CommonPluginProcessor.cpp:82-87` documents as *"shader-only effects (no audio processing)"*.

### Tier A — bake into the table, zero hot-path cost

"Zero" is literal, and now measured on the board: `block(240)` costs 525.8 / 526.8 / 526.2 µs on
2000 / 4000 / 8000-entry tables — a 0.2% spread, i.e. flat — so neither a baked warp nor the extra
table length it may want costs the hot loop anything. A warped table is the same walk over the same
code path.

The cost moves to the rebuild: **10.9 ms for an affine bake, 13.3 ms for a swirl**, against 3.3 ms
for a warm simplex `render_text` and 6.9 ms for gothic. That clears the 20 ms re-anchor threshold by
only 7–9 ms, so a bake must never run concurrently with another rebuild — put it behind the same
rebuild mutex §1.6 needs for `render_text`, which is where every bake belongs anyway.

*Where a row below still quotes a bare µs delta with no board figure, it is a dev-box number that
predates the board measurements and has not been restated; treat it as ordering, not magnitude. On
the board, budget ~25 µs/block per extra per-sample float op and ~100 µs/block per per-sample
transcendental, against a 523 µs baseline in a 5000 µs budget. Those deltas apply only if you run
the effect live instead of baking it.*

| Effect | Math | Note |
|---|---|---|
| **Scale** | `out = in * (sx, sy)` | folds into the per-block matrix; `flip_x/y` generalised from ±1 |
| **Translate** | `out = in + (tx, ty)` | live it is two per-sample adds, and the board measures two extra per-sample float ops at **+52 µs/block** — 1% of the 5000 µs budget. Cheap enough to keep on the hot path: Bounce and Duplicator reuse it |
| **Rotate** | `x' = c·x − s·y` | **already shipped** as `spin`, and measured free on the board: 523.0 µs static text vs 524.6 µs spinning, with spin+pulse at 523.8 — the spread is noise, and the per-sample 2×2 (`:293-294`) runs whether `rot_speed` is 0 or not |
| **Skew** | `x += tx·y` | folds into `axy`. Anisotropic: a 2:1 shear gives 2:1 brightness between H and V strokes |
| **Swirl** | rotate by `10·v·hypot(x,y)` | the only non-affine warp bake measured on the board: **13.3 ms to bake**, against 10.9 ms for a pure affine — the re-resample dominates, not the warp. Live it costs **+365 µs/block** (hypot + atan2 + cos + sin per sample). **Preserves rmax exactly** |
| **Bulge** | `r' = r^(1−v)` | keep the `r == 0` guard; subdivide finely near the origin |
| **Vortex** | `r→rⁿ, θ→n·θ+φ` | at wet=1 the φ term is exactly an output rotation by −(n−1)φ, so its LFO folds into `spin` **free** |
| **Polygonizer** | quantise ⊥ distance to n-gon shells | most expensive bake in the tier; board bakes (warp + re-resample of the 2000-pt table) measure 10.9 ms affine to 13.3 ms swirl, so budget this one near the top of that band — inside the 20 ms re-anchor threshold, but not by much, so it belongs behind the rebuild mutex. Its 2 Hz phase LFO is not bakeable |
| **Kaleidoscope** | wedge mirror ×N | bake the **union of all N wedges**, not osci-render's one-wedge-per-redraw (which leans on phosphor). Arc ×1.2–3.0 |
| **Unfold** (ex-Bloom) | compress full 2π into one wedge, repeat | arc ×4.1–7.5 → 13% brightness at N=6. Cheap to port, unusable above N≈3 |
| **Spiral Bit Crush** | lattice in rotated log-polar | ~8 transcendentals/sample ≈ **+800 µs/block** live at the board's ~100 µs/block per per-sample transcendental — the dearest thing in the tier, but that is ~16% of the 5000 µs budget on top of a 523 µs baseline, so it is affordable live; **free** baked. Biggest win of the group |
| **Bit Crush** | `round(p·q)/q` | bake, or ≈+150 µs/block live — 4 per-sample multiplies plus two `round()` calls at the board's ~25 µs/block per per-sample op; the builtin calls make that a floor, and it is ~3% of budget either way. Baked, the lattice rotates with the figure |
| **Twist** | `x·cos(4π·v·y)` | z-only in 3D; in 2D a banded horizontal squeeze. Needs Perspective to be interesting |
| **Ripple** | `z += v·sin(…r²)` | **z-only — a literal no-op** on a 2D pipeline. Needs Perspective |
| **Perspective** | — | at z=0 it is *exactly* a uniform scale by `cos(fov/2)` — a duplicate of the amp slider. Ship it only as the z→xy projector that makes Twist/Ripple real |

Adding a `z` column to the table plus a baked Perspective is ~1 session and unlocks Ripple,
Twist, Skew Y/Z and Rotate X/Y together.

### Tier B — table-walk tricks (cheap, and cheaper for us than for osci-render)

| Effect | Our implementation | Measured |
|---|---|---|
| **Trace** | restrict the walk range; re-derive `tstep` from the visible span | free — `block(240)` measured invariant to `freq` (529.6 / 523.0 / 524.3 µs at 30 / 100 / 400 Hz) and to table length (525.8 / 526.8 / 526.2 µs at 2000 / 4000 / 8000), the two quantities Trace changes; osci-render needs a 1 s ring |
| **Dash** | skip table entries on a countdown | est. ≈+50 µs/block — a per-sample counter and branch at the board's ~25 µs/block per simple float op |
| **Duplicator** | recompute `ox/oy` in the `pos >= m` wrap branch | +4.1 µs |
| **Multiplex** | same wrap hook; `gridDelay` degenerates to a `pos` offset | free (their most expensive effect) — the wrap branch runs <1×/block at 90 Hz, unmeasurable against the 523 µs baseline |
| **Bounce** | update position on wrap | +3.4 µs; **crisper than osci-render**, which shears the shape mid-draw |
| **Delay** (static figure) | a second gather at a phase offset; the feedback series is bakeable | ≈+150–200 µs/block — *derived, not measured*: 6–8 per-sample ops at the board's ~25 µs/block each. 3–4% of budget, but genuinely per-sample, unlike the wrap-branch rows above |
| **Stereo** | Y read at a different phase than X | ≈+150 µs/block, *derived* — a second gather is ~6 bytecode ops at ~25 µs/block each on the board; 3% of budget, not in the picker, one of the cheapest novel looks |

> `Trace` needs a wrap guard: the naive `if pos >= hi: pos -= span` indexes past the table
> whenever `start + length > 1.0`, which is the normal animated case. That is an `IndexError` on
> the same uncaught path as §1.1.

### Tier C — cheap per-sample

Each of these is 2–6 per-sample float ops, or one transcendental for `Wobble`. At the board's
~25 µs/block per per-sample float op and ~100 µs per transcendental that is **+50 to +150 µs/block
each**, 1–3% of the 5000 µs budget: `Smoothing` (also rounds off pen-up jumps — most of what it
buys visually) · `Vector Cancelling` · `Distort` · `God Ray` with a pre-biased LUT · `Wobble`
(phase-locked to the figure, so it is a pure function of table index — bake it and the hot path
pays nothing, at the price of one more rebuild). The whole tier switched on at once is **+250 to
+750 µs**, taking the block to 0.8–1.3 ms of the 5.0 ms budget.

### Tier D — do not port

**True feedback Delay** is the one that gives trailing *rotated* ghosts, and it is the one that
overflows — gain `1/(1−decay)` = 1.67 at the default decay, straight into §1.1's int16 crash. Cost
is not the objection: a per-sample ring read/write plus a multiply-add on each axis is ~7 per-sample
ops, so ≈**+175 µs/block** by the rules above — *derived, not measured* — about 14% of the block
once added to the 523 µs baseline. Skip it for the overflow, not for the CPU.
**Lua/Custom**: the blocker was never speed, and no board Lua cost was measured. It is that
`LuaParser.cpp:42` calls `luaL_openlibs` — full `io`/`os`/`package`/`debug`, no sandbox — inside
the hot loop on a network-reachable box. Run user scripts **once per table rebuild** instead:
same expressive power, zero hot-path cost, no arbitrary code in the audio path.

---

## 7. What is genuinely impossible (and why that is fine)

The GLSL visualiser family — **Glow, Afterglow/Persistence, Focus, Ambient, Overexposure,
hue/saturation, screen Noise, Sweep/Trigger** — never touches the sample path. It simulates a
CRT that **we physically own**:

| osci-render shader | our hardware equivalent |
|---|---|
| Focus (`uSize` → Gaussian σ) | the scope's FOCUS knob |
| Afterglow / Persistence | the phosphor's actual decay constant |
| Glow (5-tap radial smear) | faceplate halation + deliberate slight defocus |
| Overexposure | turn INTENSITY up until the phosphor blooms |
| Sweep / Trigger | meaningless in X/Y mode |

`BloomEffect.h` is a 4-line alias of `UnfoldEffect.h` with no implementation — if someone asks
for Bloom they mean Unfold, or they mean Glow.

**We lose nothing relative to osci-render on hardware grounds.** Its output bus is
`AudioChannelSet::stereo()` (`PluginProcessor.cpp:49`) and grepping for `blank` across both source
trees returns nothing — it has no Z/blanking channel either, and its "brightness" is entirely
dwell time in a shader.

**Audio-reactive / sidechain** is the highest-value thing we cannot have. The algorithm is 10
lines (`SidechainState.h:38-79`), but `LEAD_US = 450_000` (`:52`) puts every reaction half a second late,
the mics are on the **slaves** (wrong side of the link), and `STATUS_PAYLOAD` carries no level
field. Structural, not unhardwired.

---

## 8. Other things worth stealing

- **A pre-baked clip player** (`kind="clip"`, raw int16 ring) is the highest value-per-effort item
  in the whole survey and **the only one that lowers show risk**: the hot loop becomes
  `buf[off:off+960]` — measured **on the board** at **1.3 µs/block** against **523 µs** for the
  live text walk, 400× cheaper and 0.03% of the 5 ms budget. CPU was never the binding constraint
  (the live walk is 10.5% of budget); the real win is that a playing clip has no rebuild path at
  all — no `render_text` in the HTTP thread, and nothing for the rebuild mutex to serialise. It
  unlocks the entire osci-render catalogue at desktop quality (3D wireframes, GIFs, fractals, Lua,
  all 30 effects) with no board-side parser. osci-render's free build records audio-only WAV at
  X=L/Y=R — the same interchange format as our payload. 192 kB/s, so a 60 s clip is 11.5 MB.
  *Caveat:* §3 still applies — a baked clip is decimated to **534 points per redraw** at the 90 Hz
  the rig is set to today (480 at 100 Hz, 1601 at 30 Hz — counted on the board), and for a clip
  that rate is fixed at bake time, so "full desktop quality" is not what arrives.
- **L-system fractals**: `FractalParser.cpp:152-173` is a 20-line command mapping and the
  definitions are 5-line JSON. Reuses `:171-200` verbatim, so the table build is the same
  equal-arc resample `render_text` does — 3.3 ms (simplex) to 6.9 ms (gothic) on the board — and
  belongs behind the rebuild mutex. The generator that feeds it is not board-measured.
- **GPLv3 art assets**: `Resources/` ships 10 OBJ models, 5 `.lsystem` definitions, 11 Lua scripts
  and real SVG art (cat, skull, alien, yinyang, bicycle, clippy). The `BRANDING-NOTICE.txt` files
  exclude only the logos. An instant preset library.
- **Recording our own output**: `stream_loop` already holds the exact bytes at `:1044`. A
  background writer turns the rehearsal into the clip library for the show.
- **Brightness-accurate dashboard preview**: `drawScope` (`:629-664`) strokes one uniform polyline,
  so it lies about the one thing an operator tunes. Setting `globalAlpha ∝ 1/segment-length` makes
  freq/amp predictive. (It also currently ignores pulse and spin entirely.)
- **MIDI-learn as a control surface** (`osci_MidiCCManager.h`): a €30 USB controller beats poking a
  phone mid-set. Useful, not urgent, and needs no LFO engine.

**Per-slave art.** Airtime is already free — `stream_loop` sends four separate unicast packets
(`:1048-1052`), only the payload is shared. Four independent pure-Python streams measure
**2.45 ms/block** on the board — one process, one thread, four separate tables, stable to ±0.2%
over three runs. That is **49% of the 5 ms budget** before a single packet is built, and 18% worse
than 4× a single stream (518 µs alone). The overhead is the four *distinct* tables, not the four
calls: four streams walking the same table cost 2.08 ms, i.e. exactly 4×. Half the budget on one
core is uncomfortable — and spreading the streams across threads in one process is not the
fallback, it is the ≥2-competing-thread regime the board measures at 342–625 ms gaps (§9). So:
**four processes, one per slave** — the QRB2210 has four A53 cores, and processes sidestep both
the CPU pile-up and the GIL; `time.monotonic()` is `CLOCK_MONOTONIC` and system-wide, so passing
one absolute epoch on the command line keeps them locked exactly as the shared epoch does today.
On baked clips the question disappears outright: four streams measure **5.4 µs/block**, 0.1% of
budget.
*(A crude version already ships — per-slave `set_pattern` + `set_mode local`. But note
`STATUS.md:70-100`: that exact path once hard-froze a slave, and the standing caveat is that the
power margin is thin.)*

---

## 9. What the board measurement settled (and what it left open)

**The board was measured on 2026-07-22** — adb over the USB tether (device `2508215365`),
benchmarked against the deployed `/home/arduino/hype_controller.py`, whose md5 is byte-identical to
the repo copy, plus purpose-built replicas of its text branch for the per-op cost model. QRB2210,
4×A53 all max 2016 MHz, `schedutil`, **Python 3.13.5**, controller RSS 22.6 MB.

**The 4–7× scaling factor is refuted — delete the model, not just the number.** The dev box runs
Python 3.14.6, whose tier-2 optimiser folds away work the board actually performs: dev-box
`math.sin` timed at **7.3 ns/op net of the loop floor — below an empty loop iteration**, against
**208.7 ns** on the board. Dev-box run-to-run spread on identical `block(240)` work was 164–361 µs
(2.2×), and the implied factor lands anywhere between **1.34× and 28.6×** depending on which line
you pick. **Do not quote a dev-box figure or a scaling factor anywhere else in this document.**
Some µs deltas in §6 Tier A/B/C are still dev-box numbers: restate them with the rules below, or
drop them for a plain "negligible".

Board numbers, against a **5000 µs** block budget (240 frames @ 48 kHz):

| quantity | board |
|---|---|
| `block(240)` — circle / text / text + spin + pulse | 474.4 / 523.0 / **523.8 µs** (9.5–10.5% of budget) |
| one extra per-sample float op | **~25–30 µs/block** (~105–127 ns/sample) |
| one extra per-sample transcendental | **~100 µs/block** (419 ns/sample for `sin`) |
| full live swirl (hypot, atan2, cos, sin) per sample | 818 µs all-in — **16% of budget** |
| four independent per-slave streams, one thread | **2.45 ms — 49% of budget** |
| warm `render_text` `HYPEROSCI`, simplex / gothic | 3.29 / 6.91 ms |
| cold font switch, worst face (`astrology`) | **36.4 ms** |
| TTF pipeline, DejaVu `HYPEROSCI`, flatten → resample | 7.65 ms |
| baked warp rebuild, affine / swirl | **10.9 / 13.3 ms** |
| `ET.fromstring`, 300-path 15 kB SVG | 1.34 ms |
| baked clip player, one stream / four | 1.3 / 5.4 µs |

Block cost is independent of table length (2000 / 4000 / 8000 entries → 525.8 / 526.8 / 526.2 µs)
and of `freq` (30 / 100 / 400 Hz → 529.6 / 523.0 / 524.3 µs), and pulse + spin + flip are
**unmeasurable** against the baseline rather than merely cheap. **CPU is not the constraint** —
what binds is thread contention, not arithmetic. The per-sample cost model §2 used — ~35 ns for a
float op, a `math.sin()` and a Python call alike — was wrong twice: a float op is ~100–130 ns and a
`sin` is 3–4× a float op, not equal to one.

**Both questions this section used to gate are answered.** A parse is affordable at the sizes we
would actually ship: the full TTF pipeline is 7.65 ms for `HYPEROSCI` in DejaVu Sans — the same
cost class as the `gothic` face already shipping (6.91 ms warm on the same string, 15.6 ms cold on
a longer one) — and `ET.fromstring` on a 300-path SVG is 1.34 ms, ~4.5 µs/path. The one parse that
still fails is the big one: at that rate an 8000-element document is ~36 ms, and because expat holds
the GIL for the whole call (§5) those 36 ms are a gap no matter what else is or is not running. So
§5's bake-offline recommendation stands, but on artwork-authoring and stage-failure grounds, not on
parse cost.

Four pure-Python per-slave streams measure **2.45 ms/block** — four sequential `gen.block()` calls
in the one stream thread, **49% of the 5000 µs budget**, stable to ±0.2% over three runs — so they
fit even in one process, single-threaded, with half the budget to spare. The four-process proposal
(§8) is now a headroom-and-robustness argument, not a capacity one. What stays forbidden is four
streams as four *threads*: that is the ≥2-competing-thread regime below.

**What binds instead is the GIL, and the cliff is at two threads.** Gap between consecutive
`block(240)` calls with N competing CPU-bound *Python* threads, all figures ms, 2 s runs, 4 runs each:

| competing threads | p50 | p99 | max | gaps > 20 ms per 2 s |
|---|---|---|---|---|
| 0 | 0.53 | 0.56 | **0.77** | 0 |
| 1 | 1.80–1.83 | 7.0–7.1 | **7.4–7.9** | **0 — clean in 4/4 runs** |
| 2 | 1.84–1.87 | 7.2–11.2 | **342–625** | 1–2 |
| 3 | 1.87–1.90 | 7.2–399 | **383–1146** | 3–7 |

One competing thread never re-anchored. Two always did. So for work the interpreter can preempt —
a font or warp rebuild — the price is not its own duration but whether a *second* CPU-bound handler
can overlap it: the 36.4 ms cold `astrology` switch is safe today only because it runs alone, and
§1.6 is the concrete way two of them overlap. A single global rebuild mutex is therefore necessary
**and sufficient** for that class, which also retracts this document's `setswitchinterval` advice
(§10). It does nothing for a C-level parse, which is why SVG is the exception above.

**Three items stay open.** The first two are contested between analyses and the board pass settles
neither — it measured how often the 20 ms re-anchor fires, not whether the `abs()` should go, and no
slave was attached to see the artifact. The third now has its metric measured on the board, and
still wants a scope:

- **Do not change `abs(drift) > 20_000` to `drift > 20_000` at `:1040`** on the strength of this
  study. One analysis recommended it; two independent replays of the loop refuted it — positive-only
  ratchets the effective lead (475/500/530/570 ms after 25/50/80/120 ms stalls) and never recovers,
  past the slave's 512 ms ring into permanent buffer-full drops. Both variants produce exactly one
  forward jump per stall, so the claimed benefit is zero. The two *safe* changes if pacing is
  touched: set `HYPE_FLAG_SYNC_PULSE` on re-anchor (the controller hardcodes `flags=0` at `:1045`,
  and the slave has an intentional-discontinuity path at `net_rx.cpp:109`), and measure drift from
  `next_audio + LEAD_US` rather than `now + LEAD_US`.
- **A re-anchor does not show the mic.** `docs/performance-heat-analysis.md:99`'s citation
  (`net_rx.cpp:100-103`) is stale. A backwards jump hits `net_rx.cpp:127-130` — ~5 dropped packets,
  no reset. A forward jump > 20 ms hits `:131-134` → `jb.reset()`, and because packets keep
  arriving `stream_active()` stays true, so `fill_conceal` runs, not the local renderer. The real
  artifact is **~450 ms of a stationary centre dot on all four scopes** — shorter than documented,
  but a phosphor-burn risk rather than a mic squiggle. Worth correcting in the perf doc.
- **"Retrace is invisible"** is still an open *hardware* question — but the metric it asked for now
  exists. Measured on the board as **jump fraction**: the share of total beam travel spent on pen-up
  jumps while drawing `HYPEROSCI` (a table step > 4× the median counts as a jump).

  | face | exposed as | jumps | jump fraction |
  |---|---|---|---|
  | `cursive` | — | 10 | **0.196** |
  | `scripts` | — | 10 | **0.196** |
  | `mathlow` | — | 13 | 0.379 |
  | `markers` | — | 14 | 0.446 |
  | `futural` | simplex | 17 | 0.434 |
  | `scriptc` | script | 28 | 0.340 |
  | `futuram` | duplex | 34 | 0.410 |
  | `timesi` | italic | 42 | 0.492 |
  | `timesrb` | times | 91 | 0.512 |
  | `gothiceng` | gothic | 124 | 0.453 |

  For *ranking retrace* that retires **contour count** — it does not rank the same way. `gothiceng`
  has 3× the jumps of `timesi` and *less* retrace; `scriptc` has 65% more jumps than `futural` and
  *less* retrace. (Contour count keeps its other job, the point/complexity budget of §5.) Jump
  fraction also brackets the question: gothic is reported acceptable at 0.453, so `script` (0.340),
  `duplex` (0.410) and `simplex` (0.434) already sit below a figure judged fine, `italic` (0.492)
  and `times` (0.512) sit above it, and the two unexposed joined scripts sit at well under half of
  it. What is left is purely the hardware test, and it is still undone: PCM5102A with FLT tied L,
  `DESIGN.md:72`'s *"try H = low-latency later; may reduce ringing on sharp vector edges"*, and the
  slave's `square` pattern shipped specifically to expose it. Minutes, with a scope in front of you.

**Missing tool.** The study deferred to "judge it on a scope" ~14 times. Nothing in the repo can
render a PCM block to an image — `find . -iname "*test*"` returns nothing. ~60 lines on the laptop
that take `gen.block()` bytes and draw them with dwell-weighted brightness (alpha ∝ 1/segment
length) and retrace drawn distinctly would settle the brightness/flicker/contour questions gating
SVG, outline fonts, Kaleidoscope, Unfold and every warp — **with no board, no slaves and no
scopes.** It should probably be built before anything else here.

---

## 10. Recommended sequencing

Today is 2026-07-22. Show 2026-08-21. PCB order gate ~Aug 1, and `PLAN.md` still has KiCad, the
2-slave sync test, slaves 3–4, the overnight soak, the battery test and the venue rehearsal all
open, with Aug 19–21 an explicit fixes-only freeze. **The rig works.** Weight accordingly.

**Before Aug 1 — defend what works (hours, not sessions; items 1–3 and 5–6 are the §1 bugs).
Items 1, 2, 3, 5 and 6 are DONE and deployed as of 2026-07-22 — see the banner at §1. Item 0 was
added and done the same day, and item 4 followed the same evening: **all seven are now done.**

0. **DONE — compose accented letters.** A `.jhf` file holds printable ASCII and nothing else, so
   every non-ASCII character rendered as `?`: `render_text("Anémi")` produced a table
   *byte-identical* to `render_text("An?mi")`, verified on the board against the old module. Now
   NFD-decomposed — base glyph from the face, combining mark drawn from a 14-entry table in the
   face's own units, no-base characters folded to ASCII (`ß`→`ss`, curly quotes, dashes). Costs
   nothing at rebuild time (`Anémi` simplex 4.43 ms, inside the existing 3.3–8.9 ms band) and
   nothing at all in the hot loop. This was not a listed ask; it became one the moment an artist
   name had an acute in it.
1. **DONE** — cap `ae` by the table's rmax (§1.1). The `try/except` around `gen.block()` was
   deliberately **not** added: with the cap the only reachable exception is gone, and a bare
   `except` there would swallow the next one silently instead of leaving a traceback.
2. **DONE** — preset loader: filter, font gate, `.get` defaults, one commit (§1.3, §1.4).
3. **DONE** — reset `PatternGen.rot` when `rot_speed` hits 0 (§1.2).
4. **DONE — persist the live pattern.** `~/hype_state.json`, written by a 3 s debounced
   `persist_loop` on its own thread (never `os.replace` from the stream thread — it can block
   for tens of ms) and restored in `State.__init__` *before* the text table is built, so the
   first block out is already right. Validated by the same `clean_preset()` as a preset, so a
   truncated or hand-edited file degrades to defaults instead of stopping the daemon; measured
   at 4 disk writes per 120 POSTs. `stream_on` is deliberately excluded — a rig that boots
   silent after a power blip is worse than one that boots drawing. `--pattern` can now also
   name `text`, and only decides the first boot before a state file exists.
5. **DONE — one global rebuild mutex (§1.6); the board measurement is done (§9).** It goes around
   every table rebuild: `render_text`, font switch, and any future warp or SVG bake (board: warp
   bake 10.9–13.3 ms, a cold font switch up to 36 ms, all in the HTTP thread). Measured necessary
   **and sufficient** — 1 competing CPU-bound thread never crossed the 20 ms re-anchor threshold in
   4/4 runs; 2 always did, at 342–625 ms worst gap. ~10 lines. Do **not** pair it with
   `sys.setswitchinterval(0.0005)`: at 3 competing threads that measured 17–24 gaps >20 ms per 2 s
   against 3–7 at the default, trading rare catastrophic stalls for frequent ones that still
   re-anchor, and it doubles p50 even at 1 thread.
6. **DONE — re-assert the SYNC egress interface (§1.5).** Re-apply `IP_MULTICAST_IF` on a timer instead of
   only on send failure, log the egress interface when it changes, and write "AP up before daemon"
   into the show-day boot order. The board was misrouting its beacons over the USB tether, with a
   healthy-looking log — this was the difference between four scopes and none, and fixing it is
   what put the first audio the rig has ever delivered onto slave 121.

**After the PCB order, before the 4-slave soak — the cheap wins:**
7. The offline PCM→image renderer (§9). Laptop-side, touches the daemon not at all.
8. Two curated extra Hershey faces — **`cursive` and `scripts`** — the two lowest-retrace of all 32
   measured: jump fraction 0.196 over 10 pen-up jumps, against 0.340 for the `script` face already
   shipping, 0.434 over 17 jumps for simplex and 0.512 over 91 for `times` (§9). One `TEXT_FONTS`
   entry and one `<option>` each. The EMS single-line set (§4) is a separate, larger step, not a
   prerequisite. Re-save and re-test presets.
9. The affine fold — Scale/Translate/Skew into the existing per-block matrix (§6 Tier A). This is
   ~4 effects for ~10 lines and no new failure mode.
10. Laptop-side SVG bake + `kind="art"` (§5). ~40 lines board-side.

**After the show:** TTF outlines, the baked-warp pipeline and its dropdown, the clip player,
per-slave streams, a `z` column + Perspective, L-systems, MIDI-learn.

**Never:** the GLSL family (§7), per-sample Lua, drag-reorderable effect chains (osci-render spends
1613 lines on that UI, with a mouse), per-parameter LFO satellites (180 extra fields at 45 params).

One structural note in favour of doing any of this eventually: **the blast radius is already
contained.** `render_text` runs in the HTTP handler thread (`:857`, `:934`), so a malformed font or
artwork raises there and `ThreadingHTTPServer` absorbs it — the phone sees a failed request and the
stream keeps running. The only routes from bad content to a dead stream were the uncaught
`OverflowError` in §1.1 and GIL starvation from concurrent CPU-bound handlers — both now closed — and on the board
that second one is both sharper and simpler than the dev box suggested (§9). One competing
pure-Python thread is harmless: worst gap 7.4–7.9 ms, zero crossings of the 20 ms threshold in 4/4
runs. Two spike to 342–625 ms, three to as much as 1146 ms. Not the 33 ms / 183 ms the dev box
predicted at 1 and 3 threads. **A single global rebuild mutex is therefore sufficient on its own**,
because it caps concurrency at the one competing thread that measured clean. The exception is a
C-level GIL hold such as expat's (§5): it is not preemptible at all, so a large enough parse
crosses the threshold with nothing else running. **`sys.setswitchinterval(0.0005)` is retracted:**
at three competing threads it caps the worst gap at 34–41 ms but lifts >20 ms excursions from 3–7
to 17–24 per 2 s, so the re-anchor rate goes *up* 3–5×, and it raises p50 at one competing thread
from 1.80 to 3.70 ms. Close those two and the rest is genuinely additive.

---

## 11. Method note

osci-render `v2.8.10.8` cloned with the `osci_render_core` submodule and read directly — all 30
effect implementations, both parsers, `ShapeVoice`, the LFO engine and the plugin registration.
Expanded by a 33-agent workflow: 6 recon passes over the two codebases, 2 measurement passes
(CPython hot-path benchmarks and a Debian-trixie/arm64 package-index audit), 8 feature
assessments, 16 adversarial verifications and a completeness critic.

A later pass, still 2026-07-22, reached the board itself for the first time — adb over the USB
tether to a deployed `hype_controller.py` md5-identical to the repo copy — and re-ran the hot path
there: three rounds of block and rebuild benchmarks, a GIL-contention sweep, and all 32 Hershey
faces, expanded by a second 105-agent workflow (8 section audits, 85 proposed edits, 71 surviving
adversarial verification, 14 refuted). It overturned conclusions the first adversarial pass had let
stand:

| first-pass conclusion | board |
|---|---|
| dev-box number × an assumed 4–7× A53 factor | no such factor — the implied ratio runs 1.34× to 28.6× depending on which line you pick; the dev box's `math.sin` had been constant-folded by 3.14's tier-2 optimiser, which the board's 3.13.5 does not have |
| ~35 ns/sample for a `(mul+add)` pair, a `sin` and a call alike | ~127 ns for a single float op — and a `sin` is 3–4× a float op, not equal to it |
| a rebuild mutex *plus* `sys.setswitchinterval(0.0005)`, neither alone sufficient | the mutex alone is sufficient; the switch interval is harmful |
| 26 unexposed Hershey faces | 19 distinct after de-duplication, ~6 of them Latin-readable |
| baked warp 0.45–1.5 ms | 10.9–13.3 ms — same class as a rebuild, so behind the same mutex |
| on-board XML parse "close to the line" | 1.34 ms per 300 paths; bake offline for authoring and stage-failure reasons, not parse cost |
| contour count per redraw as the retrace metric | jump fraction, measured — and it does not rank like contour count |
| Liberation as the OFL fallback | not installed on this board; DejaVu or Quicksand |
| four pure-Python per-slave streams "uncomfortable" at 378 µs | 2.45 ms — 49% of budget, and it still fits |

The adversarial pass earned its keep. It killed a claimed 65–230 ms font-parse stall (wrong
library — 1.30 ms with the one actually installed), corrected the int16 overflow's trigger from
"tall strings" to "spin is required", reversed the recommendation on `abs(drift)`, found the
unvalidated preset font path and the `PRESET_FIELDS` data-loss bug, and established that God Ray is
an audio effect while Bloom does not exist. Nine of the ten headline numbers in the first-pass
assessments moved.

The load-bearing facts that survived: the effect catalogue is small and mostly bakeable; our
table-walk is architecturally better placed for effects than osci-render's sample stream; the
sample budget (§3) is the real ceiling — now measured rather than derived, at 534 distinct table
entries per redraw at the board's live 90 Hz and 480 at 100 Hz, with `TEXT_TABLE_POINTS = 2000`
fully used only at ~24 Hz and below; premium is not a licensing barrier; and the A53 scaling factor
(§9) is no longer an open assumption but a dead model — what everything actually rests on is that
no more than one CPU-bound thread ever competes with the stream loop.

The first four bugs in §1 were independently re-reproduced against the committed module before
being written down, and two of those were then re-run **on the board**, against the deployed
`/home/arduino/hype_controller.py`: `LIVE|SET|2026` + spin at amp 1.00 raised `OverflowError` at
block 13 (65 ms) and was safe at 0.85, and `PatternGen.rot` sat at 2.3248 rad after 37 spin blocks
and stayed there once `rot_speed` returned to 0. The exact overflow condition is
`amp > 32767/(32000·rmax)` — 0.859 for `LIVE|SET|2026`, and **0.824 for a lone `W`**, which is a
plausible thing to type. §1.5 and §1.6 came out of the board pass: §1.5 was confirmed against the
live daemon (PID unchanged since boot, `ip route get 239.7.7.7` resolving to `usb0` with the AP up),
§1.6 from the code plus the measured two-thread GIL cliff. Because the deployed file is
byte-identical to the repo copy, every bug in §1 is live on the running daemon.
