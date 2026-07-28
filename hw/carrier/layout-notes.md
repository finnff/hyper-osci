# carrier layout notes (board v1.1, notes updated 2026-07-28)

> ## ✅ ROUTED — `carrier.kicad_pcb` is clean on **seed 33** (2026-07-28).
>
> The pre-fab pass (silk stroke → 0.15 mm, SW1 slot → 2× aspect, silk off the
> module bodies) moved copper in three places: SW1's two slot pads out to
> ±2.45 mm, SW1 itself 0.4 mm south, C3 0.79 mm north. That **killed seed 11**,
> and the 16-variant re-sweep that followed found **exactly one clean variant**.
> Seed 33 is now `route.py`'s default. Full account in **"Routing: how it was
> re-swept"** at the bottom of this file.
>
> **✅ Nothing is open before gerbers.** SW1 was the last item and it closed
> 2026-07-28 off the SS12D00 datasheet — no part in hand, no paper doll, no
> layout change (VERIFY item 2 below). The fab package is plotted and committed
> to `fab/`; regenerate it with `tools/plot_fab.py`.
>
> ## 🚀 **ORDERED 2026-07-28** — JLCPCB, qty 10, DHL Express (DDP), $45.42.
>
> Arriving **Aug 5–7**. This layout is now frozen in copper: **any edit to
> `design.py` from here produces a board that is not the one being fabbed.**
> Order record and the four spec deviations: [`fab/README.md`](fab/README.md).
>
> ⚠️ **One assembly note came out of the order.** SW1's pads 1 and 3 are plated
> slots, and the PCB remark permits JLC to substitute ⌀0.90 round holes if their
> process cannot route them (it fits — the 2.5 mm-pitch pin clears a 0.90 hole at
> the 2.45 slot centre by 0.115 mm radially). If they did substitute, the switch
> loses its 0.90 mm sliding window: **seat SW1 before soldering, not after.**

What this board hits, measured 2026-07-28 on seed 33:
`kicad-cli pcb drc --severity-all` reports **0 violations of any severity** and
**0 unconnected items**. **1343 track segments, 48 GND vias**, 30 GND through-hole
pads. Worst GND stitch gap anywhere outside the antenna keep-out: **7.8 mm**
(gate: 12 mm), at (7, 25). The pour came out **whole at phase D round 0 with zero
repair vias** — which is the property a seed is actually selected on.

**Zero 90° track corners.** The **3** acute ones are traces fanning out from a
pad — `VSW` at (48.8, 28.8), `BTN_MODE` at (21.5, 43.1), `VBAT_SENSE` at
(12.1, 3.8) — wide copper, no notch, not routed corners. (It was 2 on seed 11's
board, at different places; the set is a property of the route, not a regression.)

*Counts have moved three times: 1267/45 → 1228/50 when RV1's corrected footprint
forced a fresh route on a new seed, → 1225/50 when the TP4056 gained its USB-C-end
mount row, and → **1343/48** when the pre-fab DFM pass forced seed 11 → 33. The
first two held on seed 11; the third is why it no longer does.*

**On schematic parity:** this used to claim "0 schematic-parity items". That was
never measured — `kicad-cli pcb drc` only reports parity when passed
`--schematic-parity`, and nothing was passing it. Measured, the board has **69**,
none of them defects:

- **60 `footprint_symbol_mismatch`** — 50 because the board stores a *bare*
  footprint name (`R_Axial_…`) while the symbol stores `Lib:Name`
  (`Resistor_THT:R_Axial_…`), and 9 "exclude from BOM" attribute differences.
  The bare name is deliberate; see "what the review changed" below.
- **9 `net_conflict`** on the deliberate no-connects (J3 pads 5–9, JB1 pads 4–5,
  SW1 pad 3, **J10**), where the schematic assigns an `unconnected-(…)` net and
  the board leaves the pad netless.

The noise is unfortunate, because it is exactly what hid the eleven standing
resistors the schematic thought were horizontal. Read parity by *diffing* the
count against a known-good baseline, not by expecting zero.

*The baseline was **67** (59 + 8) until 2026-07-28, when `J10` was added: one
more bare-FPID mismatch for the new component, one more `net_conflict` for its
deliberate no-connect. Same two benign categories, +1 each — which is what a
parity diff is supposed to look like when nothing is wrong.*

`kicad-cli sch erc` likewise reports ~41 `footprint_link_issues` in a headless
checkout — that is the global footprint library table not being visible to
`kicad-cli`, not a design error.

**The schematic is a drawn wiring diagram** (gen_schematic v2, 2026-07-26): placed
functional blocks, wire-by-wire power path, per-connector pin names, safety
annotations, and rendered exports `carrier-schematic.svg` / `carrier-schematic.pdf`
(embedded by `docs/hardware/wiring.md` §3; A3, print the PDF for the bench).
Presentation lives in `gen_schematic.py`; connectivity still comes only from
`design.py` — the generator asserts every drawn wire and label against it, and
`check_netlist.py` re-verifies what KiCad actually parsed. Two plotter quirks worth
knowing: wires must carry an explicit stroke width (`kicad-cli` 10 drops
`(width 0)` wires from SVG instead of substituting the default), and root-sheet
*local* labels would netlist as `/NAME`, so every net name is a global label.

Everything is generated — do not hand-edit `carrier.kicad_pcb`; edit the tools
and re-run:

```
python3 tools/gen_footprints.py   # only if footprints change
python3 tools/gen_schematic.py    # only if design.py changes
python3 tools/gen_board.py && python3 tools/route.py
python3 tools/audit_board.py --verbose      # the geometry gate
# refresh the rendered wiring diagram after gen_schematic.py:
kicad-cli sch export svg -o /tmp/schsvg carrier.kicad_sch && cp /tmp/schsvg/carrier.svg carrier-schematic.svg
kicad-cli sch export pdf -o carrier-schematic.pdf carrier.kicad_sch
```

Use `/usr/bin/python3` — `pcbnew` is only importable from KiCad's own
interpreter, and a conda `python3` on `PATH` will fail to import it.

Three gates, and all three must pass before ordering:

| Gate | What it catches | Status |
|---|---|---|
| `check_netlist.py` | nets vs `design.py`, `config.h` GPIO map, §2/§3 invariants | pass |
| `kicad-cli pcb drc --severity-all` | clearance, connectivity, silk, courtyards | 0 at every severity |
| `audit_board.py` | what DRC cannot know: will a real module *seat* | pass |

Plus one that is **opt-in and easy to forget**: add `--schematic-parity` to the
DRC call whenever `design.py` changes. It is the only thing that catches a BOM
value or footprint drifting between the schematic and the board — which is
exactly what it found in July 2026 (eleven standing resistors the schematic
thought were horizontal). Expect 69 residual items; see above.

## What changed from v1.0

v1.0 was DRC-clean in the sense of "0 errors, 140 warnings". Four things were
actually wrong, and all four are now fixed at the source rather than nudged:

