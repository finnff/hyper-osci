# carrier layout notes (v1.0, 2026-07-24)

Fully routed, DRC-clean: **0 physical violations** (2 accepted courtyard
overlaps, below), **0 unconnected items**. 1585 track segments, 72 vias.
Everything is generated — do not hand-edit `carrier.kicad_pcb`; edit the
tools and re-run:

```
python3 tools/gen_footprints.py   # only if footprints change
python3 tools/gen_schematic.py    # only if design.py changes
python3 tools/gen_board.py && python3 tools/route.py
```

`route.py` exits non-zero unless routing succeeded AND the GND pour is one
single connectivity cluster; the retry loop inside is stochastic, so a
failed run can simply be re-run.

## Floorplan deviations from pcb.md §6

- **TP4056 sits SOUTH of the DAC**, not colinear with it: SuperMini (~24mm)
  + PCM5102A (~33mm) + TP4056 (~28mm) don't fit on a 70mm edge. USB-C still
  exits east.
- **J5/J6 are butted 1×2 sockets** forming the TP4056's single pad column
  (x=45.72, OUT− B− B+ OUT+ going south) — their courtyards kiss by design
  (accepted DRC overlap).
- **D1/D3 live in the power cluster, not the island**: they carry
  GATE/VBUS_CHG/VLOAD, and their 7.62mm pad rows would have walled off the
  §6.3 neck. D1 north of the J5 column (under the module overhang — DO-35
  lies flat, socket clearance ~8.5mm). D3 (DNP) in the open strip east of
  Q1/JP1, **outside** the module outline so it can be retrofitted without
  pulling the TP4056.
- **JP1 and Q1 sit under the socketed TP4056** — bridging JP1 (§4.4 escape
  hatch) means removing the module first.

## §6.3 AGND island — how it's actually implemented

One single `GND_main` zone on both layers; the island is carved by moat
rule-areas with a 3mm neck at x32–35, y≈13.5. (A separate same-net island
zone does NOT work: KiCad never merges same-net zone fills — the
lower-priority fill is excluded, leaving a zero-width seam that
connectivity ignores. Found the hard way.)

Reinforcements, all generated:
- **Neck strap**: explicit 1.0mm GND track pair (F+B) at x=33.02, y8.9→17.5
  through the neck, with 3 vias. The router hard-reserves this corridor for
  GND (`NECKS` in route.py).
- **Seeded stitch grid**: 33 GND vias on a 7mm grid placed BEFORE routing so
  the router treats them as obstacles. (Post-route stitching cannot find
  legal via spots in signal-dense quadrants; a pre-seeded via inside a pour
  fragment anchors it to the other layer.)
- **route.py phases** after net routing: B = stitch pour-orphaned GND pads
  (DRC-driven, A*), C = extra stitch vias on main pour, D = union-find pour
  clusters and via-bridge (0.8/0.4, small 0.65/0.3 fallback) or A*-stitch
  every stray back to main, E = prune any GND via the pour never reached.

## Accepted DRC items

- `courtyards_overlap` J2×J3: real module geometry — the socket housings
  kiss at the corner; file the housing at assembly if needed.
- `courtyards_overlap` J5×J6: butted sockets, intentional.
- Silk warnings (`silk_overlap`, `silk_over_copper`, `silk_edge_clearance`):
  cosmetic only.

## Nets narrowed at pinch points (width fallback, §6.4 policy)

- BAT_MINUS 1.0 → min 0.6 | VLOAD 1.0 → min 0.4 (0.4mm 1oz ≈ 1A+; WiFi
  peak is 0.35A — fine)
- 3V3 0.6 → 0.4; DAC_L/R, SCOPE_X/Y 0.5 → 0.4 (short pinches only)

## VERIFY before ordering (paper-doll + part-in-hand)

1. **J2↔J3 DAC socket geometry is PROVISIONAL** (Finn's phone measurement:
   1×6 column axis +2.54mm from FLT, SCK collinear with the 1×9 row).
   Confirm against the 1:1 paper mockup — `paper-doll-1to1.pdf` is current.
2. **SW1 pitch unknown** until the part arrives — footprint is dual-pitch
   (slots accept 2.0 AND 2.54mm), but verify the body/lever fits.
3. **RV1 (RV097NS) bracket lug geometry** is from the generic RV09 drawing —
   least-certain footprint on the board; check on the paper doll.
4. **TP4056 pad-row edge offset never measured** — module may sit shifted
   N/S on the J5/J6 sockets; region y24–41 has margin, but check.
5. **JST cell polarity** (silk warns): B− is NOT GND — verify wiring of the
   cell before first plug-in.
6. Antenna keepout (x<6, y6–28) is pour-free on both layers; a few thin
   signal traces may pass the region edge (soft-penalized, not forbidden).

## Files

- `carrier.kicad_pro/.kicad_sch/.kicad_pcb` — the project (generated)
- `paper-doll-1to1.pdf` — 1:1 top view for the paper mockup
- `render-top.png`, `render-bottom.png` — 3D previews
- `tools/design.py` — single source of truth (nets, parts, deviations)
- `tools/check_netlist.py` — respin gate; run after any design.py change
