# Fab package — HYPEROSCI carrier v1.1

Generated 2026-07-28 from `carrier.kicad_pcb` at commit `c1c3b11` (router seed 33),
working tree clean. **Upload `hyperosci-carrier-v1.1-gerbers.zip`.**

> **Re-plotted 2026-07-28 for the back-silk artwork.** The first set was plotted at
> `ddcbc13`, one commit before the `48` mark and `ponkiePCBv1` wordmark were added to
> B.Silkscreen, so its `carrier-B_Silkscreen.gbo` had **zero region fills** — the artwork
> was simply not in it. Nothing warned about this: the board was correct, every gate
> passed, and `plot_fab.py`'s own checks are copper/drill geometry, which did not change.
> **Any edit to a plotted layer silently invalidates this directory; re-run `plot_fab.py`.**
>
> Two things that make a diff of this directory confusing, both benign:
>
> - **The copper is unchanged and that was verified, not assumed.** Aperture draws are
>   identical (F.Cu 1952 = 1952, B.Cu 1168 = 1168), and the zone fills in the board file
>   are identical between the two commits to 1e-6 mm² (F.Cu 1720.834491, B.Cu 1943.652053)
>   with the same outline counts.
> - **Gerbers are not byte-reproducible across KiCad builds.** The first set came off
>   `10.0.5-1.fc44`, this one off `10.0.5-10.0.5~ubuntu24.04.1`. The two emit pour region
>   vertices with ~3 nm differences (e.g. `18804538` vs `18804535`) and one outline drops
>   3 collinear points, 382 → 379. That is rounding, not geometry. Do not read a large
>   `git diff` on `carrier-F_Cu.gtl` as copper having moved — compare aperture draws and
>   fill areas, not bytes.

Regenerate with `/usr/bin/python3 tools/plot_fab.py` from `hw/carrier/` — do not hand-edit
the gerbers. That script re-runs DRC, plots, asserts every mechanical item on the
`pcb.md` §8.4 checklist, and writes the zip only if all of them hold.

## Order parameters (from `docs/hardware/pcb.md` §8.3)

| | |
|---|---|
| Layers | 2 |
| Size | 70 × 50 mm |
| Qty | 5 |
| Material | FR-4 TG135+ |
| Thickness | 1.6 mm |
| Outer copper | 1 oz |
| Surface finish | HASL, lead-free |
| Mask / silk | green / white |
| Castellations, impedance control, stencil | none |
| Order number | "specify location" — placed on the **back**, under the TP4056 module (`TP4056` legend on B.Silkscreen marks the spot) |

## Zip contents

| File | Layer |
|---|---|
| `carrier-F_Cu.gtl` | Copper, L1, Top |
| `carrier-B_Cu.gbl` | Copper, L2, Bottom |
| `carrier-F_Mask.gts` | Soldermask, Top |
| `carrier-B_Mask.gbs` | Soldermask, Bottom |
| `carrier-F_Silkscreen.gto` | Legend, Top |
| `carrier-B_Silkscreen.gbo` | Legend, Bottom |
| `carrier-Edge_Cuts.gm1` | Profile (board outline) |
| `carrier.drl` | Excellon drill, PTH+NPTH merged |
| `carrier-job.gbrjob` | Gerber job file (layer stackup hint) |

No paste layers — no stencil is ordered, and the only real SMD part is Q1 (SOT-23 on
hand-solder pads). Protel extensions, RS-274X (X2 off, netlist attributes off) so the
same zip loads identically at JLCPCB and PCBWay.

## Verification run at plot time

| Check | Result |
|---|---|
| `kicad-cli pcb drc` | 0 violations, 0 unconnected |
| Edge.Cuts | 4 lines, single closed rect, exactly 70.000 × 50.000 mm |
| Copper extents | 0.500 … 69.500 / −49.500 … −0.500 (0.5 mm to the profile) |
| F.Cu flashes | 222 = 126 pads + 96 vias |
| B.Cu flashes | 212 = 116 THT pads + 96 vias |
| F/B.Mask openings | 124 / 116 regions vs 126 / 116 pads (2 merged on top) |
| Drill total | 212 plated, 0 unplated; T1 ⌀0.400 × 96 = the via count |
| **SW1 slots** (`pcb.md` §7) | **T4 = 0.900, `G00X47.08→G01X47.98` and `G00X51.98→G01X52.88` — 0.90 mm travel each, aspect 2.0** |

