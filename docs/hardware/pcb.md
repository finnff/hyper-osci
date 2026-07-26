# HYPEROSCI Carrier PCB — v1.0 Specification

**Status:** Ready for KiCad execution. Pin map / constants mirror
[`docs/DESIGN.md`](../DESIGN.md) §4 and
[`src/esp32-slave/include/config.h`](../../src/esp32-slave/include/config.h) — those are law.
**Goal:** a KiCad 9 session can execute this document start-to-finish without making design
decisions. Anything genuinely undecided is listed in §10 (open questions) or carried as a
DNP (do-not-populate) footprint so the decision moves to assembly time, not layout time.

---

## 1. Concept

- **Board:** ~70 × 50 mm, 2-layer FR-4, 1.6 mm, 1 oz copper, HASL (lead-free), green.
- **Assembly:** hand-soldered by Finn. All through-hole **except** Q1 (SOT-23) and D2 (SMA) in
  the power path — both are trivially hand-solderable on enlarged hand-solder pads. THT
  alternates for both are specified in the BOM (§5) with shared/dual footprints.
- **Modules are socketed** on female 2.54 mm header sockets, never soldered down:

| Module | Socket | Notes |
|---|---|---|
| ESP32-C3 SuperMini | 2× 1×8 female (J1A/J1B) | antenna end overhangs board edge (§6) |
| GY-PCM5102A (purple) | 1×6 female (J2) + 1×9 female (J3) | I2S end (short edge) + 9-pin analog/config end on the long edge (⊥ to J2). Module measures **~31.8 × 17 mm** — see §7/measurements.md |
| MAX4466 mic | 1×3 header (J4) | off-board on a ~10 cm pigtail (`VCC/GND/OUT`); aimed at the PA |
| TP4056 USB-C (blue, 17×27 mm, with DW01+FS8205) | 2× 1×2 female (J5 batt end, J6 out end) + 1 pad for IN+ | USB-C end at board edge (jack overhangs ~2 mm) |

- Passive parts may be placed **under the socketed modules** (sockets give ~8.5 mm clearance) —
  this is how everything fits in 70 × 50 mm.
- **Battery (off-board):** EEMB **LP103454** LiPo 3.7 V **2000 mAh**, 34 × 56 × 11 mm, ~40 g,
  with a pre-fitted JST-PH lead (no crimping). It does **not** sit on the PCB — it is mounted
  loose (velcro/pocket) in the 3D-printed enclosure, since 56 × 34 mm is large next to the
  70 × 50 mm carrier. The carrier only carries the JST-PH socket (J8). The ~10 h runtime target
  is met comfortably (≈13 h NETWORK, ≈30 h LOCAL).
- **Enclosure:** 3D-printed case; the carrier keeps its 4× M3 mounting holes (§6).
- 4 identical boards will be built (a 5th possible later); order **5 PCBs** (§8) — the fab
  minimum quantity is 5 anyway, which yields one spare bare board.

---

## 2. Connectors & sockets — pin-by-pin

Reference designators used throughout this doc and to be used in KiCad:

| Ref | Part | Function |
|---|---|---|
| J1A, J1B | 1×8 female header, 2.54 mm | SuperMini socket rows. **KiCad refs are `JA1`/`JB1`** (a KiCad reference must end in a digit); the silk keeps the doc names. Row spacing ✅ **measured 15.240 mm** (photogrammetry, worst pin 0.100 mm). `JB1` = the GPIO5-side row and it is the **north** one — see §6.1 rule 3. |
| J2 | 1×6 female header | PCM5102A I2S end: `SCK BCK DIN LCK GND VIN` (order per module silk ⚠️ VERIFY) |
| J3 | 1×9 female header | PCM5102A analog/config end (long edge). Silk (jack→digital): `LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`. Carrier nets only the analog pins: **LROUT (=X), ROUT (=Y), AGND**; FLT/DEMP/XSMT/FMT are set by the module's own H1L–H4L back-side bridges (DESIGN §5), so they are **NC** on the carrier — the rest of the 1×9 is mechanical support. |
| J4 | 1×3 header (pigtail landing) | MAX4466 on a ~10 cm 3-wire pigtail: `VCC GND OUT` (order ⚠️ VERIFY — clones differ). Mic exits the enclosure, aimed at the PA. |
| J5 | 1×2 pad pair, **3.526 mm** pitch | TP4056 north pair: `OUT− B−`. ⚠️ **Not a stock socket and not a 2.54 grid** — the module's four pads measure 0 / 3.526 / 10.960 / 14.066 mm down its short edge (photogrammetry, 2026-07-26), so the grouping is `(OUT−,B−)` + `(B+,OUT+)`, *not* the battery-end/output-end split this table used to claim. Generated footprint `HYPEROSCI:TP4056_Pads_OUTminus_Bminus`. |
| J6 | 1×2 pad pair, **3.106 mm** pitch | TP4056 south pair: `B+ OUT+`, 10.960 mm south of J5.1. Generated footprint `HYPEROSCI:TP4056_Pads_Bplus_OUTplus`. Fit four machined single sockets, or solder wires and give up removability. |
| J6b | 1 plated pad + short wire | TP4056 `IN+` (VBUS_CHG sense — see §4; pad drilled 1.0 mm). **KiCad ref is `J9`.** |
| J7 | 1×2 male header | Debug: `GPIO21 (UART0 TX)`, `GND` |
| J8 | JST-PH 2-pin, side entry (S2B-PH-K-S) | LiPo battery in (EEMB LP103454 2000 mAh, mounted off-board — cell not on the PCB outline). **Polarity silk mandatory.** |
| X, Y | 2× RCA flying-lead solder pad-pair (signal + AGND) | Scope X (=DAC L), Scope Y (=DAC R). Finn solders the reused ~50 cm RCA cables here — no connector part on the board. Silk `X` / `Y`. |
| SW1 | 1 A-rated SS12D00-class SPDT slide switch (e.g. SK12D07) | Power switch (in VSW→VLOAD path, §4); 1 A rating chosen because WiFi TX peaks ≈ 0.35 A |
| SW2 | 6×6 mm THT tactile | MODE button → GPIO7 |
| RV1 | **RV097NS** 9 mm 10 kΩ linear pot (B10K), 5-pin, body 27.3 × 9.5 × 11.3 mm. **Metal shaft turned directly — no knob.** | Filter-cutoff → GPIO3 |

