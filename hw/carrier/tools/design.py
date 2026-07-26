#!/usr/bin/env python3
"""HYPEROSCI carrier — single source of truth for the netlist.

Transcribed from docs/hardware/pcb.md §2 (connectors), §3 (net list),
§4.2 (power path) and §5 (BOM). config.h GPIO numbers are checked
mechanically by tools/check_netlist.py — do not edit pin/net pairs here
without re-running that check.

Conventions:
  - net None  = no-connect (gets an ERC no_connect marker)
  - AGND is electrically the GND net (pcb.md §3.1) — the island/neck is
    enforced in layout, not here. Pins that belong on the AGND island are
    listed in AGND_ISLAND_PINS for the layout script.
  - Diode/LED pad 1 = cathode (KiCad footprint convention).

RENAMES vs pcb.md (KiCad refs must end in a digit; the silk keeps the doc names):
  J1A/J1B -> JA1/JB1, J6b -> J9.

BUILD-TIME WARNINGS that live with the netlist, not only in the doc:
  - TO-92 PINOUT (U1, Q2): the pad map below is pad1=REF, pad2=ANODE,
    pad3=CATHODE for U1 -- the onsemi TO-92 numbering. TI numbers the same
    package the other way round (pin1=K, pin2=A, pin3=REF), and ANODE is the
    centre lead in both, so a TI-branded part drops into the footprint
    perfectly while sitting backwards. Same class of trap for Q2 (BC557
    C-B-E vs E-B-C across vendors). Check the lead order on the datasheet of
    the part actually bought -- see pcb.md 5 "TO-92 orientation".
  - D2 IS NOT FITTED IN THE JP1 FALLBACK. Bridging JP1 with D2 in place puts
    VBUS_CHG -> D2 -> VSW -> JP1 -> VBAT_OUT = B+ straight onto the cell with
    no CC/CV, in either SW1 position. JP1 fallback = bridge JP1, omit
    Q1/Q2/U1/D1/D2 -- and R7/R8 with them, since JP1 puts that divider straight
    across the cell for 231 uA with no comparator left to feed. See pcb.md 4.4.
"""

# ref: (value, symbol, footprint, {pad: net}, dnp)
STOCK = {
    "socket8": "Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical",
    "socket6": "Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical",
    "socket3": "Connector_PinSocket_2.54mm:PinSocket_1x03_P2.54mm_Vertical",
    "socket2": "Connector_PinSocket_2.54mm:PinSocket_1x02_P2.54mm_Vertical",
    "socket9": "Connector_PinSocket_2.54mm:PinSocket_1x09_P2.54mm_Vertical",
    "header2": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "r":       "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
    "c":       "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm",
    "cp":      "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm",
    "do35":    "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
    "led3":    "LED_THT:LED_D3.0mm",
    "to92":    "Package_TO_SOT_THT:TO-92_Inline_Wide",   # 2.54 pitch (plain Inline is 1.27 — fails clearance)
    "sot23":   "Package_TO_SOT_SMD:SOT-23_Handsoldering",
    "jst":     "Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
    "push":    "Button_Switch_THT:SW_PUSH_6mm_H5mm",
    "tp":      "TestPoint:TestPoint_Pad_D1.5mm",
    "m3":      "MountingHole:MountingHole_3.2mm_M3_Pad",
}

