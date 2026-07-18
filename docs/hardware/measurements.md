# Hardware measurements — fills pcb.md §7

Caliper + bench session with the four real modules on the bench. **No footprint is
drawn from internet dimensions** — every KiCad footprint comes from a number in the
tables below. **This file must be complete before KiCad layout** (pcb.md §9,
DESIGN §12 mechanical item).

Fill `Measured` and `Date` as you go. `Nominal` values are the datasheet/expected
figures from [pcb.md](pcb.md) §2/§7 — flag any measured value that disagrees.

**Bench meter:** ANENG A9002 handheld DMM. Good for steady-state microamp reads
(e.g. deep-sleep current); it **cannot** capture sub-millisecond WiFi-TX current
bursts, so TX-burst current figures stay modeled/estimated, not bench-measured.

**Bench session:** 2026-07-18 (Finn), calipers + ANENG A9002 + loupe. Reference
photos live in [`images/`](images/) and are embedded per module below.

---

## ⚠️ Findings that disagree with nominal — READ BEFORE KiCad layout

These change footprints / floorplan and must be reconciled into `pcb.md` (and DESIGN
where noted) **before** layout starts:

1. **PCM5102A is ~31.8 mm long, not ~27 mm** (+~5 mm). The `~70 × 50 mm` floorplan
   (pcb.md §1/§6) must be re-checked against a 32 mm module.
2. **PCM5102A analog end is a 1×9 header, not a 1×3 `L G R`.** Real silk (jack→digital):
   `LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`. Our analog taps become
   **X = LROUT, Y = ROUT, ground = AGND**. pcb.md §1/§2 socket **J3 = 1×3 female `L G R`
   is wrong** → it must become a 1×9 socket (or a partial socket over the LROUT/AGND/ROUT
   pins). This is the biggest change.
3. **PCM5102A has an on-board output reconstruction filter** (`471` ≈ 470 Ω series parts +
   caps near the outputs), not bare DAC pins. This is a filter, **not** DC-blocking — the
   output stays ground-centered (DESIGN §5 assumption holds). Consequence is only for R10/R11:
   with ~470 Ω already in series, decide 0 Ω vs 100 Ω on the Phase-4 bench ramp.
4. **SuperMini LDO marking reads `S2LC`, not the expected `S2QB`.** Decode/confirm the
   current rating (need ≥ ~0.35 A for WiFi TX peaks) before trusting it. Not the feared
   250 mA `LLVB`, but not the verified 500 mA `S2QB` either.
5. **Q10 RESOLVED:** SuperMini USB-C `VBUS` **is** directly tied to the `5V` pin
   (meter-confirmed). ⇒ the "never plug USB-C while the battery switch is ON" rule is
   **mandatory** (USB would back-feed the cell). Close Q10.

Non-blocking notes: SuperMini PCB width measures 17.8 mm (nominal 18 — fine). TP4056
charger IC is marked `CSM4056T` (a TP4056 equivalent) with `8205LA` (FS8205) + DW01-type
protection — behaves per spec (continuity checks pass).

---

## ESP32-C3 SuperMini

<p>
<img src="images/ESP32C3_FRONT.jpg" width="240" alt="ESP32-C3 SuperMini — front (component/USB-C side)">
<img src="images/ESP32C3_BACK.jpg" width="240" alt="ESP32-C3 SuperMini — back (silk pinout, TENSTAR ROBOT)">
</p>