1. **The SuperMini sockets were a mirror image of the real module.** Row A/row B
   were swapped end-for-end, so every net landed on the wrong pin.
   `measured.Placed._check_mirror` now compares the handedness of the declared
   carrier mapping against the handedness of the *photograph* and raises rather
   than generating that board. `JB1` (GPIO5 row) is north, `JA1` (5V row) south —
   which follows from putting the antenna west, and is not a free choice.
2. **The PCM5102A socket offset was a guess, and it was 4.06 mm out.** J2 now
   sits at the measured (−26.917, +0.583) mm from J3 pin 1.
3. **The TP4056 output pads are not on a 2.54 mm grid.** J5/J6 became generated
   footprints at the measured 3.526 / 3.106 mm pitches with a 7.43 mm gutter.
4. **The silkscreen was unreadable in places** — designators over pads, `SW2`
   printed on top of `MODE`, the fab's order number across Q1/JP1. 140 → 0.

## What the 2026-07-26 design review changed

The review (`docs/hardware/pcb-review-findings.md`) is about the §4 power path, and
**none of its fixes touch copper** — R7/R8/R9/D1/U1 are value-only edits. That was
verified rather than assumed: rebuilding with the new values produced a board
identical in every one of ~2500 canonical geometry items (pads, tracks, vias, zone
fills, silk strings and positions). The only bytes that move are five `Value`
properties on **F.Fab**, which is not in the JLCPCB gerber set — the fabricated
board is literally unchanged.

Two things did change in the tools, both found while checking that claim:

- **`FP_OVERRIDE` moved from `gen_board.py` into `design.py`.** Eleven resistors
  stand vertically on the board, but only the layout knew — the schematic still
  said `P10.16mm_Horizontal`, and `--schematic-parity` reported all eleven. Both
  generators now read the same table.
- **`kicad-cli pcb drc --schematic-parity` is opt-in, and nothing was passing it.**
  That is how five stale BOM values could have sat in the board while the schematic
  said otherwise. Run it whenever `design.py` changes.

Two things were tried and rejected, recorded so they are not retried blind:

- **Restoring full `Lib:Name` FPIDs** on loaded footprints silences the
  `footprint_symbol_mismatch` noise, but then DRC tries to resolve the nickname
  against a global footprint library table that headless `kicad-cli` cannot see,
  and 41 `lib_footprint_issues` break the "0 violations" gate. Bare names are the
  lesser evil; the residual parity noise is cosmetic.
- **Forcing VLOAD wide** — ⚠️ **HISTORICAL (seed 77, 2026-07-26). The trade described
  below no longer exists:** on seed 33 VLOAD reaches 1.0 mm over its whole length with
  no floor at all, and measures 19.5 mΩ — better than the 41.4 mΩ the floor was being
  considered *to buy*. The knob still exists; the numbers under it do not apply to the
  current board. Re-measure before reaching for it. Kept because the *failure mode* it
  documents (wider power copper crowds the pour and severs it) is still real and is the
  same one the seed sweep hunts. — `route.py` gained a `ROUTE_WIDTH_FLOOR` env knob for
  this (default off, so the committed board is unaffected). A 14-variant sweep
  says it *is* routable, and improves the power nets a lot; it is not adopted
  because it costs GND stitching, and the review's standing instruction days
  before a fab order was to change nothing in the layout. Measured:

  | Variant | VLOAD SW1→5V | VSW | stitch gap | segs | clean? |
  |---|---|---|---|---|---|
  | **as built** (no floor, seed 77) | **152.1 mΩ** | 24.2 mΩ | **7.81 mm** | 1267 | ✅ |
  | VSW+VLOAD ≥ 0.6, seed 77 | 41.4 mΩ | 8.5 mΩ | 8.6 mm | 1292 | ✅ |
  | VLOAD ≥ 0.6, seed 20260726 | 69.6 mΩ | 8.6 mΩ | 8.6 mm | 1286 | ✅ |
  | VLOAD ≥ 1.0, seed 77 | **19.9 mΩ** | 8.9 mΩ | 10.2 mm | 1602 | ✅ |
  | VLOAD ≥ 0.6, seed 77 | — | — | 8.6 mm | 1219 | ❌ 2 unconnected |

  Only 3 of 12 seeds stayed clean with a floor at all — wider power copper
  crowds the pour and severs it, which is the same failure mode the seed sweep
  hunts. The trade is real in both directions: `VSW+VLOAD ≥ 0.6` at seed 77 is
  the sweet spot (3.7× better VLOAD, 2.8× better VSW, +25 segments, gap 7.81 →
  8.6 mm against a 12 mm gate) and is one command away:
  `ROUTE_WIDTH_FLOOR='{"VLOAD": 0.6, "VSW": 0.6}' /usr/bin/python3 tools/route.py`.
  Left as a v1.2 decision rather than taken unilaterally.

## What the 2026-07-27 pot correction changed

This one *did* touch copper, and it is the only change so far that would have
scrapped the boards.

**RV1's footprint was a drawing of the wrong part.** `RV097NS_Vertical` had row 2
as two oval bracket-lug slots 9.5 mm apart and 7.0 mm behind the pot row, taken
"from the common RV09 drawing" and flagged as unverified since the first commit.
The actual part is **5-pin mono *with switch*, right-angle**: row 2 is a plain
SPST on ⌀1.0 holes **5.0 mm apart, 6.25 mm behind** the pot row, and the
**mounting surface is 5.0 mm in front of** it, not 1.2 mm. Every row-2 pad was
2.25 mm out in X and 0.75 mm in Y. The part would not have gone in.

Settled by the seller's mechanical drawing, which agrees to the decimal with
KiCad's stock `Potentiometer_Alps_RK097_Single_Horizontal_Switch` (from ALPS
`rk097.pdf`) — and, independently, by the drawing's 9.5 × 11.35 mm cross-section
matching the 2026-07-18 calipers' 9.5 × 11.3.

Consequences that were not obvious:

- **RV1's Y stopped being a free parameter.** The mounting surface has to land
  on the board's south edge, so the anchor (the wiper pad) is pinned at
  **y = 45.0**, 2.5 mm north of where it sat. `gen_board.py` says so in a
  comment; do not "tidy" it.