**SuperMini socket pin functions** (net names; physical position per module silk —
⚠️ VERIFY silk against your actual modules before finalizing the footprint, clone revisions
differ):

| J1A (expected: 5V-side row) | Net | J1B (expected: GPIO5-side row) | Net |
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
| **BAT+** | J8.1 (JST +) → J5 `B+`. Also top of battery divider R1. On this USB-C TP4056 module, `B+` and `OUT+` are the same copper (protection is low-side) ⚠️ VERIFY on your modules. |
| **BAT−** | J8.2 (JST −) → J5 `B−` **only**. ⚠️ **B− must NOT join GND** — the DW01/FS8205 protection switches the low side between B− and OUT−. Shorting them defeats protection. Silk warning next to J5. |
| **GND** | J6 `OUT−` = system ground: SuperMini GND, J2 GND, J2 SCK (see below), C1−, C2, C3, C5, R2, R6, R8, U1 anode, SW2 pin B, RV1 CCW end, LED cathodes, TP4056 `IN−` (same node module-internally), mounting-hole pads (fenced, see §6). |
| **VBAT_OUT** | J6 `OUT+` → Q1 **drain** (and nothing else). |
| **VSW** | Q1 **source** + D2 cathode + D3 anode (DNP) + sense divider top (R7) + SW1 pin 1 + testpoint TP2. |
| **VLOAD** | SW1 pin 2 → SuperMini `5V` pin, C1 (220 µF) +, C2 (100 nF). |
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
| **DAC_L / SCOPE_X** | — | J3 `LROUT` → R10 100 Ω → X RCA signal pad |
| **DAC_R / SCOPE_Y** | — | J3 `ROUT` → R11 100 Ω → Y RCA signal pad |
| **J2 `SCK`** | — | **Tie to GND on the carrier.** This implements DESIGN §5's "SCK tied to GND" (DAC generates its clock via PLL from BCK) without a solder mod on each module. |

R10/R11 (100 Ω series) protect the DAC from shorted/hot-plugged scope cables and isolate it
from cable capacitance; into a 1 MΩ scope input the attenuation is 0.01 % — invisible.
**Measured 2026-07-18:** the purple module already carries an output reconstruction filter
(~470 Ω "471" series parts + caps) between the DAC and the `LROUT`/`ROUT` pins, and the
output is **ground-centered** (no DC-blocking cap in the path). With ~470 Ω already in
series, keep the R10/R11 footprints but **fit 0 Ω** if the Phase-4 bench ramp (wiring.md)
confirms the module R is genuinely in-line — only populate 100 Ω if you want extra isolation.

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

DESIGN §9 sketches "P-FET + Schottky, both VBUS sources diode-ORed into the gate". The
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

### 4.2 Circuit

