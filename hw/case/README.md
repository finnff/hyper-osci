# HYPEROSCI slave enclosure

3D-printed case for the carrier PCB. Like `hw/carrier`, it is **generated, not
drawn**: every X/Y coordinate comes out of `carrier.kicad_pcb`, so if the board
moves, the case follows.

```
carrier.kicad_pcb ──export_geom.py (pcbnew)──▶ board-geom.json
                  ──kicad-cli pcb export step─▶ out/carrier.step
                                                     │
                       case.py (build123d) ◀─────────┘
                          └─ out/{base,lid,plunger,knob}.{step,stl}
                       audit_case.py ── collision + escape gate
                       render.py     ── out/preview.png
```

## Build

```sh
uv venv --python 3.12 hw/case/.venv
uv pip install --python hw/case/.venv/bin/python build123d matplotlib

/usr/bin/python3            hw/case/tools/export_geom.py   # needs KiCad's pcbnew
hw/case/.venv/bin/python    hw/case/tools/case.py
hw/case/.venv/bin/python    hw/case/tools/audit_case.py    # must pass before printing
hw/case/.venv/bin/python    hw/case/tools/render.py
```

Two interpreters on purpose: `pcbnew` is bound to system Python 3.12 and its
system `numpy`/`pandas` clash with build123d's, so the venv is deliberately
**not** `--system-site-packages`. `board-geom.json` is the only thing that
crosses between them.

## Interior height

`P["above_pcb"]` = **18.5 mm** = 8.5 (socket) + 1.0 (module PCB) + **7.0
(heatsink)** + 2.0. The heatsink is glued to the SuperMini, appears in no
document and in no KiCad 3D model, and was found in a bench photo; it was
measured at 7 mm on 2026-08-19 and `audit_case.py` carries it as `HEATSINK`.
It is the tallest thing in the box, so it sets the overall height — if the
module is ever re-sinked, that constant and `above_pcb` move together.

## Geometry as built

| | |
|---|---|
| Exterior | **80.8 × 61.8 × 37.1 mm** |
| Interior | 76 × 57, floor to wall top 33.1 mm |
| Wall / floor / lid | 2.4 / 2.0 / 2.0 mm (1.5 mm under the cell) |
| PCB sits at | z = 13.0 (bottom) … 14.6 (top) |
| Mounting pads | 13.0 mm tall; 3 fused into their corners, 1 free-standing |

Z stack, floor upward: floor 2.0 · **cell recessed 0.5 into it** · cell 10.0 ·
swell 0.5 · lead-tail clearance 3.0 · PCB 1.6 · headroom 18.5 · lid 2.0.

### Mounting pads, and the two rules that shape them

v1 printed the four bosses as bare ⌀6.4 columns and the inserts had almost
nothing to bite into. Each is now a **block of material tying into two walls**,
and the boolean does the shaping rather than a guess: a pad is everything within
`pad_r` of its hole, out to the two nearest walls, **minus**

1. `bay_keepout()` — where the cell is, as tall as the cell and no taller, so
   above it the pad is free to be full width again;
2. `tail_keepout()` — under the board above the lead-tail line, where only the
   **6.5 mm annulus** certified clear by `pcb.md` §6.1 rule 7 may reach the
   underside. This is what stops a big pad from sitting on clipped THT leads.

`audit_case.py` reports the result as *insert support*: the fraction of a 12 mm
circle around each hole that is solid over the insert's 5.6 mm. It reads **54 –
62 %**; a bare ⌀6.4 column is 20 %. It is not higher because the top 3 mm of
each insert sits inside the 6.5 mm annulus, which is the board's constraint and
not something the case can spend its way out of.

**One pad stays free-standing: the one nearest `J8`** (picked by distance in
`case.py`, currently H4). The JST lead is 40 mm for a ~20 mm run, so the slack
has to go somewhere, and it gets wrapped round that post. That is also why the
bay stops 15 mm short of the east wall instead of 2 mm: the cell is 34 mm wide
and the hole rows are only 42 mm apart, so any pad the cell overlaps can be no
more than 2 mm thick on the bay side. Moving the cell west feeds all four
inserts and opens a 3 mm channel east of it for the lead to rise in.

## Openings

