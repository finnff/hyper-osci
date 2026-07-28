# HYPEROSCI — Slave Unit Wiring Guide

**Scope:** one slave unit, breadboard bring-up. Every **signal** net here matches the carrier
PCB one-to-one, so the breadboard build *is* the schematic rehearsal for everything the
firmware touches. The **power chain is not** one-to-one — the carrier adds the load-sharing
path, which splits and renames things:

| Breadboard | Carrier | Note |
|---|---|---|
| `VBAT_RAW` | `BAT_PLUS` | same node: cell + / TP4056 `B+`, always live, R1 taps it here |
| `VBAT_SW` | `VSW` → SW1 → `VLOAD` | the switch now sits *downstream* of Q1; `VSW` is live off the cell whatever the switch position |
| — | `VBAT_OUT` | TP4056 `OUT+` → Q1 drain (+ JP1) |
| — | `GATE`, `VBUS_CHG`, `VSW_SENSE`, `TL431_K`, `Q2_B` | exist only on the carrier |

The breadboard has none of Q1/Q2/U1/D1/D2/D3/JP1, so §6's rules below are the *breadboard*
rules — see [pcb.md](pcb.md) §4.5 for which of them the carrier actually retires.
**Canon:** pins and constants come from [`src/esp32-slave/include/config.h`](../../src/esp32-slave/include/config.h)
and [DESIGN.md §4/§5/§9](../DESIGN.md). If anything below disagrees with those files, those files win.

Related docs: [power-budget.md](power-budget.md) (current/runtime derivation), [pcb.md](pcb.md)
(carrier board, load-sharing power path, full BOM).

---

## 1. Nets overview

| Net | Description | Voltage |
|-----|-------------|---------|
| `VBAT_RAW` | LiPo+ / TP4056 `B+` — always live | 3.0–4.2 V |
| `VBAT_SW` | after power switch, feeds SuperMini `5V` pin | 3.0–4.2 V (or 5 V from USB-C when plugged, switch OFF) |
| `3V3` | SuperMini onboard LDO output — powers *everything else* | 3.30 V |
| `GND` | single common ground (battery, logic, analog, scope) | 0 V |
| `VBAT_SENSE` | 100k/100k divider midpoint → GPIO1 | VBAT/2 = 1.5–2.1 V |

Everything except the SuperMini itself runs from `3V3`. Nothing else touches `VBAT_SW`.

## 2. Full connection table

Wire-by-wire. "SM" = ESP32-C3 SuperMini (go by **silkscreen labels**, not physical position —
silk layouts vary between clone batches; some boards print `RX`/`TX` instead of `20`/`21`.
On the ESP32-C3, silk `RX` **is** GPIO20 and silk `TX` **is** GPIO21).

### 2.1 Power chain

| # | From | To | Notes |
|---|------|----|-------|
| P1 | LiPo `+` (JST red) | TP4056 `B+` | battery pads, silk-labeled on module |
| P2 | LiPo `−` (JST black) | TP4056 `B−` | |
| P3 | TP4056 `OUT+` | power switch, terminal A | slide switch, either outer terminal + center |
| P4 | Power switch, terminal B | SM `5V` pin | this is net `VBAT_SW` |
| P5 | TP4056 `OUT−` | `GND` rail | |
| P6 | SM `GND` pin | `GND` rail | |
| P7 | SM `3V3` pin | `3V3` rail | source for all peripherals |
| P8 | Bulk cap ≥220 µF `+` | SM `5V` pin (`VBAT_SW`) | electrolytic, **observe polarity**, `−` to `GND`; buffers WiFi TX bursts |

TP4056 module pad positions differ between clone batches — always go by the silk
(`B+ B− OUT+ OUT−`, USB input on the connector end). Confirm yours is the **protected** variant: two extra ICs (DW01 6-pin + FS8205 8-pin) near
the output pads. The owned modules are the **USB-C blue** boards — `CSM4056T` + `8205LA`
(FS8205) + DW01-type, confirmed 2026-07-18. (`03962A` is the older micro-USB part number.)

