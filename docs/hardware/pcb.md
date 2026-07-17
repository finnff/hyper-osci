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
| GY-PCM5102A (purple) | 1×6 female (J2) + 1×3 female (J3) | I2S end + analog-out end |
| MAX4466 mic | 1×3 female (J4) | capsule faces up |
| TP4056 03962A (with DW01+FS8205) | 2× 1×2 female (J5 batt end, J6 out end) + 1 pad for IN+ | USB end at board edge |

- Passive parts may be placed **under the socketed modules** (sockets give ~8.5 mm clearance) —
  this is how everything fits in 70 × 50 mm.
- 4 identical boards will be built; order **10 PCBs** (§8) for spares/mistakes.

---

## 2. Connectors & sockets — pin-by-pin

Reference designators used throughout this doc and to be used in KiCad:

| Ref | Part | Function |
|---|---|---|
| J1A, J1B | 1×8 female header, 2.54 mm | SuperMini socket rows (row spacing expected 15.24 mm ⚠️ VERIFY §7) |
| J2 | 1×6 female header | PCM5102A I2S end: `SCK BCK DIN LCK GND VIN` (order per module silk ⚠️ VERIFY) |
| J3 | 1×3 female header | PCM5102A analog end: `L G R` (order ⚠️ VERIFY on real module) |
| J4 | 1×3 female header | MAX4466: `VCC GND OUT` (order ⚠️ VERIFY — clones differ) |
| J5 | 1×2 female header | TP4056 battery end: `B+ B−` |
| J6 | 1×2 female header | TP4056 output end: `OUT+ OUT−` |
| J6b | 1 plated pad + short wire | TP4056 `IN+` (VBUS_CHG sense — see §4; pad drilled 1.0 mm) |
| J7 | 1×2 male header | Debug: `GPIO21 (UART0 TX)`, `GND` |
| J8 | JST-PH 2-pin, side entry (S2B-PH-K-S) | LiPo battery in. **Polarity silk mandatory.** |
| J9 | 3.5 mm stereo TRS jack, THT (PJ-307 style) | Wired in parallel with BNCs: tip=X, ring=Y, sleeve=AGND. Fit optional (§10 Q1). |
| X1, X2 | Right-angle PCB BNC female | Scope X (=DAC L), Scope Y (=DAC R) |
| SW1 | SS12D00-style SPDT slide switch | Power switch (in VSW→VLOAD path, §4) |
| SW2 | 6×6 mm THT tactile | MODE button → GPIO7 |
| RV1 | RV09 9 mm 10 kΩ linear pot | Filter-cutoff knob → GPIO3 |

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
| **BAT+** | J8.1 (JST +) → J5 `B+`. Also top of battery divider R1. On the 03962A, `B+` and `OUT+` are the same copper (protection is low-side) ⚠️ VERIFY on your modules. |
| **BAT−** | J8.2 (JST −) → J5 `B−` **only**. ⚠️ **B− must NOT join GND** — the DW01/FS8205 protection switches the low side between B− and OUT−. Shorting them defeats protection. Silk warning next to J5. |
| **GND** | J6 `OUT−` = system ground: SuperMini GND, J2 GND, J2 SCK (see below), C1−, C2, C3, C5, R2, R6, R8, U1 anode, SW2 pin B, RV1 CCW end, LED cathodes, TP4056 `IN−` (same node module-internally), mounting-hole pads (fenced, see §6). |
| **VBAT_OUT** | J6 `OUT+` → Q1 **drain** (and nothing else). |
| **VSW** | Q1 **source** + D2 cathode + D3 anode (DNP) + sense divider top (R7) + SW1 pin 1 + testpoint TP2. |
| **VLOAD** | SW1 pin 2 → SuperMini `5V` pin, C1 (220 µF) +, C2 (100 nF). |
| **3V3** | SuperMini `3V3` pin → J2 `VIN` (PCM5102A), J4 `VCC` (mic, with C4 100 nF at the socket), R3 (GPIO2 pull-up), RV1 CW end, C5 (100 nF near J2). Per DESIGN §5 the DAC's VIN runs from 3V3 (double-LDO cascade is fine). |
| **VBUS_CHG** | J6b (TP4056 `IN+`) → D1 anode, D2 anode, testpoint TP1. |
| **GATE** | Q1 gate + R6 (100 k → GND) + D1 cathode + D3 cathode (DNP) + Q2 collector + TP3. |
| **AGND** | Analog ground island: J3 `G`, X1 shell, X2 shell, J9 sleeve, C4 GND side, J4 `GND` (mic). Joined to GND at **one neck** near the J2 GND pin (§6.3). Electrically the same net as GND — draw as GND in the schematic, enforce the single-neck join in layout. |

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
| **DAC_L / SCOPE_X** | — | J3 `L` → R10 100 Ω → X1 center (and J9 tip) |
| **DAC_R / SCOPE_Y** | — | J3 `R` → R11 100 Ω → X2 center (and J9 ring) |
| **J2 `SCK`** | — | **Tie to GND on the carrier.** This implements DESIGN §5's "SCK tied to GND" (DAC generates its clock via PLL from BCK) without a solder mod on each module. |