- **Seed 77 died with it.** Moving five pads was enough to break the routing
  seed: phase D ended `STUCK — 3 clusters left, no legal bridge spot`, and DRC
  reported 2 unconnected `GND_main` zone islands. This is the same pour-severing
  failure the VLOAD width sweep hit above. **A stale seed does not fail loudly —
  it fails as a severed pour**, so re-sweep whenever copper moves.

  A 24-variant sweep (6 seeds × 4 halo settings, `tools/search.py --spec`) put
  the damage in context — only **3 of 24** came back fully clean. *(Historical:
  this is the 2026-07-27 sweep that chose seed 11. Seed 11 was itself killed the
  next day by the pre-fab DFM pass — see the 2026-07-28 sweep at the bottom of
  this file. The table is kept because the pattern it shows repeated exactly.)*

  | variant | unconn | unrouted | gap | segments | |
  |---|---|---|---|---|---|
  | halo-off, **seed 11** | 0 | 0 | **7.81** | **1227** | ✅ adopted |
  | halo-off, seed 33 | 0 | 0 | 7.81 | 1349 | ✅ |
  | halo 1.0×1.5, seed 33 | 0 | 0 | 7.81 | 1522 | ✅ |
  | halo-off, **seed 77** | **2** | 0 | 8.6 | 1189 | ❌ the old default |
  | halo 1.3×4.0, any seed | 0 | 0 | 8.6 | 1672 | ⚠️ identical for all 6 seeds, and all 6 carried the mitre bug below |

  Seed 11 became `route.py`'s default here, with 77 recorded beside it — **and was
  itself replaced by 33 one day later**; see the bottom of this file. ("halo-off"
  is `ROUTE_GND_HALO_MM=0`; the repo default of 1.3 mm behaves the same, because
  the halo's *cost* is 0 and that is what gates it — `route.py` wraps the whole
  halo in `if HALO_COST > 0`.) Worth noting in hindsight: **seed 33 was already
  clean here too**, second on the list. It was passed over for having 122 more
  segments, and it is what the board runs on now.
- **A latent router bug surfaced.** Five of the 24 variants carried one
  `track_dangling`: `mitre_board` was building a zero-length "mitre" at a
  degenerate vertex — two legs leaving the same point in the *same* direction, a
  doubled-back spike rather than a corner, which the dot-product filter lets
  through whenever the spike is under 0.5 mm. Fixed at the source (skip before
  mutating), plus a phase F sweep that drops any zero-length track before the
  pour is filled.
- **A neighbour's designator was sitting inside RV1's body.** `find_spot`
  seeded its occupancy map with the bounding box of each individual silk
  *stroke*, and a rectangle's four strokes are slivers — so the enclosed area
  read as free and `SW2`'s designator, walked ~9 mm east by the outward ring,
  landed inside the pot's outline where it reads as the pot's label. (Same
  family as the `MODE MODE` collision §6.1 rule 2 already records.) Fixed by
  filling the hull of any footprint whose silk spans more than `BODY_MM` = 8 mm
  in both axes — swept, not guessed: at 4 mm and 6 mm the small outlines fill
  too and R8's designator runs out of room. `SW2` then wants TP3's gap, so TP3
  gained a `REF_PREFER` entry, which places it first. `SILK_UNPLACED` stays 0.
- **The switch is real, and it is parked.** Pads 4/5 are an SPST, not lugs, and
  both go to GND — deliberately. There is no safe spare GPIO (GPIO0 is
  `MIC_OUT`; GPIO8/9 are strapping pins that must be high at reset, and this
  switch makes at one end of rotation; GPIO21 is the console UART TX, an
  output), and this variant has **no bracket lugs**, so soldering both pins into
  the pour is the part's only rear anchoring against a bare shaft turned by
  hand. Closing the switch shorts GND to GND.

## What the 2026-07-28 TP4056 mount row changed

The second copper change, and the reason is mechanical rather than electrical:
**J5/J6 are four pins in one column at one end of the module, and the USB-C jack
is 21.65 mm away at the other.** A row of pins resists rotation about its own
axis only by bending, so the jack — the one connector handled on every charge
cycle, at 10–20 N of insertion force — sat on a diving board.

The module already carried the fix: two ~2.8 mm bare-copper pads on ⌀1.68 mm
plated holes at its USB-C-end corners, in line with OUT+ (`+` silk) and OUT−.
The carrier already had a pad on one of them — `J9`, drawn as a **wire pad** for
the IN+ sense tap. So:

- **`J9` became a socket and moved 0.65 mm west** onto the real hole position.
- **`J10` was added** on the other corner, **on no net**.
- `WirePad_D1.0` was replaced by `TP4056_MountPin` (same 1.0 mm drill / 2.2 mm
  pad — the name and the docstring were the only things that were wrong).

Three things that were not obvious:

- **The offset is a caliper number overriding the photogrammetry**, and it is the
  only place in `measured.py` where that happens. The picks say 22.30 mm; the
  caliper says 21.65 mm; the module's hole gives 0.39 mm of slack on a 0.64 mm
  square pin. 0.65 mm of disagreement is therefore the difference between a pin
  that drops in and one that does not, so it had to be settled rather than
  averaged. `hw/pin_locs/TP4056.txt` says why the picks lose: the frame is a
  **similarity** calibrated on a 17.30 mm reference measured *across* the output
  row, so the along-row axis is metric and the perpendicular one is not.
  Cross-check: the picks put these pads 1.68 mm inboard of the clicked east edge,
  and `25.2 − 1.935 − 1.68 = 21.59`. Rule now stated in `measured.py`: **across
  the row → photogrammetry, along the module → caliper.**
- **`J9`/`J10` are deliberately NOT in `audit_board.py`'s `FIT_MAP`.** Fitting
  them would re-measure that override and report it as a 0.65 mm error. What
  covers them instead is the module's own hole slack, and the escape hatch if a
  pin will not enter is to drill those two module holes to 2.0 mm (0.39 → 0.55 mm
  of slack, 0.4 mm of annular ring left).
- **`J10` is netless on purpose.** pcb.md §2 asserts TP4056 `IN− ≡ OUT−`, which is
  the usual protected-TP4056 topology, but it has never been ohmed on this
  module — and if it is wrong, bonding it to carrier GND shorts across the
  DW01/FS8205 and the cell loses its protection. A floating pin anchors just as
  well. This is also why the parity baseline gained a `net_conflict`.

**Seed 11 survived this one.** Unlike the RV1 correction, adding two pads in the
empty east strip (the only copper there was one VBUS_CHG run from TP1 to J9) did
not break the route: phase A landed 0 failures on attempt 11, phase D found a
single GND cluster in round 0 and placed **zero** bridging vias, and the stitch
gap stayed at 7.8 mm. No re-sweep was needed — but it was not assumed, it was
checked, because a stale seed fails silently as a severed pour.

## Module fit — measured, not assumed

Every socket position comes from the photogrammetry in `hw/pin_locs` via
`tools/measured.py`; nothing is typed in by hand. `audit_board.py` re-fits the
measured pad pattern to the board's actual pads with one translation per module,
so what is left is *shape* error — and shape error is what stops a module
seating:

| Module | Pads checked | Worst residual | Gate |
|---|---|---|---|
| PCM5102A | 15 | 0.180 mm at J2.5 | 0.25 mm |
| ESP32-C3 | 16 | 0.100 mm at JA1.4 | 0.25 mm |
| TP4056 | 4 | 0.081 mm at J6.1 | 0.55 mm slack, gated at 0.25 |

J9/J10 are absent from that table on purpose — see the 2026-07-28 section above.

0.25 mm is the misalignment a 2.54 mm female header accepts before a rigid
module pin fouls the barrel. Re-measure a module, drop the new CSV in, re-run
`gen_board.py`, and the board follows it.

## Floorplan deviations from pcb.md §6

- **TP4056 sits SOUTH of the DAC**, not colinear with it: SuperMini (~24 mm)
  + PCM5102A (~32 mm) + TP4056 (~26 mm) don't fit on a 70 mm edge. USB-C still
  exits east.