### 2.2 Battery sense (GPIO1)

| # | From | To | Notes |
|---|------|----|-------|
| S1 | TP4056 `B+` (`VBAT_RAW`) | R_top 100 kΩ → node `VBAT_SENSE` | tap the **battery** side, *before* the switch — always connected, ~21 µA drain |
| S2 | Node `VBAT_SENSE` | R_bot 100 kΩ → `GND` | |
| S3 | Node `VBAT_SENSE` | C 100 nF → `GND` | across the bottom resistor, ADC sampling reservoir |
| S4 | Node `VBAT_SENSE` | SM `GPIO1` | ADC1_CH1, `PIN_VBAT_ADC` |

The 100 kΩ source impedance limits back-injection into the unpowered chip (switch OFF) to
~15 µA — harmless, this is the standard always-on divider arrangement.

### 2.3 I2S DAC (GY-PCM5102A)

| # | From | To | Notes |
|---|------|----|-------|
| D1 | SM `GPIO4` | PCM5102A `BCK` | `PIN_I2S_BCK`, 1.536 MHz at 48 kHz/16-bit stereo |
| D2 | SM `GPIO5` | PCM5102A `LCK` | `PIN_I2S_LRCK`, 48.0 kHz |
| D3 | SM `GPIO6` | PCM5102A `DIN` | `PIN_I2S_DOUT` |
| D4 | `3V3` rail | PCM5102A `VIN` | feeds the module's onboard XC6206 LDO (fine, see DESIGN §5) |
| D5 | `GND` rail | PCM5102A `GND` | |
| D6 | PCM5102A `SCK` | `GND` | **required** — internal PLL clock from BCK; see §4 |
| D7 | PCM5102A `LROUT` (1×9 hdr) — or jack **tip** | Scope CH1 (X) | see §5 |
| D8 | PCM5102A `ROUT` (1×9 hdr) — or jack **ring** | Scope CH2 (Y) | see §5 |
| D9 | PCM5102A `AGND` (1×9 hdr) — or jack **sleeve** | Scope GND clips | common with system `GND` |

Keep the three I2S wires short (<10 cm) and away from the mic — BCK is a 1.5 MHz square wave.

### 2.4 Microphone (MAX4466)

| # | From | To | Notes |
|---|------|----|-------|
| M1 | `3V3` rail | MAX4466 `VCC` | |
| M2 | `GND` rail | MAX4466 `GND` | |
| M3 | MAX4466 `OUT` | SM `GPIO0` | `PIN_MIC_ADC`, ADC1_CH0, idles at ~VCC/2 ≈ 1.65 V |

Set the back-side gain trimmer to mid-travel to start; fine-tune later while watching the
scope. ⚠️ VERIFY: trimmer rotation direction vs. gain varies between module clones — find it
empirically. Point the electret capsule outward/away from the boards.

**Mounting:** the MAX4466 rides on a short ~10 cm pigtail — three jumper wires (`VCC` / `GND` /
`OUT`, nets M1–M3) — that exits the enclosure; it is **not** soldered flat to the carrier. This
lets you aim the capsule at the PA and reach the gain trimpot with a screwdriver after assembly.
The carrier provides a matching 3-pin pigtail landing/header (`VCC` / `GND` / `OUT`), not a flat
on-board footprint.

### 2.5 UI: pot, button, LEDs, GPIO2 pull-up

