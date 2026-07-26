# carrier layout notes (v1.1, 2026-07-26)

Fully routed and **completely clean**: `kicad-cli pcb drc --severity-all` reports
**0 violations of any severity**, 0 unconnected items, 0 schematic-parity items.
1267 track segments, 45 GND vias, 30 GND through-hole pads. Worst GND stitch gap
anywhere outside the antenna keep-out: **7.8 mm**. Zero 90° track corners — 1174
of the 1194 track vertices are true 135° mitres, the rest obtuse or collinear,
and the single acute vertex is two traces fanning out from R1's pad (wide
copper, no notch), not a routed corner.

Everything is generated — do not hand-edit `carrier.kicad_pcb`; edit the tools
and re-run:

```
python3 tools/gen_footprints.py   # only if footprints change
python3 tools/gen_schematic.py    # only if design.py changes
python3 tools/gen_board.py && python3 tools/route.py
python3 tools/audit_board.py --verbose      # the geometry gate
```

Use `/usr/bin/python3` — `pcbnew` is only importable from KiCad's own
interpreter, and a conda `python3` on `PATH` will fail to import it.

Three gates, and all three must pass before ordering:

| Gate | What it catches | Status |
|---|---|---|
| `check_netlist.py` | nets vs `design.py`, `config.h` GPIO map, §2/§3 invariants | pass |
| `kicad-cli pcb drc` | clearance, connectivity, silk, courtyards | 0 at every severity |
| `audit_board.py` | what DRC cannot know: will a real module *seat* | pass |

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

0.25 mm is the misalignment a 2.54 mm female header accepts before a rigid
module pin fouls the barrel. Re-measure a module, drop the new CSV in, re-run
`gen_board.py`, and the board follows it.

## Floorplan deviations from pcb.md §6

- **TP4056 sits SOUTH of the DAC**, not colinear with it: SuperMini (~24 mm)
  + PCM5102A (~32 mm) + TP4056 (~26 mm) don't fit on a 70 mm edge. USB-C still
  exits east.
- **J5/J6 are two pad pairs**, not butted 1×2 sockets, at the measured pitches
  (see above). Fit four machined single sockets, or solder wires and give up
  removability.
- **D1/D3 live in the power cluster, not the island**: they carry
  GATE/VBUS_CHG/VLOAD, and their 7.62 mm pad rows would have walled off the
  §6.3 neck. D1 north of the J5 column (under the module overhang — DO-35
  lies flat). D3 (DNP) NORTH of the TP4056 body, **outside** the module
  outline, so it can be retrofitted without pulling the module.
- **JP1 and Q1 sit under the socketed TP4056** — bridging JP1 (§4.4 escape
  hatch) means removing the module first.

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
still cannot sit anywhere, a bounded local sweep and then a whole-board sweep
run before the build fails. Module outlines are corner brackets on
F.Silkscreen (the full rectangle stays on F.Fab) so they no longer rule a line
through every socket inside them.

Two placements are deliberate rather than searched:

- The **fab's order number** goes on the *back*, inside the antenna keep-out.
  That strip carries no pour and no stitching by construction, so it is the one
  place on the board where silk is guaranteed to be over bare laminate.
- **X and Y** are printed by rewriting the RCA footprint's own `S` label per
  instance. Placed by the ring search they drifted inboard until they sat
  nearer each other than their own pads — and "which pair is X" is the single
  question that legend exists to answer.

## Nets narrowed at pinch points (width fallback, §6.4 policy)

- VSW 1.0 → min 0.4, VLOAD/BAT_MINUS 1.0 → min 0.6 (0.4 mm of 1 oz copper
  carries ≈1 A; WiFi peak is 0.35 A — fine)
- 3V3 0.6 → 0.4; DAC_L/R, SCOPE_X/Y 0.5 → 0.4 (short pinches only)

## VERIFY before ordering (paper-doll + part-in-hand)

1. ~~J2↔J3 DAC socket geometry is PROVISIONAL~~ — **RESOLVED**, measured; the
   old guess was 4.06 mm out. Still worth a paper-doll check:
   `paper-doll-1to1.pdf` is current.
2. **SW1 pitch unknown** until the part arrives — footprint is dual-pitch
   (slots accept 2.0 AND 2.54 mm), but verify the body/lever fits.
3. **RV1 (RV097NS) bracket lug geometry** is from the generic RV09 drawing —
   least-certain footprint on the board; check on the paper doll.
4. **TP4056 pad-row edge offset**: the pad *pitches* are now measured, but how
   far the row sits from the module's short edge is not, so the module may sit
   shifted N/S on J5/J6. Region y 24–41 has margin; check.
5. **JST cell polarity** (silk warns): B− is NOT GND — verify the wiring of the
   cell before first plug-in.
6. Antenna keepout (x < 6, y 6–28) is pour-free on both layers; a few thin
   signal traces may pass the region edge (soft-penalised, not forbidden).
7. **R10** is a standing axial (≈7.5 mm) at x 54.35…59.49, y 4.83…7.88, and the
   DAC body reaches x 55.62 / y 6.22 — so its corner *is* under the module, with
   0.8 mm to spare against the 8.3 mm standoff. Not much. Seat it low.

## Files

- `carrier.kicad_pro/.kicad_sch/.kicad_pcb` — the project (generated)
- `paper-doll-1to1.pdf` — 1:1 top view for the paper mockup
- `render-top.png`, `render-bottom.png` — 3D previews
- `tools/design.py` — single source of truth (nets, parts, deviations)
- `tools/measured.py` — module geometry from `hw/pin_locs` photogrammetry
- `tools/audit_board.py` — geometry gate (module fit, silk, routing, clearance)
- `tools/search.py` — parallel layout/router parameter sweep
- `tools/check_netlist.py` — respin gate; run after any `design.py` change