COMPONENTS = {
    # --- SuperMini socket rows (pcb.md §2 table; row A = 5V-side, and with the
    #     antenna west (§6.1 rule 3) that row lands SOUTH — JA1 south, JB1 north.
    #     Getting this backwards is the mirror bug v1.0 shipped; measured.py
    #     now refuses to generate it. ---
    "JA1": ("SuperMini row A (J1A)", "CONN8", STOCK["socket8"], {
        "1": "VLOAD", "2": "GND", "3": "3V3", "4": "I2S_BCK",
        "5": "POT_WIPER", "6": "GPIO2_PU", "7": "VBAT_SENSE", "8": "MIC_OUT"}),
    "JB1": ("SuperMini row B (J1B)", "CONN8", STOCK["socket8"], {
        "1": "I2S_LRCK", "2": "I2S_DOUT", "3": "BTN_MODE", "4": None,  # GPIO8 LED
        "5": None,  # GPIO9 BOOT
        "6": "LED_NET_A", "7": "LED_MODE_A", "8": "DBG_TX"}),
    # --- PCM5102A: J2 = I2S end (SCK BCK DIN LCK GND VIN), J3 = analog 1x9 ---
    "J2": ("PCM5102A I2S", "CONN6", STOCK["socket6"], {
        "1": "GND",       # SCK tied to GND on the carrier (§3.2)
        "2": "I2S_BCK", "3": "I2S_DOUT", "4": "I2S_LRCK", "5": "GND", "6": "3V3"}),
    "J3": ("PCM5102A analog", "CONN9", STOCK["socket9"], {
        "1": "DAC_L",     # LROUT (jack end)
        "2": "GND",       # AGND (island)
        "3": "DAC_R",     # ROUT
        "4": "GND",       # AGND (island)
        "5": None, "6": None, "7": None, "8": None, "9": None}),  # A3V3 FMT XSMT DEMP FLT: module-internal
    # --- mic pigtail, TP4056, debug, battery, scope pads ---
    "J4": ("MAX4466 pigtail", "CONN3", STOCK["socket3"], {
        "1": "3V3", "2": "GND", "3": "MIC_OUT"}),   # VCC GND OUT (back silk)
    "J5": ("TP4056 OUT-/B-", "CONN2", "HYPEROSCI:TP4056_Pads_OUTminus_Bminus", {
        "1": "GND", "2": "BAT_MINUS"}),             # measured row: OUT- B- B+ OUT+
    "J6": ("TP4056 B+/OUT+", "CONN2", "HYPEROSCI:TP4056_Pads_Bplus_OUTplus", {
        "1": "BAT_PLUS", "2": "VBAT_OUT"}),
    "J9": ("TP4056 IN+ wire (J6b)", "PAD1", "HYPEROSCI:WirePad_D1.0", {"1": "VBUS_CHG"}),
    "J7": ("debug TX", "CONN2", STOCK["header2"], {"1": "DBG_TX", "2": "GND"}),
    "J8": ("LiPo JST-PH", "CONN2", STOCK["jst"], {"1": "BAT_PLUS", "2": "BAT_MINUS"}),
    "X1": ("scope X RCA pads", "CONN2", "HYPEROSCI:RCA_FlyingLead_Pads", {
        "1": "SCOPE_X", "2": "GND"}),
    "Y1": ("scope Y RCA pads", "CONN2", "HYPEROSCI:RCA_FlyingLead_Pads", {
        "1": "SCOPE_Y", "2": "GND"}),
    # --- load-sharing power path (§4.2 netlist table) ---
    "Q1": ("DMG3415U", "QPMOS", STOCK["sot23"], {
        "1": "GATE", "2": "VSW", "3": "VBAT_OUT"}),          # G S D
    "Q2": ("BC557", "QPNP", STOCK["to92"], {
        "1": "GATE", "2": "Q2_B", "3": "VSW"}),              # C B E
    # c[1] is the SCHEMATIC SYMBOL KEY, not the part number — leave it "TL431"
    # even though the value says TL431A, or gen_schematic.py raises KeyError.
    "U1": ("TL431A", "TL431", STOCK["to92"], {
        "1": "VSW_SENSE", "2": "GND", "3": "TL431_K"}),      # REF A K (onsemi numbering)
    # Schottky, not 1N4148: D1 carries only R6's pull-down current, so its Vf
    # sits BELOW D2's and Vgs(Q1) lands at 0..+0.1 V — hard off — instead of
    # -0.10..-0.22 V against a -0.3 V min threshold. pcb.md §4.3 states 2/3.
    "D1": ("BAT85", "D", STOCK["do35"], {"1": "GATE", "2": "VBUS_CHG"}),
    # NOT FITTED when JP1 is bridged — see the module docstring.
    "D2": ("SS34/1N5817", "D", "HYPEROSCI:D_Dual_SMA_DO41", {
        "1": "VSW", "2": "VBUS_CHG"}),
    "D3": ("BAT85 DNP", "D", STOCK["do35"], {"1": "GATE", "2": "VLOAD"}, True),
    "R6": ("100k", "R", STOCK["r"], {"1": "GATE", "2": "GND"}),
    # Sense divider. Ratio 1.82 as before, but ~10x lower impedance so the
    # TL431's REF bias current stops moving the trip: I_ref*R7 falls from
    # 164 mV to 16 mV. Costs standby drain — see pcb.md §4.2 divider table.
    "R7": ("8.2k 1%", "R", STOCK["r"], {"1": "VSW", "2": "VSW_SENSE"}),
    "R8": ("10k 1%", "R", STOCK["r"], {"1": "VSW_SENSE", "2": "GND"}),
    "R9": ("1k", "R", STOCK["r"], {"1": "TL431_K", "2": "Q2_B"}),  # >=1 mA I_KA(min)
    "R12": ("100k", "R", STOCK["r"], {"1": "Q2_B", "2": "VSW"}),
    "JP1": ("escape hatch", "JP2", "HYPEROSCI:SolderJumper_P2.54", {
        "1": "VBAT_OUT", "2": "VSW"}),
    "SW1": ("power SPDT 1A", "SW_SPDT", "HYPEROSCI:SW_Slide_SPDT_DualPitch", {
        "1": "VSW", "2": "VLOAD", "3": None}),
    "C1": ("220u 16V", "CP", STOCK["cp"], {"1": "VLOAD", "2": "GND"}),
    "C2": ("100n", "C", STOCK["c"], {"1": "VLOAD", "2": "GND"}),
    # --- battery sense divider (§3.2 VBAT_SENSE, VBAT_DIVIDER 2.0) ---
    "R1": ("100k 1%", "R", STOCK["r"], {"1": "BAT_PLUS", "2": "VBAT_SENSE"}),
    "R2": ("100k 1%", "R", STOCK["r"], {"1": "VBAT_SENSE", "2": "GND"}),
    "C3": ("100n", "C", STOCK["c"], {"1": "VBAT_SENSE", "2": "GND"}),
    # --- strap pull-up, controls, LEDs ---
    "R3": ("10k", "R", STOCK["r"], {"1": "3V3", "2": "GPIO2_PU"}),
    "SW2": ("MODE 6x6", "SW_PUSH", STOCK["push"], {"1": "BTN_MODE", "2": "GND"}),
    "RV1": ("RV097NS B10K", "POT5", "HYPEROSCI:RV097NS_Vertical", {
        "1": "GND", "2": "POT_WIPER", "3": "3V3",     # CCW wiper CW
        "4": "GND", "5": "GND"}),                     # bracket lugs (deviation 3)
    "R4": ("2.2k", "R", STOCK["r"], {"1": "LED_NET_A", "2": "D4_A"}),
    "R5": ("2.2k", "R", STOCK["r"], {"1": "LED_MODE_A", "2": "D5_A"}),
    "D4": ("LED green NET", "LED", STOCK["led3"], {"1": "GND", "2": "D4_A"}),
    "D5": ("LED amber MODE", "LED", STOCK["led3"], {"1": "GND", "2": "D5_A"}),
    # --- DAC -> scope series R (fit 0R if module 470R confirmed in-line) ---
    "R10": ("100R/0R", "R", STOCK["r"], {"1": "DAC_L", "2": "SCOPE_X"}),
    "R11": ("100R/0R", "R", STOCK["r"], {"1": "DAC_R", "2": "SCOPE_Y"}),
    # --- decoupling at DAC + mic ---
    "C4": ("100n mic", "C", STOCK["c"], {"1": "3V3", "2": "GND"}),
    "C5": ("100n DAC", "C", STOCK["c"], {"1": "3V3", "2": "GND"}),
    # --- testpoints (§3.2) + mounting ---
    "TP1": ("VBUS_CHG", "TPAD", STOCK["tp"], {"1": "VBUS_CHG"}),
    "TP2": ("VSW", "TPAD", STOCK["tp"], {"1": "VSW"}),
    "TP3": ("GATE", "TPAD", STOCK["tp"], {"1": "GATE"}),
    "TP4": ("3V3", "TPAD", STOCK["tp"], {"1": "3V3"}),
    "TP5": ("VBAT_SENSE", "TPAD", STOCK["tp"], {"1": "VBAT_SENSE"}),
    "H1": ("M3", "HOLE", STOCK["m3"], {"1": "GND"}),
    "H2": ("M3", "HOLE", STOCK["m3"], {"1": "GND"}),
    "H3": ("M3", "HOLE", STOCK["m3"], {"1": "GND"}),
    "H4": ("M3", "HOLE", STOCK["m3"], {"1": "GND"}),
}