```
                          D2 SS34
  TP4056 IN+ (VBUS_CHG) ──▶|────────────┐
        │                               │
        │  D1 1N4148                    │
        └────▶|───────────┐             │
                          │ GATE        │
  TP4056 OUT+ ──┬─────────┼──────┐      │
  (VBAT_OUT)    │        ─┴─     │      │
                │   R6   100k    │G     │
                │        ─┬─   ┌─┴─┐    │              SW1          SuperMini
                │        GND   │Q1 │    │  VSW        (power)        5V pin
                └──────────────┤ P ├────┴────┬─────────o o──────┬─────────── VLOAD
                          D    │FET│   S     │                  │
                               └───┘         │                 ─┴─ C1 220µF
              DMG3415U, body diode D→S       │                 ─┬─ + C2 100nF
                                             │                 GND
              ┌──────────────────────────────┤
              │ R7 82k                       │
              ├───────── U1 TL431 REF        │ R9 2.2k      Q2 BC557 (PNP)
              │ R8 100k  (trips VSW>4.54V)   ├──[R12 100k]──┬── E
             GND         K──[R9 2.2k]── B ───┘         B ───┤
                         A──GND                        C ───┴──▶ GATE
```

Netlist form:

| Part | Value | Connections |
|---|---|---|
| Q1 | DMG3415U (P-ch, −20 V, −4 A, Vgs(th) −0.3…−1.0 V, Rds(on) 42 mΩ @ −4.5 V) | D = VBAT_OUT, S = VSW, G = GATE |
| R6 | 100 k | GATE → GND (default: FET on) |
| D1 | 1N4148 | VBUS_CHG → GATE (charger-USB OR term) |
| D2 | SS34 (or THT 1N5817/1N5822, dual footprint) | VBUS_CHG → VSW (feeds load while charging) |
| R7/R8 | 82 k / 100 k, 1 % | VSW divider → U1 REF. Trip = 2.495 × 182/100 ≈ **4.54 V** |
| U1 | TL431 (TO-92) | REF = divider, A = GND, K = via R9 to Q2 base |
| R9 | 2.2 k | Q2 base → U1 cathode (~1 mA cathode current when tripped — only when USB present) |
| R12 | 100 k | Q2 base–emitter (holds Q2 off) |
| Q2 | BC557 (PNP, TO-92) | E = VSW, C = GATE. When on, pulls GATE to VSW ⇒ Vgs ≈ 0 ⇒ Q1 off |
| D3 | BAT85 — **DNP** | VLOAD → GATE. Experimental "naive OR" term; see §4.1 why it's not fitted |
| SW1 | slide switch | VSW → VLOAD. Downstream of FET/Schottky on purpose (see states) |
| C1/C2 | 220 µF electro + 100 nF | on VLOAD (bulk for WiFi TX bursts, per DESIGN §3) |

### 4.3 Operation state-by-state

| # | State | What happens |
|---|---|---|
| 1 | **Battery only, SW1 ON** | VBUS_CHG = 0 ⇒ D1 inert. VSW bootstraps through Q1's body diode, TL431 sees < 4.2 V ⇒ off ⇒ GATE = 0 V via R6 ⇒ Vgs = −VBAT (−3.0…−4.2 V) ⇒ Q1 fully on. Drop at 125 mA ≈ **5 mV**. Load runs from battery. |
| 2 | **Charging (TP4056 USB in), SW1 ON** | GATE pulled to ≈ 4.5 V by D1 *and* TL431 trips (VSW ≈ 4.7 V via D2) ⇒ Q1 off ⇒ battery sees only the charger: clean CC/CV, correct termination. Load runs from VBUS_CHG through D2 (VSW ≈ 4.7 V → SuperMini LDO). Unit fully operational while charging. Use a **5 V / 2 A** wall charger (mandatory): charge current (1 A default Rprog) + load (~150 mA) share one port; 2000 mAh at 1 A ≈ 0.5C ⇒ full charge ~2.5–3 h. Reprogramming Rprog to 2.4 k (500 mA) is **optional** (fiddly 0402/0603 rework) — the 2 A charger makes the 1 A default safe, so it is not required. |
| 3 | **Charging, SW1 OFF** | Same as #2 but load disconnected — unit off, battery charges clean. TL431 burns ~1 mA from the charger (irrelevant). |
| 4 | **Flashing (SuperMini USB in), SW1 ON, battery in** | SuperMini VBUS drives VLOAD = 5.0 V hard → through SW1 → VSW = 5.0 V ⇒ TL431 trips ⇒ Q1 off. Body diode blocked (drain 4.2 V < source 5.0 V). **No back-feed into the cell.** Whole board (DAC, mic, LEDs) runs from the flashing USB. |
| 5 | **Flashing, SW1 OFF** | SuperMini + 3V3 loads (DAC, mic) powered from its USB; VLOAD isolated from VSW by open switch; battery idles behind Q1. Also fine. |
| 6 | **SW1 OFF, no USB (storage)** | Q1 is on (gate 0, source floats to VBAT) so VSW sits at VBAT with no load. Standby drain = R7+R8 (23 µA) + battery divider R1+R2 (21 µA) ≈ **45 µA** ⇒ ~5 years on the 2000 mAh cell. DW01 protects the cell long before that. |
| 7 | **Race: plugging charger while SW1 OFF** | For the µs–ms before TL431 trips, Q1 is still on and D2 could push a current blip into the cell. D1 yanks the gate high in nanoseconds, closing the race. This is why D1 stays fitted even though TL431 covers the steady state. |