## Back-silk artwork — checked separately, because no gate covers it

The `48` mark plots as **4 filled G36 regions**, and every silk check this project has is
about *stroke* width. A filled polygon has none, so the DFM question — is any ink narrower
than JLC's 0.15 mm floor — was answered by rasterising the plotted gerber at 5 µm and
opening it morphologically:

| Opening disk | ⇒ min feature width | Ink kept | Features left |
|---|---|---|---|
| 0.075 mm | 0.150 mm ← JLC floor | 99.90 % | 4 of 4 |
| 0.125 mm | 0.250 mm | 99.77 % | 4 of 4 |

Nothing drops out well past the floor, and the ~0.1 % shortfall is corner rounding. A raw
skeleton measurement looks much worse — 1.4 % of ridge points under 0.15 mm, bottoming out
at 0.010 mm — and is **the wrong test**: skeletons run into every convex corner, where
width goes to zero by construction. Use the opening result.

The rest of the layer: the wordmark is stroked text at **0.1875 mm**, and the two
pre-existing legends are unchanged at **0.15 mm**. Both clear the floor.

**The `%ADD10C,0.000000*%` aperture is not a zero-width pen.** It appears only on this
layer and only ever selected *inside* G36 regions, where the Gerber spec says the current
aperture is unused; zero draws and zero flashes reference it. It is normal KiCad output for
polygon fills. Do not "fix" it.

**No silk over exposed copper:** none of the 116 B.Mask openings intersect the artwork's
bounding box, nearest 0.48 mm. The placement search that chose the spot looked at pad-clear
area on B.Cu; this confirms the result against the mask, which is the layer that matters.

## Known and accepted

**Via-in-pad on 5 SMD pads:** `Q1.1`, `Q1.2`, `TP2.1`, `TP3.1`, `TP4.1`. ⌀0.8 vias on a
0.4 mm drill, centred in 1.9 × 0.8 mm pads. The three testpoints are intentional (that
via *is* how the pad reaches its net). Q1's two are a router artifact — present in every
routed revision since `4c362ff`, so this is standing behaviour, not a seed-33 regression
(HEAD has one fewer than `e6943b5`, which also had `D2.2`). Consequence is hand-soldering
only: solder will wick to the back through a 0.4 mm hole, so feed a little extra and
expect a bead on B.Cu. Not a fab constraint and not worth risking the seed-33 route.

The §8.4 checklist item as written — "no vias inside RCA/JST pads" — passes: no
through-hole pad on the board has a via inside it.

## SW1 — closed 2026-07-28

The last gate, and it closed off the SS12D00 mechanical drawing rather than the part or
the paper doll. Nothing in the layout moved, so these gerbers are unaffected.

| | Part | Board | Margin |
|---|---|---|---|
| Pin pitch | **2.5 mm** | slot window 2.00–2.90 from the centre pin | lands 0.05 mm off slot centre |
| Pin section | 0.5 × 0.3 mm | slots 1.80 × 0.90, centre hole ⌀0.9 | 0.30 mm a side across the slot |
| Body | 8.5 × 3.7 mm, pins on the centreline | silk 9.0 × 4.0, courtyard 10.0 × 5.0 | 0.25 mm a side in X, 0.15 in Y |
| Clearance | — | TP4056 body 2.51 mm N · J8 courtyard 0.96 mm E · board edge 1.75 mm S | nothing overhangs |
| Actuator | 1.5 mm sq, 2 mm travel | sweeps X 48.23–51.73 | 3.46 mm of nail room to J8 |
| Function | middle terminal = common | pad 2 = VLOAD, pad 1 = VSW, pad 3 netless | correct |

The pitch is the interesting one: 2.5 mm is **neither** of the two values the footprint was
drawn around, and it still lands in the most comfortable part of the window. That is the
slot paying for itself.

The 6 mm handle never entered the fit — nothing sits above the switch, so handle length is
an enclosure question.

## Still open before you pay

Not a board question: load this zip in the fab's own online viewer **and** a second one
(`gerbv` or tracespace) — check outline, drills present, silk not mirrored. That is the one
item on §8.4 that cannot be asserted from here.

One check survives to assembly, not to ordering: whether the switch carries a locating lug.
The drawing has no bottom view and dimensions no post, so paper cannot settle it;
`SW_Slide_SPDT_DualPitch` has the three signal holes only, so clip any lug.