| Wall | What | Anchor |
|---|---|---|
| North | 2 × 12 mm slots, **open to the top**, for the RCA flying leads | `X1`/`Y1` **pad span** |
| South | 9 mm slot, **open to the top**, seating the RV1 shaft 7.0 mm above the PCB | `RV1` |
| South | 13 × 10 mm slot with a 45° roof, for the SS12D00 handle | `SW1` |
| South | 10 mm slot, open to the top, for the JST plug tail + cell lead | `J8` |
| East | 17 × 14 mm roofed opening, sized for a USB-C **plug overmold** | TP4056 jack |
| East | 8 mm slot, open to the top, for the MAX4466 pigtail | `J4` |
| West | — nothing. The side vents put holes at wire height and are gone. | — |
| Lid | ⌀9 SW2 hole (+ plunger), 2 × ⌀5 LED holes, 7 vent slots | `SW2`, `D4`, `D5` |

Everything that can be is **open to the top**, so wires drop in at assembly
rather than being threaded, and nothing needs support material.

Two of those slots exist because of what v1 got wrong on the bench:

- **The pot was a closed ⌀8 hole.** RV1's bushing crosses the wall line, so the
  board could not be lowered in — it had to be threaded in at an angle. It is
  now a 9 mm slot open to the wall top, with a round seat at the shaft axis.
  `audit_case.py` probes the bushing's **swept drop-in volume** (a capsule:
  round below the axis, straight-sided above), not just its final position.
- **The RCA slots were centred on the footprint origin.** For `X1`/`Y1` that
  origin sits on the *signal pad*, 2.69 mm off the pad pair's centre — and in
  opposite directions, so the two slots came out 24.1 mm apart when the pad
  pairs are 19.0 mm apart. The leads had to be splayed, loading the joints.
  `export_geom.py` now exports the **pad-copper span** and both the slot and its
  escape probe are centred on that. Each 12 mm slot brackets its 8.4 mm pad span
  with 1.8 mm to spare on each side.

There is deliberately **no opening for the SuperMini's own USB-C** — it is
mid-board, and DESIGN.md §9's rule stands: never plug it in with the battery
switch on. Flashing means opening the lid and lifting the module, which is what
`pcb.md` assumes anyway. Same for TP1–TP5 and the JP1 fallback.

## ⚠️ The cell goes in leads-EAST

The JST-PH `S2B-PH-K` at `J8` is side-entry and its **opening faces south** —
pads sit 1.46 mm from the housing's closed rear face and 6.36 mm from the mating
face, so the plug mates 1.1 mm from the board's south edge.

The cell is 56 × 34, so it can only lie with its 56 mm along the board's X axis.
That makes the lead corner the only free variable, and with a **40 mm lead** it
is decisive:

| Cell leads exit | Plan distance to the plug | |
|---|---|---|
| **East short edge** | ~8 mm → total path ~20 mm | fits, with ~20 mm of slack to wrap |
| West short edge | ~58 mm | dead before the vertical run even starts |

The bay's ribs enclose north, west and south and leave the **east end open**,
which is the keying: a cell in backwards has nowhere to route its lead. That is
not cosmetic — `pcb.md` §3.1 puts the protection FET in the cell-negative leg
and `B−` is deliberately **not** GND, so a reversed cell drives the pack
backwards through the FS8205 rather than simply failing to run. **Meter the
polarity before the first plug-in** regardless (layout-notes VERIFY 5).

### Clearance over the cell

The bay was 3 mm deeper in v1 and, once shortened, left only 0.6 mm between the
cell and the lowest geometry on the board's underside. It now reads **+1.60 mm**
to a nominal cell and **+1.10 mm** to a swollen one, bought two ways:

- `tail_clear` 2.0 → 3.0, which costs 0.5 mm of overall height;
- `bay_sink` = 0.5 — the cell drops **into** the floor, leaving 1.5 mm of floor
  beneath it. 2 mm was more than the bay needed to be stiff, so this half
  millimetre is free: it does not raise the case at all.

The recess also runs `bay_sink_e` = 3 mm **past** the cell's east face, so the
leads leave the pouch flat instead of bending over the step. The tab-to-pouch
seal is the fragile part of a pouch cell and should not rest on an edge.