R10/R11 (100 Ω series) protect the DAC from shorted/hot-plugged scope cables and isolate it
from cable capacitance; into a 1 MΩ scope input the attenuation is 0.01 % — invisible.
⚠️ VERIFY: check whether the purple module already has series resistors/filters on L/R
(trace it or measure resistance from PCM5102A pin to header). If it does, fit 0 Ω here.

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
clone modules USB VBUS is tied straight to the `5V` pin (DESIGN §9 says so itself) — i.e.
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
| 2 | **Charging (TP4056 USB in), SW1 ON** | GATE pulled to ≈ 4.5 V by D1 *and* TL431 trips (VSW ≈ 4.7 V via D2) ⇒ Q1 off ⇒ battery sees only the charger: clean CC/CV, correct termination. Load runs from VBUS_CHG through D2 (VSW ≈ 4.7 V → SuperMini LDO). Unit fully operational while charging. Use a **5 V/2 A** supply: charge current (1 A default Rprog) + load (~150 mA) share one port. Optionally reprogram Rprog to 2.4 k (500 mA) on the modules. |
| 3 | **Charging, SW1 OFF** | Same as #2 but load disconnected — unit off, battery charges clean. TL431 burns ~1 mA from the charger (irrelevant). |
| 4 | **Flashing (SuperMini USB in), SW1 ON, battery in** | SuperMini VBUS drives VLOAD = 5.0 V hard → through SW1 → VSW = 5.0 V ⇒ TL431 trips ⇒ Q1 off. Body diode blocked (drain 4.2 V < source 5.0 V). **No back-feed into the cell.** Whole board (DAC, mic, LEDs) runs from the flashing USB. |
| 5 | **Flashing, SW1 OFF** | SuperMini + 3V3 loads (DAC, mic) powered from its USB; VLOAD isolated from VSW by open switch; battery idles behind Q1. Also fine. |
| 6 | **SW1 OFF, no USB (storage)** | Q1 is on (gate 0, source floats to VBAT) so VSW sits at VBAT with no load. Standby drain = R7+R8 (23 µA) + battery divider R1+R2 (21 µA) ≈ **45 µA** ⇒ ~2.5 years on 1000 mAh. DW01 protects the cell long before that. |
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

