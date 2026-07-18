# FURTHER CLARIFICATION NEEDED

Open questions for Finn. Tags: **[ORDER-blocking]** gates the parts order
(~Jul 21); **[PCB-blocking]** gates the PCB order (~Aug 1, now with ~1 week
extra slack before the 2026-08-21 show). Resolved 2026-07-17, then the last
PCB/ORDER blockers (pot style Q3, knob Q9, VBUS check Q10) resolved 2026-07-18.
**No PCB-blocking or ORDER-blocking question remains** — only Q8 (charge-current
mandate) and Q14 (HYBRID semantics) are open, and neither gates layout or order.

## Resolved (2026-07-17)

1. **Q1 — Scope connectors [was ORDER-blocking]:** No BNC, no TRS. Board exposes
   2× RCA solder pad-pairs (signal+ground): X = PCM5102A L, Y = PCM5102A R.
   Reuse the existing ~50 cm RCA cables from the old sigma-delta units into the
   scopes' BNC→RCA adapters. Keep the 100 Ω series resistors (R10/R11). Frees
   ~25 mm board edge and ~€2/board.
2. **Q2 — Battery [was PCB-blocking]:** EEMB LP103454, LiPo 3.7 V 2000 mAh
   (34×56×11 mm, ~40 g), pre-fitted JST connector. This is the default planning
   cell. Mounted OFF the PCB — loose in the enclosure (velcro/pocket). Carrier
   keeps its JST-PH battery socket.
4. **Q4 — Enclosure:** 3D-printed case. Carrier keeps 4× M3 mounting holes.
5. **Q5 — Mic placement:** MAX4466 on a short ~10 cm jumper-wire pigtail exiting
   the enclosure (aim at PA, reach gain trimpot). Carrier provides a 3-pin
   pigtail header (VCC/GND/OUT), not a flat on-board footprint.
6. **Q6 — Unit count / spares:** Build 4 now (maybe a 5th later). Order 5 boards
   (fab min qty is 5) → one spare bare board. Finn has spare ESP32-C3s, mics,
   and DACs.
7. **Q7 — TP4056 variant [was PCB-blocking]:** USB-C, blue PCB 17×27 mm; USB-C
   jack overhangs ~2 mm (~29 mm effective depth). Size the charge-port cutout
   for USB-C.
9. **Q9 (switch half) [was ORDER-blocking]:** Order the 1 A-rated slide switch
   (SS12D00-class) — WiFi TX peaks ~0.35 A. Decided.
11. **Q11 — Show date:** Hard show date is **2026-08-21** (~5 weeks out from
    today). PCB order gate stays ~Aug 1; boards-arrive checkpoint shifts to
    ~Aug 16.
12. **Q12 — Controller:** Committed to the Arduino UNO-Q as on-stage controller
    AND the 2.4 GHz AP. NO laptop on stage. The "Python streamer" is a
    test-streamer running ON the UNO-Q, standing in for full osci-render
    integration.
13. **Q13 — UNO-Q status:** ARRIVED. Debian 13 (trixie) aarch64, kernel 7.0.0,
    QRB2210 (4× 2.0 GHz), ~3.58 GB RAM. Disk: rootfs (/) is 79% full (~2 GB
    free — TIGHT); /home/arduino has ~16 GB free → build osci-render aarch64 in
    /home/arduino, never on rootfs. osci-render aarch64 build in progress; the
    ARM64/JavaFX build is the main controller risk.
15. **Q15 — WiFi credentials:** Hardcoded `HYPEROSCI_AP` / `hyperosci2026`
    defaults are fine for v1.
16. **Q16 — LiPo leads:** The EEMB cell ships with a pre-fitted JST connector —
    no crimping needed.
17. **Q17 — 2.4 GHz AP:** The UNO-Q itself is the AP for the W1–W2 network/sync
    tests. No separate router or phone hotspot needed.
18. **Q18 — Fab:** Default to JLCPCB (no account or strong preference).
19. **Q19 — Measurement meter:** Finn has an ANENG A9002 handheld DMM — good for
    steady-state microamp reads (deep sleep). It CANNOT capture sub-ms WiFi-TX
    current bursts, so TX-burst figures stay modeled/estimated.
20. **Q20 — Scopes:** All four (Tek 2212 + three analog-dial variants) do XY
    mode. No per-scope sensitivity blocker.
21. **Q21 — Old sigma-delta units:** Keep all four INTACT as reference/backup
    A/B units. Do NOT cannibalize. Only the screwed-in RCA jacks may be reused
    non-destructively if needed.

## Resolved (2026-07-18 — bench/caliper session, see docs/hardware/measurements.md)

3. **Q3 — Pot style [was PCB-blocking]:** **RV097NS**, 9 mm PCB-mount, B10K,
   **5-pin**, body 27.3 × 9.5 × 11.3 mm. Footprint finalized (`RV097NS` 5-pin).
9. **Q9 — Knob/shaft [was ORDER-blocking]:** **No knob.** The pot's bare metal
   shaft is turned directly by hand, so nothing to order and the enclosure needs
   only a shaft hole.
10. **Q10 — VBUS→5V bench check:** **CONFIRMED tied** — SuperMini USB-C `VBUS`
    beeps continuous to the `5V` header pin (USB-C→A cable + meter). So the
    load-share power path / "no USB while the battery switch is ON" rule stands
    as designed, and the D3 "naive OR" diode stays DNP. Q10 closed.

**Also captured this session (not blocking):** the GY-PCM5102A analog end is a
**1×9** header (`… A3V3 AGND ROUT AGND LROUT`), not a 3-pin `L G R`; the module is
**~31.8 mm** long (not ~27); it carries a ~470 Ω "471" output filter but is
ground-centered (DC-coupled OK). All propagated to DESIGN/pcb/wiring.

## Still open

- **Q8 — TP4056 charge current:** Keep the 1 A default (measured RPROG = 1.19 kΩ).
  Leaning toward mandating 5 V/2 A wall chargers instead of swapping RPROG to
  ~0.5 A (fiddly to solder). 2000 mAh at 1 A ≈ 0.5C → full charge ~2.5–3 h.
  **Confirm: mandate 2 A chargers, RPROG swap optional?**
- **Q14 — HYBRID mic semantics:** Canon default stands (per-unit local mic mix,
  `HYBRID_MIC_GAIN` 50%). Central-mic mixing (one mic into the UNO-Q, mixed to
  all scopes) noted as a possible v2. Confirm canon, or request central as v2.