| # | From | To | Notes |
|---|------|----|-------|
| U1 | Pot pin 3 (CW end) | `3V3` rail | 10 kΩ pot = **RV097NS-B10K** (5-pin **mono with switch**, right-angle; metal shaft turned by hand — no knob). Pins 4/5 are the SPST, not lugs: both to GND, unused |
| U2 | Pot pin 1 (CCW end) | `GND` rail | this orientation ⇒ clockwise = higher voltage = higher Y-filter cutoff |
| U3 | Pot pin 2 (wiper) | SM `GPIO3` | `PIN_POT_ADC`, ADC1_CH3 |
| U4 | Mode button, leg A | SM `GPIO7` | `PIN_BTN_MODE`; internal pull-up in firmware — **no external resistor** |
| U5 | Mode button, leg B | `GND` rail | 6×6 mm tactile: use two legs on the *same* switch side, or diagonal legs |
| U6 | SM `GPIO10` | 2.2 kΩ → green LED anode | `PIN_LED_NET`, active high |
| U7 | Green LED cathode | `GND` rail | flat side / short leg to GND |
| U8 | SM `GPIO20` (silk may say `RX`) | 2.2 kΩ → amber LED anode | `PIN_LED_MODE`, active high |
| U9 | Amber LED cathode | `GND` rail | |
| U10 | SM `GPIO2` | 10 kΩ → `3V3` rail | strapping pin, must be high at reset; **nothing else** on this pin |

2.2 kΩ gives ~0.5 mA per LED — deliberately dim for battery life; modern high-efficiency
3 mm LEDs are clearly visible. If yours are too dim, 1 kΩ is fine (config.h says "~2.2k").

### 2.6 Not wired (reserved / onboard)

| Pin | Status |
|-----|--------|
| SM `GPIO8` | onboard blue LED (active-low heartbeat) — leave the pin unconnected |
| SM `GPIO9` | onboard BOOT button (test-pattern cycling after boot) — leave unconnected |
| SM `GPIO21` (silk may say `TX`) | reserved UART0 TX debug header — leave unconnected for now |
| TP4056 USB connector | charge input only, own cable; see rules in §6 |
| SM USB-C | flash/debug/console only; see rules in §6 |

**Wire count sanity check:** 8 power + 4 sense + 6 DAC digital + 3 DAC analog + 3 mic +
3 pot + 2 button + 4 LED + 1 pull-up ≈ **34 connections**.

## 3. Overall wiring diagram

The wiring diagram is a real KiCad schematic, generated from the same netlist source of
truth as the carrier PCB (`hw/carrier/tools/design.py`) and gated by the same checks
(`check_netlist.py` against `config.h`, ERC, DRC schematic parity) — unlike the ASCII art
that used to live here, it cannot silently drift from the board or the firmware pin map:

![HYPEROSCI slave unit wiring diagram](../../hw/carrier/carrier-schematic.svg)

Printable A3: [`hw/carrier/carrier-schematic.pdf`](../../hw/carrier/carrier-schematic.pdf).
Regenerate after any `design.py` change: `python3 tools/gen_schematic.py`, then re-export
the SVG/PDF per [`hw/carrier/layout-notes.md`](../../hw/carrier/layout-notes.md).

Reading it as a *breadboard* guide: everything **except** the dashed "LOAD-SHARING POWER
PATH" block is wired 1:1 on the breadboard (the §2 tables above stay the wire-by-wire
authority). The breadboard power chain is `OUT+ → switch → SuperMini 5V pin` direct — see
the "BREADBOARD DELTA" note on the sheet and the net-name mapping table at the top of this
file (`VBAT_RAW` = `BAT_PLUS`, `VBAT_SW` ≈ `VLOAD`).

## 4. GY-PCM5102A module prep (do this FIRST, before any wiring)

The purple GY-PCM5102 modules **often ship with all four back-side solder bridges open**.
With bridge 3 (XSMT) open/low the DAC is *hard-muted* — the #1 cause of "no output".
Solder all four before first power-up:

| Bridge | Function | Set to | Meaning |
|--------|----------|--------|---------|
| 1 | FLT | **L** | normal-latency FIR filter (revisit H = low-latency during §12 ringing test) |
| 2 | DEMP | **L** | de-emphasis off |
| 3 | XSMT | **H** | soft-mute released — DAC actually outputs |
| 4 | FMT | **L** | standard I2S format |