Quantities are per board; build 4, order parts for 6. Example parts are LCSC-searchable MPNs;
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
| R10, R11 | 2 | 100 Ω | generic | axial | DAC → BNC series |
| C1 | 1 | 220 µF ≥ 10 V electrolytic | e.g. Rubycon/Chang 220 µF 16 V | radial 6.3 mm | VLOAD bulk |
| C2, C4, C5 | 3 | 100 nF X7R | generic | 2.54 mm radial | decoupling |
| C3 | 1 | 100 nF X7R | generic | 2.54 mm radial | across R2 (ADC filter) |
| SW1 | 1 | SPDT slide | **SS12D00G4** style; prefer a 1 A-rated variant (e.g. SK12D07) — SS12D00 is rated 0.3 A and WiFi TX peaks ≈ 0.35 A ⚠️ VERIFY rating | THT | power |
| SW2 | 1 | Tactile 6×6 mm | **TS-1102** / Omron B3F-1000 style | THT 6×6, 4-pin | MODE |
| RV1 | 1 | 10 k linear pot, 9 mm vertical | **RV09AF-40-15K-B10K** (⚠️ VERIFY shaft length code) | RV09 THT | knob to taste |
| X1, X2 | 2 | BNC female, right-angle PCB | generic "BNC-KWE" / Amphenol 31-5431 class | THT 4-leg | ⚠️ VERIFY footprint against the physical part you buy — clone BNC leg patterns vary |
| J9 | (1) | 3.5 mm stereo jack | **PJ-307** / PJ-324M | THT | optional fit — open question §10 Q1 |
| J8 | 1 | JST-PH 2-pin side entry | **S2B-PH-K-S** (JST) | THT | battery |
| J1A/J1B | 2 | Female header 1×8 | generic 2.54 female | THT | SuperMini |
| J2 | 1 | Female header 1×6 | generic | THT | DAC I2S |
| J3, J4 | 2 | Female header 1×3 | generic | THT | DAC analog / mic |
| J5, J6 | 2 | Female header 1×2 | generic | THT | TP4056 |
| J7 | 1 | Male header 1×2 | generic | THT | debug TX |
| JP1 | 1 | 0 Ω / solder jumper | — | 2.54 mm | power-path escape hatch (open by default) |
| TP1–TP5 | 5 | testpoint pad | — | 1.5 mm pad | no part |
| — | 4 | M3 screw + standoff | — | — | mounting (§6) |

Order **fixed-size** female headers (1×8, 1×6, 1×3, 1×2) — female strips do not snap cleanly;
each cut destroys one position. Male pin headers for the TP4056 pads: solder standard 2.54 mm
male pins into the module's drilled B+/B−/OUT+/OUT− pads so it plugs into J5/J6.
⚠️ VERIFY: pad spacing on the 03962A is close to but not guaranteed 2.54 mm-grid — measure
(§7) and place J5/J6 at the measured positions (custom footprint), not on an assumed grid.
`IN+` connects via a short soldered wire from the module pad to J6b — it only carries the
D1/D2 sense/feed current (≤ 200 mA), a 5 cm wire is fine.

---

## 6. Layout guidance

### 6.1 Floorplan (top view, 70 × 50 mm)

```
        ◀──────────────────────── 70 mm ────────────────────────▶
  ┌─(M3)────────────────────────────────────────────────────(M3)─┐   ▲
  │            X1 BNC ▲(X)            X2 BNC ▲(Y)     [J9 TRS]   │   │
  │            └─R10─┐                └─R11─┐  ┌──────────────┐  │   │
 ~~~ antenna   ┌─────┴──[J3 L G R]──────────┴─┐│   MAX4466    │  │   │
 ~~~ overhang  │   GY-PCM5102A (on J2 + J3)   ││ (J4, capsule │  │   │
 ~~~ (keep-out)│  ┌───────────────────────────┘│  faces UP)   │  │  50mm
  │  ┌─────────┴──┐│      ANALOG ZONE          └──────────────┘  │   │
  │  │ ESP32-C3   ││  ┌ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┐  ┌──────────────┐ │   │
  │  │ SuperMini  ││    AGND island (§6.3)      │TP4056 03962A │ │   │
  │  │ (J1A/J1B)  ││  └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘  │ (J5/J6/J6b)  │═╪══ USB
  │  │ USB ▼ edge?│└──[J2: SCK BCK DIN LCK…]    │ Q1 D1 D2 U1  │ │edge│
  │  └────────────┘   POWER PATH under/near ───▶│ Q2 JP1 C1    │ │   │
  │   [J7][TP…]                                 └──────[J8 JST]┘ │   │
  │  D4●  D5●   [SW2 btn]   (RV1 pot)◎   [SW1 slide]             │   │
  └─(M3)────────────────────────────────────────────────────(M3)─┘   ▼
        CONTROL EDGE: LEDs, button, pot, power switch
```

Hard placement rules:

1. **BNCs (X1, X2) on the top (north) long edge**, connectors pointing off-board, centers
   ≥ 20 mm apart for finger/cable clearance. Silk: `X` and `Y`. J9 TRS beside them if fitted.
2. **Controls grouped on the bottom (south) long edge**: SW1 (power), RV1, SW2, D4, D5 —
   all reachable/visible in one line when units are racked. Silk-label every control
   (`PWR`, `CUTOFF`, `MODE`, `NET`, `MODE`).