Photos: [front](images/ESP32C3_FRONT.jpg) · [back](images/ESP32C3_BACK.jpg)

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | 22.5 × 18 mm | **22.5 × 17.8 mm** (PCB); **24 mm** L incl. USB-C overhang. Width 17.8 vs 18 — OK | 2026-07-18 |
| Pin-row pitch | 2.54 mm | 2.54 (0.1″ header) ✓ | 2026-07-18 |
| Pins per row | 8 | 8 ✓ | 2026-07-18 |
| Row-to-row spacing | 15.24 mm | ~15.2 (reads 15.24) ✓ | 2026-07-18 |
| First-pin offset from each board edge | — | **1.3 mm** on USB-C end, **2.8 mm** on antenna end (edge → inside of pin hole) | 2026-07-18 |
| Antenna end (which end) + overhang of antenna region from last pin row | — | **Non-USB-C end.** Integrated PCB antenna (silk `C3`), **flush with board edge — 0 mm overhang** (copper keep-out, not a mechanical overhang) | 2026-07-18 |
| USB-C end (which end) + connector overhang past board edge | — | **Opposite the antenna.** Connector overhangs **~1.5 mm** past the board edge | 2026-07-18 |
| Pin silk order of both rows vs pcb.md §2 table | matches §2 | ✓ **matches §2.** Back silk: L col `5V G 3.3 4 3 2 1 0`, R col `5 6 7 8 9 10 20 21`. Board marked "TENSTAR ROBOT ESP32-C3 Super Mini" | 2026-07-18 |
| Bottom-side component height (socket clearance) | ≤ ~8.5 mm | **~8.3 mm** ✓ (within 8.5; socket clearance OK but tight) | 2026-07-18 |
| LDO marking (SOT-23-5, loupe) | ME6211C33 ("S2QB"); beware "LLVB" = 250 mA | ⚠️ **reads `S2LC`** — not `S2QB`, not `LLVB`. Decode + confirm ≥0.35 A rating before trusting | 2026-07-18 |
| USB-C VBUS ↔ 5 V pin beep test (direct tie?) — Q10 | expected tied | ✅ **CONFIRMED tied** (meter continuity). ⇒ USB back-feeds the cell — the "no USB while batt switch ON" rule is mandatory. **Closes Q10.** | 2026-07-18 |

## GY-PCM5102A (purple)

<p>
<img src="images/PCM5102A_FRONT.jpg" width="300" alt="GY-PCM5102A — front (PCM5102A IC, 6-pin I2S header, 9-pin analog/control header, LINE OUT jack)">
<img src="images/PCM5102A_BACK.jpg" width="300" alt="GY-PCM5102A — back (silk: LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT; H1L–H4L config jumpers)">
</p>

Photos: [front](images/PCM5102A_FRONT.jpg) · [back](images/PCM5102A_BACK.jpg)

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | ~27 × 17 mm | ⚠️ **~31.8 × 17 mm** — module is ~5 mm LONGER than nominal. Re-check the 70×50 floorplan | 2026-07-18 |
| 6-pin header pitch | 2.54 mm | 2.54 (0.1″ header) ✓ | 2026-07-18 |
| 6-pin header offset | — | on the short (digital) edge; single 1×6 row | 2026-07-18 |
| 6-pin header order silk | SCK BCK DIN LCK GND VIN | ✅ **`SCK BCK DIN LCK GND VIN`** (top→bottom on the short edge) — matches | 2026-07-18 |
| ~~3-pin analog header position~~ → **actual: 1×9 analog/control header** | (assumed 1×3 `L G R`) | ⚠️ **NOT a 3-pin header.** It is a **1×9 header along the long edge.** Analog out lives here: use **X=LROUT, Y=ROUT, gnd=AGND.** → pcb.md **J3 must be 1×9, not 1×3** | 2026-07-18 |
| 9-pin header order silk | (n/a) | Back silk, jack-end → digital-end: **`LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`** (front abbreviates the analog end `… G R G L`) | 2026-07-18 |
| 9-pin header distance from 6-pin row | — | perpendicular: 6-pin on short edge, 9-pin on long edge (measure spacing at layout) | _pending_ |
| 3.5 mm jack overhang | may hang off carrier edge | **~1.6 mm** (jack makes total 18.6 vs 17 mm board) — hangs off edge, fine | 2026-07-18 |
| Solder-bridge state (DESIGN §5) | 1=L, 2=L, 3=H, 4=L | Pads present on back: **`H1L H2L H3L H4L`** (+ `PCM5100/5101/5102` selector). State not readable from photo — **verify by continuity** against 1=L,2=L,3=H,4=L | _verify_ |
| Series R/filter on L/R outputs? (trace or measure) | expect none (fit 0 Ω if present) | ⚠️ **Filter PRESENT** — `471` (≈470 Ω) series parts + caps sit between the DAC and the output pins/jack. Confirm whether LROUT/ROUT are pre- or post-filter; revisit R10/R11 100 Ω plan | 2026-07-18 |

## MAX4466