- **J5/J6 are two pad pairs**, not butted 1×2 sockets, at the measured pitches
  (see above), **and there is a second mount row 21.65 mm east** (J9 = IN+,
  J10 = IN−/NC). Fit **six** machined single sockets, or solder wires and give up
  removability.
- **D1/D3 live in the power cluster, not the island**: they carry
  GATE/VBUS_CHG/VLOAD, and their 7.62 mm pad rows would have walled off the
  §6.3 neck. D1 sits north of the J5 column at x 45.42…55.20, y 21.73…24.27 —
  which puts it under the **PCM5102A** (body x 23.60…55.62, y 6.22…23.59), not
  the TP4056 (whose body starts at y 24.58); DO-35 lies flat. D3 (DNP) NORTH of
  the TP4056 body, **outside** every module outline, so it can be retrofitted
  without pulling anything.
- **Executing the JP1 fallback means lifting BOTH modules.** JP1 (54.6, 27.9)
  and Q1 (50.3, 27.9) are under the TP4056; **D1** (x 45.42…55.20, y 21.73…24.27) and
  **D2** (x 40.55…45.10, y 9.57…22.95) are both under the PCM5102A. The amended §4.4 recipe requires D2 off
  as well as Q1/U1/Q2/D1 — with D2 fitted, a bridged JP1 puts the raw charger
  input on the cell. So decide fit-or-DNP for the power path **before** the
  TP4056 is wired down; if J5/J6 end up soldered rather than socketed, JP1
  becomes a desoldering job.

## Parts under modules, and header access

`audit_board.py` gates this: nothing taller than the 8.3 mm socket standoff may
sit inside a module's measured body outline. As built, nothing does. The two
parts that were specifically questioned:

- **C1** (220 µF, 11.5 mm tall) occupies x 22.57…29.43, y 24.82…31.81. Its west
  edge is 2.27 mm east of JA1 pin 1's centre (x = 20.30) — about 1.5 mm of open
  air past the pad itself, enough to get a jumper wire onto the inner header,
  but it is the tightest access on the board. Check it on the paper doll.
- **Q2** (TO-92, 5.2 mm) occupies x 4.96…12.12, y 26.75…31.54. The ESP32 body's
  south edge is y = 25.87, so Q2 clears it by 0.88 mm and is not under the
  module at all.

Module bodies as built (from the measured outlines):

```
ESP32C3    x  -0.66 .. 21.71   y   7.98 .. 25.87
PCM5102A   x  23.60 .. 55.62   y   6.22 .. 23.59
TP4056     x  43.91 .. 69.79   y  24.58 .. 42.04
```

## §6.3 AGND island — how it's actually implemented

One single `GND_main` zone on both layers; the island is carved by moat
rule-areas with a 3 mm neck at x 32–35, y ≈ 13.5. (A separate same-net island
zone does NOT work: KiCad never merges same-net zone fills — the lower-priority
fill is excluded, leaving a zero-width seam that connectivity ignores. Found
the hard way.)

Reinforcements, all generated:

- **Neck strap**: explicit 1.0 mm GND track pair (F+B) at x = 33.02,
  y 8.9 → 17.5 through the neck, with 3 vias. The router hard-reserves this
  corridor for GND (`NECKS` in `route.py`).
- **Seeded stitch grid**: 33 GND vias on a 7 mm grid placed BEFORE routing so
  the router treats them as obstacles. Post-route stitching cannot find legal
  via spots in signal-dense quadrants; a pre-seeded via inside a pour fragment
  anchors it to the other layer. (Swept: 5.5 mm is worse, not better — it
  crowds the router into a physical DRC violation and a severed fragment.)
- **Zone minimum thickness 0.15 mm**, not KiCad's 0.25 mm default. The routing
  chops the pour into ribbons and a 0.25 mm floor discards every sliver
  narrower than that. Swept: the default leaves a severed fragment. JLCPCB's
  1 oz minimum copper width is 0.127 mm, so 0.15 is in spec.

## route.py phases

- **A** — net routing, grid A* on a 0.127 mm lattice over exact pad geometry.
  Net order decides whether the board routes at all, so it climbs a "promote
  the nets that just failed" ladder for 8 attempts, then shuffles from a fixed
  seed, and keeps the *best* attempt rather than the last one. Seeded, so the
  board is reproducible; the pad walk is sorted for the same reason (KiCad
  hands out fresh UUIDs on load, and an unsorted walk made two runs of the same
  design produce 1300 and 1716 segments).
- **mitre** — collapses the staircase vertices A* leaves behind into true 45°.
  Both legs are cut back by `d = min(0.6, trace width, both leg lengths)` and
  bridged with a diagonal; because `d ≤ width`, the bridge stays inside copper
  the two original segments already covered, so it can never open a clearance
  violation. Right angles are always cut. Acute vertices are cut too — they are
  the sharper acid trap — but only when one leg is a single grid step, i.e. the
  staircase artefact; a genuinely acute junction is two traces fanning out from
  a pad and chamfering that would be pointless. Runs on the *board*, not the
  router's segment list: only the board has the whole picture (gen_board's neck
  strap is copper too), and the board is what the audit measures.
- **B** — stitch GND pads the pour could not reach (driven by a real DRC run).
- **C** — pour-stitching vias on a 4 mm grid, only where both layers are free
  and both sit on the main pour.
- **D** — union-find over the filled polygons; bridge every stray cluster back
  to main with a via, or failing that A*-route a GND track to it.
- **E** — prune GND vias the pour never reached. **Folded into the D rounds**,
  not run after them: dropping a via changes the fill (island removal deletes
  the copper that via was holding), which can sever a region that reached main
  through it. The last thing the loop sees must be the state that gets saved.

### The bug that cost the most time