Each bridge is three pads in a row: center pad plus `H` and `L` pads. Blob solder from
**center to L** on bridges 1, 2, 4 and **center to H** on bridge 3. Inspect with
magnification — a bridge accidentally spanning all three pads shorts 3V3 to GND on that pin.

**SCK to GND (front side):** the DAC then generates its clock internally via PLL from BCK —
no MCLK from the ESP32 needed. Two options:

1. Breadboard: simply jumper the `SCK` header pin to `GND` (wire D6). Always works.
2. Many purple boards have a small two-pad solder jumper on the front near the SCK pin that
   ties SCK to GND through the board. ⚠️ VERIFY with continuity (SCK↔GND = 0 Ω after
   soldering) before trusting it — pad presence varies between batches.

On the carrier PCB the SCK socket pin is tied straight to the ground plane.

**After prep, before install:** continuity check SCK↔GND (short), and no short VIN↔GND.

## 5. PCM5102A → oscilloscope

| PCM5102A | Scope | Signal |
|----------|-------|--------|
| `LROUT` — 1×9 header pin, or 3.5 mm jack **tip** | CH1 | X |
| `ROUT` — 1×9 header pin, or jack **ring** | CH2 | Y |
| `AGND` — 1×9 header pin, or jack **sleeve** | probe GND clips | common ground |

- **Analog header (measured 2026-07-18):** the purple module's analog end is a **1×9** header
  on the long edge (⊥ to the 6-pin I2S row), silk (jack→digital):
  `LROUT AGND ROUT AGND A3V3 FMT XSMT DEMP FLT`. Tap **X = LROUT, Y = ROUT, gnd = AGND**. It is
  **not** a 3-pin `L G R` header — the old docs assumed wrong.
- **Deliverable output path (carrier board):** the carrier has *no* BNC or TRS jack. It
  exposes two RCA flying-lead solder pad-pairs (signal + ground), fed through the series
  R10/R11 positions — **fitted as 0 Ω links** (pcb.md §3.2 / §10 Q2, closed 2026-07-28):
  **X = PCM5102A LROUT**, **Y = PCM5102A ROUT**. Signal chain is
  board RCA pad → reused ~50 cm RCA cable (RCA male, salvaged from the old sigma-delta units)
  → BNC→RCA adapter already fitted on the scope → scope CH, in XY mode.
- **Bench bring-up shortcut (recommended for W1–W2):** you can skip the carrier RCA pads
  entirely and drive the scopes straight from the module's on-board **3.5 mm LINE OUT jack** —
  tip = X (LROUT), ring = Y (ROUT), sleeve = GND — via a 3.5 mm→2×RCA (or →2×BNC) cable. This
  needs no carrier at all, so early testing isn't gated on the PCB. Final units use the RCA pads.
- Note: the module carries a ~470 Ω "471" output filter before these pins/jack (measured) —
  the output is still ground-centered. **R10/R11 is settled: fit 0 Ω links** (pcb.md §10 Q2,
  closed 2026-07-28). Do not spend bench time deciding it on the ramp — the module's own
  470 Ω swamps a 100 Ω part, which moves amplitude by 0.01 % and phase at 10 kHz by ≲0.07°,
  and does so identically on both channels, so the test has nothing to show.