# Per-part footprint overrides — vertical (standing) axials wherever the
# horizontal 10.16mm span doesn't fit. NOT under modules (9mm standing height).
# This lives here rather than in gen_board.py because the SCHEMATIC has to agree:
# when it only existed layout-side, eleven resistors were standing on the board
# while the schematic still said P10.16mm_Horizontal, and
# `kicad-cli pcb drc --schematic-parity` reported every one of them.
VERT_R = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical"
FP_OVERRIDE = {r: VERT_R for r in
               ["R1", "R2", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12"]}

def footprint_of(ref):
    """The footprint actually used — apply this in every generator."""
    return FP_OVERRIDE.get(ref, norm(COMPONENTS[ref])[2])

# Pads whose copper belongs on the AGND island (layout only — same GND net):
AGND_ISLAND_PINS = [("J3", "2"), ("J3", "4"), ("X1", "2"), ("Y1", "2"),
                    ("J4", "2"), ("C4", "2")]

# Schematic grouping (a column per group, for readability):
GROUPS = [
    ("SuperMini socket", ["JA1", "JB1", "R3", "J7"]),
    ("PCM5102A + scope out", ["J2", "J3", "R10", "R11", "X1", "Y1", "C5"]),
    ("Mic + controls", ["J4", "C4", "RV1", "SW2", "R4", "D4", "R5", "D5"]),
    ("Battery + TP4056", ["J8", "J5", "J6", "J9", "R1", "R2", "C3"]),
    ("Power path", ["Q1", "Q2", "U1", "D1", "D2", "D3", "R6", "R7", "R8",
                    "R9", "R12", "JP1", "SW1", "C1", "C2"]),
    ("Test + mech", ["TP1", "TP2", "TP3", "TP4", "TP5", "H1", "H2", "H3", "H4"]),
]

def norm(c):
    """(value, symbol, footprint, pins, dnp)"""
    return (c[0], c[1], c[2], c[3], c[4] if len(c) > 4 else False)

if __name__ == "__main__":
    nets = {}
    for ref, c in COMPONENTS.items():
        for pad, net in norm(c)[3].items():
            if net:
                nets.setdefault(net, []).append(f"{ref}.{pad}")
    for n in sorted(nets):
        print(f"{n:12s} {' '.join(sorted(nets[n]))}")