Phase D kept declaring a fragment at (16, 37) — the one holding R8's GND pad —
`UNBRIDGEABLE`, and no amount of moving R8 or re-seeding the router fixed it.
It was not a clearance problem: there is free B.Cu running west out of that
fragment to main copper about 6 mm away. The main cluster spans the whole
board, and phase D sampled its candidate target points in raster order and
truncated to the first 40 — so every "nearest target" was 40 mm away in the
north-west corner, and every A* call failed. Sorting the candidates by distance
to the stray fragment before truncating fixes it outright, with R8 left where
it was. GND pour-tie stitches may also fall back to 0.2 mm (they carry no
current; JLCPCB's 1 oz minimum is 0.127 mm).

## Searching the layout instead of guessing at it

`tools/search.py` runs board variants **in parallel** — each in its own scratch
directory, nothing in the repo touched — and ranks them on unconnected items,
physical DRC, silk, unrouted nets, unplaced designators, right angles and stitch
gap. Every knob is an environment variable (`ROUTE_SEED`, `ROUTE_GND_HALO_*`,
`ROUTE_STITCH_PITCH`, `CARRIER_ZONE_MIN`, `CARRIER_SEED_PITCH`,
`CARRIER_PLACE={"R8":[24.5,37.5,0]}`), so a variant is data rather than an edit.

```
python3 tools/search.py --jobs 24 --stage full
python3 tools/search.py --spec my_variants.json
```

This is how the zone-thickness, seed-pitch and GND-halo choices above were
settled, how the R8 fragment was shown to be a code bug rather than a placement
problem, and how the default `ROUTE_SEED` was chosen. Ten seeds, sixteen
variants, one wall-clock pass:

| Variant | unconnected | physical | unrouted | stitch gap | segments |
|---|---|---|---|---|---|
| **seed 77 (default)** | **0** | **0** | **0** | **7.8 mm** | **1267** |
| seed 20260726 | 0 | 0 | 0 | 8.6 mm | 1286 |
| seeds 55, 88 | 1 | 0 | 0 | 8.6–10.2 | severed pour |
| seeds 11, 22, 33, 44, 66, 99 | 1–2 | 0 | 1–2 | — | a net went unrouted |
| GND halo on | 1–2 | 0 | 1–2 | — | halo costs routability |
| zone min 0.25 (KiCad default) | 1 | 0 | 0 | 8.6 mm | severed pour |
| seed pitch 5.5 mm | 1 | **1** | 0 | 7.6 mm | over-crowded |
| R8 moved east / flipped | 1 | 0–2 | 0–1 | 9.9–10.2 | also loses a designator |

Two things worth reading off that table. First, moving R8 — the obvious
"fix" — was never the answer; it was a code bug. Second, routing success is
**not** seed-independent: six of ten seeds leave a net unrouted, and two more
route fully but strand a pour fragment. Seed 77 is the only one whose pour comes
out whole with *no* repair at all (phase D finds one cluster in round 0 and
places zero bridging vias), which is why it is the default. `route.py` exits
non-zero unless every net routed AND the pour is a single cluster, so a bad run
announces itself; re-run it, or hand it a different `ROUTE_SEED`.

## Silkscreen

Every legend and every reference designator is placed by search, not by a
footprint's default offset. Each string walks a 16-direction ring outward and
takes the first spot clearing the pads, the board edge and everything already
placed, shrinking toward the 0.8 mm DRC text floor before it gives up; if it
still cannot sit anywhere, a local sweep and then a wider — but still bounded,
see below — sweep run before the build fails. Module outlines are corner brackets on
F.Silkscreen (the full rectangle stays on F.Fab) so they no longer rule a line
through every socket inside them.

Every stroke is **0.15 mm**, not 0.12 — JLCPCB's minimum silkscreen line width.
Below it the fab may thin the stroke or drop the item, and until 2026-07-28
*all 70 text items on the board*, every refdes and every safety legend, were at
0.12. Text **height** stays at 0.8 mm, which is JLC's absolute minimum (1.0 mm
is only recommended): raising it would grow every bounding box on a board that,
as the rest of this section explains, has no room to give.

### The module shadow (2026-07-28)

The three socketed modules cover **1180 mm² of a 3500 mm² board**, and until
2026-07-28 the placement search did not know they existed — `module_outline()`
draws them with `claim=False` and its return value was discarded. So the search
treated that third of the board as prime empty silk, and put the battery
warnings, the pad names and the board's own name under it. **Silk you cannot
read once the unit is assembled is silk that is not there.**

The bodies are now in a `shadowed` list that `_free()` honours, with one
exemption. Some legends are read *before* the modules drop in, and for those
the shadow is not a problem:

- the module-name and orientation legends (`ESP32-C3 SuperMini`, `GY-PCM5102A`,
  `TP4056`, `USB-C`) — they say what goes here and which way round;
- `IN+` / `IN-` on the TP4056's mount row, and `OUT-` / `B-` / `B+` / `OUT+` on
  J5/J6: these name pads the module physically sits on top of, so there is no
  reading of them that happens with it fitted;
- a **designator, under its own module only**. By the time you read it the part
  is in your hand. Straying into a *neighbour's* shadow is still a failure —
  that is the same defect as SW2's designator landing inside RV1's outline.

`audit_board.py`'s `silk shadow` section is the assertion that this stays true.
Run against the pre-fix board it reports **13 failures**; against this one, 0.

### Three other things the same pass fixed

- **The whole-board sweep is now bounded** (`SCAN_REACH = 12 mm`). An unbounded
  sweep does not fail, it *wanders*: `VERIFY CELL POLARITY` came out 34 mm from
  the battery connector, in the middle of the R6/R12 field, and `B- is NOT GND`
  14.7 mm from the pad it warns about. A legend that has left its subject behind
  is worse than a missing one, because it reads as a caption for whatever it
  landed on. Past the reach it is now reported instead.
- **Designators are placed most-constrained-first**, re-counting the free spots
  after each placement, instead of alphabetically. Alphabetical is
  first-come-first-served by accident of name, which is exactly why SW1, SW2,
  TP4 and TP5 — late letters, crowded south — kept losing their last spot to a
  part that had a dozen others. (The old REF_PREFER note about placing TP3
  before SW2 was this same problem, handled one collision at a time.)
- **A designator may fall back into its own courtyard.** "Do not sit on your own
  part" is a legibility preference, not a rule, and it is the weakest one here;
  `occupied` still keeps it off the pads and off every other legend.

### What had to give

Blocking a third of the board costs more silk area than the board has. Three
legends were rewritten rather than dropped, and two parts moved 0.4 and 0.8 mm:

- **`VERIFY CELL POLARITY` is gone**, replaced by **`+ cell -`** placed in the
  strip between the TP4056's south edge and SW1 — the only free silk that is
  both next to J8 and still visible with the modules fitted. J8 has no clear
  silk on any side of it: module north, its own connector outline south, SW1
  west, H4's 6.4 mm pad east. **SW1 moved 0.4 mm south (y → 46.4)** to make that
  strip 1.86 mm tall instead of 1.46, which is what a line of 0.8 mm text needs.
- **`B- is NOT GND` became a `NOT GND` pad legend on J5.2**, directly under the
  `B-` label. Two lines of it in the open strip west of the module do fit, but
  only at the cost of another designator, and at the pad is where the eye
  actually is when a probe is about to go somewhere it should not.
- **The board name lost its date and moved to the mid-south band.** Outside the
  shadows there is no 12 × 3 mm patch anywhere; two shorter lines fit at
  (27.0, 37.3). The date is in git and on the back silk.
- **C3 moved 0.79 mm north (y → 2.0).** Between its own body silk and the DAC's
  north edge there was a 1.6 mm gap, and a designator is 1.86 mm tall — C3 was
  the one part on the board with literally nowhere to put its name.

Result: **`SILK_UNPLACED 0`**, which is the build's own hard gate.

### Deliberate rather than searched

- The **fab's order number** goes on the *back*, inside the antenna keep-out.
  That strip carries no pour and no stitching by construction, so it is the one
  place on the board where silk is guaranteed to be over bare laminate.
- **X and Y** are printed by rewriting the RCA footprint's own `S` label per
  instance. Placed by the ring search they drifted inboard until they sat
  nearer each other than their own pads — and "which pair is X" is the single
  question that legend exists to answer.
