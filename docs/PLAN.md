# HYPEROSCI — 5-Week Execution Plan

**Today:** 2026-07-17 · **Target:** 2026-08-21 (4 assembled units + rehearsal)
**Canon:** [DESIGN.md](DESIGN.md) — anything here that conflicts with it is a bug in this file.

## Critical path

```
W1: close DESIGN §12 checklist ──▶ W2: KiCad + ORDER PCB ≤ Aug 1 ──▶ W3: fab+ship ──▶ W4–5: assemble + rehearse
         (blocks layout)              (HARD GATE)                      (dead time,        (~1 wk slack now;
                                                                        do software)       ~Aug 16 arrival)
```

Everything else (firmware, sync, UNO-Q port) runs in parallel and never blocks the PCB. The one unforgiving dependency chain is: **§12 checklist → layout → order by Aug 1 → boards in hand ~Aug 8–16 → ~1 week to assemble and rehearse before the Aug 21 show.** JLCPCB standard 2-layer runs are produced in ~24–48 h and DHL to Europe is 3–5 days, but customs can add up to a week with no tracking updates — so Aug 1 stays the order gate, now with a real week of slack behind it (it was a knife-edge at the old Aug 14 show date). ([JLCPCB production time](https://jlcpcb.com/help/article/pcb-fabrication-services-and-production-time), [shipping & delivery](https://jlcpcb.com/help/article/shipping-methods-and-delivery-time))

---

## Week 1 — Jul 17–24: breadboard bring-up + firmware core

Goal: **one slave doing everything on a breadboard, and every DESIGN §12 checkbox ticked.** Also: place the parts order below *this week* so connectors/sockets arrive before W4 assembly.

Deliverables:

- [x] **Order the shopping list** (bottom of this file) — ✅ **placed 2026-07-27**.
- [ ] Solder PCM5102A bridges per DESIGN §5 (FLT=L, DEMP=L, XSMT=H, FMT=L, SCK→GND) on 1 module; wire to SuperMini per `config.h` pin map (BCK=4, LRCK=5, DIN=6).
- [ ] I2S tone out: 48 kHz/16-bit stereo via `i2s_std`, no MCLK — sine on L, cosine on R → circle on the scope. Closes §12 "PLL locks at 32× fs BCK".
- [ ] DC ramp test on L/R outputs — closes §12 "output truly passes DC". **This is the go/no-go test for the whole architecture; do it day 1–2.**
- [ ] Square Lissajous / sharp-step pattern, compare FLT=L vs FLT=H ringing on the scope — closes §12 filter item.
- [ ] `adc_continuous` at 72 kHz total / 3 ch (mic GPIO0, vbat GPIO1, pot GPIO3), verify no drops over 10 min — closes §12 ADC item — pass criterion: `stat` console counters adc overflow/errors delta = 0 over 10 min (the firmware exposes them).
- [ ] Port local mode from the proven `esp32c3SIGMADELTA` unit: X = DC-blocked mic, Y = 2-pole Butterworth LPF (Q = 0.7071), pot-mapped cutoff 20–300 Hz, now at 48 kHz out via I2S. Visually compare against an old unit.
- [ ] Firmware core skeleton running: audio task (240-frame blocks, blocking I2S write paces the loop), mode state machine, button/LED UI per DESIGN §7, USB CDC console.
- [ ] WiFi RX path: connect to the UNO-Q's 2.4 GHz AP (it has arrived — it is now the on-stage controller *and* AP; no laptop), `WiFi.setSleep(false)`, bind port 5000, parse `HYPE_AUDIO` into the jitter buffer; trivial Python sender on the UNO-Q for a first smoke test.
- [ ] Measure currents per mode at 3.7 V (bench supply or LiPo + meter) vs DESIGN §9 table — closes §12 current item.
- [ ] Probe SuperMini 3V3 during WiFi TX burst at VBAT = 3.5 V, check for brownout — closes §12 LDO item.
- [x] Caliper-measure all four module outlines + pin positions — closes §12 mechanical item. ✅ calipers Jul 18, photogrammetry Jul 26, **TP4056 pad-row edge offset Jul 27** (the last open one — see [hardware/measurements.md](hardware/measurements.md) §Nubs).

**Exit gate:** all DESIGN §12 boxes ticked (WiFi robustness item may carry a partial pass — full 4-stream test lands in W3, but a 1–2 stream loss measurement must exist).

## Week 2 — Jul 25–31: KiCad + PCB ORDER (hard gate ~Aug 1) + 2-slave sync

Goal: **Gerbers uploaded and paid by Aug 1**, and two slaves drawing the same picture at the same time.

Deliverables:

- [ ] KiCad schematic: carrier per DESIGN §3/§9 — socketed modules, power path (P-FET + Schottky load sharing per [hardware/pcb.md](hardware/pcb.md)), battery divider, switch, button, pot, LEDs, GPIO2 pull-up, ≥220 µF bulk, 2× RCA output pad-pairs (signal + ground, fed through series R10/R11 — **fitted as 0 Ω links**, since the DAC module already carries ~470 Ω of its own; pcb.md §10 Q2 — no board-mounted BNC/TRS).
- [ ] Layout 70 × 50 mm, 2-layer, THT except Q1 (SOT-23) and D2 (SMA); footprints from photogrammetry (`hw/pin_locs` via `measured.py`), not W1 calipers. Defensive layout: 0R links / bodge points on anything unproven, module sockets mean a footprint error only costs an adapter.
- [ ] Design review pass against `config.h` pin map (it is law) + DRC clean.
- [ ] **ORDER: 2-layer × 5 boards at JLCPCB (default; PCBWay = fallback), DHL express, by Aug 1.** Build 4 units + 1 spare bare board (5 is the fab min qty anyway). Cost is trivial (~€2–10 board run + ~€15–20 express shipping ⚠️ VERIFY: exact quote at checkout).
- [ ] **Python test-streamer** running on the UNO-Q — no laptop (this is the seed of the UNO-Q app, see [../src/unoq-controller/README.md](../src/unoq-controller/README.md)): implements [protocol.md](protocol.md) — 988 B `HYPE_AUDIO` packets every 5 ms unicast to each slave on port 5000, `HYPE_SYNC` beacon every 500 ms on 5001, listens for 1 Hz status on 5002 and prints a live stats table (RSSI, buffer depth, drops, underruns, clock offset).
- [ ] Wire mic/pot/etc. onto a second breadboard slave (modules are socketed later; breadboard is fine now).
- [ ] **2-slave sync test:** stream the same pattern to both, verify ±5 ms — probe both DACs' Y outputs on a 2-channel scope, or toggle a GPIO at each 240-frame block boundary and measure skew.
- [ ] Jitter-buffer behaviour: kill the stream mid-play → LOCAL fallback within 1 s (`STREAM_TIMEOUT_MS`); restore → resync.

**Exit gate:** order confirmation email from the fab, dated ≤ Aug 1.

## Week 3 — Aug 1–8: 4-slave sync + UNO-Q port (while PCBs ship)

Goal: the full distributed system working on breadboards, so W4 is *only* mechanical.

Deliverables:

- [ ] Bring up slaves 3 and 4 on breadboards (solder bridges on remaining PCM5102A modules).
- [ ] **4-slave sync test** from the UNO-Q test-streamer: 30+ min run, packet-loss/underrun stats from status packets, RSSI at 10 m — fully closes DESIGN §12 WiFi robustness item.
- [x] UNO-Q bring-up: DONE 2026-07-18 — AP is a NetworkManager connection `hyperosci-ap` (**2.4 GHz ch 6, 192.168.50.1/24**; not hostapd/dnsmasq, and not 192.168.4.x/10.42.x — those collided with the USB tether).
- [ ] **UNO-Q as controller + AP** — it has ARRIVED: Debian 13 (trixie) aarch64, kernel 7.0.0, QRB2210 (4× 2.0 GHz), ~3.58 GB RAM. ⚠️ Disk: rootfs (`/`) is 79 % full (~2 GB free) — build/run osci-render aarch64 under `/home/arduino` (~16 GB free), **never** on the rootfs. The controller is DONE and deployed (systemd `hyperosci-controller`: streamer + web UI + patterns circle/lissajous/rose/**Hershey-font text** with effects and presets — see src/unoq-controller/README.md). osci-render: aarch64 build succeeded but integration was **rejected** (no headless entry point); the text renderer was reimplemented natively instead — docs/text-rendering-findings.md. The controller risk is retired.
- [ ] 4 slaves on the UNO-Q AP, sustained stats run; log RSSI / loss / underruns per slave and compare against the W2 2-slave numbers.
- [ ] Overnight soak: 4 slaves + UNO-Q streaming 8+ h, zero crashes, log stats.
- [ ] Firmware hardening: battery thresholds (`VBAT_*` in config.h), `HYPE_CMD` handling (set_mode / identify / set_gain / reboot), slave-id console override.
- [ ] Track PCB shipping daily. **If no tracking movement by Aug 6 → activate Risk 3 plan B now, not on ~Aug 16.**

## Week 4–5 — Aug 8–21: assemble, integrate, soak, rehearse

Goal: 4 boxed units + UNO-Q that a stranger could set up from the README.

- [ ] Day boards arrive: assemble **one** PCB, full smoke test (power path first — verify the USB-while-battery-ON rule is actually fixed by the load-sharing circuit before trusting it), then the remaining 3. If boards arrive after ~Aug 16, the battery test and rehearsal run on the breadboard/protoboard units (Risk 3 posture) and carrier assembly slips past Aug 21 — the PCB never gates the rehearsal.
- [ ] Move all modules from breadboards into sockets; label units 1–4, set slave ids.
- [ ] Battery test: full charge → NETWORK-mode streaming until `VBAT_WARN` (expect ~13 h @ 2000 mAh — the selected EEMB LP103454 cell, mounted off-board in the 3D-printed enclosure — per DESIGN §9); confirm warn/WiFi-off/sleep thresholds fire.
- [ ] Full-system soak: 4 units + 4 scopes + UNO-Q, 2+ h continuous.
- [ ] **Venue-style rehearsal:** scopes at realistic spread (10 m+), channel scan on site, walk-test RSSI, deliberately kill/restore the AP mid-show (fallback + recovery must look intentional, not broken), practice the power-on ritual (UNO-Q first, then slaves).
- [ ] Buffer days Aug 19–21: fixes only, no new features.

---

## Risk register (top 5)

| # | Risk | Trigger (when to pull plan B) | Plan B |
|---|------|-------------------------------|--------|
| 1 | **PCM5102A module doesn't pass DC** (module-added output caps would break slow X/Y drawing) | W1 day 1–2 ramp test shows AC coupling | Inspect module: bridge/bypass any series output caps (datasheet output is ground-centered, DC-capable — the caps, if any, are the module's, not the chip's). Worst case: MCP4822 SPI DAC fallback per research v3.2 — big change, decide by Jul 22. **MCP4822 has no parts on hand** — either add "5× MCP4822 (DIP-8), ~€15, Risk-1 insurance" to the buy-now order or accept ~1 week sourcing delay if the ramp test fails. |
| 2 | **§12 checklist not closed by Jul 31 → order gate slips** | Any checklist item still open Jul 29 | Order anyway with a defensive layout (everything socketed, 0R links, bodge pads) and accept a possible v1.1 respin; the show can run on hand-wired protoboard units (all-THT modules make this a 1-evening-per-unit job). |
| 3 | **Fab/shipping/customs delay eats W4** | No DHL tracking movement by Aug 6, or ETA > Aug 16 | Build 4 units on perma-proto/protoboard in W3–W4 (same sockets, same wiring as breadboard); PCBs become the durable v1 after the deadline. Rehearsal happens either way. |
| 4 | **`adc_continuous` unstable at 72 kHz / 3 ch on Arduino core 3.x** | W1 bring-up shows drops or corrupted frames after 10 min | Mic-only continuous DMA at 24 kHz + one-shot vbat/pot reads at 10 Hz from `loop()`; if the wrapper itself is the problem, call the IDF driver directly (already the plan per DESIGN §10). |
| 5 | **WiFi jitter/loss with 4 unicast streams** (UNO-Q hostapd weak) | W3 stats: sustained >0.5 % loss or recurring underruns at 10 m | In order: pick a clean channel; raise the controller lead / buffer depth (already done 2026-07-18: capacity 512 ms, LEAD_US 450 ms — latency is irrelevant for scope art); reposition/elevate AP; last resort — dedicated travel router as AP with the controller as a client. |

Runner-up — RESOLVED 2026-07-19: osci-render integration was investigated and rejected (no headless entry point — docs/text-rendering-findings.md); the Python controller's native renderer (circle/Lissajous/rose/Hershey-font text with effects + presets) *is* the show renderer, not a fallback. No laptop on stage.

---

## Order THIS WEEK (Jul 17–24) — ✅ PLACED 2026-07-27

Authoritative full BOM: [hardware/pcb.md](hardware/pcb.md) §5, which carries the order record
and the two substitutions made at order time. This is the *buy-now* subset — connectors and
mechanicals with the worst lead times. Quantities sized for 4 units + 1 spare board.

**Scope of what was bought: a §4.5 plan-A build.** The power-path block (U1, D1, D2, D3, R7,
R8, R9) was deliberately **not** ordered — JP1 is bridged instead, and D2 must never be fitted
on such a board. Q1 and PNPs for Q2 were bought anyway as cheap insurance in case §4.4 later
passes on an assembled carrier.

| Item | Qty | Status | Notes |
|------|-----|--------|-------|
| Scope outputs | — | n/a | **RESOLVED — no BNC/TRS on the board.** 2× RCA output pad-pairs per board (signal + ground: X = PCM5102A L, Y = R, fed through 100 Ω R10/R11). Reuse the existing ~50 cm RCA cables from the old sigma-delta units; scopes are driven via their existing BNC→RCA adapters. **€0 connector spend** — frees ~25 mm of board edge and un-blocks the parts order. |
| Female headers, 2.54 mm, fixed sizes: 1×8 / 1×6 / 1×9 / 1×3 | 12 / 6 / 6 / 6 | ✅ ordered 2026-07-27 | **Bought as a 155-piece assortment (4/6/8/10/12/16/20/40), which has no 1×9 and no 1×3** — cut J3 from a 10-pin and J4 from a 4-pin, accepting the destroyed position. SuperMini 2× 1×8, PCM5102A 1×6 + **1×9** (analog end is a 9-pin header, measured 2026-07-18), MAX4466 1×3 — parts for 6 boards, per [hardware/pcb.md](hardware/pcb.md) §5. **Fixed sizes — female strips don't snap cleanly** (each cut destroys a position), so order the exact lengths, not 40-pin strips to cut. |
| **Machined single-pin sockets (turned-pin)** | 30 | ✅ in stock | **TP4056 J5/J6/J9/J10 — 6 per board** (was 4; the USB-C-end mount row was added 2026-07-28, pcb.md §5). ⚠️ **Not 1×2 female headers**: the module's pads measure 0 / 3.526 / 10.960 / 14.066 mm, so nothing on a 2.54 grid mates with them (photogrammetry 2026-07-26). Singles broken off a machined DIP socket also work. Alternative: solder wires and give up removability. |
| 10 kΩ potentiometer, PCB-mount, linear | 5 | ✅ ordered 2026-07-27 | 5 pcs = 4 boards + 1 spare, no margin for a wrong variant — paper-doll the first one that arrives. **RESOLVED 2026-07-18 — RV097NS-B10K**, body 27.3 × 9.5 × 11.3 mm. **No knobs to order** — the metal shaft is turned directly by hand. ⚠️ **Order the "5-pin mono *with switch*", right-angle variant specifically** — sellers use "RV097NS" for a vertical/bracket-lug part too, and its holes do not line up (pcb.md §5 box, corrected 2026-07-27). |
| Slide switch, SPDT, THT (power) | 5 | ✅ ordered 2026-07-27 (×20) | **SS12D00, 6 mm handle.** Revised call: the earlier "1 A part required, WiFi TX peaks ~0.35 A" compared a peak current against a **make/break rating specified at 50 VDC** — at the ~4 V this switch interrupts there is no arc, and 0.35 A through the contacts is thermally irrelevant. 6 mm handle so the actuator clears the enclosure wall. On arrival: check pins against the paper doll, and clip any locating lug — the footprint has the 3 signal holes only. |
| Tactile button 6×6 mm THT | 10 | ✅ in stock | MODE button + spares. |
| LED 3 mm green + amber | 5 + 5 | ✅ in stock | + 2.2 kΩ series resistors (per config.h note). |
| Resistors: 100 kΩ ×10, 10 kΩ ×10, 2.2 kΩ ×10 | — | ✅ in stock | Divider, pull-ups, LED series. Bin checked 2026-07-27. |
| 100 nF ceramic ×10, ≥220 µF electrolytic ×5 | — | ✅ in stock | Divider filter + bulk per DESIGN §3. 220 µF **10 V** ×10 on hand — fine, VLOAD tops out at 5 V from USB. Confirm the body fits `CP_Radial_D6.3mm_P2.50mm` (⌀6.3 mm, 2.5 mm lead pitch) when you seat C1. |
| Power-path parts (full set per [hardware/pcb.md](hardware/pcb.md) §5) | see notes | ⛔ **not ordered — plan A** | **Only `DMG3415U-7 ×10` (Q1) and a TO-92 PNP assortment (Q2) were bought**, as insurance. `U1 / D1 / D2 / D3 / R7 / R8 / R9` were deliberately skipped: per pcb.md §4.5 the boards ship with **JP1 bridged**, where those parts have no function and **D2 is actively dangerous**. If §4.4 later passes on an assembled carrier, populating the block needs one small follow-up order. ⚠️ Kit PNPs are **not** drop-in for BC557 — `design.py:107` wires the TO-92 pads **C-B-E**, while BC327 / S8550 / 2N3906 are E-B-C and A1015 is E-C-B; cross the outer leads. Historical note: ⚠️ **Values changed by the 2026-07-26 design review — order these, not the originals.** DMG3415U P-FET ×6, SS34 (or 1N5817) ×6, **TL431A** ×6 (±1 % grade, *not* plain TL431), BC557 ×6, **BAT85S ×12** (D1 fitted **and** D3 DNP experiments — 1N4148 is no longer used for D1), **8.2 k 1 % ×6**, **10 k 1 % ×6**, **1 k ×6**, 100 k 1 % ×12 (R1/R2) + 100 k ×12 (R6/R12), 100 Ω ×12. Plus **2× SOT-23→DIP breakout boards** for breadboarding Q1, and a **milliohm-class shunt** (0.1 Ω or lower) for the state-4 current measurement — a DMM ammeter's burden resistance makes that test pass spuriously (pcb.md §4.4 item 3). |
| JST-PH **2.00 mm** 2-pin (LiPo) | 5 | ✅ ordered 2026-07-27 | ⚠️ **A JST XH-2.54 kit was ordered first and swapped.** XH is 2.5 mm and mates neither J8 (`S2B-PH-K`, 2.00 mm) nor a cell's pre-fitted lead — the two families look alike in listings. J8 is the board-side socket; the cell brings its own plug. |
| Perma-proto / protoboard | 4 | ⬜ not in the 2026-07-27 order | **Risk 2/3 insurance.** €10 well spent. |
| **LiPo cells, ~2000 mAh** | 4–5 | ⬜ Amazon, separate order | Sourcing changed 2026-07-27: **Amazon instead of the EEMB LP103454**, to keep the cell off the slow-freight path. Two checks the substitution puts on you: the lead must be **JST-PH 2.00 mm**, and **meter the polarity before first plug-in** — J8's silk assumes the EEMB convention and `B−` is not GND on this board (pcb.md §1/§3.1). |
| 5 V / 2 A USB wall charger | 1+ | ✅ on hand | Mandatory per pcb.md §4.3 state 2 — a 1 A supply sags under the TP4056's 1 A charge current plus ~150 mA load. |

Already owned/ordered (DESIGN §3): 4× ESP32-C3 SuperMini, 4× GY-PCM5102A, 4× MAX4466, 4× TP4056, LiPos (**~2000 mAh, Amazon-sourced as of 2026-07-27**; the EEMB LP103454 stays the reference for size, capacity and connector), **UNO-Q (arrived)**. Spare ESP32-C3s, mics and DACs on hand. **Keep the old sigma-delta units intact as A/B reference/backup — do not cannibalize; only their screwed-in RCA jacks may be reused non-destructively if needed.**