Put a strip of foam or tape over the cell at assembly regardless. If you want
more, the next millimetre has to come out of `tail_clear` — measure how short
your leads are actually clipped first, since the KiCad models' pin tails hang
1.9 mm and that is what the audit is measuring against.

## Parts to print

| File | Notes |
|---|---|
| `base.stl` | open side up. No supports. ~34 cm³ |
| `lid.stl` | flat, **outside down**. No supports — the snap strips bridge ~18 mm |
| `plunger.stl` | SW2 pushrod — the lid sits ~18 mm above a 5 mm button |
| `knob.stl` | **optional**, push-fit on RV1, bore ⌀5.9 |

`pcb.md` §2 specs "metal shaft turned directly — **no knob**". That was about
not *sourcing* one; the shaft clears the wall by only 8.6 mm, which is thin to
pinch mid-set, and a printed one costs nothing. ⌀13 keeps it clear of the SW1
slot 13 mm east. Skip it if you'd rather keep the doc literal.

## If you want it lighter

`wall` 2.4 and `floor`/`lid_t` 2.0 are on the heavy side — 6 and 5 perimeters at
a 0.4 mm nozzle. Dropping `wall` to 2.0 takes roughly 4 cm³ off the base and
0.8 mm off each exterior dimension. It is one parameter, but it also moves the
snap pockets and the lid lip, so re-run `audit_case.py` and reprint the lid with
it. Do not thin `floor` below 2.0 while `bay_sink` is 0.5 — that leaves under
1.5 mm beneath the cell.

## Fasteners

**4 × M3 heat-set inserts, 5.0 mm long, OD 4.6** — pressed into the top of each
mounting pad (⌀4.0 pilot, 5.6 mm deep). **4 × M3 × 6** cap screws through the PCB.
The lid is snap-fit: a full-perimeter tongue-and-groove lip does the locating
(snaps alone give ~0.4 mm play, and the LEDs, SW2 and the vents all need the lid
positioned), 4 beads on the east and west lip do the retention. North and south
lips carry no snaps — those walls are full of cutouts.

## The lid clips, and why v1's were designed to break

v1 hung a 14 × 1.6 mm cantilever off the lid plate and put the bead 3.5 mm down.
A 3.5 mm lever arm is not a spring. Both faces of the bead were square — no
lead-in, no release angle — so it butted on the way in and could only be pried
off. v2 replaced the spring and overshot: it went on and off easily and sat
slack. v3 and v4 walk it back up. The arithmetic the audit prints, for all four:

| | v1 (jammed) | v2 (slack) | v3 (still slack) | v4 |
|---|---|---|---|---|
| spring | 3.5 mm cantilever, 14 × 1.6 mm | 18 mm bridge, 0.8 × 2.8 mm | 0.8 × 3.8 mm | 18 mm bridge, **0.8 × 5.8 mm** |
| `lip_fit` per side | 0.30 mm | 0.45 mm | 0.35 mm | **0.30 mm** |
| engagement | 0.60 mm | 0.25 mm | 0.40 mm | **0.40 mm** |
| lift slop | 0 | 0.25 mm | 0.10 mm | **0.05 mm** |
| bending strain | **~12 %**, across layers | 1.3 % | 2.0 % | **2.0 %**, along them |
| force to deflect | ~600 N | 7 N | 14 N | **21 N** |
| to push one on | — (square: it butts) | ~5 N | ~11 N | **~18 N** |
| to pull one off | — (square: it cannot cam) | ~15 N | ~28 N | **~43 N** |

**The pockets in the base never changed**, so none of this needs the base
reprinted — `snap_w` and `snap_from_lid` still size them and `base.stl` comes
out byte-identical across all three. Everything that moved is on the lid.

The spring is the lip itself. `snap_span` of it is slotted away from the plate
(`snap_gap` tall), and the strip left below is a beam **built in at both ends**
rather than a stubby cantilever: span carries the deflection, so the same job
costs a fortieth of v1's force. The slots stop 2 mm short of the corners — a
corner is the one part of the ring that has to stay stiff.

### Which knob to turn for more grip

The strip's **height** is the one that is free. Stiffness is linear in it and
it does not appear in the bending strain at all. Thickness and engagement buy
the same grip at 3rd and 1st power *in the strain*, and the strain is already
at PLA's yield — past which the tab takes a permanent set and goes loose by
itself, which is the failure being chased. So:

    strain  ∝ δ·t / L²                 force  ∝ h·t³·δ / L³

