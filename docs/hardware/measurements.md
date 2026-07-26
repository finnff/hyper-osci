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

**Photogrammetry session:** 2026-07-26. Every socket position the carrier draws now
comes from a calibrated two-sided photo of the real module, not from calipers and not
from a datasheet — see [§ Photogrammetry](#photogrammetry-hwpin_locs--the-numbers-the-carrier-actually-uses)
below. Where the two disagree, the photogrammetry wins and the caliper row is marked
_superseded_.

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

6. **TP4056 output pads are NOT on a 2.54 grid** (2026-07-26 photogrammetry). They are
   two pairs — `OUT−`/`B−` at 3.526 mm and `B+`/`OUT+` at 3.106 mm — with a 7.43 mm
   gutter between them, spanning nearly the whole 17.3 mm edge. No stock 1×2 socket
   mates with that, so J5/J6 became generated footprints at the measured pitches.
7. **PCM5102A row-to-row offset was 4.06 mm out** (2026-07-26 photogrammetry). The 1×6
   I2S column is not collinear with the 1×9 row and sits past its FLT end; see the
   PCM5102A table below. This is the socket the v1.0 board got wrong.

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
| 9-pin header distance from 6-pin row | — | ~~PROVISIONAL (phone-measured): 1×6 row axis +2.54 mm from FLT, SCK collinear with the 1×9 row~~ **SUPERSEDED — the guess was 4.06 mm out.** Photogrammetry: SCK (J2 pin 1) sits **26.917 mm west and 0.583 mm south** of LROUT (J3 pin 1), i.e. the 1×6 column is *past* the FLT end of the 1×9 row and **not** collinear with it. Worst pin 0.180 mm off an ideal 2.54 mm socket. See §Photogrammetry | 2026-07-26 |
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
| B+, B−, OUT+, OUT− pad positions + drill | ~2.54 grid (usually *almost*) | Order confirmed: THT pads on the left short edge, top→bottom **`OUT− · B− · B+ · OUT+`** (+ `M` mounting mark). ⚠️ **"~2.54 grid" is WRONG** — photogrammetry gives **0 / 3.526 / 10.960 / 14.066 mm**: two pairs (3.53 and 3.11 pitch) with a 7.43 mm gutter, spanning nearly the whole 17.3 mm edge. Nothing on a 2.54 grid mates with it. Holes 2.0 mm ⇒ ±0.55 mm slack per pin. See §Photogrammetry | 2026-07-26 |
| IN+ / IN− pads: drilled or SMD-only? position | — | **drilled**, next to the USB-C jack | 2026-07-18 |
| USB connector type (micro-B vs USB-C) + overhang | — | **USB-C**; overhang **1.4 mm** (28.3 − 26.9) | 2026-07-18 |
| RPROG value (read resistor / marking) | 1.2 kΩ → 1 A default | **measured 1.19 kΩ** (no legible marking) ⇒ ~1 A default ✓ | 2026-07-18 |
| B+ ↔ OUT+ continuity | 0 Ω (same copper) | ✅ **continuous** (0 Ω) | 2026-07-18 |
| B− ↔ OUT− non-continuity | open (protection FET) | ✅ **open** (protection FET present) | 2026-07-18 |

_ICs: main charger marked `CSM4056T` (TP4056-equivalent), `8205LA` = FS8205 dual FET,
`U2` = DW01-type protection. Behaves per the nominal TP4056+DW01+FS8205 spec._

## Photogrammetry (`hw/pin_locs`) — the numbers the carrier actually uses

Calipers give you a board outline and a pitch. What a socket needs is the position of
every pin *relative to every other pin*, and that is what the caliper session could not
supply — the two numbers it guessed at (the PCM5102A's row-to-row offset and the
TP4056's "~2.54 grid") were both wrong, and both were wrong in the layout.

Method: two calibrated photographs per module (front and back), pin holes picked by
hand on both faces, a homography fitted to the pin lattice, and only the through-holes
confirmed from *both* sides kept. Origin is pin 1 of the module's primary row.
Raw output — CSV, JSON and annotated overlays — lives in [`hw/pin_locs/`](../../hw/pin_locs/).

`hw/carrier/tools/measured.py` is the only consumer: it re-expresses each module in a
row-aligned frame, least-squares-fits an *ideal* 2.54 mm socket to each row, and hands
the layout the pair (offset, worst residual). Nothing in the board is typed in by hand —
re-measure a module, drop the new CSV in, re-run `gen_board.py`, and the board follows.
`python3 tools/measured.py` prints the table below live.

| Quantity | Value (mm) | Worst pin | Note |
|---|---|---|---|
| PCM5102A J3 (1×9) | datum = J3 pin 1 (LROUT) | 0.117 | within-row scatter, irreducible |
| PCM5102A J2 (1×6) from J3.1 | **(−26.917, +0.583)** | 0.180 | the layout chooses this offset — the one number that was guessed before |
| PCM5102A outline from J3.1 | x −28.47…+3.55, y −1.40…+15.97 | — | 32.0 × 17.4 mm |
| TP4056 pads from OUT− | **0 / 3.526 / 10.960 / 14.066** | — | 2.0 mm holes ⇒ ±0.55 mm slack on our own pins |
| TP4056 J6 from J5.1 | (0.000, +10.960) | — | the 7.43 mm gutter between the pairs |
| TP4056 J9 (IN+) from J5.1 | (+22.358, +13.910) | — | soldered wire pad, not a fit datum |
| TP4056 outline from J5.1 | x −1.81…+24.07, y −1.82…+15.64 | — | 25.9 × 17.5 mm |
| ESP32-C3 JA1 (5V row) from JB1.1 | (−0.018, +15.240) | 0.100 | confirms the nominal 15.24 row spacing |
| ESP32-C3 outline from JB1.1 | x −20.98…+1.39, y −1.32…+16.57 | — | 22.4 × 17.9 mm |
| MAX4466 1×3 pitch | 2.5400 | 0.066 | on a pigtail — fit not constrained |

Acceptance gate is **0.25 mm per pin**, the misalignment a 2.54 mm female header takes
before a rigid module pin fouls the barrel. All three socketed modules pass with margin
(worst: PCM5102A 0.180 mm at J2.5). `tools/audit_board.py` re-checks this against the
actual board on every build, so a placement edit cannot silently break a module fit.

Two facts the millimetre tables cannot carry, both taken from the pixel coordinates:

- **Handedness.** A module plugs in component-side-up, so the front photo and the KiCad
  top view are the *same* view. If the declared carrier mapping flips that determinant,
  the two socket rows are swapped end-for-end and every net lands on the wrong pin —
  which is exactly what v1.0 did to the SuperMini. `measured.Placed._check_mirror` now
  raises rather than generating that board.
- **Which row is which.** The SuperMini's back silk reads `5V G 3.3 4 3 2 1 0` down the
  left column with USB-C up, so on the component side that column is on the right. With
  the antenna west (§6.1 rule 3) the 5V row therefore lands **south** — JA1 south, JB1
  north.

## Bought parts

| Measurement | Nominal | Measured | Date |
|---|---|---|---|
| RCA output pads (X = PCM5102A L, Y = PCM5102A R): signal pad ↔ reused ~50 cm RCA cable tip; ground pad ↔ RCA shell | signal via R10/R11 (100 Ω series); ground pad = board GND, shell = GND | design item (no module to measure) — pads are simple signal+AGND lands. **Note:** analog source is now **LROUT/ROUT off the 9-pin header**, and a module-side 470 Ω filter exists (see PCM5102A ⚠️) — confirm R10/R11 value in that light | _design_ |
| SW1 slide-switch pin pitch | 2× 3-pin @ 2.0 or 2.54 mm | _not in hand yet_ | _pending_ |
| TP4056 pad-row → module short-edge offset | — | _open — gates the order; calipers, module in hand_ | _pending_ |
| RV1 pot | (was undecided) | ✅ **RV097NS-B10K**, 5-pin **mono with switch**, **right-angle / side-adjust** (shaft parallel to the board, exiting the south edge — *not* vertical, as this row implied until 2026-07-27), body **27.3 × 9.5 × 11.3 mm**, metal shaft. **No knob** — turned by hand → enclosure needs only a shaft hole. Resolves Q3 + Q9 | 2026-07-18 |
| RV1 row-2 geometry vs the RV097NS footprint | "bracket lugs" from the generic RV09 drawing: 9.5 mm apart, 7.0 mm behind the pot row, oval 1.2 × 2.0 slots | ✅ **WRONG — corrected 2026-07-27.** The part is *5-pin mono **with switch***; it has no bracket lugs. Row 2 is an SPST on ⌀1.0 holes **5.0 mm apart, 6.25 mm behind** the pot row, and the **mounting surface is 5.0 mm in front of** the pot row (was drawn at 1.2 mm). Every row-2 pad was 2.25 mm out in X and 0.75 mm in Y — the part would not have gone in. Source: the seller's mechanical drawing (`5Pin Mono (with Switch)`, [drawing](https://i.ebayimg.com/images/g/3KIAAOSwd9VjM8EZ/s-l1600.webp)), which agrees exactly with KiCad's stock `Potentiometer_Alps_RK097_Single_Horizontal_Switch` (from ALPS `rk097.pdf`) | 2026-07-27 |
| RV1 body cross-section (the check that identifies the part) | — | ✅ drawing says **9.5 mm** wide and 6.5 + 4.85 = **11.35 mm** tall; the 2026-07-18 calipers said **9.5 × 11.3**. Independent confirmation that this drawing is our part. The third caliper figure, 27.3 mm, is the back-of-body-to-shaft-tip length: drawing gives body 13.0 + shaft L 15.0 = 28.0 nominal, so **re-measure that one** — 0.7 mm unexplained, though it changes no hole position | 2026-07-27 |

## Power path — nothing measured yet

Every number in [pcb.md](pcb.md) §4.3 is paper. The 2026-07-26 design review found two of
them wrong on paper, so the bench values matter. There is no row here yet because the test
cannot run on the current USB-only rig (no battery, no TP4056 in circuit, Q1 is SOT-23) — it
moves to the assembled carrier via TP1/TP2/TP3. Fill these in when it does:

| Measurement | Expected | Measured | Date |
|---|---|---|---|
| TL431 trip point (VSW rising), R7/R8 = 8.2 k/10 k + TL431A | **≈4.56 V** (window 4.46–4.66) | _pending_ | |
| State 1 VBAT→VLOAD drop at 150 mA | < 20 mV | _pending_ | |
| **State 4 cell current, source sagged to 4.4 / 4.5 / 4.6 V**, milliohm shunt, SoC 3.6–4.0 V | ≤ 1 mA — *this is the one the review says will fail* | _pending_ | |
| State 2/3 cell trickle while charging (D1 Schottky fitted) | ≤ 1 mA | _pending_ | |
| State 6 standby current at the cell | ~260 µA fully populated / ~30 µA on a JP1 plan-A board | _pending_ | |
| U1 / Q2 TO-92 lead order vs the footprint (ohm REF against R7/R8) | REF on the pad wired to VSW_SENSE | _pending_ | |

Also still open and **resolvable now on the USB rig**: the SuperMini LDO marking `S2LC`
(recorded above) is undecoded and the carrier has **no fallback LDO footprint**, so if it turns out to
be a ~250 mA part there is no board-level remedy. Stress-test it: run NETWORK streaming with
`WiFi.setSleep(false)` and watch for resets or rail sag. If sustained streaming already works
on these boards without resets, that closes the current-rating half of the question — record
it here.