- Scope setup: **XY mode**, CH1 = X, CH2 = Y, both **DC coupled**, both **1 MΩ** input
  (never 50 Ω termination — the line-out stage can't drive it); on the bench use ×1 probes,
  on the finished unit the reused RCA cable into the scope's BNC→RCA adapter. 1 V/div, traces
  centered.
- Expected full-scale output: **≈2.1 V RMS ≈ ±3 V peak (~6 Vpp) per channel**, ground-centered
  and DC-coupled (the chip's internal charge pump makes a negative rail — no DC offset to
  fight, a full-scale circle sits centered on the graticule).
- Don't hang headphones/speakers on the outputs while scoping; the scope's 1 MΩ is the
  intended load.

## 6. Power chain and safety rules (DESIGN §9)

Breadboard power chain, in order:

1. LiPo → TP4056 `B+`/`B−` (charge via its own USB; DW01 protection) → `OUT+`
2. `OUT+` → slide switch → SuperMini `5V` pin → onboard LDO → `3V3` rail
3. `3V3` → PCM5102A `VIN`, MAX4466 `VCC`, LEDs, pot, pull-ups
4. LiPo+ → 100k/100k divider → GPIO1 (always connected, ~21 µA)

**RULE 1 — the breadboard rule: NEVER have the power switch ON while the SuperMini USB-C is
plugged in.** Most SuperMini clones tie VBUS straight to the `5V` pin, so USB 5 V would
back-feed through the switch into TP4056 `OUT+` and push uncontrolled current into the LiPo,
bypassing the charger's CC/CV control. Flash and debug on USB **with the switch OFF** (the
whole circuit runs happily from USB: VBUS → 5V pin → LDO → 3V3).

**This rule does not automatically retire on the carrier.** The load-sharing path is meant to
remove it, but design review (2026-07-26) found that the SuperMini-side term — a voltage
threshold on the load node — cannot reliably distinguish a sagging laptop port from a charged
cell, and that this is exactly the situation the rule exists for. So: **keep RULE 1 until
pcb.md §4.4 passes on an assembled carrier**, and keep it permanently on a JP1-bridged board.
(Separately, and about the *charger's* USB rather than this one: in a JP1 build with D2 still
fitted, plugging the charger injects into the cell upstream of the switch, so RULE 2 below
does not protect you either — D2 must be omitted. See [pcb.md](pcb.md) §4.4/§4.5.)

**RULE 2 — charge with the switch OFF.** With a load on `OUT+`, the TP4056 can't tell load
current from charge current and may never terminate; the cell also sits at 4.2 V under load.
Charging via the TP4056's own USB with the switch OFF is the clean state.

**RULE 3 — one USB at a time is the safe habit.** TP4056-USB (switch OFF) = charging.
SuperMini-USB (switch OFF) = development. Switch ON with no USB = normal battery operation.

Practical bench habits:

- Put the switch where you can see it; flip it OFF before reaching for either USB cable.
- The GPIO1 divider is wired to `VBAT_RAW`, so battery voltage telemetry works even while
  running from USB with the switch OFF — handy for watching a charge cycle.
- All GNDs are one net, including scope probe grounds. When the scope (earthed) and a
  desktop PC (earthed) are both attached you've made a ground loop; at these signal levels
  it's harmless, but pure battery + scope is the clean measurement setup.

## 7. Breadboard layout tips

- Give the SuperMini the end of the breadboard with its **antenna overhanging the edge**;
  keep all wiring, the pot, and your fingers away from the antenna end.
- Power rails: top rail = `3V3`, bottom rail = `GND`. `VBAT_SW` only exists on the short
  jumper switch → `5V` pin, plus the bulk cap.
- Mic far from the DAC's I2S wires and from the switch-mode charge pump area of the
  PCM5102A module; mic wiring short.
- TP4056 modules have no breadboard pins — solder a 4-wire pigtail (`B+ B− OUT+ OUT−`) or
  a right-angle header. Same for the LiPo JST: use a JST-PH pigtail, never bare-wire the cell.
- The pot's 3 pins rarely fit a breadboard cleanly — pigtail it too.

## 8. Bring-up checklist

Order is deliberate: **rails → DAC → mic → WiFi**. Don't skip ahead; each phase assumes the
previous one measured clean. DMM required; scope required from Phase 4.

### Phase 0 — module prep (nothing powered)

1. PCM5102A: solder the 4 back bridges + SCK↔GND per §4.
   - **Measure:** continuity SCK↔GND = 0 Ω; VIN↔GND = open (no short); visual: no bridge
     spans all three pads.