Height (`h`) is in the second and not the first. v2→v3 spent `snap_gap` down to
1.2 mm (its floor — much less and the slicer fuses the strip back onto the
plate); v3→v4 spent `lip_h`, 5 → 7 mm, which is what makes the tab visibly more
substantial as well as 1.5× stiffer. The tongue can go deeper still, but not
much: the binding obstacle is the **USB-C plug overmold**, whose escape volume
tops out 2.1 mm below where the lip now ends.

Two percent is the number usually quoted as PLA's limit, and v3/v4 sit at it —
but which two percent matters. The strip's long axis lies in the layer plane
and it bends about a vertical axis, so the tension runs **along** the
extrusions. v1's cantilever bent about a horizontal axis and pulled its root
apart layer from layer: the same strain against a far lower limit.

`snap_play` and `snap_lead_h` trade against each other and the trade is easy to
miss. The bead's lead-in has to be fully retracted by the time it reaches the
pocket's *lower* lip, or it wedges against solid wall instead of dropping in.
Every 0.1 mm of play spent pushes the bead down and costs ~0.2 mm of ramp. v4
clears by 0.28 mm; the audit prints that margin and complains under 0.15,
because 0.06 mm survives the boolean and would not survive a fat print.

Three other things changed on the lid, all of which were part of "too tight":

- `lip_fit` **0.30 mm** per side (0.45 on v2, 0.35 on v3), and the tongue has a
  0.6 mm chamfer on its bottom edge. v1's was square-ended, so it had to be
  lined up before it would even start to enter — the chamfer, not the
  clearance, is what made v1's fit unworkable.
- `snap_play` **0.05 mm**: the bead sits that far below the pocket's ledge when
  seated, so that is how far the lid lifts before anything bites. v2 carried
  0.25 as budget for the bridge sagging *towards the bed* as it prints, which
  would raise the bead and preload the snap. Three prints later the sag has not
  shown up, so it is not there to pay for.
- A **0.5 mm land** at full protrusion (`snap_flat`) — two layers at 0.2 mm, so
  the slicer cannot round the bead away. A bead that prints short is a lid that
  feels loose no matter what the model says.
- A ⌀8 × 1.2 mm **fingernail scoop** at the middle of each long edge, between
  the snaps. A flush seam has nothing to lift by, and peeling from there
  releases two beads at a time (~30 N) instead of four (~60 N).

`audit_case.py` measures all of this off the solid — strip section, bridged
span, how far the bead sticks past the wall face — rather than trusting the
parameter block, and prints the strain and the two cam forces. Numbers assume
PLA at 3 GPa and µ = 0.35; PETG is roughly two thirds as stiff, so softer
again. It then **drives the lid down through the base in 0.5 mm steps** and
reports the worst interference on the way: it has to be the 0.25 mm the beads
are meant to give, and nothing else. The seated pose is the one pose that
always looks fine, which is exactly why a static check missed v1.

**No metal standoffs, and no screw may bridge two mounting holes.** `pcb.md` §6.3:
H2's pad falls on the AGND island side of the moat, so any metal tying two
mounting holes together gives the star ground a return that bypasses the neck.
Brass-into-plastic is fine — each insert is isolated by the case.

## What the audit proves, and what it doesn't

`audit_case.py` intersects base and lid against the populated board and fails on
any non-zero volume, then fires **escape probes** — the volume each thing needs
on its way out (USB-C plug and overmold, mic pigtail, both RCA lead pairs, a
finger on SW1, the knob's swept volume, the battery lead riser, the JST plug
tail). Collision-free alone is not enough: an opening in the wrong place also
collides with nothing.

Its board solid comes from `kicad-cli pcb export step`, which is authoritative
for the 32 footprints that have a 3D model and **silent about the 19 that
don't** — including `RV1` and `SW1`, the two parts that define the south wall.
The three socketed modules are not in the board file at all. `STANDIN` fills
both gaps with measured boxes and cylinders, and **is only as good as those
measurements**. The ones taken from a photo rather than a caliper are the
heatsink, the TP4056's machined-socket standoff height, and RV1's 7.0 mm shaft
axis. Those three are what to check on the first print.