- **`+ cell -`**, the board name and `UNIT #__` now carry fixed anchors too, for
  the reasons above.

### Still at 0.12 mm: 282 footprint outlines

The 0.15 mm floor covers everything `gen_board.py` draws — all 78 text items and
its own module brackets. It does **not** reach the silk that stock KiCad
footprints carry, which is 0.12 mm by library convention: 282 part-body outlines
and polarity marks. In practice JLCPCB prints those fine (it is the same silk on
every KiCad board they build), and they are decoration rather than information,
so this is an accepted residual rather than an open defect. If it ever matters,
bump them in the footprint loop in `gen_board.py` — but expect to re-fight the
placement, because the board converges with no slack at all.

## Nets narrowed at pinch points (width fallback, §6.4 policy)

Measured off the board, not recalled — an earlier version of this list was wrong
in four of five clauses:

*From `tools/measure_copper.py` on the routed board, seed 33, 2026-07-28.*

| Net | Target | As built | Breakdown |
|---|---|---|---|
| BAT_PLUS, BAT_MINUS, VBAT_OUT, VBUS_CHG | 1.0 | **1.0** | never narrowed (74.8 / 19.9 / 18.3 / 51.5 mm) |
| **VSW** | 1.0 | **0.3** | 51.8 mm @ 1.0, 74.4 @ 0.8, 23.1 @ 0.6, **60.0 @ 0.3** |
| VLOAD | 1.0 | **1.0** | 82.6 mm, never narrowed |
| 3V3 | 0.6 | **0.6** | never narrowed (88.7 mm) |
| MIC_OUT | 0.3 | 0.3 | 67.8 mm |
| DAC_L/R, SCOPE_X/Y | 0.5 | 0.4 | short pad fan-outs, 4–10 mm each |

**Read this table against the previous one before trusting any memory of it: VSW
and VLOAD have swapped roles.** Every earlier version said VLOAD was the narrow
net (91 mm @ 0.3, 152 mΩ) and VSW was fine. On seed 33 **VLOAD is 1.0 mm over its
whole 82.6 mm and measures 19.5 mΩ** SW1 → SuperMini-5V, and VSW is the one
carrying 60 mm of 0.3 mm copper.

That is much less alarming than the breakdown looks, and the nodal number is why:
**Q1 source → SW1 is 30.7 mΩ.** VSW is a fan-out node (D2, JP1, Q1, Q2, R12, R7,
SW1, TP2) and most of the thin copper sits on *sense and bias* branches — R7's
divider tap, Q2's base network, the TP2 testpoint — not the load path, which also
has 15 vias giving it parallel routes. "60 mm @ 0.3" is not 60 mm of load current
through 0.3 mm copper.

Q1 source → 5V pin end to end is now **≈50 mΩ** (30.7 + 19.5, plus SW1's contact
resistance, which is not copper): 17 mV at a 350 mA burst, 6 mV at the 125 mA
NETWORK average, against the ~176 mΩ the old table implied. **C1 is 3.3 mΩ from
the 5V pin**, still on the load side, so the bulk cap sources the burst locally
regardless — the argument that made the old number acceptable survives the
inversion intact.

A caution the three stale generations of this table have earned: **it is wrong by
default after any re-route, and it has twice been wrong in the flattering
direction.** Regenerate, do not recall.

## VERIFY before ordering (paper-doll + part-in-hand)

1. ~~J2↔J3 DAC socket geometry is PROVISIONAL~~ — **RESOLVED**, measured; the
   old guess was 4.06 mm out. Still worth a paper-doll check:
   `paper-doll-1to1.pdf` is current.
2. ~~**SW1 pitch unknown** until the part arrives~~ — **RESOLVED 2026-07-28
   from the SS12D00 mechanical drawing**, without the part and without the
   paper doll, and nothing in the layout moved.
   **Pitch is 2.5 mm** — neither of the two the footprint was drawn for, and
   that is exactly why the slot was drawn instead of a hole. It lands 0.05 mm
   off the centre of the 2.00–2.90 mm window, the most comfortable place in it.
   **Pins are 0.5 × 0.3 mm**, so the 0.90 mm slot width leaves 0.30 mm a side
   and the ⌀0.9 centre hole swallows the middle pin.
   **Body is 8.5 × 3.7 mm on the pin centreline** (centreline confirmed against
   the part), which sits inside the 9.0 × 4.0 silk with 0.25 mm a side in X and
   0.15 in Y, and inside the 10.0 × 5.0 courtyard with 0.75 / 0.65.
   **Nothing overhangs it:** the TP4056 body stops 2.51 mm north, J8's
   courtyard 0.96 mm east, the south board edge 1.75 mm south. The 1.5 mm
   square actuator sweeps X 48.23–51.73 over its 2 mm of travel with 3.46 mm of
   nail room to J8. Handle length never entered the fit — nothing sits above
   the switch, so the 6 mm handle is an enclosure question only.
   **Function checks out too:** the datasheet's middle terminal is the common,
   and the board has pad 2 = VLOAD on the middle, pad 1 = VSW, pad 3 netless.
   *Still open, but at assembly rather than before ordering:* whether the part
   has a locating lug. The drawing has no bottom view and dimensions no post,
   so this is the one thing paper cannot settle; the footprint has the three
   signal holes only, so clip any lug.
   **Re-cut 2026-07-28:** the slots were 1.44 × 0.90 mm, an aspect of 1.6, and
   **JLCPCB will not plate a slot under 2×**. That gets you a DFM query against
   the order date, or a silent conversion to a round ⌀0.9 hole at one fixed x —
   at which point *neither* pitch fits and the boards are scrap. They are now
   **1.80 × 0.90 (aspect 2.0)** centred at ±2.45, so a pin may sit 2.00–2.90 mm
   from the middle pin. The pad stayed 2.5 mm long: growing it with the slot
   left 0.20 mm to the centre pad, which clears JLCPCB's 0.127 mm floor but not
   this board's own 0.25 mm netclass clearance — four DRC violations on a gate
   that has to read zero. Check it on the drill file, not the board: the routed
   slots should be `G00X47.08 → G01X47.98` and `G00X51.98 → G01X52.88`, i.e.
   0.90 mm of travel on the 0.900 tool (it was 0.54).
   **SW1 also moved 0.4 mm south, to y = 46.4** — see the silkscreen section.
3. ~~**RV1 (RV097NS) bracket lug geometry** is from the generic RV09 drawing —
   least-certain footprint on the board~~ — **RESOLVED 2026-07-27, and it was
   wrong.** The part is *5-pin mono with switch*, right-angle: row 2 is an SPST
   on ⌀1.0 holes 5.0 mm apart and 6.25 mm behind the pot row, not two oval lug
   slots 9.5 mm apart and 7.0 mm behind. Every row-2 pad was 2.25 mm out in X
   and 0.75 mm in Y, and the mounting surface 3.8 mm out — the part would not
   have gone in. Redrawn from the seller's mechanical drawing, which matches
   KiCad's stock `Potentiometer_Alps_RK097_Single_Horizontal_Switch` exactly.
   **RV1's Y is now constrained, not chosen:** the mounting surface is 5.0 mm
   south of the anchor (the wiper pad), so y=45.0 lands it on the board's south
   edge with the bushing and shaft overhanging. A paper-doll pass is still
   worth doing, but it now confirms a drawing rather than a guess.
