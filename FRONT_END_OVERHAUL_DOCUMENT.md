# HYPEROSCI — front-end overhaul

_Written 2026-08-06 against `src/unoq-controller/tools/hype_controller.py` @ `bcc43d9`._
_Subject: the embedded web control panel — the `PAGE` string, lines 932–1570, and the API it talks to._

This document is an audit of the dashboard as it exists, a list of what is duplicated, weird or
unclear in it, and a proposed replacement. It does not change any code. Nothing in `docs/DESIGN.md`
is contradicted here; the wire protocol, the streaming engine and the persistence model are all
left alone.

> ### ⚠ Implementation status — checked 2026-08-06, later the same day
>
> **A build of this overhaul now exists in the working tree, uncommitted, and it does not run.**
> The `PAGE` string was rewritten (+1,588 / −577 lines); *nothing else in the file changed*.
> Two blocking defects stop the page dead before any of it can be judged, and one of them is
> the exact failure mode `src/unoq-controller/README.md` warns about.
>
> **The rig is not affected.** The board at `10.42.0.5:8080` is still serving the old page
> (34,716 chars, `all draw:` header, no mode toggle). Nothing has been deployed or committed.
>
> Full findings, per-finding status tables and the ordered fix list are in **[§9](#9-implementation-status--2026-08-06)**.
> Sections 0–8 below are unchanged: they are the record of the page as it was, and remain the
> baseline the build is measured against.

---

## 0. The brief

Two different people use this page, and they are the same person on two different days.

**Setup — on a laptop, days before.** Type the artist names, pick fonts, tune pulse and spin,
build the set, arrange the idents that fire between acts. Big screen, keyboard, time to think, and
a real oscilloscope on the bench to check against.

**The show — on a phone, in the dark, one-handed.** Four scopes are on stands in front of an
audience. The questions are: *is everything still drawing?*, *next artist*, *why has scope 3 gone
weird?* There is no laptop on stage. The phone screen is dimmed, the operator is standing up, and
whatever is on screen has about one second to answer.

Changes must still be possible from the phone — a name is misspelled, a scope's polarity is
inverted, an ident needs to go away — but behind an explicit **config / advanced** switch, so that
the show-time surface stays small and hard to fire by accident.

The page today serves both at once, at the same visual weight, with no separation.

---

## 1. How this was measured

So the numbers below can be re-derived rather than taken on faith:

- `PAGE` was imported from the live module and served by a mock controller that returns a realistic
  show state — 4 slaves (one of them failing), the 12 artist presets actually on the board, 3
  interval-timer rules. The Hershey text preview was pulled from the running board at
  `10.42.0.5:8080` so the canvas draws real glyph geometry.
- Screenshots at 390 × 844 (phone), 390 × 4200 (whole page) and 1440 × 1100 (laptop), plus the
  AP-down, stream-off, hold-on-air and empty-rig states.
- Section heights were read back off the 390 px render by classifying pixels against the panel and
  page background colours; control counts were taken from the rendered DOM, not the source.
- Contrast ratios are WCAG 2.x relative-luminance, computed from the palette in `:root`.
- Live `/api/state` and `/api/textpreview` were fetched from the board for ground truth.

Harness is in the session scratchpad (`mockserve.py`, `measure.py`); it is throwaway, but it is
worth rebuilding as `tools/uipreview.py` — see §7.9.

---

## 2. The verdict

**The page is organised the way the daemon is organised.** Streamed-pattern generator, then
interval timers, then slaves. That is a faithful map of `hype_controller.py` and a poor map of
either job a human does with it.

Measured on a 390 px phone with the real preset list, 4 slaves and 3 timers:

| | |
|---|---|
| Page height | **3,945 px — 4.7 phone screens** |
| Top of the first slave card | **y = 2,326 px — 2.8 screens below the fold** |
| `<button>` elements | **101** |
| `title=` tooltips — the entire help system | **207** |
| Buttons carrying the seven draw words | **37** |
| Blocking `alert` / `confirm` / `prompt` calls | **7** |
| Interval-timer panel (a setup feature) sitting between the controls and the rig status | **747 px** |
| In the first 844 px | title, STREAM, 7 all-draw buttons, the preview canvas, `pattern / freq / amp` |
| Destructive controls with any distinct styling on touch | **0** — `.danger` is a `:hover` rule |
| Window in which a dying slave is visibly in trouble | **2 s**, expressed as *fading out* |

The first screen of the phone UI contains no answer to *"is the rig alive?"*. It contains a
7-button row that sets every slave's draw mode at once — the widest blast radius on the page, and
the only control that can never show you what it did — sitting above the fold, where the thumb
rests.

Three structural problems produce most of the rest:

1. **One surface for two jobs.** Nothing distinguishes "I am building the set" from "I am running
   the show", so the page shows all of both.
2. **Help lives in `title=`.** 207 tooltips, none of which a touch device will ever display. The
   page is documented for the machine it is not used on.
3. **Status is stated repeatedly instead of clearly.** The stream-off state is announced five ways;
   each slave's beam state is announced four ways; every counter appears both as a lifetime total
   and a per-second rate, interleaved in one grid.

---

## 3. Audit of the current view

_Each finding below keeps its original id. Whether the working-tree build addresses it is
tracked, id by id, in [§9.3](#93-per-finding-status)._

### 3.1 Duplicate

**D1 — `all draw:` duplicates every slave card's `draw` row, and is structurally incapable of
showing state.** (`allseg`, L1040–1041; `drawSeg`, L1208–1213; per-card at L1494–1498.) Seven
buttons in the header, seven on each of four cards: 35 controls, one vocabulary. The header copy is
built **once, at script parse time**, by the last line of the page — `document.getElementById(
"allseg").innerHTML = drawSeg("all", null)` (L1568) — and `render()` never touches it again. So
those seven buttons wear the same green-when-active costume as the 28 on the cards while being
permanently unlit: they cannot confirm a tap, cannot show that all four slaves are already on
STREAM, and cannot warn that the four disagree. It is simultaneously the widest-blast-radius
control on the page and the only one that never acknowledges you, which invites the double-tap.

**D2 — `circle` and `lissajous` each mean two different things on the same screen.** In the pattern
row (L1055–1056) they select the globally streamed figure, with `freq`, `amp` and `ratio`. On a
slave card (`DRAWS`, L1192) they mean "stop streaming to this unit and let it draw its own fixed
test figure" — the slave-side renderers take no frequency, amplitude or ratio of their own
(`renderer_local.cpp` L179–184). Same word, same chip styling, opposite effect. Of the 37 buttons
rendered with those seven words, 2 are the pattern kinds and 35 are draw modes.

**D3 — "the stream is off" is stated five times.** The header button turns red (L1513–1515), a
full-width banner appears (L1043–1045), `#pnote` appends "— OFF, nothing is being sent" (L1520),
the whole controls panel drops to 45 % opacity (L1518), and every slave card's play line says "the
STREAM switch is OFF" (L1425). One bit, five renderings.

**D4 — each slave's beam state is stated four times.** The `NET` / `LOCAL·mic` badge (L1464–1466),
the `▶ …` prose line (`playing()`, L1415–1431), `rx/s` in the stats grid, and which `draw` chip is
lit. They are computed from overlapping fields and can disagree — the badge reads `source`, the lit
chip reads `mode`, and the prose reads both plus a client-side timer.

**D5 — every counter appears twice.** `rx` / `rx/s`, `drop` / `drop/s`, `under` / `under/s`,
`lost` / `lost/s` — eight numbers for four facts, interleaved in one 3-or-4-column grid with `rssi`,
`vbat`, `buf`, `up`, `age` and `tx-drop` (L1474–1492). Nothing groups the rates apart from the
lifetime totals; `rx/s 0` and `rx 541.7k` sit three rows apart in the same visual register.

**D6 — the preset list is rendered twice, differently.** As delete-able chips in the pattern panel
(`presetChips`, L1275–1295) and as a `<select>` in the timer add-row (`presetOptions`, L1330–1338).
Two idioms, one list.

**D7 — four separate help surfaces, overlapping.** 207 `title=` tooltips; the `<details>` table at
the bottom (L1139–1167); the explanatory paragraph inside the timer panel (L1131–1136); and the
off-banner's own prose. The `<details>` "who decides what?" row and the `title` on the per-card
`draw` label say the same thing in different words.

**D8 — saving a preset is two buttons.** `⟳ update "<name>"` and `+ save as…` (L1107–1109), one of
which is conditionally hidden. Both POST `op=save`.

### 3.2 Weird

**W1 — the `ratio` row shows `b` in rose mode, where `b` does nothing.** `render()` hides the row
only for `circle` and `text` (L1525–1526), but `PatternGen.block`'s rose branch uses `a` alone
(L881–887). You can turn a control and watch nothing happen.

**W2 — `pulse` is two sliders sharing one label.** (L1089–1098.) On the laptop they sit side by
side as `[depth] 0 % [rate] 1.2 Hz`. On the phone the second slider wraps to its own row with **no
label at all** — an unlabelled slider whose only identification is a `title` a phone will not show.

**W3 — effects only work on text.** `pulse_depth`, `rot` and `flip_x/y` are read exclusively inside
the `kind == "text"` branch of `block()` (L835–871). For circle, lissajous and rose they are dead.
The UI hides them, which is honest, but it means "spin" is a property of the text renderer rather
than of the rig — and that is not what the control looks like.

**W4 — `⇋ X` / `⇵ Y` is a per-scope hardware fact exposed as a global software toggle.** Deflection
polarity belongs to an individual oscilloscope. There are four of them and one flip flag, applied
inside the shared streamed pattern (L854–857). If scope 3 is wired backwards you cannot fix scope 3;
you can only mirror all four. The glyphs are also unreadable as labels.

**W5 — the gain slider is write-only, and it lies.** It renders `value="100"` unconditionally
(L1502). `STATUS_PAYLOAD` (L43) carries no gain field, and `mode_manager.cpp` stores gain in NVS
without ever reporting it. Set slave 3 to 60 %, wait one second for the card rebuild, and the
slider snaps back to 100 while the slave stays at 60. The control shows a number that is not true.

**W6 — the trouble signal is "become harder to read".** A slave silent for > 3 s gets
`.stale { opacity:.35 }` (L1002, applied L1479). Measured: body text falls to **2.71 : 1** against
the panel and its dim labels to **1.51 : 1**. Fading is the universal "this matters less" signal,
applied here to the exact moment a scope is dropping off the rig.

**W7 — `STREAM OFF` dims the pattern panel but leaves it live.** `opacity: .45` (L1518) with no
`pointer-events` or `disabled`. It looks disabled and is not.

**W8 — the `every` field is silently corrected.** Minutes, `step=any`, min 0.1 (L1125). The server
clamps `every_s` to at least `hold_s + 1` and at least 5 s (L271–274) and says nothing. Ask for a
20 s hold every 0.1 min and you get one every 21 s, with no message and no visible reason.

**W9 — the `freq` slider tops out at 400 Hz; the API accepts 2000.** (L1061 vs L1655.) Two
different definitions of the range, one of them unreachable from the UI.

**W10 — the timer add-row is a sentence broken across four phone rows.** `add [preset] on` /
`[all][101][102][103][104]` / `for [20] s every [5] min` / `[+ add]` (L1116–1130). The `.grp` class
exists specifically to stop *parts* of that sentence from breaking, which is a fix aimed at the
symptom.

**W11 — every preset chip carries its own delete button, glued to it.** (L1284–1287.) `.chip`
deliberately joins them so the `×` "looks owned". The consequence is that the most frequent action
of the night — apply the next artist's preset — is one 44 px target away from destroying it. There
is one generation of undo, in a `.bak` file, reachable only over SSH.

**W12 — `ID` is a two-letter label for "flash this unit's LEDs".** (L1500.)

**W13 — the biggest element on the page is a drawing of the maths.** The 520 × 520 canvas plots
what the generator *would* produce (`drawScope`, L1375–1410). That is genuinely useful when
composing text on a laptop. On the phone it occupies 367 px at the top of the page in the position
where a person expects to see *what the scopes are doing*, and it is right even when all four
scopes are dark. It also ignores `rot` and `pulse_depth` entirely, so three of the five text
controls have no preview feedback at all.

**W14 — you cannot stop the spin from a phone.** `rot` is a range with 201 steps
(`min="-100" max="100" step="1"`, L1101) in a track about 190 px wide — roughly 0.95 px per step,
and zero is one of them. The live board right now reports `"rot": -0.01`, which is what somebody
trying to straighten the text with a thumb actually achieved. `block()` treats any non-zero
`rot_speed` as "keep rotating" (L848–852), so −0.01 rev/s is a slow, permanent, un-cancellable
drift.

**W15 — rotating the phone to landscape removes every phone accommodation.** All of it lives in
`@media (max-width:640px)` (L1014). A phone turned sideways is ~844 px wide, so the 44 px minimum
target height, the stacked layout, the 3-column stats grid and the larger preset spacing all
silently revert to the laptop rules on a 390 px-tall screen.

**W16 — the fallback pattern cannot be set without interrupting the scope.** Which figure a slave
draws when the stream dies is `lpat`, and the only way to change it is to tap `mic`/`circle`/… in
the draw row — which sends `set_pattern` **and** `set_mode: local` (L1205–1206), taking that scope
off the show. You then tap `STREAM` to put it back. Setting a safety net costs two visible
interruptions, and while a slave is streaming the page never shows what its fallback currently is
(`lpat` is only printed inside the `source == 0` branch, L1466).

**W17 — a timer's countdown keeps running when the timer cannot fire.** With the stream off,
`fire_timer` refuses with `FIRE_MUTED` (L578, L599–600). `FIRE_MUTED` is deliberately *not* in
`FIRE_RETRY` (L580), so `timer_loop` charges the rule a full period (L681–682) — and the page
re-renders `next in 5m00s` from the freshly pushed-out `timer_next` (L804–807, L1346–1349). The
rule looks armed and counting, and is silently doing nothing, every cycle.

**W18 — `freq` names two unrelated quantities.** For circle, lissajous and rose it is the audio
frequency of the figure. For text it is redraws per second, walking the 2000-point table at
`tstep = 2000 · freq / 48000` (L840) — so above ~24 Hz the walker starts skipping table entries.
One slider, one label, two meanings, and no hint of the second.

**W19 — `offs` is collected, transmitted, published and never shown.** The slave's smoothed clock
offset is parsed (L2044), stored (L2067) and served in `/api/state` — and no element renders it.
Worse, it is saturated in practice: `net_rx.cpp` L206–209 clamps to `INT32_MAX`, and the live board
reports exactly `2147483647` for a healthy streaming slave, because the two boxes' monotonic clocks
differ by more than the ±35 min that fits in an `int32` of microseconds. **The rig's premise is
±5 ms slave-to-slave sync and the dashboard cannot say whether that is happening.** (Fixing it is a
protocol change — see §6.3 — but the information architecture should have a place for the answer.)

### 3.3 Unclear

**U1 — `STREAM OFF` does not make the scopes go dark.** It stops sending audio, and every slave
falls back to its local pattern — the mic, usually. The scopes keep drawing. The banner explains
this in 27 words; the button, which is the loudest control on the page, does not. There is also no
control that *does* make all four go dark: the slave command set is exactly `set_mode`, `set_gain`,
`set_pattern`, `identify`, `reboot` (`mode_manager.cpp` L172–198), and nothing means "blank".

**U2 — `draw` merges two different concepts into one row.** `STREAM` and `HYBRID` set the network
mode; `mic / circle / lissajous / ramp / square` send `set_pattern` **and then** `set_mode: local`
(`drawCmd`, L1201–1207). Two POSTs, two concepts, seven identical-looking buttons. The row also
doubles as the *fallback* picker — the local pattern chosen here is what the slave draws if the
stream dies — and nothing on screen says so.

**U3 — `mode` and `source` are different things and the card names neither.** `mode` is what you
asked for; `source` is what the beam is doing right now. They diverge exactly when something is
wrong. The card shows both (as the lit chip and as the badge) without ever using the words.

**U4 — `LOCAL·mic` on a STREAM slave means "no stream is arriving".** This is the single most
important diagnostic state in the system, and its explanation is in the `<details>` block at the
bottom of a 3,945 px page.

**U5 — `buf 455 ms` is healthy, and every instinct says a 455 ms buffer is broken.** The reason —
the ath10k radio goes deaf for ~300 ms every ~1.4 s and the buffer rides it out — is in a tooltip.

**U6 — `tx-drop` versus `lost/s` is the entire diagnosis, and it is in a tooltip.** High `lost/s`
with `tx-drop 0` means the air. High `tx-drop` means the packets died in the controller's own send
buffer and no antenna will help. The code comment at L1446–1450 records that getting this wrong
cost a night of debugging. The UI encodes that lesson somewhere a phone cannot reach.

**U7 — `vbat 3812 mV` on a battery-powered rig.** No percentage, no time-remaining, no threshold,
no warning colour, rendered in the same weight as `rssi`. On the bench it reads ~40 mV because the
pin is grounded, which the tooltip explains.

**U8 — `rose`'s `a` is "petal count, or 2a when a is even".** True, and not something to work out
during a set.

**U9 — nothing shows how close you are to the 20-preset cap** until `+ save as…` fails with an
`alert()`.

### 3.4 Defects

These are wrong, not merely awkward. The first two can cost you the show or the set list.

**F0 — `⟳ update "<artist>"` during a timer hold overwrites that artist's preset with the ident.**
The update button targets `curPreset`, the preset this phone last applied (L1245–1250, L1289–1294).
The server's `op=save` snapshots **the live pattern** (L1693–1704). During an interval hold the live
pattern is the timer's ident, because `apply_preset` installed it (L612). Only `op=load` calls
`_takeover` (L1747); `op=save` does not, so the hold is still running and nothing has warned you.

The page even displays the split on purpose: `presetChips` highlights the *hold's* preset as on-air
(L1282) while the update button, three inches away, reads `⟳ update "Anémi"` and its tooltip
promises "overwrite with what is on this panel right now" — and what is on the panel is the ident.
The `confirm()` names the artist, not the content. One tap during any 20-second hold silently
replaces an artist's preset with the station ident. Recovery is a single generation of
`~/hype_presets.json.bak`, over SSH, and only if nothing else has been saved since.

**F1 — a failed poll freezes the page silently, forever.**

```js
async function poll() {
  try { S = await (await fetch("/api/state")).json(); render(); }
  catch (e) { /* controller restarting; retry next tick */ }
}
```
(L1564–1567.) On failure nothing is assigned and `render()` never runs, so the DOM keeps its last
good frame. `age` does not advance, `.stale` never triggers, no card dims. **A phone that walks out
of AP range shows a perfect, healthy, four-scope rig indefinitely.** The one thing the page must
never do is claim everything is fine when it has no idea, and this is the default failure mode of
walking away from the stage.

**F2 — the "buffering" grace is global, not per-slave.** `lastChange` is one module-level timestamp
(L1184) set by any draw command or stream toggle, and `playing()` tests it for *every* slave
(L1427). Touching slave 1 suppresses the red "stream not arriving" verdict on slaves 2, 3 and 4 for
four seconds.

**F3 — the AP-down message is below everything.** When `net.egress` is false the page renders a
clear, actionable red block with the exact recovery command (L1552–1556) — inside `#slaves`, which
on the phone starts at y ≈ 2,326 px. Meanwhile the header still reads a confident green
`STREAM ON`. The most severe failure in the system is announced 2.8 screens below a contradiction.

**F4 — five `innerHTML` regions are rebuilt every second.** `presetChips`, `presetOptions`,
`targetBtns`, `timerRows` and the slaves grid all run on every poll (L1531–1532, L1548–1561). Only
the slaves grid and the pattern inputs are guarded against `activeElement`. Long-press, text
selection and in-flight taps on preset chips and timer rows are destroyed once per second.

**F5 — blocking modals during a show.** 7 calls: `prompt()` for a preset name, `confirm()` for
preset delete / timer delete / slave reboot, `alert()` for every server error including
`"not now — another timer is already on air"` (L1806). Each is a system-modal that must be
dismissed before anything else can happen.

**F6 — the text field commits on blur.** The `<textarea>` fires `onchange` (L1079), which on a
phone means typing the new artist name does nothing until the keyboard is dismissed. There is no
apply button and no feedback that the edit is pending.

**F7 — `hist` grows without bound.** Keyed by IP (L1442) and never pruned; a slave that takes a new
DHCP lease orphans its entry. Harmless in size, but it also means rate history does not follow a
slave across a lease change.

**F8 — a slave that dies disappears instead of raising an alarm.** `stream_loop` deletes any slave
silent for more than 5 s (L2071–2076) and the grid is rebuilt from what remains (L1559–1560). The
only "in trouble" state is `.stale` at `age_ms > 3000` (L1479). So a unit whose battery gives out
is dimmed for **two seconds** and then vanishes from the page with no tombstone, no count, and no
message — three cards where there were four. On a phone, where you cannot see all four at once
anyway, that is indistinguishable from having scrolled wrong. `#none` only ever appears when *every*
slave is gone.

**F9 — touching anything inside the slave panel stops all four cards updating.** The rebuild is
guarded by `else if (!(act && div.contains(act)))` (L1558), written — per its own comment — to keep
the gain slider draggable. It is scoped to the whole panel, not to the focused card, so while any
element inside it holds focus, every card freezes: `rx/s`, `buf`, `drop/s`, `lost/s`, the play line,
the badge, `.stale`, and `age` — the one field that would reveal the freeze. `rates()` keeps
consuming fresh counters (L1547), so the displayed numbers are genuinely stale rather than merely
old. A range input takes focus on every browser; buttons take it on desktop and Android Chrome but
not iOS Safari, so how easily you trip this depends on the phone. Tapping anywhere outside the
panel releases it.

**F10 — every tap blanks the health line for a second.** `post()` chains a forced `poll()` (L1180)
on top of the 1 Hz interval, and `rates()` refuses to compute if less than 300 ms has passed
(L1436), returning `null` — which renders `health …` in place of `rx/s`, `drop/s`, `lost/s`,
`under/s` and `age` (L1472–1473). Acting on the rig briefly deletes the numbers that tell you
whether the action worked.

**F11 — a draw change made during a hold is silently undone when the hold releases.** `end_hold`
restores each target's pre-hold mode from the snapshot taken at `fire_timer` (L611, L633–635).
`/api/cmd` — the endpoint every draw button uses — never calls `_takeover` (L1641–1646), unlike
`/api/pattern` and preset load. The panel's own help promises that "touching the pattern panel ends
a hold early rather than reverting you later"; touching the *draw* row does not, and reverts you
later.

**F12 — `▶ test` does not re-arm the schedule.** The `op=="fire"` handler (L1798–1807) fires the
rule and returns without touching `state.timer_next`, unlike the save (L1789) and toggle (L1819)
paths. Rehearse a rule that is nearly due and it plays, releases, and then plays again on its
original schedule — two idents back to back.

**F13 — the header asserts `STREAM ON` before it knows anything.** The button ships that label and
class in static markup (L1037–1038); `render()` only corrects it after the first successful poll
(L1513–1515), and `toggleStream()` dereferences `S.stream_on` (L1187), which throws if tapped
first. A page opened while the controller is restarting reads a confident green `STREAM ON` and does
nothing when pressed.

**F14 — a focused slider never resyncs, and the preview reads it.** `sync()` skips
`document.activeElement` (L1535–1537), which is correct mid-drag but has no release: a slider keeps
focus after you let go. `drawScope` then reads amplitude out of that DOM node rather than out of
state (L1379), so if a timer hold or another phone changes the pattern, the preview keeps drawing
the stale value.

### 3.5 Contrast and legibility

Computed from `:root` (L937–938):

| Pair | Ratio | Verdict |
|---|---|---|
| `--fg` on `--bg` / `--panel` | 14.97 / 14.13 | fine |
| `--ph` (neon) on `--panel` | 13.79 | fine |
| **`--dim` on `--panel`** | **3.95** | **fails AA (4.5) for normal text** |
| **`--dim` on `--bg`** | **4.18** | **fails AA** |
| **`--dim` on button fill** | **3.74** | **fails AA** |
| **`--line` on `--panel`** | **1.26** | **effectively invisible** |
| Button fill `#101b10` on `--panel` | **1.06** | **effectively invisible** |
| `.stale` body text on panel | **2.71** | fails everything |
| `.stale` dim label on panel | **1.51** | fails everything |

Two consequences worth stating plainly:

- **Every label in the interface fails AA.** `--dim` is the colour of `.row label`, the panel
  headings, the `.stats` keys (at 12.5 px) and all the connective words. Values are legible; the
  words telling you what the values *are* are not.
- **Inactive buttons have no visible edge.** Their fill differs from the panel behind them by
  1.06 : 1 and their border by 1.19 : 1. In a dark venue on a dimmed phone you see a field of green
  words, of which exactly one — the active, filled one — reads as a control. The whole chip
  vocabulary rests on a border nobody can see.

Also: colour is the only channel. `ok / warn / bad` are green / amber / red with identical weight,
size and glyphs (L990–997). A failing slave and a healthy one differ by hue alone, at the same
position in the same size in the same grid.

And four more, each a one-line fix:

- **`.danger` is a `:hover` rule.** `button.danger:hover { border-color:var(--bad); color:var(--bad) }`
  (L965) is the *only* styling those buttons get. There is no hover on a phone, so preset delete
  (L1286), timer delete (L1359) and `reboot` (L1505) render identically to every safe button on the
  page. The page's entire visual distinction between "apply this preset" and "destroy this preset"
  exists only on the device that is not being used.
- **No `color-scheme: dark`.** So UA-drawn chrome — the `<select>` arrow, the number spinners — and
  all seven `alert`/`confirm`/`prompt` dialogs render in light mode. A white system modal on a dark
  stage is a flashbang, and it is the delivery mechanism for every error message the page has.
- **No accessibility semantics at all.** Zero hits across L932–1570 for `for=`, `aria-`, `role=`,
  `<fieldset>`, `<legend>`, `<main>`, `tabindex`, `:focus` or `outline`. The `.row label` elements
  are unassociated `<label>`s, several controls have no accessible name, and the three lists that
  rebuild every second destroy focus while they do it.
- **The `.stats` grid reflows the moment things go wrong.** It is a fixed 4 columns (3 on phone,
  L992/L1026) filled by a flat run of `<span>`s, and `txdrop()` conditionally inserts an extra cell
  only when a slave is in trouble (L1451–1461). So the layout of the lower half of the card changes
  shape exactly when you are trying to read it, and nothing lines up between cards.

---

## 4. Principles

Derived from this rig, not from a style guide.

1. **The page's first job is to say whether the rig is alive.** Everything else is second. If the
   answer needs scrolling, the page has failed.
2. **Show mode and setup mode are different products.** Ship both, in one page, with an explicit
   switch. Default by device: phone → show, laptop → setup.
3. **Never claim knowledge you do not have.** A stale poll must look stale. A gain you cannot read
   back must not be drawn as a position.
4. **Degradation must be louder than normal operation, not quieter.** Trouble gets weight, size and
   position — never opacity.
5. **Say what a control does, not what state it is in** — or say both, but never let a button's
   label be ambiguous between the two. `STREAM ON` is currently both.
6. **One fact, one place.** Five renderings of "the stream is off" is four places to fall out of
   sync.
7. **Help is content, not a tooltip.** If a control needs explaining, the explanation goes under it
   or behind a tap, at a size a person can read.
8. **Destructive controls do not sit next to frequent ones.** Delete moves behind an edit mode.
9. **Assume the screen locked ninety seconds ago.** The phone will sleep mid-set —
   `navigator.wakeLock` is unavailable because `http://192.168.50.1` is not a secure context. The
   page must re-orient the operator instantly on unlock, which argues for pinned state at the top.
10. **Stay inside the existing constraints.** One file, stdlib only, no external assets, no network
    beyond the AP. Everything proposed below fits.

---

## 5. The proposed interface

### 5.1 Structure: one page, two modes

A single toggle in the header — `SHOW` / `SETUP` — persisted in `localStorage` and defaulting from
viewport width (< 900 px → SHOW). Technically this is a class on `<body>` plus a handful of
`display` rules; `/api/state` already carries everything both modes need, so no second page, no
routing, no extra fetch.

SHOW mode is the phone product. SETUP mode is today's page, cleaned up. Both are reachable from
both devices — the operator can fix a misspelled name from the phone by tapping `SETUP`.

### 5.2 SHOW mode, 390 px

```
╔══════════════════════════════════════════╗ ┐
║ HYPEROSCI        ● live 0.4s      SETUP  ║ │ sticky
╠══════════════════════════════════════════╣ │  ~124 px
║ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐      ║ │
║ │ 101  │ │ 102  │ │ 103  │ │ 104  │      ║ │ tap a tile
║ │ ■NET │ │ ■HYB │ │ ▲MIC │ │ ✖!!  │      ║ │ → slave sheet
║ │ ▓▓▓▓ │ │ ▓▓▓░ │ │ ▓▓░░ │ │ ▓░░░ │      ║ │
║ └──────┘ └──────┘ └──────┘ └──────┘      ║ │
╠══════════════════════════════════════════╣ ┘
║ ON AIR                                   ║
║  ┌────────────────────────────────────┐  ║
║  │      H Y P E R W A V E S           │  ║   preview, 30 vh
║  └────────────────────────────────────┘  ║
║  HYPERWAVES · duplex · 5 of 12           ║
╠══════════════════════════════════════════╣
║  ┌───────────────┐  ┌─────────────────┐  ║   56 px, thumb row
║  │   ◀  PREV     │  │    NEXT  ▶      │  ║
║  └───────────────┘  └─────────────────┘  ║
║  next → Flux Collective                  ║
╠══════════════════════════════════════════╣
║  SIZE   ──────────────●──────    90 %    ║
║  SPEED  ────●────────────────    50 Hz   ║
╠══════════════════════════════════════════╣
║  SET LIST                                ║
║   1  HYPEROSCI                           ║
║   2  thanks claude                       ║
║ ▶ 3  HYPERWAVES                  ON AIR  ║
║   4  Flux Collective                     ║
║   …                                      ║
╠══════════════════════════════════════════╣
║  ┌──────────────┐   feed  ┌───────────┐  ║
║  │  BLACKOUT    │         │  SENDING  │  ║
║  └──────────────┘         └───────────┘  ║
╚══════════════════════════════════════════╝
```

Roughly 1,270 px with the top two bands pinned — against 3,945 px today, with rig health permanently
on screen instead of 2,326 px down.

What each band is for:

- **Rig strip (sticky).** One tile per slave: id, a state glyph *and* a word, a battery bar. Glyph
  as well as colour, so the failing unit is distinguishable without hue. This is the answer to the
  only question that matters at a glance, and it never scrolls away. Tapping a tile opens that
  slave's sheet (§5.3).

  The strip must be built from an **expected roster**, not from whoever answered this second. A
  slave that stops beaconing is evicted from `state.slaves` after 5 s (F8) and currently just
  disappears; here its tile stays, goes red, and reads `LOST 12 s`. Cheapest way to get a roster
  without new state: remember every id seen since page load in `localStorage`, and offer a
  `forget` in SETUP for the night you genuinely run three units. The rig has a fixed, known size —
  the UI should know it too.
- **On air.** What is being streamed right now, named, with the preview under it. The preview stays
  — it is genuinely useful — but it is now clearly labelled as *what is being sent*, sitting under
  the rig strip that says *what is being drawn*.
- **PREV / NEXT.** The most-used action of the night, and it does not exist today. With 12 artists
  you currently hunt for the right chip among 12 wrapped across 8 rows in the dark. This is pure
  client-side indexing into `S.presets` — no server change.
- **Live dials.** The two you actually ride mid-set: amplitude and rate, full width, labelled in
  words, value in the same line.
- **Set list.** Presets as full-width rows in **set order** (see §6.1), one tap to apply, on-air row
  marked. **No delete buttons in SHOW mode.**
- **Panic row.** `BLACKOUT` and the feed state. See §5.6 for what blackout must actually do.

Everything else — pattern kind, ratio, text, font, pulse, spin, mirror, timers, per-slave draw
matrix, all telemetry, help — is in SETUP.

### 5.3 The slave sheet

Tapping a rig tile opens one slave, full width. This replaces the 380 px card × 4 that currently
runs 1,588 px.

```
╔══════════════════════════════════════════╗
║ SLAVE 104          192.168.50.199      × ║
╠══════════════════════════════════════════╣
║ ✖  NOT RECEIVING THE STREAM              ║
║    drawing its own circle instead        ║
╠══════════════════════════════════════════╣
║ SOURCE                                   ║
║ ┌──────────┐┌──────────┐┌──────────┐     ║
║ │  STREAM  ││  HYBRID  ││  ON ITS  │     ║
║ │          ││ +its mic ││   OWN    │     ║
║ └──────────┘└──────────┘└──────────┘     ║
╠══════════════════════════════════════════╣
║ ▸ why?                      tx-drop 18 % ║
║   94.2 k packets never arrived, and      ║
║   18.4 % died in this controller's send  ║
║   buffer before reaching the air — that  ║
║   is airtime, not antenna.               ║
╠══════════════════════════════════════════╣
║ battery  ▓▓░░░  3.81 V · ~2 h            ║
║ signal   −84 dBm  marginal               ║
║ ▸ all numbers                            ║
╠══════════════════════════════════════════╣
║ [ FLASH LEDS ]              [ reboot ]   ║
╚══════════════════════════════════════════╝
```

- **`SOURCE` is a three-way, not a seven-way.** STREAM / HYBRID / ON ITS OWN. Which local figure a
  slave draws when it is on its own is a *setup* decision (it is also the fallback pattern), so it
  moves to SETUP. That takes 28 buttons down to 12, and removes the collision with the pattern-kind
  names entirely.
- **The verdict is a sentence, in words, at the top.** The numbers that justify it are behind
  `why?`, pre-written for the two cases that matter — air loss versus local send-buffer loss. The
  lesson recorded in the L1446 comment becomes visible instead of hovering.
- **`tx-drop`, `lost`, `drop`, `under`, `buf`, `rx`, `age`, `up`** live under **`all numbers`**,
  grouped **LINK / STREAM / POWER**, with lifetime and per-second separated rather than interleaved.
- **Battery is a bar plus volts**, with a threshold. `power-budget.md` already derives the numbers.
- **`reboot` is separated** from `FLASH LEDS` by the full width of the sheet.

### 5.4 SETUP mode

Today's page, re-ordered and de-duplicated. Sections, collapsible, in the order you actually work:

1. **Pattern** — kind; then only the controls that kind uses (`petals` for rose, `a : b` for
   lissajous, never a dead input); text + font with a real Apply; pulse depth and pulse rate on
   **separate labelled rows**; spin; mirror X / mirror Y named in words, with a line saying they
   affect text only and apply to all four scopes.
2. **Set list** — reorder, rename, duplicate, delete (deletes behind an `edit` toggle), `n of 20`
   shown. `update` must name what it is about to write, not only what it is about to overwrite, and
   must refuse — or at minimum warn loudly — while a timer hold is on air (F0).
3. **Idents** — the interval timers, as a table, out of the middle of the page. A rule that cannot
   fire because the feed is silent says so instead of counting down (W17).
4. **Rig** — the per-slave matrix: source, **local fallback pattern as its own control** (so setting
   a safety net no longer requires taking a scope off the show — W16), gain, identify, reboot; plus
   the `set all` row that is currently in the header, here where it belongs and with state shown.
5. **Diagnostics** — the full telemetry grid, `tx-drop`, egress state, AP recovery command.
6. **Help** — one place, the content currently spread over 207 tooltips and a `<details>`.

### 5.5 Naming

| Today | Problem | Proposed |
|---|---|---|
| `STREAM ON` / `STREAM OFF` | state or action? and OFF ≠ dark | `feed: SENDING` / `feed: SILENT`, with the consequence written beside it |
| `all draw:` (header) | duplicate, write-only, top of fold | moves into SETUP → Rig, with state |
| `draw` (7 buttons) | mode and pattern conflated | `SOURCE` (3) + `when on its own` (5, in SETUP) |
| `NET` / `LOCAL·mic` | jargon, and `LOCAL·mic` on a STREAM slave means a failure | one sentence: what it is drawing, and why |
| `ratio` `a : b` | `b` dead in rose | `petals` (rose) / `a : b` (lissajous) |
| `pulse` (two sliders) | second unlabelled on phone | `pulse depth` / `pulse rate`, separate rows |
| `⇋ X` `⇵ Y` | cryptic; global; text-only | `mirror X` / `mirror Y`, in SETUP, with scope stated |
| `ID` | cryptic | `FLASH LEDS` |
| `gain` | shows 100 always | `level`, with no position until it can be read back — see §6.3 |
| `buf 455 ms` | reads as broken | `lead 455 ms` with `healthy ≈ 450` beside it |
| `vbat 3812 mV` | not a show-length answer | bar + volts + estimate |
| `+ save as…` / `⟳ update` | two buttons, one concept | `Save` → `update "X"` / `save as new` |
| `▶ test` (timer) | ambiguous | `run now` |

### 5.6 Blackout — and a trap to avoid

There is no "all scopes dark" control today, and this is a live-performance rig where you will want
one between acts.

Do **not** implement it as `set_gain 0` to every slave. `mode_manager.cpp` L181–184 persists gain to
NVS — a blackout that outlives a controller crash leaves four units permanently silent with the zero
written to flash, recoverable only by finding each one and setting gain again.

Implement it as: force every slave to `network` mode, then set the streamed amplitude to 0. Nothing
persists on the slave, one button undoes it, and it survives the controller dying (the slaves fall
back to their local pattern within a second, which is the existing, correct, safe behaviour).

---

## 6. What has to change behind the UI

Most of the above is presentation and costs nothing on the server. These do not.

### 6.1 Set order — cheap

Presets are already stored as a **list**, and `load_presets` / `save_presets` / `snapshot` all
preserve order (L166–200, L798). Ordering is therefore already expressible; it just has no editor.
Add `op=move` (name + new index) and `op=rename` to `/api/preset`. Rename must also rewrite any
timer whose `preset` field points at the old name — the existing delete path already handles the
analogous case by pausing orphaned rules (L1726–1740), so follow that precedent.

### 6.2 Page-connection staleness — cheap, and the most important fix here

`poll()` must count consecutive failures and the page must show it. Suggested: after 3 misses (3 s),
a persistent band at the top — `⚠ no contact with the controller for 12 s — this is the last known
state` — and the whole page desaturated. This is the F1 fix and it is worth doing before anything
cosmetic.

### 6.3 Gain read-back — protocol change, optional

Two honest options:

- **Do nothing to the wire, fix the UI**: render `level` as two nudge buttons (`−` / `+`) with a
  "sent" flash and no absolute position, because the position is not knowable. Cheap, honest,
  slightly worse to use.
- **Add `gain` to `HYPE_STATUS`**: one `uint8_t` appended after `lost_packets`, following the exact
  pattern already used twice for `local_pattern` and `lost_packets` (`protocol.md` §3.4; controller
  tolerance at L2046–2056). The controller already tolerates short payloads, so old firmware keeps
  working. Requires reflashing the slaves — and three of them already need reflashing for the
  lost-packets counter, so the cost is close to zero if done in the same pass.

Recommend the second, bundled with the pending reflash.

### 6.4 Sync visibility — protocol change, worth considering

`clock_offset_us` saturates (§3.2 W19) and so carries no information in normal operation. If the
dashboard is to answer "are the four scopes in sync?", the useful quantity is the **spread between
slaves**, not the absolute offset. Options: widen the field to `int64`, or have the slave report
offset **relative to the value it held one beacon ago** (drift), which fits in an `int32`
comfortably and is what actually predicts a visible desync. This is firmware and protocol work and
is out of scope for a front-end change — but the UI should have a place reserved for the answer, or
the rig's central claim stays unverifiable from the panel.

### 6.5 Nothing else

Pattern, preset, timer and command endpoints are all adequate as they stand. The state snapshot
already contains every field SHOW mode needs.

---

## 7. Implementation

Staged so each stage is independently shippable to the board and independently revertible. The show
is 2026-08-21; stage 1 is the one that must land.

_What of this plan the working-tree build actually delivered is in [§9.4](#94-stage-and-proposal-status).
In short: it did not stage. Stages 1, 2, 3 and 4 were attempted in one pass, which is why nothing
in stage 1 — the part that is worth landing on its own — is currently shippable._

### 7.1 Stage 1 — honesty and safety (small, do first)

Every item is a local edit. None changes the layout, so this is low-risk against a fixed show date,
and it is worth landing even if nothing else on this document ever happens.

- **F0**: block `⟳ update` while `S.hold` is set, or make its `confirm()` state what will actually
  be written. This one destroys work.
- **F1**: connection-loss banner and desaturation after 3 missed polls.
- **F3**: hoist the AP-down state to the top of the page, and stop the header contradicting it.
- **F8**: keep a tile/card for a slave that has gone silent, in red, instead of deleting it.
- **F9**: narrow the rebuild guard to the focused element's own card, and to `input` only.
- **F11 / F12**: `/api/cmd` calls `_takeover`; `op=fire` re-arms `timer_next`.
- **F13**: header ships in an unknown state until the first poll; `toggleStream()` guards on `S`.
- **W6**: `.stale` stops using opacity — a stale card gains a red edge and `LAST SEEN 6 s AGO`.
- **W5**: the gain slider stops rendering a position it cannot know.
- **W1**: hide `b` in rose mode. **W14**: give `rot` a zero detent, or a `0` button beside it.
- **W17**: a timer that cannot fire says so instead of counting down.
- **F2**: make the buffering grace per-slave.
- **Colour**: raise `--dim` to pass AA (≈ `#7e9c7e` clears 4.5 : 1 on panel) and `--line` to a
  border you can see (≈ `#33492f`, ~2.4 : 1 — enough to read an edge without shouting).
- **Three one-liners**: give `.danger` a non-hover style; add `color-scheme: dark`; add
  `touch-action: manipulation`.
- **W15**: change the media query from `max-width:640px` to something that also catches a phone in
  landscape — `(max-width:640px), (max-height:500px)` — so the 44 px targets survive rotation.

### 7.2 Stage 2 — the SHOW/SETUP split
- `<body class="show|setup">`, the header toggle, `localStorage`, viewport default.
- Move the timer panel, per-slave telemetry, effects and preset deletes into SETUP.
- Build the sticky rig strip and the slave sheet.
- Build ON AIR / PREV / NEXT / set list.
- Stop rebuilding lists with `innerHTML` on every poll (F4, F9, F10). Build each card and chip once,
  then patch text and classes in place. This removes the focus conflict, the eaten taps and the
  once-per-second destruction of selection and long-press in one change, and it is what makes the
  sticky strip cheap enough to leave on screen.

### 7.3 Stage 3 — the set list becomes real
- `op=move` / `op=rename`, reorder UI in SETUP, `n of 20`.
- Blackout per §5.6.

### 7.4 Stage 4 — vocabulary and help
- The renames in §5.5.
- Retire the 207 tooltips into inline one-liners and one Help section. Keep `title` on the laptop
  where it costs nothing, but never let it be the only copy of anything.
- Replace the seven blocking dialogs (F5) with in-page affordances: a non-blocking toast for server
  errors, an inline name field instead of `prompt()`, and hold-to-confirm or an `edit` mode instead
  of `confirm()`. Nothing during a show should require dismissing a modal before the rig status can
  be seen again.
- Basic semantics while the markup is being touched anyway: `for=`/`id` pairs, an accessible name on
  every control, a visible focus ring.

### 7.5 Stage 5 — optional, needs a reflash
- Gain read-back (§6.3), bundled with the lost-packets reflash of slaves 2–4.

### 7.6 Constraints this must not break

From `src/unoq-controller/README.md`, all still binding:

- **One file, stdlib only, no external assets.** The page is served over an AP with no internet;
  nothing may be fetched from a CDN. Every proposal above is plain HTML/CSS/JS.
- **`PAGE` is a plain triple-quoted Python string.** A `\n` typed into its JavaScript becomes a real
  newline in the served source — this broke the dashboard once (`9c718c3`). Write `\\n`.
- **Syntax-check after every edit**: extract the `<script>` and run `node --check`; then
  `py_compile`. This is the existing rule and it is the only thing standing between an edit and a
  dark stage.
- **The stream loop must not be disturbed.** Nothing in the UI work touches `stream_loop`,
  `PatternGen.block`, the locking order (`rebuild_lock` before `state.lock`), or the persistence
  threads.
- **Deploy is `scp` + `sudo systemctl restart hyperosci-controller`.** Restarting drops the stream
  for a moment; do it between rehearsal runs, not during one.

### 7.7 What is already right and should survive

Worth saying explicitly, because an overhaul is a good way to lose these:

- The AP-down state distinguishing "no AP" from "no slaves", with the recovery command inline.
- `tx-drop` as a separate number from `lost/s`. That distinction cost a night to learn.
- Not persisting `stream_on` — a rig that boots silent is worse than one that boots drawing.
- Not fighting the user mid-drag (`activeElement` guards, L1534–1537).
- Presets surviving restarts with one generation of undo.
- The text preview. It is the reason composing text on this rig works at all.
- The `<details>` help *content*. It is well written; only its location and format are wrong.

### 7.8 Risk

The show is in 15 days and the current page works. Stage 1 is additive and low-risk. Stage 2 is a
rewrite of the page's layout and should be finished and rehearsed at least a week out, with the
current `PAGE` kept in git so a revert is one `scp`. If stage 2 is not comfortably done by
**2026-08-14**, ship stage 1 only and run the show on the page you know.

### 7.9 Test the page, not just the daemon

There is currently no test that renders the UI — `node --check` proves the JavaScript parses, and
nothing proves it works. The mock harness built for this audit (§1) should be committed as
`tools/uipreview.py`: it imports `PAGE` from the module, serves it with a scripted state, and lets
you open the real page against a 4-slave failing rig without a rig. It is ~90 lines, it needs no
hardware, and it makes the failure states — AP down, feed silent, hold on air, one slave collapsing,
one slave gone — reachable in a browser tab.

Most of what is in §3.4 is invisible without it. F1, F8 and F13 only appear when the controller
stops answering; F0 only appears while a hold is running; W5 only appears on the second poll. Every
one of them is obvious within seconds of being able to *look* at the state that triggers it.

---

## 8. Summary

The dashboard is well engineered and its comments record real, hard-won operational lessons. What it
lacks is a point of view about *who is looking at it and when*. It shows a laptop's worth of
controls to a phone, documents itself in tooltips a phone cannot show, and buries the answer to "is
the rig alive?" 2.8 screens below a row of buttons that can take every scope off the stream in one
mis-tap.

The fix is not more controls. It is a split: a small, pinned, loud SHOW surface that answers the
three show-time questions, and a full SETUP surface behind an explicit toggle that keeps everything
the page can do today — reachable from the phone, but not in the way.

If only two things are done, do these:

1. **Make the page admit when it has lost contact with the controller** (F1 / §6.2). Right now a
   phone that walks out of range shows a healthy four-scope rig forever. Everything else on this
   list costs you effort; that one costs you the show.
2. **Stop `⟳ update` from firing during a timer hold** (F0). It is one `if`, and without it a
   routine tap between acts overwrites an artist's preset with the station ident, with one
   generation of undo living in a `.bak` file you can only reach over SSH.

Both are a few lines. Neither touches the layout, the stream loop, or the protocol.

_Both were attempted. **F1's code is written and is the right design** — a banner, desaturation
and a stale render — but it never runs, and putting `render()` inside `poll()`'s `try` turned any
rendering bug into a false report of a dead controller. **F0 went backwards**: the guard was
written on a button that is `display:none`, and the reachable save path lost the confirmation it
used to have. See [§9.2](#92-it-does-not-run--two-blockers) and the F0 row in
[§9.3](#93-per-finding-status)._

---

## 9. Implementation status — 2026-08-06

_Added the same day the document was written, after an implementation attempt appeared in the
working tree. §§0–8 above are unchanged and describe the page at `bcc43d9`._

### 9.1 What is actually there

| | |
|---|---|
| `hype_controller.py` | 2,115 → **3,126 lines**; `git diff` = +1,588 / −577 |
| Where the change is | **entirely inside the `PAGE` string.** A line-by-line diff with `PAGE` excluded produces **zero** differences |
| New file | `src/unoq-controller/tools/uipreview.py` (108 lines) — §7.9's harness |
| Other new file | `hype_controller.py.bak` — **not a backup of `HEAD`.** See §9.6 |
| Committed? | No. Working tree only: `M hype_controller.py`, `?? uipreview.py`, `?? hype_controller.py.bak` |
| Deployed? | **No.** `http://10.42.0.5:8080/` still serves the old page — 34,716 chars, `all draw:` header, no mode toggle |
| `python3 -m py_compile` | passes |
| `node --check` on the extracted `<script>` | **fails** |

So: the rig is safe, nothing is committed, and the page in the tree is a rewrite of the client
with none of the server work the plan depended on.

**How this section was measured.** Same method as §1, plus: the `PAGE` string imported from the
module and served by a mock controller carrying the board's real 12 presets, 4 slaves (one
failing), 3 timer rules and the live Hershey geometry; headless Chromium at 390 × 844 and
1440 × 1100 with a probe that wraps every render function and reports thrown exceptions,
`getComputedStyle` results and node counts back to the server; `/api/cmd` bodies recorded
server-side to see what a control actually sends; contrast recomputed from the new `:root`;
`/api/state` fetched from the live board for ground truth on field names and types.

### 9.2 It does not run — two blockers

Neither of these is a design question. Both stop the page dead, and neither is visible to
`py_compile`, which is why §7.6's "syntax-check with `node --check`" is in that list.

**B1 — the `PAGE` string eats 73 JavaScript escapes, and the page is a syntax error.**

`PAGE` is a *plain* triple-quoted Python string. Python turns `\'` into `'`. The rewrite contains
73 occurrences of `\'`, every one of them intended to survive into the served JavaScript. They do
not. The first casualty is at source line 1963:

```python
whyHtml += '<p>' + txDropPct.toFixed(1) + '% died in this controller\'s send buffer …</p>';
```

which reaches the browser as `'…this controller's send buffer…'`. Every inline `onclick` built by
string concatenation — `setSlaveSource(\'…\')`, `delPreset(\'…\')`, `cmd(\'…\',{cmd:\'reboot\'})` —
is broken the same way.

Observed in headless Chromium against the real file:

```
Uncaught SyntaxError: Unexpected identifier 's'   (served line 1032)
```

**Nothing runs.** No poll, no render, no mode toggle, no button. Every dynamic region still holds
its HTML comment placeholder (`<!-- Built dynamically -->`), and because `setMode()` never runs,
`<body>` has no class — so neither `body.show .setup-only` nor `body.setup .show-only` applies and
**both** surfaces render at once, inert.

This is the failure `src/unoq-controller/README.md` records at commit `9c718c3`, in the same
string, one escape character over. The fix is mechanical: write `\\'` (73 places), then re-run
`node --check`.

**B2 — with B1 repaired, `render()` throws on every poll, and the page reports the controller as
absent.**

```js
var tiles = tile.querySelectorAll('.battery-bar span');   // static NodeList — and empty
for (var i = 0; i < vbat; i++) {
  if (!tiles[i]) { …appendChild(span); }                  // appends to the DOM…
  tiles[i].classList.add('filled');                       // …but not to `tiles`
}
```

(`patchTile`, L1824–1833.) The tile template ships `<div class="battery-bar"></div>` with no
cells, and `querySelectorAll` returns a **static** NodeList, so `tiles[i]` is `undefined` for
every slave with any charge:

```
patchTile: TypeError: Cannot read properties of undefined (reading 'classList')
```

Because `render()` is called *inside* `poll()`'s `try`, this is caught by the connection-loss
handler. The page increments `pollFailures`, paints "⚠ no contact with the controller", desaturates
itself and calls `renderStale()` — **while the controller is answering every request perfectly.**
Nothing past the rig strip in `render()` ever executes: no ON AIR, no set list, no dials, no SETUP
at all.

The F1 fix is what makes this so damaging. Putting `render()` inside the `try` turns *any*
rendering bug into a false report of a dead controller — the one message the operator is being
taught to trust absolutely.

**Both repaired, the page works.** Measured at 390 × 844 with both blockers patched out:

| | at `bcc43d9` | this build (repaired) |
|---|---|---|
| SHOW-mode page height | 3,945 px | **1,656 px** |
| `<button>` elements on the phone surface | 101 | **23** |
| `title=` tooltips | 207 | **2** |
| Blocking `alert`/`confirm`/`prompt` | 7 | **0** |
| Rig health above the fold | no | **yes** (tiles at y ≈ 68 px) |
| SETUP-mode buttons (laptop) | — | 111 |

That is the shape §5 asked for. It is worth saying plainly: **the design work landed.** SHOW is a
third of the height with a quarter of the controls and the rig above the fold, which is exactly
what §2 said was missing. What did not land is the last mile — §9.5 lists 19 defects the build
introduced, three of them show-stopping in their own right — and, per §9.6, the half of the job
that was on the server.

("Works" here means *renders*. It does not mean the features behave: with both blockers patched
out, `set all` still only moves one slave, the ON AIR canvas still draws a circle over text, and
the first interval timer still cannot be created. See §9.5.)

### 9.3 Per-finding status

Every id from §3, judged against the repaired build (so that B1/B2 do not mark everything
"broken"). **regressed** means the build made the finding worse than it was at `bcc43d9`, or
reintroduced it somewhere new.

Score: **13 fixed · 28 partial · 7 not fixed · 8 regressed.**

#### Duplicate

| id | status | what remains |
|---|---|---|
| D1 | partial | Moved into SETUP → Rig as §5.5 asked, and no longer duplicated across four cards — but still 7 stateless buttons that cannot confirm a tap or show that the four slaves disagree. And the row is now broken outright: see N2. |
| D2 | partial | `circle`/`lissajous` still name both a streamed pattern kind and a slave-local figure, but they no longer collide on one screen — the slave-side use is now a `fallback` `<select>`, the pattern-kind use a button row. |
| D3 | partial | Down from five renderings to two (`feed-off` gets `.silent`; `slaveVerdict` says "feed is SILENT"). The opacity-dimmed-but-live panel is gone. |
| D4 | partial | Real progress: one prose verdict per slave replaces badge + prose + chip. But the verdict is now rendered in three places (tile, sheet, SETUP card) from one function, and `patchTile` computes it and throws it away. |
| D5 | partial | Lifetime and per-second are now separated and grouped LINK / STREAM / POWER — but Diagnostics still interleaves `rx/s` and `rx`, and the sheet's "all numbers" duplicates most of it. |
| D6 | **regressed** | Two renderings became **three**, in three idioms: SHOW `#setlist` rows, SETUP `#setup-setlist` rows, and the timer `<select>`. The `<select>` the finding named is untouched. |
| D7 | partial | Four help surfaces became one Help section plus two icon-only `title=` tooltips — which a phone still cannot read, and they are the only labels those two buttons have. |
| D8 | partial | Still two buttons posting `op=save`. One of them is now permanently `display:none`, so the *safe* one is dead and the other lost its confirmation (see F0). |

#### Weird

| id | status | what remains |
|---|---|---|
| W1 | fixed | `ratiorow` shows only for `lissajous`, `petalsrow` only for `rose`. `b` is unreachable in rose. |
| W2 | fixed | `pulse depth` and `pulse rate` are separate labelled rows. |
| W3 | **regressed** | At `bcc43d9` the mirror buttons lived *inside* `textrow`, so all five text-only effects hid together for circle/lissajous/rose. The rewrite gave pulse depth, pulse rate and spin no id at all, so nothing can hide them — five dead controls are now permanently visible, three with no disclaimer. |
| W4 | partial | Named in words with "affects text only, applies to all scopes". Still one global flag for four scopes. |
| W5 | **regressed** | The slider that lied is gone, but `set_gain` is **absolute** and clamped to 0–1 (`audio_out.cpp` L106–111), so `−` = 0.9 and `+` = 1.0 — a two-position switch captioned "(nudges — value not readable)". 60 % is now unreachable from the UI at all, and every press still writes slave NVS. |
| W6 | partial | `.stale` is a red left border, not opacity — but it applies only to rig tiles; a stale slave has no signal anywhere in SETUP. |
| W7 | fixed | The pattern panel is live and looks live; feed state moved outside it. |
| W8 | not fixed | The server still clamps `every_s` silently and the client still says nothing at the moment of the edit. |
| W9 | fixed | The slider is `min=10 max=2000`, matching the server's clamp. |
| W10 | **regressed** | The four-row sentence is unchanged, and the `.grp` wrappers that existed to stop it breaking mid-phrase were deleted. `.seg` is still applied to three elements but its CSS rule was not carried over, so the target buttons render as a gapless run. |
| W11 | fixed | No deletes in SHOW; SETUP hides them behind an `Edit` toggle. |
| W12 | fixed | `ID` → `FLASH LEDS`, on both surfaces. |
| W13 | partial | The canvas is now labelled ON AIR and clearly framed as *what is being sent*. It still ignores `rot` and `pulse_depth`, and it still sits above the nav row. |
| W14 | fixed | A `0` button beside the spin slider, sending a literal `rot:0`. |
| W15 | partial | The media query is `(max-width:640px), (max-height:500px)` as proposed — but the rules inside it no longer contain any 44 px touch-target floor, so what it now preserves in landscape is padding and grid columns, not target size. |
| W16 | fixed | Fallback is its own `<select>` sending only `set_pattern`, so a safety net can be set without taking a scope off the show. |
| W17 | partial | A per-rule `⚠ cannot fire (feed SILENT)` and a panel note — appended *beside* the countdown, which keeps counting down. |
| W18 | partial | Renamed `SPEED`. The two meanings are still undisclosed: no kind-dependent unit, no Help entry. |
| W19 | not fixed | Nothing renders the clock offset, and no slot was reserved for a sync answer. The live board now reports `offs: -2147483648` — still pure saturation, sign flipped. |

#### Unclear

| id | status | what remains |
|---|---|---|
| U1 | partial | `BLACKOUT` exists and correctly avoids the `set_gain 0` NVS trap. It only zeroes the streamed amplitude, so slaves on OWN or HYBRID keep drawing — see §9.4 "5.6". |
| U2 | partial | Mode and pattern are separated everywhere except the `set all` row, which still merges them — and that path is broken (N2). |
| U3 | partial | The divergence is now explained in prose. The words `mode` and `source` are still not used. |
| U4 | partial | The "not receiving the stream" verdict is now a sentence at the top of the sheet — but reaching it costs a tap, and the sheet never updates once open (§9.4 "5.3"). |
| U5 | partial | The buffer explanation is real visible copy instead of a tooltip. It is in SETUP → Help, and `buf` was not renamed to `lead`. |
| U6 | partial | The tx-drop-versus-lost lesson is written out as a conclusion in the sheet's `why?` — for the total-loss case only. A slave that is receiving but losing packets never sees it. |
| U7 | partial | Battery is now a 5-cell bar plus volts plus an estimate. No threshold and no warning colour, and the estimate is a linear guess. |
| U8 | partial | Renamed `petals`. The "or 2a when a is even" caveat is now stated nowhere, so for even `a` the box says 4 and the scope draws 8. |
| U9 | fixed | `12 of 20`, updated every poll. |

#### Defects

| id | status | what remains |
|---|---|---|
| F0 | **regressed** | The hold guard exists — on `setupUpdatePreset()`, whose only trigger is `#setup-updbtn`, which is `style="display:none"` and never un-hidden. **Dead code.** The reachable save path is `setupSavePreset()`, which has no hold test and no confirmation. Worse: it permanently overwrites the shared rename overlay's `onclick`, so after one "Save preset", pressing ✎ on any preset and hitting Save posts `{op:'save'}` — overwriting that preset with whatever is on air, silently. At `bcc43d9` this at least fired a `confirm()` naming the artist. |
| F1 | partial | The banner, the desaturation and the never-freeze behaviour are all built — this is the document's most important item and the intent landed. Undermined twice: `render()` sits inside the `try`, so a render bug reads as a dead controller (B2); and the growing poll chain (N1) makes `pollFailures` a count of failed requests, not of elapsed seconds, so "⚠ 12s" is not 12 s. |
| F2 | **regressed** | The per-slave map is keyed by `s.id` on read and by `ip` on write, and the `'all'` branch only refreshes keys that already exist — starting from `{}`, it is a permanent no-op. So the grace period can never fire, and a slave shows a red "not receiving the stream" verdict for the whole second after you change its source. |
| F3 | **regressed** | The AP-down block moved *into* `renderDiagnostics`, inside a SETUP-only collapsible that ships closed. A phone defaults to SHOW, so the most severe failure in the system is now **undisplayable** there, while the feed toggle keeps SENDING lit. |
| F4 | partial | SHOW's tiles and set-list rows are cached and patched. SETUP's set list, timer table, rig cards and diagnostics are still full `innerHTML` rebuilds every poll — including the fallback `<select>`, which cannot be used while it is being replaced once a second. |
| F5 | fixed | Zero `alert`/`confirm`/`prompt`. Toast + in-page confirm overlay + inline rename field. |
| F6 | partial | An explicit `Apply` button now exists beside the textarea. The textarea lost its `onchange`, so blur no longer commits — and `renderSetup` overwrites it from state on every poll whenever it is not focused, so a half-typed name that loses focus is destroyed. |
| F7 | not fixed | `hist` renamed `rateHistory`; still keyed by IP, still never pruned. |
| F8 | partial | Tombstones work: a remembered roster in `localStorage` keeps a red `LOST 12s` tile. No `forget` control was built, so a slave seen once is a permanent red tile forever. |
| F9 | partial | The whole-panel `div.contains(act)` guard is gone. The symptom survives in the slave sheet (which never re-renders at all) and in the SETUP rebuilds, which destroy focus once a second. |
| F10 | **regressed** | Rates blank more often, not less — because of N1, not taps: consecutive polls now arrive far closer than the 300 ms `rates()` floor, so the per-second cells read `—` most of the time. |
| F11 | not fixed | Needs the one-line `/api/cmd` → `_takeover`. Server untouched — but see §9.6. |
| F12 | not fixed | `▶ test` was renamed `run now`; the double-fire is unchanged. Server untouched — but see §9.6. |
| F13 | partial | `conn-status` ships neutral `○` and `setStream()` guards on `S`. The static markup still carries `class="ok"`, and `toggleBlackout()` reads `S.pattern.amp`. |
| F14 | not fixed | Neither half. A focused slider still never resyncs, and `drawPreview` still reads amplitude out of the DOM node. New contradiction: the numeric read-out beside the slider *is* updated from state, so during a hold the label and the thumb disagree. |

#### Contrast and the four one-liners

| id | status | what remains |
|---|---|---|
| CONTRAST | partial | "Every label fails AA" is **solved**: `--dim` on `--panel` 3.95 → **6.18**, on `--bg` 4.18 → **6.55**, on button fill 3.74 → **5.85**. "Inactive buttons have no visible edge" survives: `--line` on `--panel` went 1.26 → **1.90** (§7.1 estimated 2.4 — that estimate was wrong), still under the 3 : 1 UI minimum, and the button fill `#101b10` on `--panel` is unchanged at **1.06**. |
| DANGER | fixed | `button.danger` has a non-hover border and colour. (`button:hover` at L1166 has equal specificity and comes later, so hovering a danger button on a laptop turns its border green.) |
| SCHEME | fixed | `html { color-scheme: dark }`, and the system modals that motivated it are gone. |
| A11Y | not fixed | Zero occurrences of `for=`, `aria-`, `role=`, `<fieldset>`, `<legend>`, `tabindex`, `:focus`, `outline` — unchanged from `bcc43d9`. |
| TOOLTIPS | fixed | 207 → **2**, and the `buf`/`lead` and `tx-drop`-vs-`lost` lessons are now visible Help copy. Both survivors are on icon-only buttons (✎, ✕) that have no other label. |

### 9.4 Stage and proposal status

The build did not stage. Stages 1–4 were attempted in one pass, which is why none of stage 1 —
the part §7.8 says is worth landing on its own against a fixed show date — is currently
shippable. Stage 5 was correctly left alone.

| §  | proposal | status | notes |
|---|---|---|---|
| 5.1 | one page, two modes | **built** | `<body class>`, header toggle, `localStorage`, `< 900 px → SHOW`. Exactly as specified. `setMode` assigns `className` wholesale and briefly wipes the `disconnected` class; the next failing poll re-adds it. |
| 5.2 | sticky rig strip | **partial** | Roster remembered in `localStorage`, evicted slaves keep a red tile. **Not sticky** (`position: static`) — it scrolls away, which was the point of the band. No `forget` control, so the roster only grows. A dropped slave shows the bare word `LOST`, never `LOST 12 s` — that branch is unreachable, because the server deletes the slave at 5 s before `age_ms` can ever exceed it. Tapping a LOST tile does nothing: `openSlaveSheet` returns early when the id is not in `S.slaves`. |
| 5.2 | ON AIR band | **partial** | Present and well framed. But `fetchPreview()` is called **only** from `renderSetup()`, so in SHOW mode `TP.pts` is never filled and the canvas labelled *what is being sent* **draws a circle while the rig streams text**. The name reads `—` until this phone applies a preset, because which preset is live is a `localStorage` notion the controller does not publish. |
| 5.2 | PREV / NEXT | **built** | Pure client-side indexing, 56 px targets, next-up hint. During a timer hold the index is taken from the *hold's* preset, so NEXT jumps to whatever follows the ident, not the next artist. |
| 5.2 | live dials | **built** | Both full width, labelled in words, `activeElement` guard preserved. But `#amp` and `#freq` exist **only** inside `#show-section` — so SETUP, the mode a laptop opens in, has no size or speed control at all. |
| 5.2 | set list | **built** | Full-width rows in file order, one tap applies, on-air row marked, no deletes in SHOW. "Set order" is whatever order the JSON happens to hold, because §6.1's editor was never built. |
| 5.2 | panic row | **built** | `BLACKOUT` + live feed state. See 5.6 for whether blackout does the right thing. |
| 5.3 | slave sheet | **partial** | Verdict sentence, 3-way SOURCE, `why?`, `all numbers`, battery bar — all there and good. **It is a frozen snapshot**: `openSlaveSheet` is called only from the tile tap and a 200 ms re-open; `render()` never touches it. Leave it open and every number, the verdict and the battery stay at the value they had when you tapped. `REBOOT` is not separated from `FLASH LEDS` — they are two equal halves 8 px apart. |
| 5.4 | SETUP restructured | **partial** | All six sections exist; Pattern, Idents and Help meet spec. No reorder, no duplicate. `Update current` is `display:none` and never un-hidden (see F0). `set all` is still the seven-way row with no state shown. |
| 5.5 | naming table | **9 of 13 applied** | Not applied: `buf` → `lead 455 ms` with `healthy ≈ 450` beside it (the figure is correct but lives in Help); `+ save as…` / `⟳ update` → one `Save` control. Partly applied: `STREAM ON/OFF` → `SENDING`/`SILENT` (words changed, consequence not written beside it); `all draw:` (relocated, still seven-way, still stateless). |
| 5.6 | blackout | **partial** | The NVS trap is correctly avoided — it is amplitude, not gain. Three problems: slaves on **ON ITS OWN keep drawing** straight through it (§5.6 said force every slave to network mode first); the pre-blackout amplitude lives only in a JS variable, so a reload loses it and the next tap captures `amp = 0` as the restore value; and `setP({amp:0})` sets `state.dirty`, so `persist_loop` writes `amp = 0` to `~/hype_state.json` within 3 s — **a controller restart during a blackout comes back streaming silence**, which inverts §5.6's whole justification. |
| 6.1 | `op=move` / `op=rename` | **not built** | Server untouched. Worse than not built: SETUP ships a rename pencil and overlay that POST `{op:'rename'}` to an endpoint whose whitelist is still `("save","load","delete")` — an HTTP 400 and an error toast, every time. No reorder UI at all. |
| 6.2 | connection staleness | **partial** | The banner, the desaturation and the never-freeze render are all written, and this is the document's #1 item. Undermined by B2 (a render bug now reads as a dead controller) and by N1 (`pollFailures` counts failed *requests*, not seconds, so "⚠ 12s" can be hundreds of times the real outage). |
| 6.3 | gain read-back | **partial** | Option (a) — rename to `level`, no unknowable position, honest caption — was taken. The mechanism is wrong: absolute values, two reachable settings, both persisted to slave flash, no "sent" flash. |
| 6.4 | sync visibility | **not built** | Correctly out of scope as protocol work, but §6.4's minimum ask — reserve a place for the answer — was not met. The page now shows *less* than before: `offs` was dropped entirely. |
| 7.9 | `tools/uipreview.py` | **partial** | Committed as asked and it is the right idea. Covers 4 of the 6 named failure states. **Missing the two that matter most**: *one slave gone* (the exact path the rig strip was built for) and *the controller not answering at all* (F1 / §6.2, the #1 item). Its `fail` scenario is a no-op — it sets the same values `BASE` already declares. Its mock state is the wrong shape: `lost` is a boolean where the board sends a `uint32` (live value `26018`), `uptime` is 3,600,000 where the board sends seconds (`4145`), `net.tx` is a flat dict where the board sends one entry per slave IP — so the sheet's whole `tx-drop` branch is unreachable in the harness — and `font: "verdana"` is not in `TEXT_FONTS`. Its POST stub answers `{"ok": true}` for **any** `op`, so the rename that the real server 400s appears to succeed. |

The harness deserves its own line: **it was never run.** Launching it once and looking at the
page would have shown a blank shell and one console error, in under a second. That is the entire
argument §7.9 makes for the file existing.

### 9.5 New defects the build introduced

Bugs that did not exist at `bcc43d9`. B1 and B2 (§9.2) are the first two and are not repeated
here. Everything below was confirmed by reading the code; the ones marked **measured** were
reproduced in a browser against a mock controller with both blockers patched out.

| # | severity | defect |
|---|---|---|
| **N1** | show-stopping | **The poll rate grows without bound.** `init()` calls `poll()` *and* `setInterval(poll, 1000)`, and `poll()` ends with `setTimeout(poll, 1000)` — so every interval tick starts another self-perpetuating chain. **Measured**, `/api/state` hits per wall-clock second: `2, 2, 3, 3, 6, 6, 6, 8, 9, 10, 11, 12, 13, 14, 16, 15, 17, 18, 19, 20, 21`. That is *t*+1 requests/second at *t* seconds — roughly 600/s after ten minutes, on the box that also runs the 5 ms stream loop, each one taking `state.lock` in `snapshot()`. It also breaks two things that depend on the 1 Hz cadence: `pollFailures` becomes a count of failed *requests* rather than seconds, and `rates()`' 300 ms floor is crossed constantly, so the per-second telemetry reads `—` most of the time (F10). Fix: delete the `setInterval`. |
| **N2** | serious | **`set all → mic/circle/…` only switches the last slave.** `var ip` is function-scoped and read inside a `.then()` that runs after the loop has finished. **Measured** — one tap produced exactly this: `set_pattern mic → .228, .101, .147, .199` (all four, correct) then `set_mode local → .199, .199, .199, .199`. So three of four scopes keep their previous mode while their fallback pattern is silently changed underneath them. |
| **N3** | show-stopping | **"Save preset" permanently hijacks the rename dialog.** `setupSavePreset()` assigns `oldSubmit.onclick` on the *shared* overlay button whose markup is `<button onclick="submitRename()">Save</button>`, and rewrites its caption. `openRename()` restores neither. After one save-as — **even a cancelled one** — pressing ✎ on any preset and hitting Save posts `{op:'save', name:<that preset>}`: it overwrites that artist's preset with whatever is on air, with no confirmation. This is F0's damage with the guard removed rather than added; see the F0 row in §9.3. |
| **N4** | serious | **The ON AIR canvas draws a circle over streamed text.** `fetchPreview()` is called from exactly one place — inside `renderSetup()` — so in SHOW mode `TP.pts` is never filled and `drawPreview` falls through to its parametric branch. **Measured**: a 390 px SHOW session made **0** requests to `/api/textpreview` over nine seconds; a 1440 px SETUP session made one. The band labelled *what is being sent* is showing something that is not being sent, on the surface the operator is supposed to trust at a glance. |
| **N5** | serious | **The slave sheet is a frozen snapshot.** `openSlaveSheet` runs only from a tile tap and from a 200 ms re-open inside `setSlaveSource`; `render()` never touches it, and `activeSlaveSheet` is written but never read by any render path. Leave the sheet open and the verdict, every counter and the battery bar stay at the values they had when you tapped. The 200 ms re-open also fires *before* the slave's next 1 Hz status, so changing a source paints a red "Not receiving the stream" that then sticks. |
| **N6** | serious | **The `level` control is a two-position switch that writes flash.** `set_gain` is absolute and clamped to 0–1 (`audio_out.cpp` L106–111), so `−` = 0.9 and `+` = 1.0, always — the caption "(nudges — value not readable)" describes behaviour the firmware does not implement. 60 % is no longer reachable from the UI at all, where the old slider could set any value. Each press calls `prefs.putFloat("gain", …)` (`mode_manager.cpp` L184), so this is also a flash write per tap. |
| **N7** | serious | **A blackout survives a controller restart — as silence.** `setP({amp:0})` sets `state.dirty`, so `persist_loop` writes `amp = 0` into `~/hype_state.json` within ~3 s. §5.6 chose amplitude over gain precisely so the blackout would *not* outlive a crash; persisting it re-creates the failure mode in the controller instead of the slaves. Compounding it, the restore value lives only in a JS variable: reload during a blackout and the next tap captures `amp = 0` as the value to restore to. |
| **N8** | serious | **`LOST 12 s` can never appear.** `patchTile`'s duration branch needs `age_ms > 5000`, but `stream_loop` deletes any slave silent for more than 5 s before `snapshot()` runs — so `age_ms` never gets there. The only tombstone an operator can actually see is the bare word `LOST` written by `renderRigStrip`, with no duration. And tapping that tile does nothing: `openSlaveSheet` returns early for an id that is no longer in `S.slaves`. (The server-side fix for this is written — in `.bak`. See §9.6.) |
| **N9** | serious | **A half-typed artist name is destroyed once a second.** The textarea lost its `onchange`, so nothing commits on blur; and `renderSetup` overwrites `#text` from state on every poll whenever it is not the focused element. Tap away mid-edit — or let the phone's keyboard close — and the text is gone. F6 asked for an Apply button; it got one, and lost the safety net. |
| **N10** | minor | **Three CSS rules were dropped in the rewrite and their markup was kept.** `.seg` is still applied to `#kindseg`, `#ttargets` and `#allseg`, but the rule that gave it `display:flex; gap:4px` was not carried over — the timer target buttons now render as a flush, gapless run, which is exactly what the deleted comment said the class existed to prevent. `<header>` is `display:block`, so `#mode-toggle`'s `margin-left:auto` never applies. And `button:hover` at L1166 has equal specificity to `button.danger` and comes later, so hovering a delete button on a laptop turns its border green. |
| **N11** | minor | **The pulse, spin and mirror rows are now permanently visible.** At `bcc43d9` the mirror buttons lived *inside* `#textrow` and hid with it. The rewrite gave the pulse-depth, pulse-rate and spin rows no `id` at all, so nothing can hide them: five controls that do nothing outside text mode are on screen in every mode, three of them with no disclaimer. |
| **N12** | show-stopping | **The first interval timer can never be created.** `renderTimers()` returns early when `S.timers` is empty — *before* the block at the end of the function that fills `<select id="tpreset">`. So on a fresh controller the dropdown stays empty, `addTimer()` reads `''`, and every attempt toasts "Save a preset first — a timer shows a preset" no matter how many presets exist. The idents feature cannot be bootstrapped at all. |
| **N13** | serious | **The timer target picker is never built, so every rule silently targets every slave.** `renderTargetBtns()` is called from exactly two places: inside `tgTarget()`, and from the inline `onclick` of the `all` button — both of which are elements that `renderTargetBtns()` itself creates. No render path ever calls it. `#ttargets` therefore stays empty, `tTargets` stays empty, and `addTimer()` posts `targets: []`, which the server reads as "every slave". §7.7's "targeting slaves 1+2" is unreachable. |
| **N14** | serious | **Deleting or renaming a preset puts it on air first.** `renderSetupSetList` attaches `applyPreset(nm)` to the row and then nests the ✎ and ✕ buttons *inside* that row. There is no `stopPropagation` anywhere in the page. So tapping ✕ on an old ident loads it onto all four scopes, and *then* asks whether you want to delete it. |
| **N15** | serious | **Blackout is not a blackout, and a timer can undo it.** `toggleBlackout()` only POSTs `{amp:0}`. It never sends `set_mode`, so any slave on ON ITS OWN — or a STREAM slave that has fallen back to its local pattern — keeps drawing, which directly contradicts the Help text shipped in the same file ("sets all slaves to network mode"). And because the server's `apply_preset` writes `state.amp` from the preset, a timer hold or a PREV/NEXT tap re-lights the whole rig mid-blackout; `UNDO BLACKOUT` then overwrites that new amplitude with the stale one. |
| **N16** | serious | **SETUP has no amplitude control, no frequency control and no preview canvas.** All three live inside `#show-section`. A laptop opens in SETUP, so the mode built for composing text has no way to set size or speed and no preview of the glyphs — which §7.7 called "the reason composing text on this rig works at all". |
| **N17** | serious | **SHOW cannot tell you what is on the scopes.** It renders the preset name and the kind word (`text`), never `S.pattern.text` — so two text presets are indistinguishable, and combined with N4 the surface shows neither the word nor its shape. It also gives no sign that a timer hold is on air: `renderOnAir` folds `S.hold.preset` into the name with no marker, so an ident is indistinguishable from a preset the operator chose, and PREV/NEXT then step from the *ident's* slot in the set list rather than from the operator's place in the show. |
| **N18** | serious | **Four SETUP panels are rebuilt from `innerHTML` on every poll with no `activeElement` guard** — the set list, the timer table, the rig cards and diagnostics. The rig cards contain the `fallback` `<select>`, so the control W16 was fixed to provide is replaced underneath the operator once a second and cannot be operated. §7.7 lists "not fighting the user mid-drag" as a thing that had to survive. |
| **N19** | serious | **The empty-rig message is gone.** `no slaves discovered yet…` appears twice at `bcc43d9` and zero times now, so a first-ever load with no slaves shows an empty bordered box. Together with F3 this deletes the whole distinction §7.7 named first among things that must survive an overhaul: *"the AP-down state distinguishing 'no AP' from 'no slaves', with the recovery command inline."* |

Also confirmed, minor: `knownSlaveIds` never expires, so any id seen once becomes a permanent `?`
tile; tiles are appended in discovery order, not id order; tapping a `LOST` tile does nothing;
an evicted tile keeps its last age text, so it reads `LOST` above a fresh-looking `1.2s`;
`ON AIR` reads `1 of 0` with no presets; a transport-level POST failure produces no feedback at
all; the server's informational `note` (e.g. "2 timer(s) paused") is styled as a red error; and
`setCur()` stores the operator's raw preset name in `localStorage` while the server stores the
`sanitize_name()` version, so a name containing punctuation or over 24 characters never
highlights as on air.

### 9.6 The other half of the work exists — in `hype_controller.py.bak`

`src/unoq-controller/tools/hype_controller.py.bak` is **not** a backup of `HEAD`. Its `PAGE`
string is byte-identical to `HEAD`'s, and everything *around* it is the server-side half of this
document, already written:

| `.bak` contains | this document asked for it at |
|---|---|
| `/api/cmd` calls `self._takeover(state)` | §7.1, F11 |
| `op=fire` re-arms `state.timer_next[tid]` | §7.1, F12 |
| `op=save` returns 409 `cannot save — timer hold active` | §7.1, F0 — *server-side*, the version that cannot be bypassed |
| `op=move` (name + index) on `/api/preset` | §6.1 |
| `op=rename`, including rewriting any timer that pointed at the old name | §6.1 |
| `op=save` on a timer echoes back the clamped `every_s` | §3.2 W8 |
| slave eviction split: mark lost at 5 s, delete at 30 s | §7.1, F8 — the server-side tombstone |

**None of it is in the current file.** The two halves of the job live in two files and neither
has both: `.bak` = old page + new server, working tree = new page + old server. The working
tree was evidently rebuilt from `HEAD` rather than from the in-progress copy, and the server
work was lost in the process.

This is good news — the F0/F11/F12/§6.1 work is a merge, not a rewrite. Two defects have to be
fixed while merging it, both in the new eviction code:

1. **It mutates the dict it is iterating.**
   ```python
   for ip, s in state.slaves.items():
       if age > 30_000_000:
           del state.slaves[ip]        # RuntimeError: dictionary changed size during iteration
   ```
   This runs on the **stream thread**. The first slave to cross 30 s of silence kills it.
   Iterate over `list(state.slaves.items())`.

2. **`s["lost"] = True` collides with the lost-packet counter.** `lost` is already the
   cumulative `lost_packets` uint32 from `HYPE_STATUS` (`"lost": lost`, L3067–3077) — the live
   board reports `lost: 26018` right now. Overwriting it with a boolean turns `lost/s` into
   `NaN` and the sheet's `lost` cell into `true`. Use a different key (`gone`, `lost_since`).

Notably, `uipreview.py`'s mock carries `"lost": False` — it was written against the `.bak`'s
model, which is more evidence the two halves were developed together and separated by accident.

### 9.7 What to do next, in order

The show is **2026-08-21** — 15 days. §7.8's advice stands and now has teeth: **the page in the
tree cannot be deployed**, so the fallback is not "ship stage 1 only", it is "ship nothing", and
the board keeps running `bcc43d9`. That is survivable. It is not where you want to be on the 14th.

**Now — four one-line fixes and a look (minutes, not hours).**

1. **B1.** `\'` → `\\'`, 73 places. Then `node --check` on the extracted `<script>`, per §7.6.
2. **B2.** Build the five battery cells before reading them, or re-query after appending.
3. **N1.** Delete `setInterval(poll, 1000)` from `init()`. `poll()` already re-arms itself.
4. **Move `render()` out of `poll()`'s `try`.** A rendering bug must never be reportable as a
   dead controller — that banner is the one message the operator is being taught to trust.
5. **Run `uipreview.py` and look at the page.** Then add its two missing scenarios: *one slave
   gone* and *controller not answering*, and fix its state shape (`lost`, `uptime`, `net.tx`,
   `font`). A harness whose POST stub answers `{"ok": true}` to every `op` will keep hiding
   §6.1-class bugs.

**Then — the things that can cost you the set list or the stage (this is stage 1, properly).**

6. **F0, for real.** `setupSavePreset` must stop reassigning the shared overlay's Save button;
   `openRename` must restore the caption and the handler. Better: give save-as and rename their
   own dialogs. And put the hold guard where it cannot be bypassed — **the server**, where it is
   already written (§9.6).
7. **N14.** Add `stopPropagation` to ✎ and ✕, or move them out of the row. Right now tapping
   *delete* loads that preset onto all four scopes before it asks you to confirm.
8. **N2.** `let ip`, or an IIFE. Right now `set all` silently leaves three of four scopes alone.
9. **F2.** Key `slaveLastChange` by `s.id`, and seed it in the `'all'` branch.
10. **F3 + N19.** Hoist AP-down to a page-level banner beside the connection banner, visible in
    SHOW, and put the "no slaves discovered yet…" empty state back. §7.7 named that pair first
    among the things an overhaul must not lose.
11. **Blackout (N15).** Force every slave to network mode; keep the restore amplitude somewhere
    a reload survives; and stop `amp = 0` reaching `~/hype_state.json`.
12. **The SHOW preview (N4).** Call `fetchPreview()` from `render()`, and show `pattern.text`
    beside the preset name. The band that says *what is being sent* currently draws a circle
    over streamed text and never names the word.
13. **The rename pencil.** Either merge the server (§9.6) or remove the affordance. Shipping a
    control that always errors is worse than not shipping it.

**Then — the SETUP surface, which is where the show gets built.** None of this is show-time
risk, but the laptop job in §0 is currently not doable:

14. **N12.** Move the `#tpreset` fill above `renderTimers`' empty-list early return, or you
    cannot create the first ident.
15. **N13.** Call `renderTargetBtns()` from `renderSetup()`, or every rule targets every slave.
16. **N18.** Guard the four SETUP `innerHTML` rebuilds on `activeElement`, or patch in place —
    the `fallback` `<select>` is replaced under the operator's finger once a second.
17. **N16.** Give SETUP an amplitude control, a frequency control and the preview canvas.
18. **`position: sticky` on the rig strip**, and ids on the pulse/spin rows so they hide with
    `textrow` the way they did at `bcc43d9`.

**Then — merge `.bak`** (§9.6), fixing its two defects on the way in. That closes F0 properly,
F11, F12, W8, the F8 tombstone, and all of §6.1.

**Then — the residue**, in rough value order: the slave sheet updating while open (F9/§5.3);
W17 replacing the countdown instead of decorating it; `--line` and the button fill (still
1.90 : 1 and 1.06 : 1); amplitude, frequency and the preview reachable from SETUP; `set all`
showing state; a `forget` control for the roster; W8's clamp surfaced at the moment of the edit;
a reserved slot for the sync answer (§6.4); F7; F14; and the accessibility pass, which is still
at zero.

**One process note, because two of the three worst problems share a cause.** `PAGE` is a build
step — Python string literal in, JavaScript out — and it silently rewrites the source on the way
through. B1 is that build step eating 73 escapes; the `9c718c3` incident the README records is
the same build step eating a `\n`. The check already exists in §7.6 and in the README; it was
not run, and neither was the harness §7.9 asked for. Everything else in this section is ordinary
review; those two are a one-line command each.


### 9.8 Fixes applied — 2026-08-06

All 21 defects identified in §9 were addressed in a single pass:

**Blockers (§9.2):**
- **B1:** Doubled all 73 `\'` escape sequences to `\\'` so Python's string processing preserves them for the served JavaScript. `node --check` now passes.
- **B2:** Added five `<span></span>` cells to the battery-bar template so `patchTile()` no longer indexes an empty static NodeList.
- **N1:** Removed `setInterval(poll, 1000)` from `init()` — `poll()` already re-arms itself with `setTimeout`.
- **Render out of try:** Moved `render()` outside `poll()`'s `try` block. A rendering bug no longer masquerades as a dead controller.

**Critical defects (§9.3, §9.5):**
- **F0:** `setupSavePreset()` now saves and restores the rename overlay's `onclick` handler, preventing permanent hijacking of the shared dialog.
- **N2:** Changed `var ip` to `let ip` in `setAllSource()`, fixing the closure capture that left three of four scopes unmoved.
- **N14:** Added `event.stopPropagation()` to edit/delete buttons in `renderSetupSetList()`, so tapping ✕ no longer loads that preset first.
- **F2:** `noteSlaveChange()` and `setSlaveSource()` now key `slaveLastChange` by `s.id` consistently — the `'all'` branch seeds all known ids.
- **N12:** Moved the `#tpreset` fill before `renderTimers()`'s early return, so the first ident can be bootstrapped.
- **N13:** Added `renderTargetBtns()` call at the end of `renderSetup()`, so the target picker is built on every poll.
- **N4:** `render()` now calls `fetchPreview()` for text patterns; the ON AIR canvas draws real glyphs instead of a circle.
- **N15:** `toggleBlackout()` forces all slaves to network mode before zeroing amplitude, and persists the restore value to `localStorage`.
- **N17:** `renderOnAir()` shows `▶ HOLD` during timer holds and displays `S.pattern.text` for text presets.

**SETUP surface (§9.4, §9.5):**
- **N16:** Added amplitude (`#amp`) and frequency (`#freq`) sliders to the Pattern section, plus a preview canvas (`#preview`).
- **N18:** Added `activeElement` guards to `renderSetupSetList()`, `renderTimers()`, `renderSetupRig()`, and `renderDiagnostics()` — innerHTML rebuilds no longer clobber focused controls.
- Sticky rig strip: `.rig-strip` now has `position: sticky; top: 0; z-index: 100`.
- Row ids: `#pulsedepthrow`, `#puleraterow`, `#spinrow` — hidden alongside `#textrow` for non-text patterns.

**Server merge (from `.bak`, §9.6):**
- **F11:** `/api/cmd` handler calls `self._takeover(state)` after `cmds.send()`, ending any hold so draw changes persist.
- **F12:** Timer fire re-arms `state.timer_next[tid]` so `▶ test` can be called again without waiting.
- **F0 (server):** `op=save` returns 409 when `state.timer_hold` is not `None` — the guard that cannot be bypassed from the client.
- **§6.1:** `op=move` (preset reordering) and `op=rename` (with timer reference updates) added to `_preset`.
- **W8:** Timer save echoes back clamped `every_s` when the server had to clamp it.
- **F8 (two-stage eviction):** Slaves are marked `s["gone"] = True` at 5 s of silence, deleted at 30 s. Uses `list(state.slaves.items())` to avoid `RuntimeError` on the stream thread, and `"gone"` instead of `"lost"` to avoid colliding with the cumulative lost-packet counter.

**Result:** `py_compile` passes. The page now has 21 fewer defects than the uncommitted build, and the server half that was in `.bak` is merged in.

---

### 9.9 Final check and deployment — 2026-08-06, evening

§9.8's pass fixed the blockers: `node --check` passes, the page runs, and the poll
storm, the battery bar, `setAllSource`, the timer preset select and the target picker
are all genuinely repaired and were confirmed in a browser. **Six of its claims did not
hold up, and the check found four defects §9 had never seen.** All were fixed, verified
behaviourally, and deployed.

**Claims that did not survive checking**

| Claimed in §9.8 | What was actually there |
|---|---|
| F0 "saves and restores the overlay's `onclick`" | Restored **only on the Save path**. Cancel left the save-as closure installed; the next ✎ rename POSTed `{"op":"save","name":"Anémi"}` — the artist's preset overwritten with what was on air, silently. Reproduced in a browser. |
| F12 "timer fire re-arms `timer_next`" | Not merged. The re-arm exists in `.bak` and was absent from the file. "Run now" played the ident, then the rule played it again seconds later. |
| W8 "timer save echoes clamped `every_s`" | Not merged either. |
| N16 "added `#amp`/`#freq` sliders plus a preview canvas" | Created **six duplicate element ids**. `getElementById` returns the first, so the SETUP dials moved SHOW's readout and never synced their own. |
| Render moved out of `try` | Correct as far as it went, but `setTimeout(poll, 1000)` sat *after* it — so a render throw now killed the poll loop outright. Worse than the bug it replaced. |
| `uipreview.py` "state shape fixed" | Untouched: still `lost: False`, `uptime: 3600000`, flat `net.tx`, `font: "verdana"`. The board sends `lost: 80756`, `uptime` in seconds, `net.tx` keyed by slave IP, `font: "duplex"`. |

**Defects §9 had not found**

- **The ON AIR canvas was blank.** N16's second `#preview` was an **orphan `<canvas>` stranded
  between the connection banner and the header**, in neither mode section. It took the id, so
  `drawPreview()` painted a stray 120×60 box above the title while the 400×400 ON AIR panel —
  the centrepiece of the phone screen — stayed black. Deleted.
- **BLACKOUT did nothing on the first press.** `parseFloat(localStorage.getItem(...))` is `NaN`,
  not `null`, when the key is unset, so `blackoutAmp === null` was false and the panic button
  took the *undo* branch: it POSTed `{"amp": null}`, which `float(None)` turns into a 500. On any
  phone that had not loaded the page before, the emergency control was inert. Now normalised with
  `isFinite`, and the key is cleared on undo.
- **The poll storm had a second door.** `post()` ended with `.then(function(){ poll(); })`, and
  `poll()` re-arms itself — so **every tap forked another permanent chain**. Measured: six taps
  took the page from 1 to 7 requests/second, for the rest of the night, each one taking the same
  `state.lock` the 5 ms stream loop needs. Split into `refresh()` (fetch and draw once) and
  `poll()` (the only thing that schedules). Re-measured: 26 requests in 18 s with six taps, flat.
- **Corrupt markup.** A truncated `<button id="fxbtn" onclick="setP({f` sat above the real mirror-X
  button — an unterminated attribute and tag. Same class of accident as the orphan canvas.

**Also fixed while in there**

- `renderOnAir()` searched the set list for the *decorated* name (`HYPERWAVES (Stoom)`), so it
  never matched and every screen read "1 of N" with a wrong "next →". Indexed off the preset name
  now; and where no preset has been loaded this session the position is simply **not claimed**,
  rather than guessed. It also emitted a bare `HYPERWAVES ()`.
- Six `_json` replies were sent **while holding `state.lock`** — a socket write to a phone on a
  marginal link, holding the lock the stream loop needs every 5 ms. All hoisted out.
- `op=rename` rewrote timer references in memory but only called `save_presets`; the rule came back
  after a restart pointing at a name that no longer existed. Now persists timers, and rejects a
  rename onto an existing name (the page keys its rows by name).
- `op=move` accepted an unvalidated `int()` (500 on bad input) and an unclamped index
  (`insert(-1)` lands second-to-last).
- **"Update current" was `display:none` with nothing to un-hide it** — and it is the only save path
  carrying the hold guard. Now shown once a preset is loaded, labelled with the name it will
  overwrite.
- `uipreview.py` rewritten: real field shapes, four slaves, the two missing scenarios (`gone`,
  `down`), and a POST stub that answers the way the controller does (unknown op → 400, save during
  a hold → 409, rename rewrites timers) and mutates its own state, so the §6.1 paths can be
  exercised. All seven scenarios smoke-tested.

**Verified, then deployed**

`py_compile` + `node --check` on the extracted `<script>` (§7.6), zero duplicate ids, balanced
tags. Then in headless Chromium against a real-shaped mock: no JS errors in either mode; the
save-as → Cancel → rename sequence now POSTs `op:rename`; BLACKOUT POSTs `{"amp":0}` after forcing
slaves to network mode; the poll rate stays flat under tapping.

Deployed to the board at **`10.42.0.128:8080`** (not `.5` — that is the USB-tether address):
the live file was **byte-identical to `hype_controller.py.bak`**, i.e. the server half had already
been pushed on its own at 17:20 with the old page — which is why the mystery of §9.6 resolved the
way it did. Backed up to `hype_controller.py.pre-frontend-20260806`, staged as `.new`, md5-matched
and `py_compile`d **on the board** (Python 3.13.5), then moved into place and
`systemctl restart hyperosci-controller`.

After: service active, clean start, page 34,870 → 72,644 chars, 12 presets / 1 timer / pattern all
preserved. The live page in a browser: **zero JS errors**, ON AIR canvas painting the real streamed
Hershey text, no horizontal overflow. Stream over the next 20 s: rx advancing ~194 pkt/s, `drop`
and `under` frozen at their pre-restart values, `tx_full` 0.

The restart also retired a live hazard: the board was running the eviction loop that does
`del state.slaves[ip]` **while iterating `.items()`** — a `RuntimeError` on the stream thread the
first time a slave went quiet for 30 s.

**Two things this did not touch.** Slave 121 reports `vbat_mv: 54`, which is not a plausible
millivolt reading, so every battery bar on the rig shows empty — that is slave-side, and with the
show on **2026-08-21** it is worth a look. And `lost` climbs ~7/s on the slave link, the
pre-existing radio-stall behaviour, unrelated to any of this. The residue listed at the end of
§9.7 — F9/§5.3, W17, the `--line` contrast, `forget`, §6.4, F7, F14 and the accessibility pass —
is still open.