<p>
<img src="images/MAX4466_FRONT.jpg" width="220" alt="MAX4466 — front (electret capsule, 'VCC 2.4-5.5V Adjustable gain', silk OUT GND VCC)">
<img src="images/MAX4466_BACK.jpg" width="220" alt="MAX4466 — back (op-amp, gain trimmer, silk VCC GND OUT)">
</p>

Photos: [front](images/MAX4466_FRONT.jpg) · [back](images/MAX4466_BACK.jpg)

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | ~20 × 13 mm | **20.8 × 13.5 mm** ✓ | 2026-07-18 |
| Header pitch | 2.54 mm | 2.54 (3-pin 0.1″) ✓ | 2026-07-18 |
| Header offset | — | 3-pin row centered on one short edge | 2026-07-18 |
| Pin order silk (clones differ) | VCC GND OUT | ✅ **`VCC GND OUT`** reading from the back (op-amp) side; front (capsule side) silk mirrors it as `OUT GND VCC`. Matches §2 J4 — wire the pigtail by **back** silk | 2026-07-18 |
| Capsule diameter + position (silk aperture circle) | — | electret capsule **~9.7 mm** dia, top-center on the capsule (front) side; 2× mounting holes at top corners | 2026-07-18 |
| Gain trimmer position (reachable when socketed?) | — | multiturn trimpot on the **back** (component) side, bottom-center → **screwdriver-reachable on the ~10 cm pigtail** ✓ | 2026-07-18 |

## TP4056 (USB-C, blue — CSM4056T + DW01-type + FS8205)

<p>
<img src="images/TP4056_FRONT.jpg" width="300" alt="TP4056 — front (USB-C, CSM4056T charger, 8205LA FET, B+ B- OUT+ OUT- pads)">
<img src="images/TP4056_BACK.jpg" width="300" alt="TP4056 — back (traces, mounting pads)">
</p>

Photos: [front](images/TP4056_FRONT.jpg) · [back](images/TP4056_BACK.jpg)

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| Board outline L × W | ~26 × 17 mm | **26.9 × 17.3 mm** ✓ | 2026-07-18 |
| B+, B−, OUT+, OUT− pad positions + drill | ~2.54 grid (usually *almost*) | THT pads on the left short edge, top→bottom: **`OUT− · B− · B+ · OUT+`** (+ `M` mounting mark). ~2.54 grid | 2026-07-18 |
| IN+ / IN− pads: drilled or SMD-only? position | — | **drilled**, next to the USB-C jack | 2026-07-18 |
| USB connector type (micro-B vs USB-C) + overhang | — | **USB-C**; overhang **1.4 mm** (28.3 − 26.9) | 2026-07-18 |
| RPROG value (read resistor / marking) | 1.2 kΩ → 1 A default | **measured 1.19 kΩ** (no legible marking) ⇒ ~1 A default ✓ | 2026-07-18 |
| B+ ↔ OUT+ continuity | 0 Ω (same copper) | ✅ **continuous** (0 Ω) | 2026-07-18 |
| B− ↔ OUT− non-continuity | open (protection FET) | ✅ **open** (protection FET present) | 2026-07-18 |

_ICs: main charger marked `CSM4056T` (TP4056-equivalent), `8205LA` = FS8205 dual FET,
`U2` = DW01-type protection. Behaves per the nominal TP4056+DW01+FS8205 spec._

## Bought parts

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| RCA output pads (X = PCM5102A L, Y = PCM5102A R): signal pad ↔ reused ~50 cm RCA cable tip; ground pad ↔ RCA shell | signal via R10/R11 (100 Ω series); ground pad = board GND, shell = GND | design item (no module to measure) — pads are simple signal+AGND lands. **Note:** analog source is now **LROUT/ROUT off the 9-pin header**, and a module-side 470 Ω filter exists (see PCM5102A ⚠️) — confirm R10/R11 value in that light | _design_ |
| SW1 slide-switch pin pitch | 2× 3-pin @ 2.0 or 2.54 mm | _not in hand yet_ | _pending_ |
| RV1 pot | (was undecided) | ✅ **RV097NS-B10K**, 5-pin, body **27.3 × 9.5 × 11.3 mm**, metal shaft. **No knob** — turned by hand → enclosure needs only a shaft hole. Resolves Q3 + Q9 | 2026-07-18 |
