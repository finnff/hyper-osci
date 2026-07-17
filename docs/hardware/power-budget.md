# Power Budget & Battery Analysis

**Status:** estimates pending week-1 measurements (see [§7](#7-measurement-plan-week-1)). Constants and thresholds mirror [`config.h`](../../src/esp32-slave/include/config.h) and [DESIGN.md §9](../DESIGN.md) — those files are law; this doc derives and justifies them.

Every number tagged *est.* below gets replaced by a measured value in week 1. Numbers tagged *datasheet* come from the cited source.

---

## 1. Power chain (recap)

```
LiPo 3.0–4.2 V ──▶ TP4056 module (DW01 + FS8205 protection FETs, ~50 mΩ)
                      OUT+ ──▶ slide switch ──▶ ≥220 µF bulk ──▶ SuperMini "5V" pin
                                                                    │
                                                              onboard LDO (ME6211C33, ⚠️ VERIFY per board)
                                                                    │
                                                              3V3 rail ──▶ ESP32-C3, PCM5102A VIN,
                                                                           MAX4466, pot, LEDs, pull-ups
LiPo+ ──▶ 100k/100k divider ──▶ GPIO1 (always connected, ~21 µA)
```

Key structural fact: the regulator is a **linear LDO**, so battery current ≈ 3.3 V-rail current (plus ~40 µA quiescent). There is no buck conversion gain — battery-life math is simply `usable mAh ÷ rail mA`. The LDO "efficiency loss" shows up as voltage headroom burned off as heat, not as extra current.

---

## 2. Component-by-component current, per mode (3.3 V rail)

All values in mA unless noted. LED math: 3 mm LED at Vf ≈ 2.0–2.1 V through the ~2.2 kΩ series resistor from `config.h` → (3.3 − 2.05)/2200 ≈ **0.57 mA** when lit.

| Component | NETWORK | HYBRID | LOCAL (WiFi off) | Deep sleep | Basis |
|---|---:|---:|---:|---:|---|
| ESP32-C3 core + WiFi RX, modem sleep OFF (`WiFi.setSleep(false)`, DESIGN §2.3) | 95–105 | 95–105 | — | — | datasheet RX 84 mA (radio only) + CPU/peripheral overhead, *est.* |
| ESP32-C3 core only, 160 MHz, I2S + ADC DMA running | — | — | 35–40 | — | [research/esp32-c3-vs-s3.md](../research/esp32-c3-vs-s3.md), *est.* |
| ESP32-C3 deep sleep | — | — | — | 0.005 | datasheet ~5 µA |
| Averaged TX overhead (802.11 ACKs for ~200 pkt/s inbound audio + 1 Hz `HYPE_STATUS`, `STATUS_INTERVAL_MS 1000`) | 3–8 | 3–8 | — | — | *est.*: ~1–2 % TX duty × ~300 mA |
| PCM5102A module (playing 48 kHz, incl. onboard XC6206 LDO) | 19 (29 max) | 19 (29 max) | 19 (29 max) | ~0.5–0.8 (datasheet power-down DVDD) | datasheet: DVDD 8 mA + AVDD/CPVDD ~11 mA typ / 29 mA max |
| MAX4466 mic | 0.03 | 0.03 | 0.03 | 0.03 | datasheet 24 µA typ, <0.5 mA worst-case bound |
| 10 kΩ pot across 3V3 (GPIO3 wiper) | 0.33 | 0.33 | 0.33 | 0.33 | 3.3 V / 10 kΩ, exact |
| LEDs — NET green (`PIN_LED_NET`) | 0.57 (solid) | 0.57 | 0 | 0 | DESIGN §7 semantics |
| LEDs — MODE amber (`PIN_LED_MODE`) | 0 | ~0.3 (1 Hz blink) | 0.57 (solid) | 0 | |
| LEDs — onboard blue heartbeat (active-low, GPIO8) | ~0.05 avg | ~0.05 | ~0.05 | 0 | ~10 % duty, ⚠️ VERIFY clone's series R |
| ME6211 LDO quiescent | 0.04 | 0.04 | 0.04 | 0.04 | datasheet 40 µA typ |
| Battery divider 100k+100k (battery side, not rail) | 0.021 @ 4.2 V | 0.021 | 0.021 | 0.021 | exact: VBAT/200k |
| GPIO2 10 kΩ pull-up, BTN pull-ups | ~0 | ~0 | ~0 | ~0 | no DC sink unless button held |
| **Total (rail ≈ battery)** | **~120–135** | **~120–135** | **~50–60** | **~0.9–1.3** | |
| **Design figure (DESIGN §9)** | **110–125** | **110–125** | **40–50** | — | use these for planning |

Notes:

- **HYBRID ≈ NETWORK.** The mic ADC runs in every mode (vbat/pot share the same `adc_continuous` scan), so mixing it in costs nothing extra.
- **Deep sleep is ~1 mA, not µA**, because the 3V3 rail stays up: the PCM5102A no-clock standby (~0.5–0.8 mA) dominates, then the pot (330 µA), LDO Iq (40 µA), mic (24 µA), divider (21 µA). That is fine — deep sleep only needs to protect the cell for days-to-weeks until recharge, not months. From `VBAT_SLEEP_MV` (3050) down to the DW01 cutoff there is roughly 30–50 mAh left in a 1000 mAh cell → **~1.5–3 weeks of margin. Charge promptly after any low-battery shutdown.** (A future carrier-PCB rev could add a peripheral-power gate FET; out of scope for v1.)

### Worst-case TX bursts

| Event | Peak rail current | Duration | Frequency |
|---|---:|---|---|
| 802.11 ACK (basic rate) | ~300 mA (chip TX) + ~9 mA rest-of-board | tens of µs | ~200/s while streaming |
| `HYPE_STATUS` packet (54 B on the wire: 20 B header + 34 B payload) | same | ≤ ~1 ms at low MCS | 1 Hz |
| WiFi scan/connect/DHCP | same, back-to-back frames | bursts over ~1–3 s | boot + reconnects |
| Absolute worst case | **335 mA** chip TX (802.11b 1 Mbps @ +21 dBm, datasheet) → **~345 mA board** | sub-ms | rare (management frames at basic rate) |

Typical 802.11g/n data-rate TX sits around **276–285 mA** (ESP32-C3 datasheet 802.11g 54 Mbps / 802.11n MCS7 rows). Burst duty is <2 %, so bursts barely move the *average* — their significance is entirely in the **brownout analysis** (§4).

---

## 3. Battery life

80 % usable-capacity derating covers: real capacity < label (typical for hobby LiPo), the firmware cutting to LOCAL at 3.30 V and sleeping at 3.05 V (top ~90 % of the curve only), cell aging, and cold venues. DESIGN §9's table quotes un-derated nominals ("~7.5 h / 1000 mAh"); the figures below are what to actually plan a show around.

| Battery | Usable (×0.8) | NETWORK / HYBRID (120–135 mA) | LOCAL (50–60 mA) |
|---|---:|---:|---:|
| 1000 mAh | 800 mAh | **5.9–6.7 h** | **13.3–16 h** |
| 1500 mAh | 1200 mAh | **8.9–10.0 h** | **20–24 h** |
| 2000 mAh | 1600 mAh | **11.9–13.3 h** | **26.7–32 h** |

Planning guidance:

- A typical evening show (≤ 5 h incl. soundcheck) is safe on **1000 mAh** with ~25 % margin.
- Festival day / all-day installation → **2000 mAh**, or top up over lunch (1.5–3 h full charge, see §5).
- The low-battery ladder itself buys time: at `VBAT_WIFI_OFF_MV` the unit drops to LOCAL and current falls ~60 %, so the *visuals keep running* well past the end of network life. Caveat: below ~3.3–3.4 V VBAT the PCM5102A (behind the double-LDO cascade) is under its recommended supply minimums (CPVDD/DVDD 3.1 V, AVDD 3.0 V), so through the LOCAL low-battery band (3.30–3.05 V) the visuals may lose amplitude or accuracy — watch DAC output amplitude during the battery-rundown test.

---

## 4. Regulator chain analysis

### 4.1 Which LDO is it?

Documented SuperMini designs carry a **ME6211C33M5G** (SOT-23-5): 500 mA max output (spec'd at VIN = 4.3 V), **dropout 100 mV @ 100 mA** (datasheet), Iq 40 µA, VIN 2–6 V. However, cheap clones have been observed with a **250 mA-rated part marked "LLVB"** instead — a real risk for our 345 mA bursts.

**⚠️ VERIFY: read the SOT-23-5 marking on all four owned SuperMinis with a loupe before the PCB order** (week-1 checklist item, also DESIGN §12). If any board carries the 250 mA part, either (a) confirm by measurement that sub-ms 345 mA bursts survive (peak vs continuous rating), or (b) fit a known LDO (e.g. ME6211/HT7833) on the carrier PCB and feed the SuperMini's 3V3 pin directly, bypassing its LDO.

### 4.2 Dropout margin at low battery

Series resistances upstream of the LDO (*est.*): FS8205 dual FET ~50 mΩ + slide switch ~30–50 mΩ + wiring/connectors ~50–100 mΩ + fresh-cell internal resistance ~80–150 mΩ ≈ **0.2–0.35 Ω loop**. ME6211 dropout scales roughly linearly with current (RDS-like): ~100 mV @ 100 mA (datasheet) → ~120 mV @ 120 mA, extrapolated ~330 mV @ 335 mA (⚠️ VERIFY — datasheet only specifies 100 mA).

| Condition | Regulation lost below VBAT ≈ | Consequence below that |
|---|---:|---|
| NETWORK steady 120 mA | 3.3 + 0.12 (dropout) + 0.03 (loop IR) ≈ **3.45 V** | rail tracks VBAT minus drops; C3 still fine (3.0–3.6 V spec) but headroom shrinking |
| TX burst 335 mA | 3.3 + 0.33 (dropout) + 0.10 (loop IR) ≈ **3.73 V** | during each burst the rail dips to ≈ VBAT − 0.43 V |
| LOCAL 47 mA | ≈ **3.36 V** | benign — no bursts exist with radio off |

This is exactly why the `config.h` thresholds sit where they do:

- **`VBAT_WARN_MV 3450`** — the point where the LDO enters dropout under NETWORK steady load. The rail is still healthy, but the operator gets notice (~45–60 min left on 1000 mAh).
- **`VBAT_WIFI_OFF_MV 3300`** — during a 335 mA burst at VBAT = 3.3 V the rail dips toward ≈ 2.87 V, below the 3.0 V WiFi-TX minimum. The C3 IDF default brownout threshold is **2.51 V** (`ESP_BROWNOUT_DET_LVL_SEL_7`; selectable 2.51 / 2.64 / 2.76 / 2.92 / 3.10 / 3.27 V), so a 2.87 V dip does *not* trip the default detector — raising it to `SEL_4` (2.92 V) in sdkconfig is a firmware task if the "brownout catches the sag" reasoning is to hold. Killing the radio at 3.30 V removes the bursts *before* they cause spontaneous resets, and drops draw to LOCAL levels; the `VBAT_WIFI_OFF_MV = 3300` conclusion stands regardless of the brownout level.
- Aged cells or long leads push the loop toward 0.5–1 Ω, adding 170–335 mV of burst sag — another reason the radio-off threshold is deliberately conservative.

### 4.3 Why the ≥220 µF bulk capacitor is required

The SuperMini and its LDO carry only small ceramics (~1–10 µF). A 300+ mA TX burst is a current step with sub-10 µs rise time; across the 0.2–1 Ω battery loop (plus lead inductance) that means an instantaneous 60–335 mV dip **at the LDO input**, stacked on top of dropout, precisely when headroom is scarcest. The ≥220 µF electrolytic across the battery rail (placed **after** the slide switch, at the SuperMini 5V pin, so it is switched with the load and doesn't defeat the switch):

- sources the first ~50–100 µs of each burst while the cell's chemistry and lead inductance catch up (220 µF supplying 300 mA sags ~1.4 mV/µs — it comfortably covers the transient window);
- halves the effective source impedance at WiFi-burst timescales;
- damps the L·di/dt spike from battery leads.

220 µF is the floor, not a target — if week-1 scope measurements (§7 item 4) still show >100 mV of rail dip during WiFi connect at VBAT = 3.5 V, go to 470 µF. This mirrors Espressif's long-standing guidance for battery-fed ESP designs.

---

## 5. TP4056 / DW01 behavior and the firmware threshold ladder

The 03962A module combines a TP4056 linear charger with DW01 + FS8205 protection.

**Charging (TP4056):** CC/CV profile, CV = **4.2 V ±1 %**, charge current set by RPROG — the module default is 1.2 kΩ → **1 A** (⚠️ VERIFY the actual RPROG on the owned modules). Termination at C/10 (~100 mA), auto-recharge when the cell relaxes ~150 mV below 4.2 V. 1 A into a 1000 mAh cell is 1C — acceptable but warm; if the small cells are used long-term, swapping RPROG to 2.4 kΩ (≈ 0.5 A, 0.5C) is kinder. Full charge takes ~1.5 h (1000 mAh) to ~2.5–3 h (2000 mAh).

**Protection (DW01):** overcharge cut ~4.3 V; overcurrent ~3 A (150 mV across the FS8205's ~50 mΩ) — irrelevant at our loads; over-discharge cutoff **~2.40 V**.

**2.4 V is a last-resort fuse, not a usable floor.** Discharging LiPo below ~3.0 V resting causes permanent capacity loss and, if repeated or left sitting, copper anode dissolution → internal shorts. The DW01 exists to prevent fire, not to preserve the cell. So the firmware enforces its own ladder, ~0.6 V above the hardware cutoff (`config.h`, measured at the ADC i.e. **under load**):

| Threshold | Value | Rationale |
|---|---|---|
| `VBAT_WARN_MV` | 3450 mV | ≈ 15–20 % SoC under load; coincides with LDO-dropout onset in NETWORK (§4.2); operator sees the blue-LED 3-blink pattern with ~45–60 min of NETWORK runtime left (1000 mAh) |
| `VBAT_WIFI_OFF_MV` | 3300 mV | ≈ 5–10 % SoC; eliminates TX-burst brownout risk (§4.2); current drops ~60 %, stretching what remains; show degrades gracefully to LOCAL instead of dying |
| `VBAT_SLEEP_MV` | 3050 mV | knee of the discharge curve — below this, voltage falls off a cliff anyway; deep sleep (~1 mA, §2) lets the cell rest and recover to ~3.1 V, leaving ~1.5–3 weeks of margin above the DW01's 2.4 V |
| `VBAT_HYSTERESIS_MV` | 50 mV | load release (e.g. WiFi turning off) lifts VBAT by I·R ≈ 30–60 mV; without hysteresis the state machine would oscillate across a threshold |

---

## 6. Charging rules

- **Breadboard / bring-up: charge ONLY with the power switch OFF.** Two independent reasons:
  1. The TP4056 terminates on charge *current*. With the system drawing 45–125 mA through OUT+ during charge, current never falls to C/10 → the charger holds the cell at 4.2 V indefinitely (stress) or cycles falsely; WiFi bursts additionally make the CV loop unstable.
  2. Most SuperMini clones tie USB VBUS straight to the 5V pin (DESIGN §9) — never have the switch ON with the SuperMini's own USB-C plugged in either, or VBUS back-feeds the battery rail. Flash/debug on USB with the switch OFF.
- **Carrier PCB: the rule disappears.** The board adds a load-sharing power path (P-FET + Schottky, both VBUS sources diode-ORed into the gate) so USB powers the load directly while the TP4056 sees only the cell. Circuit detail lives in [pcb.md](pcb.md) — the rule stated here is the requirement it must satisfy.
- **Off-state drain:** the divider (~21 µA) plus TP4056-module leakage (~2–5 µA) totals well under LiPo self-discharge; a switched-off unit keeps its charge for months. Don't store units that went into low-battery deep sleep, though — recharge those within a week or so (§2 note).

---

## 7. Battery voltage sensing

Hardware: LiPo+ → 100 kΩ / 100 kΩ divider (`VBAT_DIVIDER 2.0f`) → `PIN_VBAT_ADC` = GPIO1 = ADC1_CH1, with 100 nF from the pin to GND across the bottom resistor.

| Quantity | Value |
|---|---|
| VBAT range of interest | 3.00 – 4.20 V |
| Voltage at ADC pin | **1500 – 2100 mV** |
| Required attenuation | **11 dB** (`ADC_ATTEN_DB_11`; named `ADC_ATTEN_DB_12` in newer IDF 5.x — same setting). On the C3 its recommended measurable range is ~0–2500 mV, the only setting that covers 2100 mV; 6 dB (ATTEN2) tops out at 1300 mV on the C3. ⚠️ VERIFY exact range table for the IDF version pinned in platformio.ini |
| Conversion | `vbat_mv = cal_mv(raw) × 2` → fills `HypeStatusPayload.vbat_mv` |
| Sampling | scanned as 1 of 3 channels by `adc_continuous` at 24 kHz (config.h §Audio); firmware decimates/averages heavily (≥ 1 s window) before comparing against thresholds |
| Divider current | VBAT/200 kΩ = 21 µA @ 4.2 V, 15 µA @ 3.0 V — always connected |

**Why the 100 nF matters:** the divider's AC source impedance is 100k‖100k = 50 kΩ — far too high for the ADC's sample-and-hold to charge accurately in one multiplexed slot. The cap is a local charge reservoir that makes the source look stiff at the sampling instant, and forms a ~32 Hz low-pass (1/(2π·50k·100n)) that kills WiFi-burst ripple on the reading. Side benefit of the 100 kΩ top resistor: with the unit switched off, current injected into the unpowered GPIO1 is limited to <25 µA — below any latch-up concern.

**Calibration:** use the eFuse curve-fitting scheme (`adc_cali_create_scheme_curve_fitting`) — every C3 ships with factory ADC cal. Residual error stack: ±2 % from 1 % divider resistors (±69 mV at the warn threshold) + a few mV ADC residual. Against a 150 mV threshold spacing that is marginal, so **calibrate per unit at bring-up**: measure real VBAT with a DMM at ~3.7 V and ~4.2 V, compute a per-unit scale factor `VBAT_dmm / vbat_reported`, store it in NVS via the serial console. (Alternative: fit 0.1 % divider resistors and skip the ceremony.)

---

## 8. Measurement plan (week 1)

Goal: replace every *est.* in this file with a measured number and close DESIGN §12 checklist items 4 & 6. Tools: USB power meter, bench supply, DMM, scope, 0.1 Ω shunt resistor.

| # | Measurement | Method | Replaces |
|---|---|---|---|
| 1 | LDO identity on all 4 SuperMinis | loupe/photo of the SOT-23-5 marking (ME6211 = "S2QB"-style Microne code; beware "LLVB" = 250 mA part) | §4.1 ⚠️ |
| 2 | Average current: LOCAL idle, LOCAL playing, NETWORK streaming, HYBRID | bench supply at 3.70 V into the 5V pin (battery out, switch off); read supply's mA display, cross-check with DMM. **Caveat:** DMM mA-range burden (~1–2 Ω) distorts the rail — use the 10A range or a 0.1 Ω shunt + mV reading. USB power meter only as a coarse cross-check (linear LDO ⇒ its mA ≈ rail mA, but ~10 mA resolution and it can't see bursts) | §2 table, §3 battery lives |
| 3 | TX burst profile (peak mA, duration, repetition) | scope across 0.1 Ω shunt in the supply lead during streaming and during WiFi connect | §2 burst table |
| 4 | Rail droop / brownout onset | supply sweep 4.2 → 2.8 V in 0.1 V steps under NETWORK load; scope 3V3 rail AC-coupled during WiFi connect at 3.5 V and 3.3 V, **with and without the 220 µF**; record the VBAT at which the unit resets | §4.2 dropout math, §4.3 cap sizing, DESIGN §12 item 4 |
| 5 | ME6211 dropout curve | supply into 5V pin, fixed resistive load steps (50/120/300 mA), record VIN at which 3V3 sags 1 % | §4.2 extrapolation ⚠️ |
| 6 | Real usable capacity + threshold validation | full 4.2 V charge, run NETWORK streaming, log `vbat_mv` from 1 Hz status packets until deep sleep; integrate runtime × current; **watch DAC output amplitude during the battery-rundown test** (PCM5102A drops below its recommended supply minimums through the LOCAL low-battery band — §3) | §3 table, §5 ladder |
| 7 | Divider/ADC accuracy | DMM VBAT vs reported `vbat_mv` at ~3.0/3.3/3.7/4.2 V per unit → NVS scale factors | §7 calibration |
| 8 | TP4056 module RPROG & charge behavior | read RPROG value; measure charge current into a half-empty cell, confirm termination and charge time | §5 ⚠️ |
| 9 | Deep-sleep system current | µA-capable meter in supply lead after triggering `VBAT_SLEEP_MV` path (or a test command) | §2 deep-sleep column |
| 10 | Per-module currents (PCM5102A playing/idle, MAX4466) | modules powered individually on breadboard through the shunt | §2 rows |

---

## Sources

- [ESP32-C3 Series Datasheet (Espressif)](https://www.espressif.com/sites/default/files/documentation/esp32-c3_datasheet_en.pdf) — TX 335 mA @ 802.11b 1 Mbps +21 dBm; RX 84 mA (radio, HT20); deep sleep ~5 µA.
- [ME6211 datasheet (Microne)](https://stm32-base.org/assets/pdf/regulators/ME6211.pdf) — 500 mA max (VIN 4.3 V), dropout 100 mV @ 100 mA, Iq 40 µA, VIN 2–6 V.
- [Super Mini ESP32-C3 board analysis (sigmdel.ca)](https://sigmdel.ca/michel/ha/esp8266/super_mini_esp32c3_en.html) — ME6211 on documented boards; clone variants with 250 mA "LLVB" regulator observed.
- [docs/research/esp32-c3-vs-s3.md](../research/esp32-c3-vs-s3.md) — C3 active-mode current estimates carried into §2.
- TP4056 / DW01A / FS8205A datasheets (module behavior in §5; well-established figures, RPROG on the actual modules still ⚠️ VERIFY).