Margins (with DMG3415U Vgs(th) min −0.3 V per [Diodes datasheet](https://www.diodes.com/datasheet/download/DMG3415U.pdf)):
- State 4: TL431 trip 4.54 V vs VLOAD ≥ 4.75 V (worst-case USB) → 0.21 V margin; vs battery max 4.2 V → 0.34 V margin.
- State 2 with a sagging charger (4.75 V USB − 0.35 V D2 = 4.40 V < trip): TL431 alone would fail to trip, but D1 holds Vgs ≈ −0.15 V, above the −0.3 V minimum threshold ⇒ Q1 stays off. The two mechanisms deliberately overlap.

### 4.4 ⚠️ VERIFY — breadboard-test the whole block before committing the PCB order

Non-negotiable, ~1 evening, do it in week 1–2 with real modules and a bench meter:

1. State 1: measure VBAT→VLOAD drop at 150 mA (expect < 20 mV). If you see ~0.7 V, the FET
   isn't turning on — check gate.
2. State 2: scope the cell current while charging with load running — must show clean CC/CV,
   LED on TP4056 must reach "charged" (termination works only if load is truly disconnected).
3. State 4: ammeter in series with the cell while SuperMini USB is plugged, SW1 ON —
   must read ≤ µA (leakage), never mA into the cell.
4. State 7: plug/unplug charger 20× with SW1 in both positions; watch cell current for blips.
5. Sweep a bench supply 4.2→5.0 V on VLOAD and log the TL431 trip point; adjust R7 if your
   USB sources measure < 4.8 V loaded (75 k → ≈4.37 V; 77 k for 4.42 V — only if needed).
6. Optionally fit D3 (BAT85) and confirm the §4.1 failure yourself (battery-mode drop jumps
   to ~0.7 V) — then remove it.

**Escape hatch (already on the board):** if the block misbehaves and time runs out, bridge a
2.54 mm solder-jumper/0 Ω footprint **JP1 (VBAT_OUT → VSW)**, leave Q1/U1/Q2/D1 unfitted, and
you are back to the proven plain-switch topology plus the old "switch OFF while USB plugged"
rule. Place JP1 next to Q1. This removes all schedule risk from the power path.

---

## 5. On-board BOM (per board, modules excluded)

Quantities are per board; build 4 (a 5th possible later), order parts for ~6 (spares). Example parts are LCSC-searchable MPNs;
⚠️ VERIFY exact LCSC stock codes at order time (codes drift) — search by the MPN given.

| Ref | Qty | Part | Example MPN / LCSC search | Package | Notes |
|---|---|---|---|---|---|
| Q1 | 1 | P-FET −20 V | **DMG3415U-7** (Diodes) | SOT-23 (SMD) | hand-solder pads; THT fallback: none good at low Vgs — keep SOT-23 |
| Q2 | 1 | PNP BJT | **BC557B** | TO-92 | any small PNP works |
| U1 | 1 | Shunt ref | **TL431** (TO-92, any brand) | TO-92 | comparator duty only |
| D1 | 1 | Si diode | **1N4148** | DO-35 | gate OR |
| D2 | 1 | Schottky 3 A | **SS34** (LCSC C8678 ⚠️ VERIFY) or THT **1N5817**/**1N5822** | SMA / DO-41 | draw a dual THT+SMA footprint |
| D3 | (1) | Schottky small | **BAT85** | DO-35 | **DNP** — experiment only (§4.1) |
| D4 | 1 | LED 3 mm green | e.g. Hongli 3 mm green | THT | NET status |
| D5 | 1 | LED 3 mm amber/yellow | e.g. Hongli 3 mm yellow | THT | MODE status |
| R1, R2 | 2 | 100 k 1 % | generic 1/4 W metal film | axial | battery divider (`VBAT_DIVIDER 2.0`) |
| R3 | 1 | 10 k | generic 1/4 W | axial | GPIO2 strap pull-up |
| R4, R5 | 2 | 2.2 k | generic 1/4 W | axial | LED series (per config.h "~2.2k") |
| R6, R12 | 2 | 100 k | generic 1/4 W | axial | gate pull-down / PNP B-E |
| R7 | 1 | 82 k 1 % | metal film | axial | sense divider top |
| R8 | 1 | 100 k 1 % | metal film | axial | sense divider bottom |
| R9 | 1 | 2.2 k | generic | axial | TL431 cathode drive |
| R10, R11 | 2 | 100 Ω | generic | axial | DAC → RCA-pad series |
| C1 | 1 | 220 µF ≥ 10 V electrolytic | e.g. Rubycon/Chang 220 µF 16 V | radial 6.3 mm | VLOAD bulk |
| C2, C4, C5 | 3 | 100 nF X7R | generic | 2.54 mm radial | decoupling |
| C3 | 1 | 100 nF X7R | generic | 2.54 mm radial | across R2 (ADC filter) |
| SW1 | 1 | SPDT slide, **1 A-rated** | **SK12D07**-class (1 A); the base SS12D00G4 is only 0.3 A and WiFi TX peaks ≈ 0.35 A, so the 1 A part is required | THT | power |
| SW2 | 1 | Tactile 6×6 mm | **TS-1102** / Omron B3F-1000 style | THT 6×6, 4-pin | MODE |
| RV1 | 1 | 10 k linear pot, 9 mm (B10K) | **RV097NS-B10K** (5-pin, metal shaft, body 27.3 × 9.5 × 11.3 mm) | RV097NS THT (5-pin) | shaft turned directly — **no knob** (enclosure needs only a shaft hole) |
| X, Y | — | RCA output landings (signal + AGND solder pads) | no connector part — reuse the ~50 cm RCA cables from the old sigma-delta units | 2× pad-pair | Finn solders the RCA cable leads directly; scope side uses a BNC→RCA adapter |
| J8 | 1 | JST-PH 2-pin side entry | **S2B-PH-K-S** (JST) | THT | battery socket; cell = EEMB LP103454 2000 mAh (off-board, pre-fitted JST-PH lead) |
| J1A/J1B | 2 | Female header 1×8 | generic 2.54 female | THT | SuperMini |
| J2 | 1 | Female header 1×6 | generic | THT | DAC I2S |
| J3 | 1 | Female header 1×9 | generic | THT | DAC analog/config end (only LROUT/ROUT/AGND netted; rest mechanical/NC) |
| J4 | 1 | Female header 1×3 | generic | THT | mic pigtail (`VCC GND OUT`) |
| J5, J6 | 2 | Female header 1×2 | generic | THT | TP4056 |
| J7 | 1 | Male header 1×2 | generic | THT | debug TX |
| JP1 | 1 | 0 Ω / solder jumper | — | 2.54 mm | power-path escape hatch (open by default) |
| TP1–TP5 | 5 | testpoint pad | — | 1.5 mm pad | no part |
| — | 4 | M3 screw + standoff | — | — | mounting (§6) |

Order **fixed-size** female headers (1×8, 1×6, 1×9, 1×3, 1×2) — female strips do not snap cleanly;
each cut destroys one position. Male pin headers for the TP4056 pads: solder standard 2.54 mm
male pins into the module's drilled B+/B−/OUT+/OUT− pads so it plugs into J5/J6.
⚠️ VERIFY: pad spacing on this USB-C TP4056 module is close to but not guaranteed 2.54 mm-grid — measure
(§7) and place J5/J6 at the measured positions (custom footprint), not on an assumed grid.
`IN+` connects via a short soldered wire from the module pad to J6b — it only carries the
D1/D2 sense/feed current (≤ 200 mA), a 5 cm wire is fine.

---

## 6. Layout guidance

### 6.1 Floorplan (top view, 70 × 50 mm)

```
        ◀──────────────────────── 70 mm ────────────────────────▶
  ┌─(M3)────────────────────────────────────────────────────(M3)─┐   ▲
  │            X RCA pad ▲            Y RCA pad ▲               │   │
  │            └─R10─┐                └─R11─┐  ┌──────────────┐  │   │
 ~~~ antenna   ┌─────┴──[J3 9pin ]──────────┴─┐│  J4 mic hdr  │  │   │
 ~~~ overhang  │  GY-PCM5102A ~32mm (J2⊥J3)   ││ → MAX4466 on │  │   │
 ~~~ (keep-out)│  ┌───────────────────────────┘│ ~10cm pigtail│  │  50mm
  │  ┌─────────┴──┐│      ANALOG ZONE          └──────────────┘  │   │
  │  │ ESP32-C3   ││  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐  ┌──────────────┐ │   │
  │  │ SuperMini  ││    AGND island (§6.3)      │TP4056 USB-C  │ │   │
  │  │ (J1A/J1B)  ││  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘  │ (J5/J6/J6b)  │═╪══ USB-C
  │  │ USB ▼ edge?│└──[J2: SCK BCK DIN LCK…]    │ Q1 D1 D2 U1  │ │edge│
  │  └────────────┘   POWER PATH under/near ───▶│ Q2 JP1 C1    │ │   │
  │   [J7][TP…]                                 └──────[J8 JST]┘ │   │
  │  D4●  D5●   [SW2 btn]   (RV1 pot)◎   [SW1 slide]             │   │
  └─(M3)────────────────────────────────────────────────────(M3)─┘   ▼
        CONTROL EDGE: LEDs, button, pot, power switch
```

> **⚠️ DAC module footprint (measured 2026-07-18):** the purple GY-PCM5102A is **~31.8 mm**
> long (not the old ~27 mm nominal) with two **perpendicular** sockets — J2 (1×6 I2S) on a
> short edge, J3 (1×9 analog/config) on the long edge. Draw the footprint `GY-PCM5102A_6+9`
> from measured header spacing and re-confirm the 70 × 50 mm fit with the 1:1 paper-doll
> check (§9) before routing — this is the tightest module on the board.

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
3. **SuperMini at the west short edge, antenna end overhanging the board outline.**
   The antenna end must extend past the PCB edge, and a **copper/parts keep-out** inboard
   of that edge on both layers (no pour, no traces) covers the case where the overhang
   ends up smaller than planned. ✅ RESOLVED (2026-07-26): antenna and USB-C are at
   **opposite** ends of the module, so the old second sentence here — "orient so the
   module's USB-C faces the same west edge" — contradicted the first and could not be
   satisfied. **Antenna west wins** (RF clearance beats flashing convenience; the module
   lifts out of its sockets for flashing anyway). USB-C therefore faces east. The keep-out
   as built is x < 6 mm, y 6–28 mm, and because the SuperMini's antenna is *flush* with
   its board edge rather than overhanging (measurements.md), that keep-out is what
   actually does the work. Consequence: the 5V row lands **south** ⇒ `JA1` south, `JB1`
   north, which is the mirror-image bug v1.0 shipped and `measured.py` now refuses.
4. **TP4056 (USB-C, blue 17 × 27 mm) at the east short edge**, its USB-C jack overhanging the
   board edge by ~2 mm (~29 mm effective module depth). Size the board-edge cutout / charge-port
   opening to clear the USB-C connector. Power-path parts (Q1, Q2, U1, D1, D2, R6–R9, R12, JP1,
   C1) cluster next to it — they can hide under the socketed module.
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
10. Silk: board name `HYPEROSCI carrier v1.1`, date, a blank `UNIT #__` box, polarity marks
    on J8/C1/D-series, `B- is NOT GND` warning at J5. Every legend and every reference
    designator is *placed by search* (`gen_board.py`), not by a footprint's default
    offset: each string walks a ring of candidate spots outward and takes the first that
    clears the pads, the board edge and everything already placed, shrinking to the
    0.8 mm DRC floor before it gives up. Anything with nowhere to sit fails the build.
    The fab's order number goes on the **back**, inside the antenna keep-out — the one
    strip of the board guaranteed to carry neither pour nor stitching vias.

### 6.2 Ground pour

Both layers: GND pour, 0.3 mm clearance. **No pour or traces in the antenna keep-out**
(rule 3). Bottom layer under the analog zone belongs to the AGND island (§6.3).

Stitching happens in two passes, because one is not enough on a 2-layer board this dense:

- **Seeded, before routing** — a 7 mm grid of GND vias placed by `gen_board.py` so the
  router treats them as obstacles. Post-route stitching cannot find legal via spots in the
  signal-dense quadrants (tracks land ~1.27 mm apart), and a fragment with no via in it is
  a fragment nothing can rescue.
- **Post-route, on a 4 mm grid** — `route.py` phase C, placed only where both layers are
  free *and* both sit on the main pour. (A via in a pour pocket welds an isolated F+B
  scrap together, which island removal then keeps as floating copper.)

As built that gives a **worst stitch gap of 8.6 mm** anywhere on the board outside the
antenna keep-out; `audit_board.py` gates it at 12 mm. Zone minimum thickness is 0.15 mm,
not KiCad's 0.25 mm default: the routing chops the pour into ribbons and a 0.25 mm floor
discards every sliver narrower than that, which is one way a GND pad ends up on a fill
fragment with no path home. JLCPCB's 1 oz minimum copper width is 0.127 mm, so 0.15 is
in spec.

### 6.3 Star ground (analog)

- The AGND island carries: J3 `G`, the X and Y RCA ground pads, J4 mic GND, C4.
- It joins the main GND pour at **exactly one neck**, ~3 mm wide, placed next to the J2 `GND`
  pin (the DAC module's ground reference). DRC trick: make AGND a separate net-tie footprint
  or a deliberate pour bridge; verify with the DRC that no second join exists.
- Result: DAC output return currents and mic return currents do not share copper with the
  SuperMini's digital/WiFi return path.

### 6.4 Trace widths

| Net class | Width |
|---|---|
| BAT+/BAT−/VBAT_OUT/VSW/VLOAD (to 350 mA bursts) | 1.0 mm |
| 3V3 | 0.6 mm |
| Signals (I2S, ADC, LEDs, buttons) | 0.3 mm |
| DAC_L/DAC_R to RCA pads | 0.5 mm, over AGND only |

---

## 7. ⚠️ VERIFY — caliper session before layout (mandatory, ~45 min)

Do this with the four real modules on the bench; write numbers straight into a
`docs/hardware/measurements.md` scratch table. **No footprint is drawn from internet
dimensions.** Capture, per module:

**ESP32-C3 SuperMini**
- [ ] Board outline L × W (nominal 22.5 × 18 mm)
- [ ] Pin row: pitch (2.54?), pins per row (8?), row-to-row spacing (expected 15.24 mm)
- [ ] First-pin offset from each board edge
- [ ] Antenna end: which end, overhang length of antenna region from last pin row
- [ ] USB-C: which end, connector overhang past board edge
- [ ] Confirm pin silk order of both rows against §2 table
- [ ] Bottom-side component height (socket clearance check)

**GY-PCM5102A (purple)** — done 2026-07-18, see measurements.md
- [x] Outline — **measured ~31.8 × 17 mm** (~5 mm longer than the old ~27 nominal ⚠️)
- [x] 6-pin header order silk **`SCK BCK DIN LCK GND VIN`** ✓ (short edge)
- [x] Analog end is a **1×9** header (not 1×3), ⊥ to the 6-pin on the long edge. Silk
  (jack→digital): `LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`. Taps: X=LROUT, Y=ROUT, gnd=AGND
- [x] 3.5 mm jack overhang ~1.6 mm (hangs off edge — fine)
- [ ] Output filter present (~470 Ω "471") but ground-centered — confirm DC pass on the ramp test
- [ ] Confirm solder-bridge state per DESIGN §5 (1=L, 2=L, 3=H, 4=L) — H1L–H4L pads, verify by continuity

**MAX4466 (off-board on a ~10 cm pigtail — carrier only needs the J4 3-pin header)**
- [ ] Confirm module pin order silk (`VCC GND OUT`?) so the J4 3-pin header wiring matches
- [ ] Capsule + gain trimmer stay on the loose module (aimed at the PA, reachable by hand) —
  nothing about the mic constrains the carrier footprint beyond the J4 pin order

**TP4056 (USB-C, blue variant)**
- [ ] Outline (nominal ~17 × 27 mm, blue USB-C board)
- [x] Positions + drill of B+, B−, OUT+, OUT− pads — ✅ **answered, and the answer is no.**
  Not a 2.54 grid at all: 0 / 3.526 / 10.960 / 14.066 mm from OUT−, holes 2.0 mm
  (±0.55 mm pin slack). See measurements.md §Photogrammetry.
- [ ] IN+ / IN− pads: drilled or SMD-only? position
- [ ] USB-C jack overhang past the board edge (~2 mm, ~29 mm effective module depth)
- [ ] Confirm B+ ↔ OUT+ continuity (0 Ω) and B− ↔ OUT− **non**-continuity (protection FET)

**Bought parts**
- [ ] RCA output pads (X/Y): nothing to verify — they are simple signal+AGND solder pad-pairs
  (~2 mm pads) for the reused ~50 cm RCA cable leads; confirm the pad comfortably takes the
  stripped signal + shield wires
- [ ] SW1 slide switch pin pitch (SS12-style: 2× 3-pin @ 2.0 mm? some are 2.54) — module not in hand yet
- [x] RV1 = **RV097NS-B10K**, 5-pin, body 27.3 × 9.5 × 11.3 mm, metal shaft (no knob) — draw the 5-pin RV097NS footprint

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
| ≤ Jul 27 | Breadboard-verify power path (§4.4) + caliper session (§7) |
| Jul 28–30 | KiCad evenings 1–2 (§9) |
| **Jul 31 – Aug 1** | **Upload gerbers, pay, DHL express** ← hard order-by date |
| Aug 2–4 | Fab (24–48 h + weekend slack) |
| Aug 5–11 | DHL to NL (3–7 days) |
| Aug 11–13 | Solder 4 boards (an evening for all 4 — it's all THT + 2 easy SMD) |
| **Aug 21** | Assembled, firmware bring-up on real carriers (show date; ~1 week slack after the ~Aug 1 order gate) |

If boards arrive after ~Aug 16, the PLAN W4 battery test and rehearsal run on the breadboard/protoboard units (PLAN Risk 3 posture) and carrier assembly slips past Aug 21 — the PCB never gates the rehearsal.

Buffer is thin: if the §4.4 breadboard test slips past Jul 30, order anyway **with JP1 as
the committed fallback** — the board supports both topologies, so the PCB order never waits
on the power-path verdict.

### 8.3 Order parameters

2 layers · 70 × 50 mm · qty 5 · FR-4 TG135+ · 1.6 mm · 1 oz outer copper · HASL lead-free ·
green mask, white silk · no castellations, no impedance control, no stencil ·
"Order number: specify location" (put the JLCPCB job number under the TP4056 module).

### 8.4 Gerber checklist (what goes in the zip)

- [ ] `F.Cu`, `B.Cu` (gerbers)
- [ ] `F.Mask`, `B.Mask`
- [ ] `F.Silkscreen`, `B.Silkscreen`
- [ ] `Edge.Cuts` (single closed outline, 70 × 50 mm, corner radius 2 mm optional)
- [ ] Drill: PTH + NPTH, Excellon, merged file fine for JLCPCB
- [ ] Plot with KiCad's built-in **JLCPCB preset** (KiCad 9: File → Fabrication Outputs →
      Gerbers → "JLCPCB" plot preset), which sets protel extensions + correct precision
- [ ] Sanity-load the zip in JLCPCB's online gerber viewer **and** a second viewer
      (e.g. `gerbv` or tracespace) — check outline, drills present, silk not mirrored
- [ ] Confirm: no paste layers needed (no stencil), no vias inside RCA/JST pads,
      soldermask-defined nothing (all THT pads mask-opened normally)

---

## 9. KiCad 9 execution checklist (~2 evenings)

### Evening 1 — libraries + schematic (~3 h)

- [ ] New project `hw/carrier/` in repo; grid mm; KiCad 9 defaults
- [ ] Make footprints from §7 caliper data: `SuperMini_Socket_2x1x8`,
      `GY-PCM5102A_6+9` (1×6 ⊥ 1×9, module ~31.8 mm), `Mic_Pigtail_1x3`, `TP4056_USBC_pads`,
      `RCA_FlyingLead_Pads` (measured), reuse stock: `TO-92`, `SOT-23_HandSolder`,
      `SMA+DO-41` dual, axial R, radial C, `SW_SS12D00`, tactile 6×6, `RV097NS` (5-pin),
      JST-PH S2B, pin sockets
- [ ] Draw schematic exactly per §3 netlist + §4.2 power path; net names as given here
- [ ] Add JP1, D3 with **DNP flag set** (KiCad "Do not populate" attribute)
- [ ] ERC clean (power flags on VLOAD/3V3/GND; no-connect flags on GPIO8/9 socket pins)
- [ ] Cross-check every GPIO number against `config.h` one final time (5 min, checklist §3.2)

### Evening 2 — layout + outputs (~3–4 h)

- [ ] Board outline 70 × 50 mm, M3 holes, place per §6.1 floorplan
- [ ] Place sockets first, verify module paper-doll cutouts (print 1:1, lay real modules on
      the print — 10 minutes that catches every footprint disaster)
- [ ] Route power nets (widths §6.4), then I2S bundle, then everything else
- [ ] Pours + AGND island with single neck (§6.3); antenna keep-out enforced (§6.1 rule 3)
- [ ] DRC with JLCPCB rules (min track/clearance 0.127 mm capability, but stay ≥ 0.25 mm);
      zero errors, justify every warning
- [ ] Silk pass per §6.1 rule 10; print 1:1 again, final module fit check
- [ ] Fabrication outputs per §8.4, zip, upload, order **by Aug 1**

---

## 10. Open questions (Finn)

*(Resolved 2026-07-17 and folded into the spec above: scope outputs = RCA flying-lead pads,
no BNC and no TRS; TP4056 = USB-C variant; SW1 = 1 A-rated slide switch; mic = off-board on a
~10 cm pigtail; battery = EEMB LP103454 2000 mAh, off-board.)*
*(Resolved 2026-07-18 from the bench session, see measurements.md: **RV1 = RV097NS-B10K**,
5-pin, metal shaft turned directly, **no knob** — pot footprint finalized; SuperMini
**VBUS is directly tied to the 5 V pin — CONFIRMED** with a meter, so the load-share path /
"no USB while switch ON" rule stands as designed, and D3 stays DNP; DAC analog end is a **1×9**
header, not 1×3.)*

1. **Rprog mod on TP4056 (1 A → 500 mA):** the plan mandates 5 V/2 A chargers so the 1 A
   default is safe, but the Rprog swap (one 0402/0603 resistor per module) is still your call —
   optional, not mandated (§4.3 state 2). *(Measured Rprog = 1.19 kΩ ⇒ ~1 A, as expected.)*
2. **R10/R11 value:** the module carries its own ~470 Ω output filter (§3.2, measured) — decide
   0 Ω vs 100 Ω on the Phase-4 bench ramp. Not a layout blocker (footprint stays either way).