2. TP4056: confirm protected variant (DW01 + FS8205 present near output pads).
   - Note: stock PROG resistor (1.2 kΩ) ⇒ ~1 A charge = ~0.5C on the selected 2000 mAh cell
     (EEMB LP103454) — comfortable, full charge ~2.5–3 h. Mandate 5 V/2 A wall chargers;
     the R_PROG swap is optional (fiddly to solder). On legacy 1000 mAh cells 1 A = 1C (warm)
     — swap R_PROG (2 kΩ ≈ 580 mA, 3 kΩ ≈ 400 mA).
3. MAX4466: gain trimmer to mid-travel.
4. LEDs: identify anode (long leg) before wiring; a DMM diode-test lights them.

### Phase 1 — battery + charger (no ESP32 yet)

1. LiPo → `B+`/`B−`. **Measure `OUT+`↔`OUT−`: 3.0–4.2 V**, equal to the cell voltage
   (protection FETs drop only millivolts).
2. Plug USB into the **TP4056**: red/CHRG LED on. Leave until blue/green (STDBY) if the
   cell is low. **Measure:** cell climbs toward 4.20 V ± 0.05 and terminates there.
3. Unplug TP4056 USB before continuing.

### Phase 2 — bare power rails

1. Wire P3–P8 (switch, SuperMini, bulk cap). No peripherals yet. USB-C **unplugged**.
2. Switch **ON**. **Measure:** SM `5V` pin = battery voltage; SM `3V3` pin = 3.30 V ± 0.1;
   nothing warm to the touch.
3. Optional: DMM (mA range) in series with the switch — a factory-fresh SuperMini draws a
   few tens of mA. Record the number; it's your baseline.
4. Switch **OFF**.

### Phase 3 — full wiring, quiescent checks

1. Wire everything else in §2 (DAC, sense divider, mic, pot, button, LEDs, GPIO2 pull-up).
2. Power from **USB-C, switch OFF**. Measure with the DMM (DC volts, black lead on GND):

   | Node | Expect | Meaning if wrong |
   |------|--------|------------------|
   | `3V3` rail | 3.30 V ± 0.1 | short/overload on a peripheral if it sags |
   | PCM5102A `VIN` | = `3V3` | wiring |
   | MAX4466 `OUT` (GPIO0) | ≈ 1.65 V DC (VCC/2) | dead mic module or missing VCC |
   | `VBAT_SENSE` (GPIO1) | = cell voltage ÷ 2 (≈1.9 V at 3.8 V) | divider values / tap point |
   | GPIO2 | 3.3 V | pull-up missing (boot will be unreliable) |
   | Pot wiper (GPIO3) | 0 → 3.3 V smoothly over full rotation | dirty/miswired pot |
   | GPIO7 | 3.3 V idle, 0 V pressed (after firmware enables pull-up) | button wiring |
   | PCM5102A `LROUT`/`ROUT` | ≈ 0 V DC | (no I2S clocks yet — anything near 0 V is fine here) |

### Phase 4 — flash + DAC test (circle pattern)

1. **Switch OFF**, USB-C plugged. Flash the slave firmware (PlatformIO, `esp32-c3-devkitm-1`).
   USB-CDC console shows the boot banner; onboard blue LED starts its 0.5 s heartbeat.
2. Probe the I2S bus (normal Y-T mode first):
   - **`GPIO5`/LCK: 48.0 kHz square, ~50 %** duty.
   - **`GPIO4`/BCK: 1.536 MHz** (48 kHz × 32, 16-bit stereo — the PLL ratio DESIGN §5
     requires).
   - `GPIO6`/DIN: data activity.
3. Short-press **BOOT** (onboard) to cycle the local test pattern to **circle** (DESIGN §7:
   mic → circle → Lissajous).