4. ~~**TP4056 pad-row edge offset**~~ — **RESOLVED 2026-07-27, and it seats as
   drawn.** The pad column sits 0.05 mm south of the module's body centre
   against 0.12 mm modelled: **Δ 0.07 mm on a 0.25 mm gate**, so there is no
   N/S shift and no copper moves. Two by-products: the module's west edge
   carries **two depanelization nubs** (+1.6 mm at the OUT−/OUT+ corners) that
   the photogrammetry's hand-clicked corners averaged across — **file them
   flush**, or the SW corner comes within ~0.7 mm of RV1's body instead of
   2.4 mm; and the body's east end lands ~0.8 mm west of the modelled 69.79,
   which only adds board clearance but changes the enclosure's charge-port
   cutout. `TP4056.json` is deliberately **not** edited — every delta is inside
   the gate or adds clearance, and touching the outline would force a
   `gen_board` + `route` re-run and a seed re-sweep for nothing. Full reduction:
   `docs/hardware/measurements.md` §Nubs.
5. **JST cell polarity** (silk warns): B− is NOT GND — verify the wiring of the
   cell before first plug-in.
5b. **The two TP4056 mount pins (J9/J10) are the one fit on this board measured
   with a caliper rather than the photogrammetry** — 21.65 mm from the output
   row, against 0.39 mm of slack in the module's own 1.68 mm hole. Dry-fit it:
   six pins in the carrier's sockets, module lowered on. If a mount pin will not
   enter, **drill those two module holes to 2.0 mm** (they carry no current here,
   and 2.8 mm pads leave a 0.4 mm ring). Do not force it — bending a pin at the
   far end of a 21.65 mm lever is how you damage the output pads.
6. Antenna keepout (x < 6, y 6–28) is pour-free on both layers; a few thin
   signal traces may pass the region edge (soft-penalised, not forbidden).
7. **R10** is a standing axial at x 54.35…59.49, y 4.83…7.88, and the DAC body
   reaches x 55.62 / y 6.22 — so its corner *is* under the module. Whether it
   clears depends on a number nothing has measured: `audit_board.py` calls a
   standing `R_Axial_DIN0207` **7.5 mm** and passes it with 0.8 mm of margin
   against the 8.3 mm standoff, while `design.py` calls the same package
   **9 mm**, which would not fit at all. **Fit R10/R11 as 0 Ω wire links**
   (pcb.md §10 Q2, closed 2026-07-28) — flat, so the question stops existing,
   and electrically indistinguishable from 100 Ω given the module's own ~470 Ω
   output filter. If a future build populates real resistors here, reconcile
   `PART_HEIGHT_MM` against a caliper first and seat them low.
8. **TO-92 lead order for U1 and Q2** — added 2026-07-26. `design.py` wires U1 as
   pad 1 = REF, pad 2 = ANODE, pad 3 = CATHODE (onsemi numbering). TI numbers the
   same TO-92 package pin 1 = K, pin 2 = A, pin 3 = REF, and since the anode is
   the centre lead either way, a TI part seats perfectly while sitting backwards.
   The footprint is an inline 3-pad strip, so nothing about the silk catches it.
   Q2 (BC557) has the same trap. Ohm REF against R7/R8 before first power-up.
9. **The §4.4 power-path bench test is deferred to the assembled carrier**
   (TP1/TP2/TP3, Aug 11–16) with the amended procedure — sagged 4.4/4.5/4.6 V
   source, milliohm shunt, ≤ 1 mA criterion. It is *not* an order gate. Until it
   passes, build to pcb.md §4.5 plan A: JP1 bridged, power-path parts omitted.

Of these, only **2, 3 and 4** actually gate the fab order — and 2 (SW1) is not
resolvable until the part arrives, so in practice it is 3 and 4, both 10-minute
checks with parts already in hand.

## Files

- `carrier.kicad_pro/.kicad_sch/.kicad_pcb` — the project (generated)
- `paper-doll-1to1.pdf` — 1:1 top view for the paper mockup
- `render-top.png`, `render-bottom.png` — 3D previews
- `tools/design.py` — single source of truth (nets, parts, deviations)
- `tools/measured.py` — module geometry from `hw/pin_locs` photogrammetry
- `tools/audit_board.py` — geometry gate (module fit, silk, routing, clearance,
  and — since 2026-07-28 — legends printed where a fitted module hides them)
- `tools/measure_copper.py` — as-built track widths and pad-to-pad DC, so the
  §6.4 table can be regenerated from the board instead of from memory
- `tools/search.py` — parallel layout/router parameter sweep
- `tools/check_netlist.py` — respin gate; run after any `design.py` change
- `tools/gen_artwork.py` — traces a 1-bit image into `art/*.json` silk polygons
- `art/484848-mono.png`, `art/484848.json` — the back-silk mark (source + traced)

## Back silkscreen artwork (2026-07-28)

The back carried **2 silk items against the front's 374**, and the fab prints
that layer whether or not anything is on it, so the space was already paid for.
It now holds the `48` mark and a `ponkiePCBv1` wordmark.

Placement was searched, not eyeballed: the largest pad-clear rectangle on B.Cu
is **14.5 × 15.0 mm at (26.8, 9.2)**. The mark takes the top 10.4 mm (its traced
aspect is 1.393) and the wordmark sits in the 4.6 mm underneath, so the two read
as one lockup. *Both* windows quoted during the search — a 14.8 × 15.2 mm block
and a 22 × 4 mm "via-free banner" — were found by independent searches and
**overlap**; only one of them can hold artwork, which is why the wordmark ended
up under the mark rather than in a separate strip.

Four things worth knowing before touching it:

- **It is not copper.** Adding, moving or removing silk artwork cannot
  invalidate `ROUTE_SEED`. Verified rather than assumed: regenerating with the
  artwork in place re-routed to *the same* 1343 segments / 48 vias / 0 failures,
  and `measure_copper.py` returned identical widths.
- **It is board-level graphics, not a footprint, and that is deliberate.** A
  footprint with no schematic symbol is an `extra_footprint` under
  `--schematic-parity`, which would move the documented 69-item baseline for a
  decoration. Parity stayed at exactly 69 (60 + 9) with the artwork in.
- **Back-layer graphics are stored in top-view coordinates**, so `back_art()`
  mirrors in X. Without that the mark reads backwards on the physical board.
  The check is `render --side bottom`, not the editor.
- **`bitmap2component` was not used** — it is GUI-only, does nothing under
  xvfb, and emits an opaque `.kicad_mod` nothing here could regenerate.
  `gen_artwork.py` uses `potrace` (`-a 0 -n`, so polygons rather than Béziers),
  keyholes the holes — KiCad graphics have no hole primitive, and without it the
  counters of the `8` fill solid — and simplifies to ~0.03 mm, an order of
  magnitude under what the fab holds. 822 → 324 vertices, no visible change.
  `potrace` is needed only to re-trace; `gen_board.py` reads the committed JSON.

