# HYPEROSCI Carrier PCB — v1.1 Specification

> ## ✅ 2026-07-28 — routed and gate-clean on **seed 33**.
>
> The pre-fab DFM pass (silk stroke 0.12 → **0.15 mm**, SW1's plated slots re-cut
> to JLC's 2× aspect, silk legends kept off the module bodies) moved copper, which
> killed router seed 11. A 16-variant re-sweep found **exactly one clean result**
> — `halo-off`, **seed 33** — now `route.py`'s default. The board is
> **1343 segments, 48 GND vias, 0 unconnected, 0 DRC violations at every
> severity**, ground pour whole with zero repair. §6.4's width table and §4.4's
> drop figures have been **regenerated from the routed board** and are as-built.
>
> **✅ No gates remain — the gerbers are plotted, verified and committed** to
> `hw/carrier/fab/` (`tools/plot_fab.py` regenerates them and refuses to write
> the zip unless the board still passes). SW1, the last one, closed 2026-07-28
> from the SS12D00 datasheet without the part in hand and without a layout
> change: 2.5 mm pitch inside the slot's 2.00–2.90 window, 8.5 × 3.7 body on the
> pin centreline inside the 9.0 × 4.0 silk. See §7 and `layout-notes.md` →
> VERIFY item 2.

**Status:** layout v1.1 complete, routed and gate-clean (2026-07-28, seed 33) — see
[`hw/carrier/layout-notes.md`](../../hw/carrier/layout-notes.md). This document is the spec
of record for the fab order and the BOM. Pin map / constants mirror
[`docs/DESIGN.md`](../DESIGN.md) §4 and
[`src/esp32-slave/include/config.h`](../../src/esp32-slave/include/config.h) — those are law.
The board is **generated, not drawn**: `hw/carrier/tools/design.py` is the netlist source of
truth and the scripts build the schematic, footprints, board and routing from it (§9).
Anything genuinely undecided is listed in §10 (open questions) or carried as a
DNP (do-not-populate) footprint so the decision moves to assembly time, not layout time.

> **Review status (2026-07-26).** The load-sharing power path (§4) has been through an
> adversarial design review — [`pcb-review-findings.md`](pcb-review-findings.md). Two of its
> conclusions are load-bearing and are folded into the text below: **§4.3 state 4 does not
> work as originally written**, and the **JP1 fallback recipe was unsafe** because it left D2
> fitted. The §4 block is therefore an *experiment*, not the plan of record — see §4.5.

---

## 1. Concept

- **Board:** ~70 × 50 mm, 2-layer FR-4, 1.6 mm, 1 oz copper, HASL (lead-free), green.
- **Assembly:** hand-soldered by Finn. All through-hole **except** Q1 (SOT-23) and D2 (SMA) in
  the power path — both are trivially hand-solderable on enlarged hand-solder pads. THT
  alternates for both are specified in the BOM (§5) with shared/dual footprints.
- **Modules are socketed** on female 2.54 mm header sockets, never soldered down:

| Module | Socket | Notes |
|---|---|---|
| ESP32-C3 SuperMini | 2× 1×8 female (J1A/J1B) | antenna end at the west edge. The antenna is **flush** with the module's own board edge (0 mm overhang, measured) — what protects it is the copper keep-out inboard of that edge, §6.1 rule 3 |
| GY-PCM5102A (purple) | 1×6 female (J2) + 1×9 female (J3) | I2S end (short edge) + 9-pin analog/config end on the long edge (⊥ to J2). Module measures **32.0 × 17.4 mm** (photogrammetry) — see §7/measurements.md |
| MAX4466 mic | 1×3 header (J4) | off-board on a ~10 cm pigtail (`VCC/GND/OUT`); aimed at the PA |
| TP4056 USB-C (blue, **26.9 × 17.3 mm** *with* the two west-edge depanelization nubs; **25.2 mm body** once they are filed off — §5) | **6× machined single-pin sockets**: the output row (J5 = `OUT−`/`B−` @ 3.526 mm, J6 = `B+`/`OUT+` @ 3.106 mm) **plus a mount row at the USB-C end** 21.65 mm away (J9 = `IN+`, J10 = `IN−`/NC) — four in one line left the jack on a 21.65 mm cantilever, §5 | USB-C end at board edge; the jack overhangs the module's own east edge by **1.4 mm**, which lands it ~0.5 mm proud of the carrier edge (§6.1 rule 4 — an enclosure constraint). **Not a stock 1×2 socket** — the pads are not on a 2.54 grid, see §2 |

- Passive parts may be placed **under the socketed modules**. The gated figure is
  **8.3 mm**: that is the SuperMini's own bottom-side component height (measured), and it is
  what a 2.54 mm socket pair must clear, so `audit_board.py` refuses any part taller than
  8.3 mm inside a module's measured body outline. As built, nothing is. This is how
  everything fits in 70 × 50 mm.
- **Battery (off-board):** EEMB **LP103454** LiPo 3.7 V **2000 mAh**, 34 × 56 × 11 mm, ~40 g,
  with a pre-fitted JST-PH lead (no crimping). It does **not** sit on the PCB — it is mounted
  loose (velcro/pocket) in the 3D-printed enclosure, since 56 × 34 mm is large next to the
  70 × 50 mm carrier. The carrier only carries the JST-PH socket (J8). The ~10 h runtime target
  is met comfortably (≈13 h NETWORK, ≈30 h LOCAL).
  **Sourcing changed 2026-07-27: cells come from Amazon**, not the EEMB part, to keep the LiPo
  off the slow-freight critical path. Two things the substitution puts on you, because a
  generic cell guarantees neither: **(a)** it must terminate in a **JST-PH 2.00 mm** plug — PH,
  XH and mini-Tamiya all ship on Amazon LiPos; **(b) meter the polarity before the first
  plug-in** (layout-notes VERIFY 5). Cell-lead polarity is not standardised, J8's silk assumes
  the EEMB convention, and on this board `B−` is deliberately **not** GND (§3.1), so a reversed
  cell drives the pack backwards through the FS8205 rather than simply failing to run. Keep the
  capacity near 2000 mAh — every runtime and threshold figure in
  [power-budget.md](power-budget.md) is written against it.
- **Enclosure:** 3D-printed case; the carrier keeps its 4× M3 mounting holes (§6).
- 4 identical boards will be built (a 5th possible later); order **5 PCBs** (§8) — the fab
  minimum quantity is 5 anyway, which yields one spare bare board.

---

## 2. Connectors & sockets — pin-by-pin

Reference designators used throughout this doc and to be used in KiCad:

| Ref | Part | Function |
|---|---|---|
| J1A, J1B | 1×8 female header, 2.54 mm | SuperMini socket rows. **KiCad refs are `JA1`/`JB1`** (a KiCad reference must end in a digit); the silk keeps the doc names. Row spacing ✅ **measured 15.240 mm** (photogrammetry, worst pin 0.100 mm). `JB1` = the GPIO5-side row and it is the **north** one — see §6.1 rule 3. |
| J2 | 1×6 female header | PCM5102A I2S end: `SCK BCK DIN LCK GND VIN` ✅ order confirmed against the module silk 2026-07-18 |
| J3 | 1×9 female header | PCM5102A analog/config end (long edge). Silk (jack→digital): `LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`. Carrier nets only the analog pins: **LROUT (=X), ROUT (=Y), AGND**; FLT/DEMP/XSMT/FMT are set by the module's own H1L–H4L back-side bridges (DESIGN §5), so they are **NC** on the carrier — the rest of the 1×9 is mechanical support. |
| J4 | 1×3 header (pigtail landing) | MAX4466 on a ~10 cm 3-wire pigtail: `VCC GND OUT` ✅ confirmed on the back silk 2026-07-18. Mic exits the enclosure, aimed at the PA. |
| J5 | 1×2 pad pair, **3.526 mm** pitch | TP4056 north pair: `OUT− B−`. ⚠️ **Not a stock socket and not a 2.54 grid** — the module's four pads measure 0 / 3.526 / 10.960 / 14.066 mm down its short edge (photogrammetry, 2026-07-26), so the grouping is `(OUT−,B−)` + `(B+,OUT+)`, *not* the battery-end/output-end split this table used to claim. Generated footprint `HYPEROSCI:TP4056_Pads_OUTminus_Bminus`. |
| J6 | 1×2 pad pair, **3.106 mm** pitch | TP4056 south pair: `B+ OUT+`, 10.960 mm south of J5.1. Generated footprint `HYPEROSCI:TP4056_Pads_Bplus_OUTplus`. Fit four machined single sockets, or solder wires and give up removability. |
| J6b | 1 machined single-pin socket | TP4056 `IN+` (VBUS_CHG sense — see §4; 1.0 mm drill). **KiCad ref is `J9`.** No longer a wire pad: it is half of the **USB-C-end mount row** (§5), 21.65 mm east of the J5/J6 column, on the module's `+`-marked corner pad. |
| — | 1 machined single-pin socket | **`J10`** — the other USB-C-end corner pad (`IN−`). **On no net, deliberately**: its job is mechanical, and `IN− ≡ OUT−` is asserted in this table but has never been measured on this module. Bonding it to GND on a wrong assumption shorts across the DW01/FS8205. See [measurements.md §The second mount row](measurements.md). |
| J7 | 1×2 male header | Debug: `GPIO21 (UART0 TX)`, `GND` |
| J8 | JST-PH 2-pin, side entry (S2B-PH-K-S) | LiPo battery in (EEMB LP103454 2000 mAh, mounted off-board — cell not on the PCB outline). **Polarity silk mandatory.** |
| X, Y | 2× RCA flying-lead solder pad-pair (signal + AGND) | Scope X (=DAC L), Scope Y (=DAC R). Finn solders the reused ~50 cm RCA cables here — no connector part on the board. Silk `X` / `Y`. |
| SW1 | SPDT slide switch — **SS12D00, 6 mm handle** (ordered 2026-07-27) | Power switch (in VSW→VLOAD path, §4). Its 0.3 A rating is a *make/break* figure at 50 VDC; this switch breaks 4 V, so the ≈0.35 A WiFi peak is accepted — see the §5 row |
| SW2 | 6×6 mm THT tactile | MODE button → GPIO7 |
| RV1 | **RV097NS** 9 mm 10 kΩ linear pot (B10K), **5-pin mono *with switch*, right-angle**, body 27.3 × 9.5 × 11.3 mm. **Metal shaft turned directly — no knob.** Pads 4/5 are the SPST, not bracket lugs (the part has none) — both parked on GND, see §5. The **mounting surface sits on the board's south edge**; the M7×0.75 bushing and 15 mm shaft hang off it. | Filter-cutoff → GPIO3 |

**SuperMini socket pin functions** (net names; physical position per module silk —
✅ confirmed 2026-07-18 against the owned TENSTAR ROBOT modules, `5V G 3.3 4 3 2 1 0` /
`5 6 7 8 9 10 20 21`. Re-check if a future batch is a different clone revision):

| J1A (5V-side row, **south**) | Net | J1B (GPIO5-side row, **north**) | Net |
|---|---|---|---|
| 5V | VLOAD | GPIO5 | I2S_LRCK |
| GND | GND | GPIO6 | I2S_DOUT |
| 3V3 | 3V3 | GPIO7 | BTN_MODE |
| GPIO4 | I2S_BCK | GPIO8 | *NC (onboard LED)* |
| GPIO3 | POT_WIPER | GPIO9 | *NC (onboard BOOT)* |
| GPIO2 | GPIO2_PU (10 k to 3V3 only) | GPIO10 | LED_NET_A |
| GPIO1 | VBAT_SENSE | GPIO20 | LED_MODE_A |
| GPIO0 | MIC_OUT | GPIO21 | DBG_TX |

---

## 3. Complete net list (schematic description)

Every net on the board. Pin numbers match `config.h` exactly.

### 3.1 Power nets

| Net | Connects |
|---|---|
| **BAT+** | J8.1 (JST +) → **J6.1** `B+`. Also top of battery divider R1. On this USB-C TP4056 module, `B+` and `OUT+` are the same copper (protection is low-side) — ✅ measured 0 Ω 2026-07-18. |
| **BAT−** | J8.2 (JST −) → **J5.2** `B−` **only**. ⚠️ **B− must NOT join GND** — the DW01/FS8205 protection switches the low side between B− and OUT−. Shorting them defeats protection (✅ measured open 2026-07-18). Silk warning next to J5. |
| **GND** | **J5.1** `OUT−` = system ground: SuperMini GND, J2 GND, J2 SCK (see below), C1−, C2, C3, C5, R2, R6, R8, U1 anode, SW2 pin B, RV1 CCW end **and both ends of RV1's integrated SPST (pads 4/5 — see §5)**, LED cathodes, mounting-hole pads (fenced, see §6). ⚠️ **`J10` (TP4056 `IN−`) is deliberately NOT on this list** — this row used to end "TP4056 `IN−` (same node module-internally)", which contradicts §2, `design.py` (`J10 = {"1": None}`) and the board itself. `IN− ≡ OUT−` has never been measured on this module, and wiring it to GND on that assumption shorts across the DW01/FS8205. Because the protection FET is in the **cell-negative** leg, every carrier load — including the R1/R2 divider — returns through it and is genuinely cut at the DW01's 2.4 V trip. |
| **VBAT_OUT** | J6.2 `OUT+` → Q1 **drain** + **JP1 pin 1** (the §4.4 escape hatch). |
| **VSW** | Q1 **source** + D2 cathode + sense divider top (R7) + **R12** + **Q2 emitter** + **JP1 pin 2** + SW1 pin 1 + testpoint TP2. Note this node is **upstream of SW1**: it is live off the cell through Q1 whenever a battery is connected, whatever position the switch is in (§4.3 state 6). |
| **VLOAD** | SW1 pin 2 → SuperMini `5V` pin, C1 (220 µF) +, C2 (100 nF), **D3 anode (DNP)**. |
| **3V3** | SuperMini `3V3` pin → J2 `VIN` (PCM5102A), J4 `VCC` (mic, with C4 100 nF at the socket), R3 (GPIO2 pull-up), RV1 CW end, C5 (100 nF near J2). Per DESIGN §5 the DAC's VIN runs from 3V3 (double-LDO cascade is fine). |
| **VBUS_CHG** | J6b (TP4056 `IN+`) → D1 anode, D2 anode, testpoint TP1. |
| **GATE** | Q1 gate + R6 (100 k → GND) + D1 cathode + D3 cathode (DNP) + Q2 collector + TP3. |
| **AGND** | Analog ground island: J3 `AGND` (the two AGND pins of the 1×9), the X and Y RCA ground pads, C4 GND side, J4 `GND` (mic). Joined to GND at **one neck** near the J2 GND pin (§6.3). Electrically the same net as GND — draw as GND in the schematic, enforce the single-neck join in layout. |

### 3.2 Signal nets

| Net | GPIO (config.h) | Connects |
|---|---|---|
| **MIC_OUT** | GPIO0 (`PIN_MIC_ADC`) | J4 `OUT` → GPIO0 directly (mic self-biased ~VCC/2, same as old unit) |
| **VBAT_SENSE** | GPIO1 (`PIN_VBAT_ADC`) | R1 (100 k from BAT+) / R2 (100 k to GND) midpoint; C3 100 nF across R2 |
| **GPIO2_PU** | GPIO2 (strapping) | R3 10 k → 3V3. **Nothing else.** Must be high at reset. |
| **POT_WIPER** | GPIO3 (`PIN_POT_ADC`) | RV1 wiper. RV1 pin 1 (CCW) → GND, pin 3 (CW) → 3V3, so clockwise = higher cutoff |
| **I2S_BCK** | GPIO4 (`PIN_I2S_BCK`) | → J2 `BCK` |
| **I2S_LRCK** | GPIO5 (`PIN_I2S_LRCK`) | → J2 `LCK` |
| **I2S_DOUT** | GPIO6 (`PIN_I2S_DOUT`) | → J2 `DIN` |
| **BTN_MODE** | GPIO7 (`PIN_BTN_MODE`) | SW2 → GND. No external pull-up or cap; internal pull-up + 30 ms firmware debounce per config.h |
| **LED_NET_A** | GPIO10 (`PIN_LED_NET`) | GPIO10 → R4 2.2 k → D4 green 3 mm anode; cathode → GND (active high) |
| **LED_MODE_A** | GPIO20 (`PIN_LED_MODE`) | GPIO20 → R5 2.2 k → D5 amber 3 mm anode; cathode → GND (active high) |
| **DBG_TX** | GPIO21 | → J7.1 (J7.2 = GND). Log-only header, no RX (GPIO20 is repurposed as LED) |
| **DAC_L / SCOPE_X** | — | J3 `LROUT` → R10 **0 Ω link** → X RCA signal pad |
| **DAC_R / SCOPE_Y** | — | J3 `ROUT` → R11 **0 Ω link** → Y RCA signal pad |
| **J2 `SCK`** | — | **Tie to GND on the carrier.** This implements DESIGN §5's "SCK tied to GND" (DAC generates its clock via PLL from BCK) without a solder mod on each module. |

The R10/R11 footprints exist to protect the DAC from shorted/hot-plugged scope cables and to
isolate it from cable capacitance — a job a 100 Ω series part would do, at an attenuation into
a 1 MΩ scope input of 0.01 %, i.e. invisible. **They are fitted as 0 Ω links**, for the reason
below.
**Measured 2026-07-18:** the purple module already carries an output reconstruction filter
(~470 Ω "471" series parts + caps) between the DAC and the `LROUT`/`ROUT` pins, and the
output is **ground-centered** (no DC-blocking cap in the path). With ~470 Ω already in
series, keep the R10/R11 footprints but **fit 0 Ω wire links** — §10 Q2, closed 2026-07-28.
The Phase-4 bench ramp cannot decide this: 100 Ω against the module's 470 Ω moves the phase
at 10 kHz by ≲0.07° and the amplitude by 0.01 %, identically on both channels, so there is
nothing to see. Populate 100 Ω only if a future cable turns out to need the isolation.

GPIO8 and GPIO9 socket positions are **not connected** on the carrier (onboard LED / BOOT
button, both strapping pins — DESIGN §4).

Testpoints (plated pads, 1.5 mm): TP1 = VBUS_CHG, TP2 = VSW, TP3 = GATE, TP4 = 3V3,
TP5 = VBAT_SENSE.

---

## 4. Load-sharing power path

**Purpose (DESIGN §9):** kill the bring-up rule "never have the switch ON while any USB is
plugged". Requirements: (a) battery → load with near-zero drop; (b) when *either* USB is
present, the battery is disconnected from the load so TP4056 charges it cleanly (proper
CC/CV + termination) and USB can never back-feed the cell; (c) load runs from USB meanwhile.

### 4.1 The one hard constraint discovered during design

DESIGN §9 originally sketched "P-FET + Schottky, both VBUS sources diode-ORed into the gate"
(that file now carries the corrected description). The
TP4056's VBUS is directly accessible (`IN+` pad). **The SuperMini's VBUS is not**: on these
clone modules USB VBUS is tied straight to the `5V` pin (**meter-confirmed 2026-07-18**,
measurements.md — VBUS↔5V continuous) — i.e.
the SuperMini's "VBUS" *is the load node*. A plain OR-diode from the load node to the gate
self-biases the gate to (VLOAD − Vf) in battery mode, holding Vgs at ≈ −0.2…−0.5 V forever,
so the FET never fully turns on and you eat the body-diode drop (~0.7 V) — which browns out
the SuperMini below ~3.6 V battery. **So the "SuperMini VBUS" OR term must be implemented as
a voltage threshold on the load node instead of a diode.** Logic: `VLOAD > 4.5 V` can only
mean a USB source is present (battery tops out at 4.2 V; USB min is 4.75 V). The circuit
below keeps the DESIGN §9 architecture (both VBUS sources drive the gate) with the second
term implemented as a TL431 comparator. A DNP diode footprint (D3) is kept for breadboard
experiments.

> **⚠️ That logic does not survive review, and §4.3 state 4 is where it fails.** Two things
> are wrong with "`VLOAD > 4.5 V` can only mean USB":
>
> 1. **The band is far narrower than it looks.** USB 2.0 permits **4.40 V at the device end**,
>    and a laptop port through a thin cable routinely reads 4.4–4.6 V at the SuperMini
>    connector. The usable window is therefore roughly 4.24 V (a full cell at the charger's
>    CV max) to ~4.5 V — about **200–300 mV**, most of which is eaten by the reference
>    tolerance, the divider tolerance and the REF bias current (§4.2).
> 2. **Worse, the threshold is often unreachable.** You enter state 4 *from battery mode*,
>    where Q1 is already on. The cell then clamps VSW to roughly `V_OCV + I·R_path` with
>    `R_path ≈ 0.2–0.3 Ω` (Q1 + FS8205 + JST/leads + cell IR). Lifting that node to the
>    4.46 V low corner from a 3.7–4.0 V cell needs **≈1.5–3.8 A** through the cell branch —
>    beyond a 0.5 A legacy port at any state of charge, and beyond a 1.5 A BC.1.2
>    port below roughly a 4.0 V cell. (A 3 A Type-C port on a nearly-full cell *can* reach
>    it — the failure is worst exactly when the cell is emptiest.) So the "Q1 on" state is
>    largely self-reinforcing: closer to a latch than a trip point, and a stiff 5 V bench
>    supply does not reveal it.
>
> The consequence is that **no choice of divider values fixes state 4** — the defect is in
> sensing an absolute threshold on the load node, not in the numbers. Sensing the *drop
> across Q1* instead (an ideal-diode controller such as the LTC4412) has no threshold to
> mis-set and cannot fail this way; that is the v1.2 answer. See §4.5 for what to actually
> do for the show.

### 4.2 Circuit

The block is drawn, wire by wire, in the generated schematic — see the **"LOAD-SHARING
POWER PATH"** block of
[`hw/carrier/carrier-schematic.svg`](../../hw/carrier/carrier-schematic.svg)
(printable A3: [`carrier-schematic.pdf`](../../hw/carrier/carrier-schematic.pdf)). It
replaced the ASCII art that used to live here after two successive revisions of that art
drew the Q2 corner wrong; the schematic is generated from `design.py` and netlist-gated,
so it cannot make that class of error. The Q2 corner in words, matching `design.py`
exactly:

| Node | Connects to |
|---|---|
| Q2 emitter | VSW |
| Q2 base | R9 (to the TL431 cathode) **and** R12 |
| Q2 collector | GATE |
| R12 | base ↔ emitter, i.e. **across** the base–emitter junction, holding Q2 off until the TL431 sinks current through R9 |

So R12 is a base–emitter hold-off resistor in *parallel* with the junction, **not** in series
with anything. An earlier ASCII revision of this section drew it in series between the R9
node and the emitter, which is a different and nonsensical circuit; a later attempt to fix
it in ASCII instead implied base, emitter and collector were shorted. The table above,
`design.py` and the generated schematic agree; the ASCII attempts are gone.

Netlist form:

| Part | Value | Connections |
|---|---|---|
| Q1 | DMG3415U (P-ch, −20 V, −4 A, Vgs(th) −0.3…−1.0 V, Rds(on) 42 mΩ @ −4.5 V) | D = VBAT_OUT, S = VSW, G = GATE |
| R6 | 100 k | GATE → GND (default: FET on) |
| D1 | **BAT85** Schottky (DO-35) | VBUS_CHG → GATE (charger-USB OR term). D1 carries only R6's ~45 µA, so its Vf (≤ 0.24 V) sits *below* D2's — see §4.3 state 2/3 for why that matters |
| D2 | SS34 (or THT 1N5817/1N5822, dual footprint) | VBUS_CHG → VSW (feeds load while charging). **Not fitted when JP1 is bridged** (§4.4) |
| R7/R8 | **8.2 k / 10 k, 1 %** | VSW divider → U1 REF. Trip = `Vref·(1+R7/R8) + I_ref·R7` = 2.495 × 1.82 + 2 µA × 8.2 k ≈ **4.56 V typ**, window **4.46–4.66 V** at 25 °C with a TL431A. See the divider table below |
| U1 | **TL431A** (±1 %, TO-92) | REF = divider, A = GND, K = via R9 to Q2 base. ⚠️ **check the TO-92 lead order of the part you buy** — see §5 |
| R9 | **1 k** | Q2 base → U1 cathode (~2 mA cathode current when tripped, clear of onsemi's 1 mA `I_KA(min)` maximum — TI specs its A grade at 0.6–0.7 mA, so the old 2.2 k's ~0.93 mA was marginal rather than universally out of spec. The consequence of going under is a soft, ill-defined trip, not a dead circuit.) |
| R12 | 100 k | Q2 base–emitter (holds Q2 off) |
| Q2 | BC557 (PNP, TO-92) | E = VSW, C = GATE. When on, pulls GATE to VSW ⇒ Vgs ≈ 0 ⇒ Q1 off |
| D3 | BAT85 — **DNP** | VLOAD → GATE. Experimental "naive OR" term; see §4.1 why it's not fitted |
| SW1 | slide switch | VSW → VLOAD. Downstream of FET/Schottky on purpose (see states) |
| C1/C2 | 220 µF electro + 100 nF | on VLOAD (bulk for WiFi TX bursts, per DESIGN §3) |

**The divider is a populate-time choice, not a fab-time one.** R7/R8/U1 are through-hole
parts fitted at assembly, so the board does not commit you to any row of this table. What the
original 82 k/100 k gets wrong is that `V_trip = Vref·(1+R7/R8)` **omits the TL431's REF input
bias current** flowing in R7, which at 82 k adds 164 mV typ / 328 mV max. Every trip number
published before 2026-07-26 was low by that much.

| Option | U1 | R7 / R8 | Trip typ | 25 °C window | Standby (VSW divider) | Shelf life¹ |
|---|---|---|---|---|---|---|
| as built v1.0 | TL431 (±2.2 %) | 82 k / 100 k | 4.70 V | 4.40–5.01 V | 23 µA | ~4.2 yr |
| **fitted default** | **TL431A (±1 %)** | **8.2 k / 10 k** | **4.56 V** | **4.46–4.66 V** | **231 µA** | **~10 months** |
| middle | TL431A | 18 k / 22 k | 4.57 V | 4.45–4.70 V | 105 µA | ~1.7 yr |
| best electrically | TLV431A (Vref 1.24 V) | 100 k / 38.3 k | 4.49 V | 4.37–4.64 V | 30 µA | ~3.7 yr |

¹ Drain-only, 2000 mAh, everything else in §4.3 state 6 included. The cell's own
self-discharge (order 1.5–3 %/month, ≈40–80 µA equivalent) is comparable to or larger than the
board in every row except the 8.2 k/10 k one, so treat these as "the board stops being the
limiting factor below ~50 µA", not as literal shelf life.

The **TLV431A** row is the electrically correct answer — its REF bias current is ≤0.5 µA
instead of 4 µA, so it buys the accuracy at *no* standby cost, and its TO-92 (LP) pinout is
pin-for-pin identical to the TL431's. It is not the default only because TI's A-grade TO-92
parts are NRND/obsolete (onsemi `TLV431ALPRAG` is the live source) and this is a 6-day order
window. If you can source it, fit it — R7/R8 change with it, and note Vref is 1.24 V, so the
divider ratio is completely different.

None of these fix §4.3 state 4 (see the box in §4.1). They fix the *accuracy* of a threshold
that is looking at the wrong node.

### 4.3 Operation state-by-state

| # | State | What happens |
|---|---|---|
| 1 | **Battery only, SW1 ON** | VBUS_CHG = 0 ⇒ D1 inert. VSW bootstraps through Q1's body diode, TL431 sees < 4.2 V ⇒ off ⇒ GATE = 0 V via R6 ⇒ Vgs = −VBAT (−3.0…−4.2 V) ⇒ Q1 fully on. Drop at 125 mA ≈ **5 mV**. Load runs from battery. |
| 2 | **Charging (TP4056 USB in), SW1 ON** | **D1 is what holds Q1 off here** — VSW ≈ 4.68 V (5.0 − D2's drop at ~150 mA) sits right on the trip, so the TL431 may or may not fire; do not rely on it. With D1 a **Schottky**, Vgs = Vf(D2) − Vf(D1) ≈ 0.32 − 0.24 = **+0.08 V** ⇒ hard off. Battery then sees only the charger: clean CC/CV, correct termination. Load runs from VBUS_CHG through D2 → SuperMini LDO; unit fully operational while charging. Use a **5 V / 2 A** wall charger (mandatory): charge current (1 A default Rprog) + load (~150 mA) share one port; 2000 mAh at 1 A ≈ 0.5C ⇒ full charge ~2.5–3 h. Reprogramming Rprog to 2.4 k (500 mA) is **optional** (fiddly 0402/0603 rework) — the 2 A charger makes the 1 A default safe. |
| 3 | **Charging, SW1 OFF** | Same as #2 but load disconnected — unit off, battery charges clean. This is the **worst case for the D1 term**: with the load gone, D2 carries only the sense divider (~230 µA at 8.2 k/10 k, plus ~2 mA once the TL431 fires), so its Vf falls to ~0.15–0.25 V. With a silicon D1 that leaves Vgs ≈ −0.15…−0.25 V — *below* the DMG3415U's −0.3 V minimum threshold, so Q1 is nominally off, but only by 50–150 mV of a parameter that drifts ~2 mV/°C in the TP4056's 1–1.4 W heat plume. That is sub-threshold, not hard-off, and it leaks. The Schottky D1 is what turns this into ≈ 0 V with margin. TL431 burns ~2 mA from the charger (irrelevant). |
| 4 | **Flashing (SuperMini USB in), SW1 ON, battery in** | ⚠️ **This state does not work as designed — see the box in §4.1.** The intent was: VBUS drives VLOAD → SW1 → VSW ⇒ TL431 trips ⇒ Q1 off. In reality you arrive here from battery mode with Q1 *already on*, and the cell clamps VSW; reaching the trip would need 2–4 A. A laptop port loaded to 4.4–4.6 V leaves **Vgs ≈ −4.5 V, Q1 fully enhanced at 42 mΩ**, and the port back-feeds the cell at whatever its limiter allows (0.5 A legacy / 1.5 A BC1.2 / up to 3 A Type-C) with **no CC/CV and no termination**. D1 cannot help — its anode (VBUS_CHG) is at 0 V with the charger unplugged. The only backstop is the DW01's ~4.3 V overcharge cut, tens of minutes away at mid SoC. There is no visible symptom. **Until §4.4 says otherwise, keep the manual rule: switch OFF before plugging the SuperMini's USB.** |
| 5 | **Flashing, SW1 OFF** | SuperMini + 3V3 loads (DAC, mic) powered from its USB; VLOAD isolated from VSW by the open switch; battery idles behind Q1. **This one genuinely is safe** — it is the state the manual rule tells you to flash in. |
| 6 | **SW1 OFF, no USB (storage)** | **SW1 does not disconnect the sense divider.** SW1 cuts VLOAD only; R7/R8 hang on VSW, which the cell reaches through Q1 (on, and through its body diode even if it were off). What the switch *does* remove is C1/C2 and the whole 3V3 rail including RV1's 330 µA — which is why it is worth having. Standby at 4.2 V = R7+R8 (**231 µA** at 8.2 k/10 k, was 23 µA) + R1/R2 divider (21 µA) + DW01 (~3 µA) + TP4056 standby (~2.5 µA) + D2 reverse leakage (a *typical*, not a datasheet limit: ~5 µA at 25 °C, climbing steeply to tens of µA by 50–60 °C; the SS34's specified maximum is far higher) ≈ **262 µA at 4.2 V ⇒ roughly 10.5 months** on the 2000 mAh cell (the §4.2 table's standby column uses the same 4.2 V basis throughout). **Note this is the standby of a fully-populated block** — on the §4.5 plan-A board, with R7/R8 omitted, it drops back to ~30 µA. The old "~5 years" figure was wrong twice over: it omitted the REF bias current *and* it is unreachable at **any** divider value, because the cell's own self-discharge alone is 40–80 µA equivalent. Recharge on a calendar; do not rely on the DW01 cutoff, which is a fire fuse, not a storage strategy. |
| 7 | **Race: plugging charger while SW1 OFF** | For the µs–ms before TL431 trips, Q1 is still on and D2 could push a current blip into the cell. D1 yanks the gate high in nanoseconds, closing the race. This is why D1 stays fitted — and per states 2/3 it is the *primary* isolation while charging, not merely a race-closer. |

Margins (with DMG3415U Vgs(th) min −0.3 V per [Diodes datasheet](https://www.diodes.com/datasheet/download/DMG3415U.pdf)).
**Both margin claims printed here before 2026-07-26 were void** — they used the
bias-free trip of 4.54 V. Corrected, for the fitted 8.2 k/10 k + TL431A:

- **Battery side (the one that must hold):** trip window low corner **4.46 V at 25 °C** vs a
  full cell at the charger's CV max **4.242 V** ⇒ **≈0.22 V**. Over temperature the low
  corner falls to ~4.41 V ⇒ ~0.17 V — still safe, but note that means the fitted values only
  satisfy the "never below ~4.45 V" rule in §4.4 item 5 *at 25 °C*. Safe: no false trip in battery mode, and
  WiFi-burst ripple on VSW is downward, i.e. away from the threshold. On the old 82 k/100 k
  the low corner was 4.40 V — still safe, so this was never the failing end.
- **Load side (the one that does not hold):** the window's high corner is 4.66 V at 25 °C
  (4.73 V over temperature) against a *connector* voltage that is legally as low as 4.40 V.
  There is **no guaranteed margin**, and per §4.1 the threshold is usually unreachable in
  state 4 anyway. Do not quote a load-side margin figure.
- **States 2/3 rest on D1, not on the comparator** — VSW sits within tens of mV of the trip
  while charging. That is why D1 is a Schottky: it makes Vgs ≈ 0…+0.1 V in both charging
  states instead of −0.10…−0.22 V against a −0.3 V minimum threshold. The two mechanisms were
  described as "deliberately overlapping"; in practice, in states 2 and 3, there is only one.

**Hot-enclosure note (found 2026-07-26, not yet bench-checked).** D2's reverse leakage in
storage returns through `D2 → VBUS_CHG → D1 → GATE → R6`, so it develops `I·R6` on the gate:
harmless 0.5 V at 5 µA, but at the tens of µA a 50–60 °C enclosure produces, GATE climbs
toward 2–3 V and Vgs enters the −0.3…−1.0 V threshold band. Q1 then un-enhances and the load
falls onto its body diode (~0.4–0.6 V at 125 mA), eating LDO headroom. It is self-limiting
and recovers when it cools, but it means the gate node's leakage headroom is only ~30 µA
(30 µA × R6 = 3 V on GATE ⇒ Vgs ≈ −1 V, the worst-case threshold limit). If
this shows up on the bench, the fixes are a lower-leakage D2 (SS14 in the same SMA footprint)
and/or a smaller R6 — both value-only.

### 4.4 ⚠️ VERIFY — bench-test the block on the assembled carrier

**This moved.** It was written as a breadboard evening gating the PCB order. It cannot run
there: the current bench rig has no battery and no TP4056 in circuit, and Q1 is SOT-23 —
unbreadboardable without an adapter. It is also not an order gate, because the board carries
both topologies (JP1). So: **order on schedule, run this on an assembled carrier** in the
Aug 11–16 window, where TP1 (VBUS_CHG) / TP2 (VSW) / TP3 (GATE) exist for exactly this and
every module lifts out of its socket. Until it passes unambiguously, ship on §4.5's plan A.

1. State 1: measure the drop **across Q1 alone** (TP2 vs the cell +) at 150 mA — expect
   < 20 mV. Do *not* measure cell→5V-pin and apply that number: the copper from Q1's source to
   the 5V pin adds a measured **≈50 mΩ** (30.7 mΩ VSW + 19.5 mΩ VLOAD, §6.4), i.e. **~7.5 mV
   at 150 mA** on top. If you see ~0.7 V across Q1, the FET is not turning on — check the gate.
   *(This adder read "152 mΩ / ~23 mV" until 2026-07-28. That figure was two re-routes stale
   and would have had you hunting a 23 mV drop that is not there — which, on a 20 mV
   acceptance threshold, is larger than the thing being measured. Re-run
   `tools/measure_copper.py` and update this number after **every** re-route.)*
2. State 2: scope the cell current while charging with load running — must show clean CC/CV,
   LED on TP4056 must reach "charged" (termination works only if load is truly disconnected).
3. **State 4 — the one that matters, and the one the old procedure would have passed.**
   Cell current with the SuperMini USB plugged and SW1 ON. Three amendments, all load-bearing:
   - **Source:** not a stiff 5.0 V bench supply. Feed it from a supply deliberately sagged to
     **4.4 / 4.5 / 4.6 V** (bench supply + ~0.5 Ω series), *and* at 4.75 and 5.0 V. The
     failure is specific to a real laptop port through a thin cable.
   - **Instrument:** a **milliohm-class shunt**, not a DMM in current mode. A DMM's 0.1–0.25 Ω
     burden roughly doubles the path resistance, which lets the circuit trip and read µA —
     the meter makes it pass. Remove the meter and the fault returns.
   - **Criterion:** **≤ 1 mA**, not "≤ µA" — a sub-threshold trickle through Q1 is normal and
     the old criterion would send you chasing a phantom. Repeat across cell SoC 3.6–4.0 V.
4. State 7: plug/unplug charger 20× with SW1 in both positions; watch cell current for blips.
5. Sweep a bench supply 4.2→5.0 V on VLOAD and log the trip point. **Expect ≈4.56 V** with the
   fitted 8.2 k/10 k + TL431A (≈4.7 V if you built the old 82 k/100 k) — a correct measurement
   at 4.7 V is not a build error. *Do not retune R7 downward:* the old advice here (75 k →
   "4.37 V", 77 k → "4.42 V") was doubly wrong — those values actually trip at ~4.52 / 4.57 V,
   and a low corner near 4.24 V collides with a full cell, giving battery-mode relaxation
   oscillation. Never set the low corner below ~4.45 V.
6. Optionally fit D3 (BAT85) and confirm the §4.1 failure yourself (battery-mode drop jumps
   to ~0.7 V) — then remove it.
7. Record the trip point and the state-4 current in `measurements.md`. There is currently no
   power-path row in that file at all, which is why every number in §4.3 is paper.

**Escape hatch (already on the board):** bridge the 2.54 mm solder-jumper **JP1
(VBAT_OUT → VSW)** and leave **Q1, U1, Q2, D1 and D2** unfitted — *and take R7/R8 off too*.
Those five are the safety-critical ones (they carry current or can inject into the cell), but
R7/R8 sit across VSW, which JP1 ties straight to the battery: leaving them fitted burns
**231 µA** off the cell for a divider whose comparator is not even populated, which is the
entire shelf-life budget §4.2 argues about. R6/R9/R12 do nothing with Q1/Q2/U1 absent — fit
them or not. You are then back to the
proven plain-switch topology plus the old "switch OFF while USB plugged" rule.

> **⚠️ D2 must come off — this is not optional hygiene.** With JP1 bridged and D2 still
> fitted, plugging the charger gives `VBUS_CHG → D2 → VSW → JP1 → VBAT_OUT = OUT+ = B+`, i.e.
> the raw USB input lands on the cell at ~4.6 V with the TP4056's CC/CV loop bypassed
> entirely — an initial demand of several amps, limited only by the adapter. The TP4056 sees
> an IR-inflated B+ and lights "charged" while D2 keeps feeding. The only stop is the DW01
> hiccupping between 4.10 and 4.30 V for as long as the charger is plugged in. And because
> the injection point is **upstream of SW1**, switching the unit off does not protect it. D2
> is a default-fit BOM part, so this is the state you get unless you act.

**Access cost, so it is not a surprise:** JP1 and Q1 sit under the socketed TP4056, while
**D1 and D2 sit under the PCM5102A**. Executing the fallback means lifting *both* modules.
Decide fit-or-DNP for the power path **before** the TP4056 is wired down — if J5/J6 end up
soldered rather than socketed, JP1 becomes a desoldering job.

### 4.5 What to actually build for the show

The review's conclusion, and the default this document now takes:

- **Plan A — JP1 + the manual rule.** Bridge JP1 and omit the power-path parts. **The
  load-bearing five are Q1, Q2, U1, D1 and D2** — those carry current or can inject into the
  cell. **R7/R8 must come off too**, not for safety but because JP1 ties them across the
  battery: 231 µA for a divider with no comparator behind it, which is the whole shelf-life
  budget §4.2 argues about. R6, R9 and R12 do nothing with Q1/Q2/U1 absent — fit them or
  don't. Just never leave **D2** in. Keep "switch OFF before plugging any USB" as permanent
  operating procedure. This is the topology that has been running on the breadboard for
  weeks. The hazard needs a human to plug USB with the switch ON; charging is a
  between-shows activity and flashing is bench-only, so show-time exposure is near zero.
  Add a silk-legend habit: *switch off, then plug in.*
- **Plan B — populate the block** only if the amended §4.4 passes unambiguously on an
  assembled carrier. Its headline benefit (run while charging) is not show-critical:
  ~13 h of NETWORK runtime against a ≤5 h show means mid-show charging never happens.
- **v1.2 — do it properly.** Replace the whole block with an ideal-diode controller that
  senses the drop across Q1 rather than an absolute voltage on the load node
  (**LTC4412**, SOT-23-6, hand-solderable), or move to a charger with a real power path
  (BQ24074 / MCP73871 breakout). Either deletes §4.1's hard constraint instead of working
  around it.

The PCB is identical under all three — that is what JP1 buys.

---

## 5. On-board BOM (per board, modules excluded)

Quantities are per board; build 4 (a 5th possible later), order parts for ~6 (spares). Example parts are LCSC-searchable MPNs;
⚠️ VERIFY exact LCSC stock codes at order time (codes drift) — search by the MPN given.

> **📦 ORDERED 2026-07-27 — this BOM is closed for a §4.5 plan-A build.**
>
> **Bought:** SW1 slide switches (**SS12D00, 6 mm handle** — see the SW1 row for the accepted
> 0.3 A deviation) · 2.54 mm female header assortment · **DMG3415U-7 ×10** (Q1) · TO-92
> PNP/NPN assortment (Q2 substitutes) · **JST-PH 2.00 mm** connector kit · **RV097NS-B10K ×5**
> (5-pin mono *with switch*, right-angle) · M3 heat-set inserts + screws.
>
> **From stock (drawer-checked 2026-07-27):** machined round-pin sockets (J5/J6/J9/J10 — **6**
> per board since 2026-07-28, not 4) · 6×6 mm
> tactile (SW2) · 3 mm LEDs (D4/D5) · 220 µF 10 V ×10 (C1) · 100 nF "104" (C2–C5) · all
> resistor values · male header strip + pigtail wire (J7, mic pigtails). 5 V/2 A wall charger
> confirmed on hand (mandatory per §4.3 state 2).
>
> **Deliberately not bought — the plan-B power-path parts: U1, D1, D2, D3, R7, R8, R9.**
> Per §4.5 the board ships with JP1 bridged, so these have no function; **D2 in particular must
> never be fitted on a JP1 board** (§4.4). Q1 and a PNP for Q2 *were* bought anyway, as cheap
> insurance if §4.4 later passes on an assembled carrier — populating the block then needs
> only the TL431A, two BAT85, an SS34 and three resistors.
>
> **Two near-misses worth remembering.** A **JST XH-2.54** kit was ordered first and swapped:
> XH is 2.5 mm and mates neither J8 (`S2B-PH-K`, 2.00 mm) nor the cell's pre-fitted lead. And
> the header assortment carries no 1×9 or 1×3 — **cut J3 from a 10-pin and J4 from a 4-pin**,
> accepting the destroyed position.

| Ref | Qty | Part | Example MPN / LCSC search | Package | Notes |
|---|---|---|---|---|---|
| Q1 | 1 | P-FET −20 V | **DMG3415U-7** (Diodes) | SOT-23 (SMD) | hand-solder pads; THT fallback: none good at low Vgs — keep SOT-23 |
| Q2 | 1 | PNP BJT | **BC557B** | TO-92 | any small PNP works |
| U1 | 1 | Shunt ref | **TL431A** (±1 %, TO-92) — the **grade** sets the trip window, the **brand** sets the TO-92 lead order (see the boxed note below; check both). Better if you can source it: **TLV431A** (onsemi `TLV431ALPRAG`) with R7/R8 → 100 k/38.3 k | TO-92 | comparator duty only |
| D1 | 1 | **Schottky** | **BAT85S** (DO-35) — or BAT54 if you prefer SOT-23 | DO-35 | gate OR. **Not** a 1N4148: the silicon drop is on the wrong side of D2's and leaves Q1 only partly off while charging (§4.3 states 2/3) |
| D2 | 1 | Schottky 3 A | **SS34** (LCSC C8678 ⚠️ VERIFY) or THT **1N5817**/**1N5822** | SMA / DO-41 | dual THT+SMA footprint. ⚠️ **Omit entirely if JP1 is bridged** (§4.4) |
| D3 | (1) | Schottky small | **BAT85** | DO-35 | **DNP** — experiment only (§4.1) |
| D4 | 1 | LED 3 mm green | e.g. Hongli 3 mm green | THT | NET status |
| D5 | 1 | LED 3 mm amber/yellow | e.g. Hongli 3 mm yellow | THT | MODE status |
| R1, R2 | 2 | 100 k 1 % | generic 1/4 W metal film | axial | battery divider (`VBAT_DIVIDER 2.0`) |
| R3 | 1 | 10 k | generic 1/4 W | axial | GPIO2 strap pull-up |
| R4, R5 | 2 | 2.2 k | generic 1/4 W | axial | LED series (per config.h "~2.2k") |
| R6, R12 | 2 | 100 k | generic 1/4 W | axial | gate pull-down / PNP B-E |
| R7 | 1 | **8.2 k 1 %** | metal film | axial | sense divider top — see the §4.2 divider table before committing |
| R8 | 1 | **10 k 1 %** | metal film | axial | sense divider bottom |
| R9 | 1 | **1 k** | generic | axial | TL431 cathode drive (2.2 k left the part below its 1 mA `I_KA(min)`) |
| R10, R11 | 2 | **0 Ω wire link** (100 Ω optional) | offcut of component lead, or a 0 Ω axial | axial | DAC → RCA-pad series. §10 Q2, closed 2026-07-28: the module already has ~470 Ω of its own, so 100 Ω more is 0.01 % of amplitude and ≲0.07° of phase — *equally on both channels*. Fit the link: it lies flat, and R10 stands at the DAC's corner where the standing-axial height is itself unverified |
| C1 | 1 | 220 µF ≥ 10 V electrolytic, **low ESR (≤ 0.5 Ω @ 100 kHz)** | e.g. Rubycon ZLH / Panasonic FR 220 µF 16 V — a 105 °C low-impedance part, not a general-purpose can | radial 6.3 mm | VLOAD bulk. **ESR is the spec that matters here, not the value** — at a 50 µs burst it sets the dip almost entirely, and 1000 µF of high-ESR is worse than 220 µF of low. See power-budget §4.3. |
| C2, C4, C5 | 3 | 100 nF X7R | generic | 2.54 mm radial | decoupling |
| C3 | 1 | 100 nF X7R | generic | 2.54 mm radial | across R2 (ADC filter) |
| SW1 | 1 | SPDT slide | **SS12D00, 6 mm handle — ✅ ordered 2026-07-27** (alternative: SK12D07-class, 1 A) | THT | power. ⚠️ **Accepted deviation** — this doc previously said the 1 A part was *required* because the SS12D00's 0.3 A is below the ≈0.35 A WiFi TX peak. That compares a peak current against a **make/break rating specified at 50 VDC**, and arc energy scales with the voltage being interrupted: at the ~4 V this switch actually breaks there is no arc to speak of, and 0.35 A through the contacts is a thermal non-event. Revisit only if contact resistance climbs in service. **6 mm handle** chosen so the actuator clears the enclosure wall — no length had ever been specified |
| SW2 | 1 | Tactile 6×6 mm | **TS-1102** / Omron B3F-1000 style | THT 6×6, 4-pin | MODE |
| RV1 | 1 | 10 k linear pot, 9 mm (B10K) | **RV097NS-B10K, "5-pin mono *with switch*", right-angle** (metal shaft, body 27.3 × 9.5 × 11.3 mm) | RV097NS THT (5-pin, ⌀1.0 holes) | shaft turned directly — **no knob** (enclosure needs only a shaft hole). ⚠️ **Do not substitute the 3-pin or the vertical variant** — see the box below |
| X, Y | — | RCA output landings (signal + AGND solder pads) | no connector part — reuse the ~50 cm RCA cables from the old sigma-delta units | 2× pad-pair | Finn solders the RCA cable leads directly; scope side uses a BNC→RCA adapter |
| J8 | 1 | JST-PH 2-pin side entry | **S2B-PH-K-S** (JST) | THT | battery socket; cell = EEMB LP103454 2000 mAh (off-board, pre-fitted JST-PH lead) |
| J1A/J1B | 2 | Female header 1×8 | generic 2.54 female | THT | SuperMini |
| J2 | 1 | Female header 1×6 | generic | THT | DAC I2S |
| J3 | 1 | Female header 1×9 | generic | THT | DAC analog/config end (only LROUT/ROUT/AGND netted; rest mechanical/NC) |
| J4 | 1 | Female header 1×3 | generic | THT | mic pigtail (`VCC GND OUT`) |
| J5, J6, **J9, J10** | **6** | **Machined single-pin socket** (turned-pin, e.g. a Mill-Max 0305 series single, or singles broken off a machined DIP socket) | — | THT | TP4056, **and it is six now, not four**. **A stock 1×2 female header cannot mate** — the module's pads are at 3.526 / 3.106 mm, not 2.54 (§2). J9/J10 are the USB-C-end mount row 21.65 mm away — see the assembly box below for why four in one line is not enough. Alternative: solder wires and give up removability |
| J7 | 1 | Male header 1×2 | generic | THT | debug TX |
| JP1 | 1 | 0 Ω / solder jumper | — | 2.54 mm | power-path escape hatch (open by default). ⚠️ **If bridged, D2 must be omitted** — and Q1/U1/Q2/D1 with it (§4.4) |
| TP1–TP5 | 5 | testpoint pad | — | 1.5 mm pad | no part |
| — | 4 | M3 screw + standoff | — | — | mounting (§6) |

Order **fixed-size** female headers (1×8, 1×6, 1×9, 1×3) — female strips do not snap cleanly;
each cut destroys one position. **The TP4056 is the exception:** its four pads are at
0 / 3.526 / 10.960 / 14.066 mm (photogrammetry 2026-07-26 — the old "close to 2.54 grid"
guess is answered, and the answer is no), so fit **individual machined pins**, not a
2.54 mm strip, which cannot span them.

> **⚠️ TO-92 orientation — check before you solder U1 and Q2.** `design.py` assigns
> **pad 1 = REF, pad 2 = ANODE, pad 3 = CATHODE** for U1, which is the *onsemi* numbering.
> **TI numbers the same TO-92 package the other way round** (pin 1 = K, pin 2 = A,
> pin 3 = REF), and since the **anode is the centre lead in both**, a TI-branded TL431 drops
> into the footprint perfectly while sitting backwards. The footprint is an inline 3-pad
> strip, so the silk outline will not save you. Q2 (BC557) carries the same class of trap —
> C-B-E and E-B-C both exist in TO-92 across vendors. Read the lead order off the datasheet
> of the part you actually bought and confirm **REF lands on the pad wired to VSW_SENSE**
> (ohm it out against R7/R8 before powering up). This applies equally to the TLV431A option.

> **⚠️ RV1 — buy the right one of the three RV097NS variants.** Sellers list "RV097NS"
> against at least three different parts, and only one fits this board:
>
> | variant | pins | row 2 | fits? |
> |---|---|---|---|
> | **5-pin mono *with switch*, right-angle** | 5 | SPST, ⌀1.0 holes **5.0 mm apart, 6.25 mm behind** the pot row | ✅ **this one** |
> | 5-pin vertical, with bracket lugs | 3 + 2 lugs | oval lug slots ~9.5 mm apart, ~7 mm behind | ❌ shaft points up; lug slots miss |
> | 3-pin (no switch, no lugs) | 3 | — | ⚠️ pot works, two holes stay empty, and the part loses its only rear anchoring |
>
> The board expects the first. Its **mounting surface is 5.0 mm in front of the pot row and
> lands exactly on the board's south edge**, so the M7×0.75 bushing and the 15 mm ⌀6 shaft
> hang off the edge and through the enclosure wall. Identify it by the cross-section:
> **9.5 mm wide × ~11.35 mm tall** (calipers 2026-07-18 read 9.5 × 11.3).
>
> **Pads 4/5 are the switch and both go to GND.** That is deliberate, not a leftover: there
> is no safe spare GPIO to give it (GPIO0 is `MIC_OUT`; GPIO8/9 are strapping pins that must
> be high at reset, and a switch that makes at full-CCW would hold one low and block boot;
> GPIO21 is the console UART TX, an output). Parking both ends in the GND pour also gives
> the part its only rear mechanical anchoring — these two 0.8 mm pins plus the panel nut are
> all that resist the torque of a bare shaft turned by hand, because **this variant has no
> bracket lugs**. Closing the switch shorts GND to GND and nothing happens.
>
> *Corrected 2026-07-27. Before that the footprint drew row 2 as two oval bracket-lug slots
> 9.5 mm apart and 7.0 mm back — every row-2 pad 2.25 mm out in X and 0.75 mm in Y, and the
> mounting surface 3.8 mm out. The part would not have gone into the board.*

`IN+` no longer connects via a wire — **J6b (`J9`) is a socketed pin** through the module's
`+` corner pad (2026-07-28; it had to move 0.65 mm west to land on it, see below). That
matters for current, not tidiness: in §4.3 state 2 the **entire board load** runs
charger → D2 → VSW through this connection — ~150 mA average, ~350 mA on WiFi TX bursts, plus
a ~4 A/<100 µs inrush blip into C1 at plug-in. A 0.64 mm pin in a turned-pin socket carries
that with room to spare, and it removes the one hand-soldered wire from the power path. (On a
**§4.5 plan-A board** the net is idle anyway: with D1/D2 omitted, `VBUS_CHG` reaches only TP1.
Note that TP1 is then **live at 5 V whenever the charger is plugged in** — it always was going
to be, via the wire; it is just no longer optional.)

> **📐 TP4056 assembly — two steps that are not obvious, both out of the caliper sessions
> ([measurements.md §Nubs](measurements.md) 2026-07-27, §The second mount row 2026-07-28).**
>
> **1. File the two nubs off first.** The module's west edge is not straight: two
> depanelization tabs protrude ~1.6 mm at the OUT− and OUT+ corners. They are bare
> soldermask — no copper, no silk, no trace — so filing them flush to the main west edge is
> safe, and it is what makes the body match the modelled outline (west edge x ≈ 43.9, within
> 0.15 mm). Left on, the SW nub reaches x ≈ 42.2 and the gap to **RV1's body**
> (`x 32.05…41.55, y 37.0…50.0`) drops from 2.4 mm to ~0.7 mm — between a PCB corner ~4 mm up
> and an 11.3 mm-tall pot. Not a collision; just not worth living with for 30 s of work.
>
> **2. Pins into the module first, then onto the carrier.** Push the machined pins into the
> TP4056's holes, *then* lower that assembly into J5/J6/J9/J10 and solder the carrier side.
> The module's holes are **~1.5 mm, not the 2.0 mm** assumed (calipers 1.43; the
> photogrammetry's "2.0 nominal" was pulled up by one bad pick), and calipers read the pad
> span ~0.25 mm shorter than the 14.066 mm the copper is built to — well inside the ~0.45 mm
> radial slack per hole, but only if the *module* sets the pin positions. Solder the pins to
> the board first and you are fighting rigid pins into holes at a span you did not choose.
>
> Step 1 changes nothing on the board. Step 2 covers **six** pins, not four — see the next
> box for where the other two came from.

> **🔩 SIX pins, not four — the USB-C end is mounted too (2026-07-28, and this one moved
> copper).** J5/J6 are all in **one column at one end** of the module, and the USB-C jack is
> **21.65 mm** away at the other. A row of pins resists rotation about its own axis only by
> bending — order of magnitude, a **5 N off-axis nudge on the plug tilts the module ~25°** —
> and that is the one connector on this build that gets handled every charge cycle, at 10–20 N
> of insertion force each time.
>
> The module already had the fix on it: **two ~2.8 mm bare-copper pads on 1.68 mm plated
> holes** at the USB-C-end corners, in line with OUT+ (`+` silk, next to `R8`) and OUT−. The
> carrier already had a pad on one of them — J6b/`J9`, drawn as a wire pad for the IN+ sense
> tap. So: **J9 became a socket and moved 0.65 mm west** onto the real hole position, and
> **`J10` was added** on the other corner. Four-in-a-line becomes a four-corner mount.
>
> - **`J10` is on no net.** Its job is mechanical. §2 asserts `IN− ≡ OUT−` and that is the
>   usual protected-TP4056 topology, but it has **never been ohmed on this module** — and if
>   it is wrong, bonding it to carrier GND shorts across the DW01/FS8205 and the cell loses
>   its protection. A floating pin anchors just as well. Ohm it later if you want it bonded.
> - **If a mount pin will not enter,** drill those two module holes to **2.0 mm** — the pads
>   are 2.8 mm of copper carrying no current here, and it takes the slack from 0.39 to
>   0.55 mm. Three independent routes put the 21.65 mm figure inside ±0.2 mm, against 0.39 mm
>   of slack — so this should not be needed.
> - The offset is **caliper, not photogrammetry** — the picks say 22.30 mm, which is 0.65 mm
>   of a 0.39 mm slack budget. [measurements.md §The second mount row](measurements.md)
>   has the reduction and the cross-check that settles it.
>
> The **outline** in `TP4056.json` is still deliberately not edited — see measurements.md.
> What changed is a pad position and a pad count, not the module model.

---

## 6. Layout guidance

### 6.1 Floorplan (top view, 70 × 50 mm)

```
        ◀──────────────────────── 70 mm ────────────────────────▶
  ┌─(M3)────────────────────────────────────────────────────(M3)─┐   ▲
  │            X RCA pad ▲            Y RCA pad ▲               │   │
  │            └─R10─┐                └─R11─┐  ┌──────────────┐  │   │
 ~~~ antenna   ┌─────┴──[J3 9pin ]──────────┴─┐│  J4 mic hdr  │  │   │
 ~~~ keep-out  │  GY-PCM5102A ~32mm (J2⊥J3)   ││ → MAX4466 on │  │   │
 ~~~ (no pour) │  ┌──────────────────────────┘│ ~10cm pigtail│  │  50mm
  │  ┌─────────┴──┐│      ANALOG ZONE          └──────────────┘  │   │
  │  │ ESP32-C3   ││  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐  ┌──────────────┐ │   │
  │  │ SuperMini  ││    AGND island (§6.3)      │TP4056 USB-C  │ │   │
  │  │ (J1A/J1B)  ││  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘  │ (J5/J6/J6b)  │═╪══ USB-C
  │  │ USB-C ▶ east│└─[J2: SCK BCK DIN LCK…]   │ Q1 + JP1     │ │edge│
  │  └────────────┘                             │  under here  │ │   │
  │   [J7][TP…]                                 └──────[J8 JST]┘ │   │
  │  D4●  D5●   [SW2 btn]   (RV1 pot)◎   [SW1 slide]             │   │
  └─(M3)────────────────────────────────────────────────────(M3)─┘   ▼
        CONTROL EDGE: LEDs, button, pot, power switch
```

Only **Q1 and JP1** hide under the TP4056. **D1 and D2 sit under the PCM5102A**; D3 (DNP) is
north of the TP4056 and outside every module outline so it can be retrofitted; and the TL431
arm (U1, Q2, R7–R9) plus C1 live west/centre, not in the power corner. That matters for §4.4:
the JP1 fallback means lifting **both** socketed modules.

> **✅ DAC module footprint (photogrammetry 2026-07-26):** the purple GY-PCM5102A is
> **32.0 × 17.4 mm** (not the old ~27 mm nominal) with two **perpendicular** sockets — J2
> (1×6 I2S) on a short edge, J3 (1×9 analog/config) on the long edge, J2 pin 1 at
> (−26.917, +0.583) mm from J3 pin 1. It is the tightest module on the board and the one
> v1.0 got wrong. The footprint is generated from `hw/pin_locs`; `audit_board.py` re-checks
> the fit every build (worst pin 0.180 mm against a 0.25 mm gate).

Hard placement rules:

1. **RCA output pads (X, Y) on the top (north) long edge** — signal+AGND flying-lead pad-pairs,
   centers ≥ 20 mm apart for finger/cable clearance. Silk: `X` and `Y`. Finn solders the reused
   ~50 cm RCA cables (RCA male) directly here; the full chain is board RCA pad → reused ~50 cm
   RCA cable → BNC→RCA adapter (already fitted on each scope) → scope CH. Add strain relief for
   the flying leads at assembly (hot-glue / zip-tie).
2. **Controls grouped on the bottom (south) long edge**: SW1 (power), RV1, SW2, D4, D5 —
   all reachable/visible in one line when units are racked. Silk-label every control
   (`PWR`, `CUTOFF`, `MODE SW`, `NET`, `MODE`) — the button is the one that carries the
   qualifier, because `MODE` on the button and `MODE` on its LED landed side by side and
   read as one part labelled `MODE MODE`.
   **RV1's Y is fixed by the part, not chosen** (2026-07-27): it is a right-angle pot whose
   **mounting surface is 5.0 mm south of its wiper pad**, and that surface must land on the
   board's south edge (y = 50) so the M7×0.75 bushing and 15 mm shaft hang off it and pass
   through the enclosure wall. Hence the anchor at **y = 45.0**, and hence the pot body
   reaching **8.0 mm north** of its pin row — which is what keeps it clear of R12 (1.7 mm)
   and off the TP4056's south-west corner. Pushing RV1 further south would leave the body
   overhanging the edge, supported by five 0.8 mm pins. Do not "tidy" this coordinate.
3. **SuperMini at the west short edge, antenna end outboard.**
   The antenna is **flush with the module's own board edge** — it does not overhang
   anything (measured 2026-07-18, 0 mm). What does the work is a **copper/parts keep-out**
   inboard of that edge on both layers: **no pour and no stitching vias**. A few thin
   signal traces are allowed to clip the region (the router soft-penalises it rather than
   forbidding it) — the pour and the via field are what matter for the antenna.
   ✅ RESOLVED (2026-07-26): antenna and USB-C are at
   **opposite** ends of the module, so the old second sentence here — "orient so the
   module's USB-C faces the same west edge" — contradicted the first and could not be
   satisfied. **Antenna west wins** (RF clearance beats flashing convenience; the module
   lifts out of its sockets for flashing anyway). USB-C therefore faces east. The keep-out
   as built is x < 6 mm, y 6–28 mm, and because the SuperMini's antenna is *flush* with
   its board edge rather than overhanging (measurements.md), that keep-out is what
   actually does the work. Consequence: the 5V row lands **south** ⇒ `JA1` south, `JB1`
   north, which is the mirror-image bug v1.0 shipped and `measured.py` now refuses.
4. **TP4056 (USB-C, blue, 26.9 × 17.3 mm *including* the depanelization nubs; 25.2 mm body)
   at the east short edge**, its USB-C jack overhanging the *module's* east edge by
   **1.4 mm**. ⚠️ **Revised 2026-07-27:** with the nubs filed the body ends at **x ≈ 69.0–69.2**,
   not the modelled 69.79, so the jack's face sits only **~0.5 mm proud of the carrier's
   east edge** — not the ~1.2 mm implied before. More board clearance, but an **enclosure
   constraint**: the charge-port opening has to clear a USB-C **plug overmold** reaching a
   nearly-flush jack, so that wall wants a local relief or a recessed cutout, not a
   jack-sized slot. See [measurements.md §Nubs](measurements.md) reduction 3. As built, only
   **Q1 and JP1** hide under this module; **D1 and D2 sit under the PCM5102A**, D3 is north
   of the TP4056 body and outside every module outline, and the TL431 arm (U1, Q2, R7–R9,
   R12) plus C1 live in the **south-west** quadrant. That matters for §4.4: executing the
   JP1 fallback means lifting both socketed modules, not just the charger.
5. **Analog zone** (J3, R10/R11, RCA pads X/Y, mic header J4) in the north-east quadrant —
   the diagonal opposite of the antenna. The MAX4466 is **not** on the carrier: it hangs off a
   ~10 cm 3-wire pigtail from J4 (`VCC/GND/OUT`) and exits the enclosure so it can be aimed at
   the PA and its gain trimpot reached with a screwdriver. Place the J4 3-pin header in the
   analog zone, as far from the WiFi antenna as the board allows (mics are notorious RF-buzz
   victims); C4 100 nF hard against J4 VCC. No on-board capsule or silk aperture circle is
   needed — the mic aperture is an enclosure detail, not a PCB detail.
6. **I2S traces** (GPIO4/5/6 → J2): route as a short 3-track bundle, < 40 mm, over solid
   ground, away from the analog zone. These are 1.5 MHz+ digital edges.
7. **Mounting: 4× M3 holes** (3.2 mm drill, 6.5 mm annular keep-out), 4 mm inset from each
   corner. North-west hole sits near the antenna keep-out — keep it, standoffs are not RF
   relevant at 4 mm height, but keep pour fencing per rule 3.
8. Bulk C1 within 10 mm of the SuperMini 5V pin. 220 µF is the WiFi-burst reservoir.
9. Battery divider R1/R2/C3 near the SuperMini (short GPIO1 trace), not near the battery —
   the 100 k source impedance wants a short ADC trace.
10. Silk: board name `HYPEROSCI carrier v1.1`, a blank `UNIT #__` box, polarity marks
    on J8/C1/D-series, a `NOT GND` warning on the J5 `B-` pad. Every legend and every
    reference designator is *placed by search* (`gen_board.py`), not by a footprint's
    default offset: each string walks a ring of candidate spots outward and takes the
    first that clears the pads, the board edge and everything already placed, shrinking
    to the 0.8 mm DRC floor before it gives up. Anything with nowhere to sit fails the
    build. All strokes are **0.15 mm**, JLCPCB's silkscreen minimum.
    ⚠️ **The three module bodies are silk keep-outs** (since 2026-07-28) — a third of the
    board, and the search used to treat it as free space, so the battery warnings and the
    board's own name were printed where a fitted module hides them. What may still sit
    there: the module-name legends, the names of pads the module itself covers, and a
    part's designator under *its own* module. `audit_board.py` gates it.
    The fab's order number goes on the **back**, inside the antenna keep-out — the one
    strip of the board guaranteed to carry neither pour nor stitching vias.

### 6.2 Ground pour

Both layers: GND pour at the netclass clearance, **0.25 mm** (the router works to 0.26 mm to
stay off the limit; the 0.127 mm lattice is finer than either). **No pour and no stitching
vias in the antenna keep-out** (thin signal traces may clip it — see rule 3)
(rule 3). Bottom layer under the analog zone belongs to the AGND island (§6.3).

Stitching happens in two passes, because one is not enough on a 2-layer board this dense:

- **Seeded, before routing** — a 7 mm grid of GND vias placed by `gen_board.py` so the
  router treats them as obstacles. Post-route stitching cannot find legal via spots in the
  signal-dense quadrants (tracks land ~1.27 mm apart), and a fragment with no via in it is
  a fragment nothing can rescue.
- **Post-route, on a 4 mm grid** — `route.py` phase C, placed only where both layers are
  free *and* both sit on the main pour. (A via in a pour pocket welds an isolated F+B
  scrap together, which island removal then keeps as floating copper.)

As built that gives a **worst stitch gap of 7.8 mm** anywhere on the board outside the
antenna keep-out, with 45 GND vias; `audit_board.py` gates it at 12 mm. (8.6 mm was the
`ROUTE_SEED=20260726` variant, which the seed sweep rejected — see layout-notes.) Zone minimum thickness is 0.15 mm,
not KiCad's 0.25 mm default: the routing chops the pour into ribbons and a 0.25 mm floor
discards every sliver narrower than that, which is one way a GND pad ends up on a fill
fragment with no path home. JLCPCB's 1 oz minimum copper width is 0.127 mm, so 0.15 is
in spec.

### 6.3 Star ground (analog)

- The AGND island carries: J3 `G`, the X and Y RCA ground pads, J4 mic GND, C4 — **and
  `H2`**, whose 6.4 mm GND pad at (66.0, 4.0) falls on the island side of the moat. That is
  harmless with the plastic case and brass inserts this build uses, but it is a second path:
  **any metal standoff tying two mounting holes together gives the star ground a return that
  does not go through the neck.** Nylon or brass-into-plastic only.
- It joins the main GND pour at **exactly one neck**, ~3 mm wide, at **x 32–35, y ≈ 13.5**
  (east of the J2 `GND` pin at (25.15, 18.36), not immediately beside it — the neck has to
  clear the DAC socket).
- **How it is actually built** (this differs from the obvious approach, which was tried and
  failed): there is **one** `GND_main` zone on both layers, carved into an island by
  **moat rule-areas**, plus an explicit 1.0 mm F+B strap at x = 33.02 from y 8.9 to 17.5 with
  three vias. A *separate same-net island zone* does **not** work — KiCad merges same-net
  zones during fill, so the moat is what enforces the split. Do not "simplify" this into a
  net-tie footprint.
- Result: DAC output return currents and mic return currents do not share copper with the
  SuperMini's digital/WiFi return path.

### 6.4 Trace widths

These are the widths the router *asks* for. On a dense 2-layer board it cannot always get
them, so it steps down a ladder (1.0 → 0.8 → 0.6 → 0.4 → 0.3) at pinch points. The table
therefore carries both the target and **what the board actually has** — measured, not assumed:

*Regenerated from the board by `tools/measure_copper.py` on 2026-07-28, seed 33. **Re-run it
after every re-route** — this table has gone stale three times, and twice it was stale in the
flattering direction.*

| Net class | Target | As built (min) | Note |
|---|---|---|---|
| BAT+ / BAT− / VBAT_OUT / VBUS_CHG | 1.0 mm | **1.0 mm** ✅ | never narrowed |
| **VSW** | 1.0 mm | **0.3 mm** ⚠️ | 51.8 mm at 1.0, 74.4 at 0.8, 23.1 at 0.6, **60.0 at 0.3** — see below |
| VLOAD | 1.0 mm | **1.0 mm** ✅ | 82.6 mm, never narrowed |
| 3V3 | 0.6 mm | **0.6 mm** ✅ | never narrowed (88.7 mm) |
| Signals (I2S, ADC, LEDs, buttons) | 0.3 mm | 0.3 mm ✅ | MIC_OUT 67.8 mm |
| DAC_L/DAC_R, SCOPE_X/Y to RCA pads | 0.5 mm | 0.4 mm | short pad fan-outs only, over AGND |

Pad-to-pad DC, by nodal solve over the real track graph (not a longest-path guess):

| Path | Measured |
|---|---|
| VLOAD SW1 → SuperMini 5V pin | **19.5 mΩ** |
| VLOAD C1 + → SuperMini 5V pin | 3.3 mΩ |
| VSW Q1 source → SW1 | **30.7 mΩ** |
| BAT+ J8 → TP4056 B+ | 7.4 mΩ |
| BAT− J8 → TP4056 B− | 10.0 mΩ |
| VBAT_OUT TP4056 OUT+ → Q1 drain | 7.1 mΩ |
| VBUS_CHG TP4056 IN+ → D2 anode | 26.2 mΩ |
| 3V3 SuperMini → DAC VIN | 12.0 mΩ |

**Which net is the compromised one has inverted, and that is worth reading carefully.** Every
previous version of this table said VLOAD was the problem (91 mm at 0.3 mm, 152 mΩ) and VSW was
fine. On seed 33 it is the other way round: **VLOAD is 1.0 mm over its whole 82.6 mm and
measures 19.5 mΩ end to end**, while VSW carries 60 mm of 0.3 mm copper.

VSW is nonetheless *not* the problem VLOAD used to be, and the reason is in the nodal number
rather than the width breakdown. **Q1 source → SW1 measures 30.7 mΩ**, because VSW is a
fan-out node — D2, JP1, Q1, Q2, R12, R7, SW1, TP2 — and most of the thin copper is on
high-impedance *sense and bias* branches (R7's divider tap, Q2's base network, the TP2
testpoint), not on the main current path. 15 vias give that path parallel routes. Do not read
"60 mm at 0.3 mm" as 60 mm of load current through 0.3 mm copper; it isn't.

End to end, Q1 source → SuperMini 5V pin is now **≈50 mΩ** (30.7 VSW + 19.5 VLOAD, plus SW1's
own contact resistance, which is a mechanical part and not copper). That is **17 mV at a
350 mA WiFi burst and 6 mV at the 125 mA NETWORK average** — against ~176 mΩ implied by the
old table, and negligible beside the 0.2–1 Ω battery loop already in the power budget. **C1
still sits 3.3 mΩ from the 5V pin**, on the load side, so the bulk cap sources the burst
locally regardless of anything upstream — which was the real argument all along and survives
the inversion unchanged.

`route.py`'s `ROUTE_WIDTH_FLOOR` knob (default off) still exists, but the trade it was
introduced to buy **no longer exists on this seed**: VLOAD reaches 1.0 mm throughout without
any floor. Anyone reaching for it should re-measure first rather than trusting the seed-77-era
sweep table that used to live here (it has been moved to `layout-notes.md` and marked as
historical).

---

## 7. Module measurement record — ✅ DONE (2026-07-18 calipers, 2026-07-26 photogrammetry)

This started as a pre-layout caliper checklist. It is now a **record**, and it has been
superseded where the two disagree: pin *positions* come from photogrammetry
(`hw/pin_locs` → `measured.py`), not calipers. That matters, because the two numbers the
caliper session guessed at — the PCM5102A's row-to-row offset and the TP4056's "~2.54 grid" —
were both wrong, and both were wrong *in the v1.0 layout*. **No footprint is drawn from
internet dimensions.** Everything below cites `measurements.md`.

Three boxes below are still open, and they are marked ⬜ **OPEN**. **None of them gates the
gerber plot** — the one that did (the TP4056 pad-row edge offset) closed 2026-07-27.

**ESP32-C3 SuperMini**
- [x] Board outline L × W — **22.5 × 17.8 mm** (2026-07-18)
- [x] Pin row: **2.54 mm pitch, 8/row, 15.24 mm row-to-row** (2026-07-18; photogrammetry confirms **15.240 mm**, worst pin 0.100 mm)
- [x] First-pin offset from each board edge — **1.3 mm USB-C end / 2.8 mm antenna end** (2026-07-18)
- [x] Antenna end: **the non-USB-C end, and it is flush — 0 mm overhang** (2026-07-18). This is why §6.1 rule 3 relies on a keep-out rather than on overhang.
- [x] USB-C: **opposite the antenna, ~1.5 mm overhang** (2026-07-18)
- [x] Pin silk order of both rows vs §2 — **matches** (2026-07-18, TENSTAR ROBOT)
- [x] Bottom-side component height — **~8.3 mm**, which is the number `audit_board.py` gates against (2026-07-18)

**GY-PCM5102A (purple)** — done 2026-07-18, see measurements.md
- [x] Outline — **measured ~31.8 × 17 mm** (~5 mm longer than the old ~27 nominal ⚠️)
- [x] 6-pin header order silk **`SCK BCK DIN LCK GND VIN`** ✓ (short edge)
- [x] Analog end is a **1×9** header (not 1×3), ⊥ to the 6-pin on the long edge. Silk
  (jack→digital): `LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`. Taps: X=LROUT, Y=ROUT, gnd=AGND
- [x] 3.5 mm jack overhang ~1.6 mm (hangs off edge — fine)
- ⬜ **OPEN** Output filter present (~470 Ω "471") but ground-centered — confirm DC pass on the ramp test. *Doable now on the USB rig with a scope; not order-blocking.* **R10/R11 is no longer part of this box** — closed 2026-07-28 as 0 Ω links (§10 Q2). The ramp was never able to settle it: 100 Ω against the module's 470 Ω is 0.01 % of amplitude and ≲0.07° of phase at 10 kHz, identically on both channels.
- ⬜ **OPEN** Confirm solder-bridge state per DESIGN §5 (1=L, 2=L, 3=H, 4=L) — H1L–H4L pads, verify by continuity. *Doable now with a DMM; if the rig already outputs audio, XSMT=H is de-facto proven.*

**MAX4466 (off-board on a ~10 cm pigtail — carrier only needs the J4 3-pin header)**
- [x] Module pin order silk — **`VCC GND OUT`** on the back silk (2026-07-18); J4 matches
- [x] Capsule + gain trimmer stay on the loose module — trimmer is back-side and screwdriver-reachable on the pigtail (2026-07-18)

**TP4056 (USB-C, blue variant)**
- [x] Outline — **26.9 × 17.3 mm** (2026-07-18)
- [x] Positions + drill of B+, B−, OUT+, OUT− pads — ✅ **answered, and the answer is no.**
  Not a 2.54 grid at all: 0 / 3.526 / 10.960 / 14.066 mm from OUT−, holes 2.0 mm
  (±0.55 mm pin slack). See measurements.md §Photogrammetry.
- [x] IN+ / IN− pads — **drilled**, next to the USB-C jack; IN+ at (+22.358, +13.910) from J5.1 (2026-07-18 / 2026-07-26)
- [x] USB-C jack overhang past the board edge — **1.4 mm** (28.3 − 26.9), *not* the ~2 mm this doc used to assume (2026-07-18)
- [x] B+ ↔ OUT+ continuity **0 Ω ✓** and B− ↔ OUT− **open ✓** (protection FET present) (2026-07-18)
- [x] Pad-row-to-board-edge offset — ✅ **CLOSED 2026-07-27, and it was the last thing gating the gerbers.** The pad column sits **0.05 mm south of the body centre** against 0.12 mm modelled: **Δ 0.07 mm on a 0.25 mm gate**, so the module seats north/south exactly as drawn and nothing in the layout moves. The reduction is hole-radius-free — A and B are both measured to the near rim, so the unknown radius cancels in `((A+r) + (17.3−B−r))/2`. It also resolved the 26.9-vs-25.75 length contradiction: **the west edge carries two depanelization nubs** (+1.6 mm) at the OUT−/OUT+ corners, which the photogrammetry's hand-clicked corners averaged across. **File them flush** — see §5. Raw readings and all four reductions: [measurements.md §Nubs](measurements.md).
- [x] **USB-C-end corner pads located, and the module gained a second mount row** —
  ✅ **CLOSED 2026-07-28.** Four pins in one column left the USB-C jack on a **21.65 mm
  cantilever** (≈25° of tilt per 5 N of off-axis push on the plug), so `J9` moved 0.65 mm
  west onto the module's `+` corner pad and became a socket, and **`J10` was added** on the
  `IN−` corner as a netless mechanical anchor. Offset is **caliper 21.65 mm**, not the
  photogrammetry's 22.30 — the picks' perpendicular axis is the one a similarity fit cannot
  get right, and 0.65 mm is more than the 0.39 mm of slack in a 1.68 mm hole. Reduction and
  cross-check: [measurements.md §The second mount row](measurements.md). **This one moved
  copper** — re-routed and re-gated the same day.

**Bought parts**
- [x] RCA output pads (X/Y): design item, nothing to measure — simple signal+AGND pad-pairs (~2 mm) for the reused ~50 cm RCA cable leads
- [x] SW1 slide-switch pin pitch — ✅ **CLOSED 2026-07-28 from the SS12D00 mechanical drawing**, without the part in hand, without the paper doll, and with no layout change. **The pitch is 2.5 mm** — neither of the two values the footprint was drawn around, which is the vindication of drawing a slot instead of a hole: it lands 0.05 mm off the centre of the 2.00–2.90 mm window. Pins are 0.5 × 0.3 mm (0.30 mm a side in a 0.90 mm slot; the ⌀0.9 centre hole takes the middle pin). Body is **8.5 × 3.7 mm on the pin centreline**, inside the 9.0 × 4.0 silk with 0.25 mm a side in X and 0.15 in Y. Nothing overhangs it — TP4056 body 2.51 mm north, J8 courtyard 0.96 mm east, south board edge 1.75 mm — and the 1.5 mm actuator sweeps X 48.23–51.73 with 3.46 mm of nail room to J8. The 6 mm handle never entered the fit; nothing sits above the switch, so it is an enclosure question only. The datasheet's middle terminal is the common, matching pad 2 = VLOAD / pad 1 = VSW / pad 3 netless. **One thing moved to assembly rather than closing:** whether the part carries a locating lug or post. The drawing has no bottom view and dimensions none, so paper cannot settle it — `SW_Slide_SPDT_DualPitch` has the **three signal holes only**, so clip any lug. That is a stuffing note, not an order gate.
  ⚠️ **The slots were re-cut 2026-07-28 and this is a fab constraint, not a preference.** They were 1.44 × 0.90 mm — aspect 1.6, and **JLCPCB will not plate a slot under 2×**. The outcome is either a DFM query against the order date or a silent conversion to a round ⌀0.9 hole at one fixed x, at which point *neither* pitch fits and the boards are scrap. They are now **1.80 × 0.90 (aspect 2.0)** centred at ±2.45 mm, so a pin may sit anywhere **2.00–2.90 mm** from the middle pin — still both pitches, with the copper dragged as far outboard as that allows. The pad stayed 2.5 mm long deliberately: growing it with the slot leaves 0.20 mm to the centre pad, which clears JLCPCB's 0.127 mm floor but *not* this board's own 0.25 mm netclass clearance. **Verify on the drill file, not the board:** the routed slots must read `G00X47.08 → G01X47.98` and `G00X51.98 → G01X52.88` — 0.90 mm of travel on the 0.900 mm tool, where it used to be 0.54.
- [x] RV1 row-2 geometry — **was** the least-certain footprint on the board, and it **was wrong**. Redrawn 2026-07-27 from the seller's mechanical drawing + KiCad's ALPS RK097 stock footprint: SPST at ⌀1.0, 5.0 mm apart, 6.25 mm behind the pot row; mounting surface 5.0 mm in front of it, on the board edge. *A 1:1 paper-doll check with the pot in hand is still worth ten minutes — but it is now confirming a drawing, not a guess.*
- [x] RV1 = **RV097NS-B10K**, 5-pin **mono with switch**, right-angle, body 27.3 × 9.5 × 11.3 mm, metal shaft (no knob) — footprint drawn 2026-07-18, **corrected 2026-07-27** (§5 box). ×5 ordered 2026-07-27; paper-doll the first one that arrives (§9), since 5 pieces is 4 boards plus one spare and there is no margin for a wrong variant.

---

## 8. Fab & ordering

### 8.1 Vendor comparison (verified 2026-07-17)

| | JLCPCB | PCBWay |
|---|---|---|
| 2-layer proto price | from **$2 / 5 pcs** (≤100×100 mm); 10 pcs typically ~$5 | **$5 / 10 pcs** promo (1–2 layer, ≤100×100 mm) |
| Fab time | 24–48 h for simple 2-layer | 24 h possible after gerber review; 48–72 h typical |
| Ship to NL — express (DHL/FedEx) | ~3–7 days, typically €15–25 ⚠️ VERIFY at checkout | 3–7 days after dispatch, similar cost |
| Ship to NL — economy | ~6–20 days (EU logistics center helps; ~1 week to DE/NL reported) | 5–20 days |
| EU customs | IOSS/DDP handled at checkout for EU | handled, 2–4 days dispatch overhead |

Sources: [JLCPCB](https://jlcpcb.com/), [JLCPCB prototype pricing](https://jlcpcb.com/features/pcb-prototype), [JLCPCB fab time](https://jlcpcb.com/help/article/pcb-fabrication-services-and-production-time), [PCBWay $5 promo](https://m.pcbway.com/activitypcb.aspx), [PCBWay quick-turn](https://www.pcbway.com/quickturn-pcb-fabrication.html), [PCBWay shipping to NL](https://www.pcbway.com/helpcenter/shipping_instructions/International_Order___Shipping_Information.html).

**Decision: JLCPCB, qty 5, express courier (DHL).** Both are fine; JLCPCB's EU logistics
path and $2-class base price edge it out, and there is no assembly service needed (all
hand-soldered) so JLCPCB's SMT ecosystem isn't a factor either way. Total expected:
**≈ €20–28 landed** for 5 boards. ⚠️ VERIFY final price at checkout.

### 8.2 Timeline (deadline math)

| Date | Milestone |
|---|---|
| ~~≤ Jul 27~~ | ✅ Caliper session (§7) done Jul 18; photogrammetry Jul 26. **§4.4 is not an order gate and has moved** to the assembled carrier (Aug 11–16) — it cannot run on the current bench (no battery or TP4056 in circuit, Q1 is SOT-23). |
| **Jul 27–28** | **RV1 row 2 is closed** — it was wrong, and the corrected geometry is in the board (§5 box); a paper-doll pass now only confirms a drawing. ✅ **Parts order placed 2026-07-27** (§5 box: plan-A subset, so R7/R8/R9/D1/D2/U1 were *not* bought; RV1 = the **5-pin *with switch*** variant). ✅ **TP4056 pad-row edge offset measured and CLOSED the same day** (§7) — Δ 0.07 mm on a 0.25 mm gate, layout unchanged. ✅ **2026-07-28: the TP4056 gained a second mount row** (§5 box) — J9 moved onto the module's `+` corner pad and J10 was added on the `IN−` corner, because four pins in one column left the USB-C jack on a 21.65 mm cantilever. **This one moved copper**: re-generated, re-routed and re-gated. **No open gates remain: plot gerbers.** |
| ~~Jul 28–30~~ | ✅ Layout complete Jul 26 — v1.1, 0 DRC violations at every severity. The order can go out ~5 days early. |
| **Jul 28** | ✅ **SW1 closed** from the SS12D00 drawing — 2.5 mm pitch, 0.05 mm off the slot window's centre; body 8.5 × 3.7 on the pin centreline, inside the silk with 0.25/0.15 mm a side. No part in hand, no paper doll, no layout change (§7). ✅ **Gerbers plotted, verified and committed** to `hw/carrier/fab/` — `tools/plot_fab.py` asserts §8.4 and will not write the zip if the board stops passing. **Nothing now gates the order except the two viewer loads below.** |
| **Jul 31 – Aug 1** | **Upload gerbers, pay, DHL express** ← hard order-by date |
| Aug 2–4 | Fab (24–48 h + weekend slack) |
| Aug 5–11 | DHL to NL (3–7 days) |
| Aug 11–13 | Solder 4 boards (an evening for all 4 — it's all THT + 2 easy SMD) |
| **Aug 21** | Assembled, firmware bring-up on real carriers (show date; ~1 week slack after the ~Aug 1 order gate) |

If boards arrive after ~Aug 16, the PLAN W4 battery test and rehearsal run on the breadboard/protoboard units (PLAN Risk 3 posture) and carrier assembly slips past Aug 21 — the PCB never gates the rehearsal.

The PCB order never waits on the power-path verdict — the board supports both topologies. As
of the 2026-07-26 review, **JP1 is the plan of record, not the fallback** (§4.5): bridge JP1,
leave Q1/U1/Q2/D1 **and D2** unfitted, keep the "switch OFF before any USB" rule, and populate
the TL431 block later only if the amended §4.4 passes on an assembled board.

### 8.3 Order parameters

2 layers · 70 × 50 mm · qty 5 · FR-4 TG135+ · 1.6 mm · 1 oz outer copper · HASL lead-free ·
green mask, white silk · no castellations, no impedance control, no stencil ·
"Order number: specify location" (put the JLCPCB job number under the TP4056 module).

### 8.4 Gerber checklist (what goes in the zip)

**✅ Plotted 2026-07-28 — `hw/carrier/fab/`.** Every item below except the viewer load is
now asserted by `tools/plot_fab.py`, which re-runs DRC, plots, checks, and **writes the zip
only if all of it holds**. Run it rather than the GUI; the settings below are not defaults.

- [x] `F.Cu`, `B.Cu` (gerbers)
- [x] `F.Mask`, `B.Mask`
- [x] `F.Silkscreen`, `B.Silkscreen`
- [x] `Edge.Cuts` — verified a single closed 4-segment outline at exactly 70.000 × 50.000 mm,
      with all copper inside it by ≥ 0.5 mm (no corner radius used)
- [x] Drill: PTH + NPTH merged, Excellon, mm/decimal, absolute origin
- [x] ~~Plot with KiCad's built-in **JLCPCB preset**~~ — **superseded.** The preset is a GUI
      feature and `kicad-cli` has no equivalent flag, so `plot_fab.py` sets the settings
      explicitly. Two of them are load-bearing rather than cosmetic:
      **`--excellon-oval-format route`**, because `kicad-cli` defaults to `alternate` and
      under that default SW1's plated slots **do not appear as slots at all** — the check in
      §7 would have had nothing to read, silently; and **`--no-x2 --no-netlist`**, so one zip
      loads identically at JLCPCB and PCBWay (the X2 attributes still go out as `G04`
      comments, which is what fabs parse anyway). Protel extensions and precision 6 are the
      `kicad-cli` defaults and are correct.
- [ ] Sanity-load the zip in JLCPCB's online gerber viewer **and** a second viewer
      (e.g. `gerbv` or tracespace) — check outline, drills present, silk not mirrored.
      ← **the only item here a human still has to do**
- [x] No paste layers (no stencil; Q1's SOT-23 is on hand-solder pads). Nothing
      soldermask-defined — all THT pads mask-opened normally, verified as 124/116 mask
      regions against 126/116 pads, and F.Cu/B.Cu flash counts reconcile exactly
      (222 = 126 pads + 96 vias; 212 = 116 THT + 96 vias).
- [x] No vias inside RCA/JST pads — **passes**: no through-hole pad on the board has a via
      in it. ⚠️ Five **SMD** pads do, which this checklist never asked about: `Q1.1`, `Q1.2`,
      `TP2.1`, `TP3.1`, `TP4.1`, all ⌀0.8 on a 0.4 mm drill within 0.07 mm of pad centre. The
      three testpoints are intentional — that via *is* how the pad reaches its net. Q1's two
      are a router artifact and are **not new**: every routed revision since `4c362ff` has
      had them, and seed 33 has one fewer than `e6943b5` did. Accepted. Consequence is
      hand-soldering only — solder wicks to B.Cu through 0.4 mm, so feed extra and expect a
      bead.

---

## 9. How the board is produced — ✅ DONE (v1.1, 2026-07-26)

**This section used to describe two evenings of drawing footprints and routing by hand. That
is not how this board is made.** Everything — footprints, schematic, placement, routing,
pours, silk — is generated from `hw/carrier/tools/design.py`, which is the single source of
truth for the netlist. **Do not hand-edit `carrier.kicad_pcb`**; the next regeneration will
discard it. Full detail: [`hw/carrier/layout-notes.md`](../../hw/carrier/layout-notes.md).

```bash
cd hw/carrier
/usr/bin/python3 tools/gen_footprints.py   # only if footprints change (rewrites all UUIDs)
/usr/bin/python3 tools/gen_schematic.py    # REQUIRED after any design.py change
/usr/bin/python3 tools/gen_board.py && /usr/bin/python3 tools/route.py
/usr/bin/python3 tools/audit_board.py --verbose
# re-export the human-readable wiring diagram (wiring.md §3 embeds the SVG):
kicad-cli sch export svg -o /tmp/schsvg carrier.kicad_sch && cp /tmp/schsvg/carrier.svg carrier-schematic.svg
kicad-cli sch export pdf -o carrier-schematic.pdf carrier.kicad_sch
# and the visual artifacts (these were previously undocumented — regenerate them
# whenever copper, silk or a footprint moves, or they quietly go stale):
kicad-cli pcb render --side top    -w 1544 -h 1152 --quality high -o render-top.png    carrier.kicad_pcb
kicad-cli pcb render --side bottom -w 1544 -h 1152 --quality high -o render-bottom.png carrier.kicad_pcb
kicad-cli pcb export pdf --layers F.Cu,F.Silkscreen,F.Fab,Edge.Cuts -o paper-doll-1to1.pdf carrier.kicad_pcb
# back-silk artwork — only when the source image changes (needs potrace):
/usr/bin/python3 tools/gen_artwork.py art/484848-mono.png art/484848.json
```

**The gerber plot, which nothing here used to spell out:**

```bash
kicad-cli pcb export gerbers --no-protel-ext -o gerbers/ \
  -l F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts carrier.kicad_pcb
kicad-cli pcb export drill --format excellon --excellon-separate-th -o gerbers/ carrier.kicad_pcb
```

> ⚠️ **`B.Silkscreen` has to be in that list explicitly.** It is easy to plot the
> front-only set — the paper-doll export above is deliberately front-only — and
> the back silk then just does not exist in the order. That silently drops the
> board's version string *and* the back artwork, and neither DRC nor any gate in
> this repo will complain, because the board file is fine; only the plot is
> wrong. Check the gerber set has a `B_Silkscreen`/`.GBS` member before upload.

> **The renders show 3D bodies only for parts that have a model.** The seven custom
> `HYPEROSCI.pretty` footprints (RV1, SW1, D2, J5, J6, J9, JP1 and the testpoints) carry
> none, so they appear as bare pads and a silk outline. That is cosmetic — but it is also
> why RV1 looked wrong in the render long before anyone noticed its *pads* were wrong, so
> do not treat "it renders" as a footprint check. The paper doll is the footprint check.

`gen_schematic.py` (v2, 2026-07-26) emits a properly-drawn wiring diagram — placed
functional blocks, drawn power-path wiring, per-connector pin names, safety annotations —
not a netlist dump. Presentation lives in the generator; connectivity still comes only from
`design.py`, and the generator refuses to emit a wire whose endpoints disagree with it.

Use **`/usr/bin/python3`** — `pcbnew` is only importable from KiCad's own interpreter, and a
conda `python3` on `PATH` will fail to import it. **Measured 2026-07-28 on a 32-thread box,
seed 33, nothing else running: `gen_board.py` 7 s, `route.py` 251 s (~4 min).** The ladder
early-exits the moment an attempt reaches zero failures — seed 33 gets there on attempt 28 of
40 — so a *working* seed is the cheap case and attempts reporting failures on the way are
normal; do not abort early. On a seed that does **not** work it is several times slower, a
failed A\* expanding the whole grid before giving up: the 2026-07-28 sweep's failing variants
took 430–1277 s each *while sharing the box 16 ways*. Give `tools/search.py` a
`CARRIER_SEARCH_TIMEOUT` to match (its default 1800 s silently turns a slow sweep into a wall
of `FAILED at timeout` — which is exactly what happened on the first attempt, on a laptop).
`gen_board.py` takes ~7 s, most of it re-counting each designator's remaining options after
every placement — the board is dense enough that the *order* decides whether the silk fits at
all (see layout-notes → Silkscreen), and ranking it statically is what used to lose SW1's and
TP4/TP5's designators.

`tools/measure_copper.py` reports the as-built track widths and the pad-to-pad DC resistance
behind §6.4's table. Re-run it after any re-route rather than trusting the table: the widths in
§6.4 have gone stale twice, most recently when VLOAD went from 0.3 mm over 91 mm to 1.0 mm
throughout and nothing said so.

> **`ROUTE_SEED` is board-specific and goes stale.** The default is **33** (2026-07-28); it
> was 11, and 77 before that. Each change was forced by copper moving a fraction of a
> millimetre: 77 died when RV1's corrected footprint moved five pads (phase D ended `STUCK —
> 3 clusters left`, two unconnected `GND_main` islands), and 11 died when the pre-fab DFM pass
> moved SW1's slot pads to ±2.45, SW1 itself 0.4 mm south and C3 0.79 mm north — it came back
> 1 unconnected / 1 unrouted on `BAT_PLUS`. **A stale seed does not fail loudly — it fails as
> a severed ground pour.** The 2026-07-28 sweep is the sharpest illustration so far: of 16
> variants (4 seeds × 4 halo settings) **exactly one was clean.** Whenever copper moves,
> re-sweep with `/usr/bin/python3 tools/search.py --stage router` and adopt the winner, rather
> than assuming the old seed still holds.
>
> Note the halo knobs need not be set: `ROUTE_GND_HALO_COST` defaults to 0, and `route.py`
> gates the whole halo behind `if HALO_COST > 0`, so the repo default is already behaviourally
> identical to the sweep's `halo-off`. Seed 33 reproduces from `ROUTE_SEED=33` alone.

The three gates, all of which must be clean before plotting gerbers:

| Gate | Checks | Required |
|---|---|---|
| `check_netlist.py` (against a `kicad-cli sch export netlist` dump) | design.py ↔ schematic both ways, `config.h` GPIO map, §2/§3 invariants | pass |
| `kicad-cli pcb drc --severity-all` | clearance, connectivity, silk, courtyards | **0 at every severity** |
| `audit_board.py` | module pin-fit vs photogrammetry (0.25 mm), part heights under sockets, silk collisions, 90° corners, stitch gap, **and legends printed where a fitted module hides them** | 0 fails |

Add `--schematic-parity` to the DRC call when you change a BOM value — it is **opt-in** and no
gate requests it by default, which is how stale value strings could otherwise sit in the board
while the schematic said something else. Expect **67 residual items, none of them defects**
(59 footprint-name/attribute formatting, 8 deliberate no-connects) — read the count as a diff
against that baseline, not as something to drive to zero. Details in `layout-notes.md`.

`kicad-cli sch erc` reports ~41 `footprint_link_issues` in a headless checkout — that is the
global footprint library table not being visible to `kicad-cli`, not a design error.

**Before plotting:** print `paper-doll-1to1.pdf` at 1:1 and lay the real modules on it. Ten
minutes, and it is the check that catches a footprint disaster the DRC cannot see. Then
fabrication outputs per §8.4.

---

## 10. Open questions (Finn)

*(Resolved 2026-07-17 and folded into the spec above: scope outputs = RCA flying-lead pads,
no BNC and no TRS; TP4056 = USB-C variant; SW1 = SS12D00 slide switch, 6 mm handle, its 0.3 A
make/break rating accepted at 4 V (revised 2026-07-27 from "1 A-rated"); mic = off-board on a
~10 cm pigtail; battery = EEMB LP103454 2000 mAh, off-board.)*
*(Resolved 2026-07-18 from the bench session, see measurements.md: **RV1 = RV097NS-B10K**,
5-pin, metal shaft turned directly, **no knob** — pot footprint finalized; SuperMini
**VBUS is directly tied to the 5 V pin — CONFIRMED** with a meter. That is *why* the
SuperMini-side OR term had to become a threshold rather than a diode (§4.1) — and per the
2026-07-26 review that threshold does not reliably work, so **the "no USB while switch ON"
rule stands** rather than being retired (§4.5). D3 stays DNP; DAC analog end is a **1×9**
header, not 1×3.)*

1. **Rprog mod on TP4056 (1 A → 500 mA):** the plan mandates 5 V/2 A chargers so the 1 A
   default is safe, but the Rprog swap (one 0402/0603 resistor per module) is still your call —
   optional, not mandated (§4.3 state 2). *(Measured Rprog = 1.19 kΩ ⇒ ~1 A, as expected.)*
2. ~~**R10/R11 value**~~ — **CLOSED 2026-07-28: fit 0 Ω, as wire links.** The module already
   carries its own ~470 Ω output filter (§3.2, measured), so the only question was what another
   100 Ω does. Answer: nothing you can see. Into a 50 cm RCA cable plus a scope input (20–200 pF),
   adding 100 Ω moves the phase at 10 kHz by **0.007°–0.07°** and costs **0.01 %** of amplitude
   into 1 MΩ — and it does so *identically on X and Y*, so the X-Y figure itself is untouched.
   There is no bench ramp that can resolve that, so there is nothing to decide on one.
   The tie-breaker is mechanical: R10 stands at the PCM5102A's south-east corner, and a standing
   DIN0207 is **9 mm** tall (`design.py`) against an 8.3 mm socket standoff. It only passes
   `audit_board.py`'s clearance gate because `PART_HEIGHT_MM` there still calls that package
   **7.5 mm** — i.e. by assumption, not by measurement. A **0 Ω wire link lies flat**, and the
   question stops existing. (Fit 100 Ω only if a future cable turns out to need the isolation;
   the footprint is unchanged either way, and the height discrepancy above should then be
   reconciled in `audit_board.py` first.)
3. **Populate the §4 load-share block, or bridge JP1?** Decided by the amended §4.4 test on an
   assembled carrier, not before the order. Default per §4.5 is **JP1** — and note the two
   are not symmetric in effort: the block is 10 parts you can add later, JP1 is a bridge you
   can cut later, and both live under socketed modules.
4. **Divider values / reference part** (§4.2 table): 8.2 k/10 k + TL431A is fitted by default;
   TLV431A + 100 k/38.3 k is electrically better if you can source the TO-92 part. This is a
   populate-time choice — the board does not care.
