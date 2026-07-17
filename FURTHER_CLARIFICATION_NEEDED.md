# FURTHER CLARIFICATION NEEDED

Open questions for Finn. Some of these gate ordering: questions tagged
**[ORDER-blocking]** gate the parts order that PLAN mandates *this week* (~Jul 21);
questions tagged **[PCB-blocking]** gate the PCB order (~Aug 1). The rest don't
block week-1 breadboard/firmware work. Answer inline / delete as resolved.

## Hardware / PCB

1. **[ORDER-blocking, answer by ~Jul 21] Scope connectors:** BNC right-angle PCB-mount on the board
   edge, 3.5 mm TRS jack + TRS→BNC adapter cables, or just solder pads for
   flying leads? BNC is most roadie-proof but adds ~15 mm board depth and ~€2/board.
   Do you already own BNC cables/connectors (v3.2 doc said "maybe")?
2. **[PCB-blocking] Battery capacity & physical size:** which LiPo cells exactly
   (mAh, dimensions)? Drives battery holder/velcro area on the PCB and the
   battery-life table. Also: battery mounted on the PCB or loose in the enclosure?
3. **[PCB-blocking] Pot style:** PCB-mount 9 mm pot with knob (RV09-style,
   soldered to carrier) or panel-mount pot on wires? Affects footprint + enclosure.
4. **Enclosure:** 3D-printed case, laser-cut sandwich, or bare PCB with standoffs?
   Any constraint from how the units mount at the venue (velcro on scopes? on a
   shelf behind them?). PCB has 4× M3 holes either way.
5. **Mic placement:** MAX4466 soldered to the carrier (mic points up) or on a
   short cable so it can aim at the room/PA? Cable = better audio pickup,
   worse mechanics.
6. **Do you want a 5th spare unit built?** PCBs come in multiples of 5–10 anyway;
   parts cost per extra unit ≈ €12.
7. **[PCB-blocking] Which TP4056 variant do you own — micro-USB or USB-C?**
   Changes the board-edge cutout and charging-cable logistics (pcb.md §10 Q2).
8. **TP4056 charge current:** willing to swap the Rprog resistor (1 A → 500 mA) on
   each module, or should we mandate 5 V/2 A chargers instead? (pcb.md §4.3 state 2)
9. **[ORDER-blocking] Knob & switch details:** knob style for the 10 k pot (drives
   shaft-length variant), and is a 0.3 A-rated SS12D00 slide switch OK or order the
   1 A variant? (WiFi TX peaks ≈ 0.35 A — recommend the 1 A variant.) **We will
   order the 1 A variant unless you object.** (pcb.md §5)
10. **Quick bench check when modules are in hand:** beep the SuperMini USB-C VBUS
    pin to the 5 V header pin — do your clones really tie them directly? Note the
    result in `docs/hardware/measurements.md`; it affects power-path margins
    (pcb.md §10 Q3).

## System / performance

11. **What's the actual performance date?** The old docs said "end of May 2026"
    which has passed; the current plan targets ~Aug 14 for a working system. Is
    there a hard show date after that?
12. **Controller audio source:** is the plan still osci-render on the UNO-Q
    (ARM64 build risk), or is a laptop running osci-render + the Python streamer
    acceptable for the first show, with the UNO-Q as a later upgrade? This
    changes how much of the 4 weeks goes to the UNO-Q app.
13. **UNO-Q status:** has the board arrived? Which Debian image/version is on it?
    (Needed before W3 of PLAN.md.)
14. **Mic semantics in HYBRID mode:** canon (DESIGN §7 + `config.h`
    `HYBRID_MIC_GAIN`) already defines the default as a per-unit local mic mix at
    50 % (each scope reacts to its own mic). Confirm the canon default (per-unit
    local mic mix), or request central-mic mixing (one mic into the UNO-Q, mixed
    identically to all scopes) as a v2 feature.
15. **WiFi credentials:** hardcoded `HYPEROSCI_AP` / `hyperosci2026` defaults OK,
    or do you want console-configurable credentials in v1?

## Logistics / bench

16. **Do the LiPo packs already have JST-PH leads**, or do we need to crimp/buy
    pigtails? (Affects the buy-now list; carrier has a JST-PH socket.)
17. **[Blocks W1–W2 network tests] What 2.4 GHz AP can you use before the UNO-Q
    exists?** Laptop with hostapd-capable adapter, a spare router, or phone
    hotspot? (Phone hotspots work but add jitter — fine for smoke tests, not for
    the ±5 ms sync measurement.)
18. **Fab preference:** PLAN assumes JLCPCB + DHL. Any existing PCBWay/JLCPCB
    account, coupons, or preference?
19. **Do you have a µA-capable meter or a 0.1 Ω shunt** for the deep-sleep and
    TX-burst current measurements, or should the measurement plan assume
    USB-power-meter accuracy only?

## Nice-to-know

20. **Scope models:** besides the Tektronix 2212 — what are the other three?
    (Input impedance/sensitivity check, and whether all four do XY mode.)
21. **Old sigma-delta units:** keep one intact as reference/backup for A/B
    comparison during bring-up, or cannibalize all four ESP32-C3s for the new
    carriers?