⚠️ **The plot has to include `B.Silkscreen`** or none of this ships — see
pcb.md §9. No gate in this repo catches that, because the board is correct and
only the gerber set is wrong.

## Routing: how it was re-swept (2026-07-28)

The pre-fab pass moved copper, so the shipped seed had to be re-swept. **Done —
seed 33 adopted.** This section is the record, kept because the failure modes in
it recur every time copper moves.

### What killed seed 11

Three small moves: SW1's two slot pads out to ±2.45 mm, SW1 itself 0.4 mm south,
C3 0.79 mm north. Re-swept on the new copper, seed 11 came back **1 unconnected
/ 1 unrouted** — `BAT_PLUS`, a 0.127 mm stub and R1 pad 1 left off the net.

Worth dwelling on: **seed 11 had held through two previous re-routes** (RV1's
footprint correction, the TP4056 mount row). It was not a fragile choice. Sub-
millimetre pad moves killed it anyway. There is no amount of "the seed has been
fine so far" that substitutes for re-sweeping.

### Why the first attempt produced nothing

`tools/search.py --stage router --jobs 8` was run on an 8-core laptop. All 8
first-wave variants hit `CARRIER_SEARCH_TIMEOUT` (default **1800 s**) and were
recorded as `FAILED at timeout` — 8 jobs plus a foreground router on 8 cores is
~0.8 core each, and a 40-attempt ladder on copper this hard does not fit. **Those
timeouts were a measurement artifact, not a statement about the seeds** — the
re-run proved it: three of those same variants routed to completion in 430–530 s
once given real cores.

### What was run, and what it found

```sh
cd hw/carrier
/usr/bin/python3 tools/gen_footprints.py && /usr/bin/python3 tools/gen_board.py
CARRIER_SEARCH_TIMEOUT=7200 /usr/bin/python3 -u tools/search.py --stage router --jobs 16
```

32-thread box, 1277 s wall for all 16. **Exactly one variant came back clean:**

| variant | unconn | unrouted | gap | segments | |
|---|---|---|---|---|---|
| halo-off, **seed 33** | 0 | 0 | **7.81** | **1343** | ✅ adopted |
| halo-off, seed 11 | 1 | 1 | 7.81 | 1196 | ❌ the old default (`BAT_PLUS`) |
| halo 1.3×1.5, seed 33 | 1 | 1 | 7.81 | 1338 | ❌ `GATE` |
| halo 1.0×1.5, seed 11 / 33 | 1 | 1 | 7.81 | 1351 / 1389 | ❌ `BTN_MODE` |
| halo-off / 1.0×1.5, seed 22 | 1 | 1 | 8.6 | 1196 / 1267 | ❌ `DBG_TX` |
| halo 1.3×4.0, seed 11 | 1 | 1 | 8.6 | 1496 | ❌ `TL431_K` |
| halo 1.3×1.5, seed 22 | 1 | 1 | 8.6 | 1666 | ❌ `POT_WIPER` |
| **seed 20260726**, any halo | 2–4 | 1–2 | 7.81–8.6 | 1177–1561 | ❌ phase D `STUCK`, severed `GND_main` |
| halo 1.3×4.0, seed 33 / 22 | 2 | 2 | 8.6 | 1564 / 1636 | ❌ (seed 22 also 5 physical) |

Three things that were easy to get wrong, and all three mattered:

- **`CARRIER_SEARCH_TIMEOUT=7200`.** Without it the sweep returns nothing but
  timeouts, which reads exactly like "no seed works".
- **`--jobs 16`, not 32.** The variants are single-threaded but memory-hungry
  (each holds a full `pcbnew` board plus two 552 × 394 obstacle grids).
- **`-u`.** `route.py`'s per-attempt lines are block-buffered when redirected;
  without it a multi-hour run looks identical to a hung one.

**The halo env vars did not need adopting.** `route.py` gates the entire halo
behind `if HALO_COST > 0` and the repo default cost is 0, so the sweep's
`halo-off` is behaviourally identical to the repo default. Seed 33 reproduces
from `ROUTE_SEED=33` alone — verified by rebuilding from source twice, both
landing on 1343 segments / 48 vias / 0 failures.

### Timing, measured

Solo on the 32-thread box, seed 33: **`gen_board.py` 7 s, `route.py` 251 s.** The
ladder early-exits on the first zero-failure attempt (28 of 40 here), so a
*working* seed is the cheap case. Failing seeds cost 430–1277 s each while
sharing the box 16 ways. pcb.md §9's old "~5–6 min" is now replaced with these.

### Gates, as measured on the routed board

| Gate | Result |
|---|---|
| `route.py` | 1343 segments, 48 vias, **0 failures**; phase D single cluster at round 0, **0 repair vias** |
| `kicad-cli pcb drc --severity-all` | **0 violations, 0 unconnected** |
| `--schematic-parity` | **69** (60 `footprint_symbol_mismatch` + 9 `net_conflict`) — unchanged from the pre-route baseline, i.e. the documented benign set |
| `kicad-cli sch erc --severity-all` | **41, all `footprint_link_issues`** — the headless library-table artifact, **not** 0; real violations 0 |
| `tools/check_netlist.py` | OK |
| `tools/audit_board.py --verbose` | all five gates; `silk shadow 25 deliberate, 0 that should not be` |
| silk stroke census | **0** items below 0.15 mm (was 70) |
| drill `G85` slots | T4 = **0.900** tool; `X47.08→X47.98` and `X51.98→X52.88`, 0.90 mm travel each (was 0.54) |
| `tools/measure_copper.py` | fed into pcb.md §6.4, §4.4 item 1 and the width table above — **all three now as-built** |

⚠️ **The ERC row is the one to not misread.** The gate table used to say "0
violations"; a headless checkout reports ~41 `footprint_link_issues` because
`kicad-cli` cannot see the global footprint library table. Read ERC the way
parity is read — diff the count and the *types* against this baseline, not
against zero.

### What is still open

**Nothing.** SW1 (VERIFY item 2) was the last item and it closed 2026-07-28,
from the SS12D00 mechanical drawing rather than the part or the paper doll — see
that item for the numbers. Renders and the 1:1 paper doll were regenerated
against the routed board on 2026-07-28 and are current.

Worth stating why the doll was not needed, because the plan had budgeted an
evening for it: every quantity that decides the fit is *dimensioned on the
drawing*, and comparing two numbers beats eyeballing a print. The doll answers
"does this look right", which is the question you ask when you do not have the
drawing. It stays the right tool for RV1, whose lug geometry was reconstructed
rather than read.

The fab package is plotted, verified and committed to `fab/`. Two things remain
before paying, neither of them a board question: load the zip in the fab's own
viewer and a second one, and — on arrival, at assembly — check whether the part
carries a locating lug. The datasheet has no bottom view, so that one cannot be
read off paper; `SW_Slide_SPDT_DualPitch` has the three signal holes only, so
any lug must be clipped. It is an assembly note, not an order gate.
