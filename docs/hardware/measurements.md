# Hardware measurements — fills pcb.md §7

Caliper + bench session with the four real modules on the bench. **No footprint is
drawn from internet dimensions** — every KiCad footprint comes from a number in the
tables below. **This file must be complete before KiCad layout** (pcb.md §9,
DESIGN §12 mechanical item).

Fill `Measured` and `Date` as you go. `Nominal` values are the datasheet/expected
figures from [pcb.md](pcb.md) §2/§7 — flag any measured value that disagrees.

---

## ESP32-C3 SuperMini

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | 22.5 × 18 mm | | |
| Pin-row pitch | 2.54 mm | | |
| Pins per row | 8 | | |
| Row-to-row spacing | 15.24 mm | | |
| First-pin offset from each board edge | — | | |
| Antenna end (which end) + overhang of antenna region from last pin row | — | | |
| USB-C end (which end) + connector overhang past board edge | — | | |
| Pin silk order of both rows vs pcb.md §2 table | matches §2 | | |
| Bottom-side component height (socket clearance) | ≤ ~8.5 mm | | |
| LDO marking (SOT-23-5, loupe) | ME6211C33 ("S2QB"); beware "LLVB" = 250 mA | | |
| USB-C VBUS ↔ 5 V pin beep test (direct tie?) | expected tied | | |

## GY-PCM5102A (purple)

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | ~27 × 17 mm | | |
| 6-pin header pitch | 2.54 mm | | |
| 6-pin header offset | — | | |
| 6-pin header order silk | SCK BCK DIN LCK GND VIN | | |
| 3-pin analog header position | — | | |
| 3-pin analog header order silk | L G R | | |
| 3-pin header distance from 6-pin row | — | | |
| 3.5 mm jack overhang | may hang off carrier edge | | |
| Solder-bridge state (DESIGN §5) | 1=L, 2=L, 3=H, 4=L | | |
| Series R/filter on L/R outputs? (trace or measure) | expect none (fit 0 Ω if present) | | |

## MAX4466

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | ~20 × 13 mm | | |
| Header pitch | 2.54 mm | | |
| Header offset | — | | |
| Pin order silk (clones differ) | VCC GND OUT | | |
| Capsule diameter + position (silk aperture circle) | — | | |
| Gain trimmer position (reachable when socketed?) | — | | |

## TP4056 03962A (with DW01 + FS8205)

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | ~26 × 17 mm | | |
| B+, B−, OUT+, OUT− pad positions + drill | ~2.54 grid (usually *almost*) | | |
| IN+ / IN− pads: drilled or SMD-only? position | — | | |
| USB connector type (micro-B vs USB-C) + overhang | — | | |
| RPROG value (read resistor / marking) | 1.2 kΩ → 1 A default | | |
| B+ ↔ OUT+ continuity | 0 Ω (same copper) | | |
| B− ↔ OUT− non-continuity | open (protection FET) | | |

## Bought parts

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| BNC (X1/X2) actual leg pattern | draw from physical part | | |
| SW1 slide-switch pin pitch | 2× 3-pin @ 2.0 or 2.54 mm | | |
| RV1 (RV09) pin pattern + shaft length vs knob | 2.5/5.0 mm triangle | | |