3. **SuperMini at the west short edge, antenna end overhanging the board outline by ~3–5 mm.**
   The antenna end of the SuperMini must extend past the PCB edge. Additionally keep a
   **copper/parts keep-out ≥ 5 mm** inboard of that edge on both layers (no pour, no traces)
   in case the overhang ends up smaller after caliper check. Orient so the module's USB-C
   faces the same west edge (flashing access). ⚠️ VERIFY which end carries the antenna vs USB
   on your modules before fixing socket orientation.
4. **TP4056 at the east short edge**, its USB connector flush with/overhanging the board
   edge (charging access). Power-path parts (Q1, Q2, U1, D1, D2, R6–R9, R12, JP1, C1)
   cluster next to it — they can hide under the socketed module.
5. **Analog zone** (J3, R10/R11, BNCs, TRS, mic J4) in the north-east quadrant —
   the diagonal opposite of the antenna. Keep the MAX4466 as far from the WiFi antenna as
   the board allows (they are notorious RF-buzz victims); C4 100 nF hard against J4 VCC.
   Mic capsule faces **up** on a flat-socketed module; leave 8 mm component-free silk circle
   around the capsule position and note the aperture location for the future enclosure.
   (Alternative: right-angle female header to aim the capsule outward off the north edge —
   decide at assembly, the same J4 footprint serves both.)
6. **I2S traces** (GPIO4/5/6 → J2): route as a short 3-track bundle, < 40 mm, over solid
   ground, away from the analog zone. These are 1.5 MHz+ digital edges.
7. **Mounting: 4× M3 holes** (3.2 mm drill, 6.5 mm annular keep-out), 4 mm inset from each
   corner. North-west hole sits near the antenna keep-out — keep it, standoffs are not RF
   relevant at 4 mm height, but keep pour fencing per rule 3.
8. Bulk C1 within 10 mm of the SuperMini 5V pin. 220 µF is the WiFi-burst reservoir.
9. Battery divider R1/R2/C3 near the SuperMini (short GPIO1 trace), not near the battery —
   the 100 k source impedance wants a short ADC trace.
10. Silk: board name `HYPEROSCI carrier v1.0`, date, a blank `UNIT #__` box, polarity marks
    on J8/C1/D-series, `B− ≠ GND!` warning at J5.

### 6.2 Ground pour

Both layers: GND pour, 0.3 mm clearance, stitched with vias every ~10 mm along signal
corridors. **No pour or traces in the antenna keep-out** (rule 3). Bottom layer under the
analog zone belongs to the AGND island (§6.3).

### 6.3 Star ground (analog)

- The AGND island carries: J3 `G`, both BNC shells, J9 sleeve, J4 mic GND, C4.
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
| DAC_L/DAC_R to BNC | 0.5 mm, over AGND only |

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

**GY-PCM5102A (purple)**
- [ ] Outline (nominal ~27 × 17 mm)
- [ ] 6-pin header: pitch, offset, exact pin order silk (`SCK BCK DIN LCK GND VIN`?)
- [ ] 3-pin analog header: position, pin order (`L G R`?), distance from 6-pin row
- [ ] 3.5 mm jack overhang (it may hang off the carrier edge — fine)
- [ ] Confirm solder-bridge state per DESIGN §5 (1=L, 2=L, 3=H, 4=L) while you have it out

**MAX4466**
- [ ] Outline (nominal ~20 × 13 mm), header pitch/offset, pin order silk
- [ ] Capsule diameter and position (silk aperture circle)
- [ ] Gain trimmer position (must stay reachable when socketed)

**TP4056 03962A**
- [ ] Outline (nominal ~26 × 17 mm)
- [ ] Positions + drill of B+, B−, OUT+, OUT− pads (are they on a 2.54 grid? usually *almost*)
- [ ] IN+ / IN− pads: drilled or SMD-only? position
- [ ] USB connector type (micro-B vs USB-C version!) and overhang
- [ ] Confirm B+ ↔ OUT+ continuity (0 Ω) and B− ↔ OUT− **non**-continuity (protection FET)

**Bought parts**
- [ ] BNC (X1/X2): actual leg pattern of the specific right-angle BNC you order — draw the
  footprint from the physical part or its datasheet drawing, clone patterns vary wildly