4. Scope in **XY mode** per §5. **Expect: a stable, centered circle.** At digital full scale
   the diameter is ~6 V (±3 V peak per axis); if the firmware's test amplitude is e.g. 0.8 FS,
   expect ±2.4 V. Verify:
   - Circle centered on (0,0) with both channels DC-coupled ⇒ confirms ground-centered,
     DC-coupled output (closes part of DESIGN §12 item 1 — also run the slow-ramp DC test
     when the firmware exposes it).
   - Clean, no gaps/jumps ⇒ PLL locked at 32× fs (closes §12 item 2).
   - In Y-T mode: CH1 and CH2 are equal-amplitude sines, 90° apart.
5. Cycle BOOT again for Lissajous; sharp-cornered patterns are the vehicle for the
   FLT=L vs FLT=H ringing comparison (§12 item 3) later.
6. No output? Checklist: XSMT bridge 3 actually soldered to **H** → SCK actually grounded →
   BCK/LCK swapped (most common wiring error) → bridge 4 FMT on L.

### Phase 5 — mic + pot (LOCAL mode)

1. Hold **MODE** (GPIO7 button) while resetting/power-cycling ⇒ boots straight into LOCAL,
   WiFi off (amber LED solid; DESIGN §7). Or just wait ~6 s for auto-fallback.
2. Scope XY: clap / whistle / play music at the mic. **Expect:** X deflection following the
   raw audio, Y showing only low-frequency content — the old proven unit's behavior, now
   at ±3 V instead of sigma-delta.
3. Sweep the pot lock-to-lock. **Expect:** Y-channel bandwidth audibly/visibly changes
   (cutoff 20 Hz fully CCW → 300 Hz fully CW). If the sense is reversed, swap pot ends
   (U1/U2).
4. Adjust MAX4466 gain trimmer so normal program material deflects most of the screen
   without clipping (clipping = flat-topped X extremes).
5. Battery telemetry: console/status should report `vbat_mv` ≈ 2 × the GPIO1 voltage you
   measured in Phase 3, within a few tens of mV.

### Phase 6 — WiFi / NETWORK mode

1. Bring up the AP (`HYPEROSCI_AP` / `hyperosci2026`, **2.4 GHz** — the UNO-Q's
   NetworkManager connection `hyperosci-ap` at 192.168.50.1/24, or any temporary
   2.4 GHz AP for a first test).
2. Reboot the slave in default mode. **Expect LED sequence (DESIGN §7):** green NET LED
   1 Hz blink (connecting/no stream) → **solid** once audio packets flow on UDP 5000 →
   5 Hz blink if you kill the stream (fallback active within ~1 s).
3. Stream a test signal from the controller; verify the scope pattern follows it and that
   killing the AP drops the unit back to the mic pattern in ~1 s (STREAM_TIMEOUT_MS).
4. **Measure current** (DMM in series with the switch, battery power, USB unplugged —
   switch ON only after unplugging!):
   - LOCAL, WiFi off: **~40–50 mA** @ 3.7 V
   - NETWORK, streaming (`WiFi.setSleep(false)`): **~110–125 mA**
   Record actuals in [power-budget.md](power-budget.md) (DESIGN §12 item 6).
5. While streaming, probe the `3V3` pin on the scope (AC-coupled, ~50 mV/div): WiFi TX
   bursts should not dip the rail more than ~100 mV. Repeat near VBAT = 3.5 V for the
   brownout check (DESIGN §12 item 4).

### Phase 7 — full battery soak

1. Everything off USB. Switch ON. Run NETWORK mode from the battery for ≥30 min.
2. **Expect:** stable circle/stream, no LED low-battery pattern above 3.45 V cell voltage,
   `vbat_mv` in status packets tracking the DMM within ~50 mV.
3. Done — this unit is the reference for cloning the other three, and its module outline
   caliper measurements (DESIGN §12 item 7) feed the PCB layout.

---

*Anything marked ⚠️ VERIFY must be checked on the physical modules before the PCB order
(~Aug 1). Deviations found go into DESIGN.md first, then back-propagate here.*