- [ ] SW1 slide switch pin pitch (SS12-style: 2× 3-pin @ 2.0 mm? some are 2.54) 
- [ ] RV1 RV09 pin pattern (2.5/5.0 mm triangle) + shaft length vs knob

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

**Decision: JLCPCB, qty 10, express courier (DHL).** Both are fine; JLCPCB's EU logistics
path and $2-class base price edge it out, and there is no assembly service needed (all
hand-soldered) so JLCPCB's SMT ecosystem isn't a factor either way. Total expected:
**≈ €25–30 landed** for 10 boards. ⚠️ VERIFY final price at checkout.

### 8.2 Timeline (deadline math)

| Date | Milestone |
|---|---|
| ≤ Jul 27 | Breadboard-verify power path (§4.4) + caliper session (§7) |
| Jul 28–30 | KiCad evenings 1–2 (§9) |
| **Jul 31 – Aug 1** | **Upload gerbers, pay, DHL express** ← hard order-by date |
| Aug 2–4 | Fab (24–48 h + weekend slack) |
| Aug 5–11 | DHL to NL (3–7 days) |
| Aug 11–13 | Solder 4 boards (an evening for all 4 — it's all THT + 2 easy SMD) |
| **Aug 14** | Assembled, firmware bring-up on real carriers |

If boards arrive after Aug 9, the PLAN W4 battery test and rehearsal run on the breadboard/protoboard units (PLAN Risk 3 posture) and carrier assembly slips past Aug 14 — the PCB never gates the rehearsal.

Buffer is thin: if the §4.4 breadboard test slips past Jul 30, order anyway **with JP1 as
the committed fallback** — the board supports both topologies, so the PCB order never waits
on the power-path verdict.

### 8.3 Order parameters

2 layers · 70 × 50 mm · qty 10 · FR-4 TG135+ · 1.6 mm · 1 oz outer copper · HASL lead-free ·
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
- [ ] Confirm: no paste layers needed (no stencil), no vias inside BNC/JST pads,
      soldermask-defined nothing (all THT pads mask-opened normally)

---

## 9. KiCad 9 execution checklist (~2 evenings)

### Evening 1 — libraries + schematic (~3 h)

- [ ] New project `hw/carrier/` in repo; grid mm; KiCad 9 defaults
- [ ] Make footprints from §7 caliper data: `SuperMini_Socket_2x1x8`,
      `GY-PCM5102A_6+3`, `MAX4466_1x3`, `TP4056_03962A_pads`, `BNC_RA_<vendor>`
      (measured), reuse stock: `TO-92`, `SOT-23_HandSolder`, `SMA+DO-41` dual, axial R,
      radial C, `SW_SS12D00`, tactile 6×6, RV09, JST-PH S2B, pin sockets
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

1. **BNC vs TRS (DESIGN §11 open question):** board carries both (X1/X2 BNC + J9 TRS in
   parallel). But which will you actually cable to the scopes? Determines whether to buy
   8× BNC connectors + BNC cables, or 4× TRS jacks + 4 breakout cables (~€2 vs ~€8 per unit,
   and TRS frees ~25 mm of edge). Fit-both costs nothing on the PCB — the money question is
   the cables.
2. **Which TP4056 variant do you own — micro-USB or USB-C?** Changes the east-edge cutout
   and charging-cable logistics only, but must be known before layout (§7).
3. **Do your SuperMini clones really tie VBUS straight to the 5 V pin?** (Beep VBUS pin
   of the USB-C to the 5 V header pin.) If a diode is present on some revision, state 4
   analysis changes (for the better) and D3 might become viable — note it in
   `measurements.md` during §7.
4. **Rprog mod on TP4056 (1 A → 500 mA):** are you willing to swap one 0402/0603 resistor
   per module, or shall we mandate 2 A chargers instead (§4.3 state 2)?
5. **Knob style for RV1** (affects shaft-length variant to order) and whether SW1's
   0.3 A-rated SS12D00 is acceptable or the 1 A variant should be ordered (§5).
6. **Mic orientation:** capsule-up (flat socket) or outward (right-angle socket) — same
   footprint, decide at assembly (§6.1 rule 5), but affects future enclosure design.
