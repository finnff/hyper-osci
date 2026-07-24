#!/usr/bin/env python3
"""Verify the exported KiCad netlist against design.py AND config.h.

This is the respin-prevention gate: it re-parses what KiCad actually
understood (not what we intended to emit) and checks:
  1. every design.py pin/net pair survived into the netlist, and nothing extra
  2. the SuperMini socket nets match config.h's PIN_* GPIO assignments
  3. the §2 socket table invariants (strapping pins NC, SCK tied to GND, ...)

Run from hw/carrier/:
  kicad-cli sch export netlist -o /tmp/carrier.net carrier.kicad_sch
  python3 tools/check_netlist.py /tmp/carrier.net
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from design import COMPONENTS, norm

# ---- tiny s-expression parser ----------------------------------------------
def parse(text):
    toks = re.findall(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+', text)
    def rd(i):
        assert toks[i] == "(", toks[i]
        out = []
        i += 1
        while toks[i] != ")":
            if toks[i] == "(":
                node, i = rd(i)
                out.append(node)
            else:
                t = toks[i]
                out.append(t[1:-1] if t.startswith('"') else t)
                i += 1
        return out, i + 1
    return rd(0)[0]

def find_all(node, tag):
    return [n for n in node if isinstance(n, list) and n and n[0] == tag]

netfile = sys.argv[1]
root = parse(open(netfile).read())
nets_sec = find_all(root, "nets")[0]

# netlist -> {(ref, pin): netname}
got = {}
netnames = {}
for net in find_all(nets_sec, "net"):
    name = next(n[1] for n in net if isinstance(n, list) and n[0] == "name")
    for node in find_all(net, "node"):
        ref = next(n[1] for n in node if isinstance(n, list) and n[0] == "ref")
        pin = next(n[1] for n in node if isinstance(n, list) and n[0] == "pin")
        if name.startswith("unconnected-("):
            continue          # KiCad's synthetic net for a no-connect marker
        got[(ref, pin)] = name
        netnames.setdefault(name, []).append(f"{ref}.{pin}")

errors = []

# ---- 1. design.py <-> netlist, both directions ------------------------------
want = {}
for ref, c in COMPONENTS.items():
    for pad, net in norm(c)[3].items():
        want[(ref, pad)] = net

for (ref, pad), net in want.items():
    if net is None:
        if (ref, pad) in got:
            errors.append(f"{ref}.{pad}: design says NC, netlist has '{got[(ref, pad)]}'")
    else:
        g = got.get((ref, pad))
        if g != net:
            errors.append(f"{ref}.{pad}: design says '{net}', netlist has '{g}'")
extra = set(got) - {k for k, v in want.items() if v is not None}
for ref, pad in sorted(extra):
    errors.append(f"{ref}.{pad}: in netlist ('{got[(ref, pad)]}') but not in design.py")

# ---- 2. config.h GPIO map <-> SuperMini socket nets -------------------------
cfg = open(os.path.join(os.path.dirname(__file__),
           "..", "..", "..", "src", "esp32-slave", "include", "config.h")).read()
def cpin(name):
    return int(re.search(rf"#define\s+{name}\s+(\d+)", cfg).group(1))

# socket position -> GPIO (pcb.md §2, TENSTAR silk confirmed measurements.md)
J1A_GPIO = {"4": 4, "5": 3, "6": 2, "7": 1, "8": 0}          # pins 1-3 = 5V/GND/3V3
J1B_GPIO = {"1": 5, "2": 6, "3": 7, "4": 8, "5": 9, "6": 10, "7": 20, "8": 21}

expect_gpio_net = {
    cpin("PIN_MIC_ADC"): "MIC_OUT",
    cpin("PIN_VBAT_ADC"): "VBAT_SENSE",
    cpin("PIN_POT_ADC"): "POT_WIPER",
    cpin("PIN_I2S_BCK"): "I2S_BCK",
    cpin("PIN_I2S_LRCK"): "I2S_LRCK",
    cpin("PIN_I2S_DOUT"): "I2S_DOUT",
    cpin("PIN_BTN_MODE"): "BTN_MODE",
    cpin("PIN_LED_ONBOARD"): None,      # GPIO8: onboard LED, must be NC
    cpin("PIN_BTN_BOOT"): None,         # GPIO9: BOOT, must be NC
    cpin("PIN_LED_NET"): "LED_NET_A",
    cpin("PIN_LED_MODE"): "LED_MODE_A",
    2: "GPIO2_PU",                      # strapping pull-up (DESIGN §4)
    21: "DBG_TX",
}
for row, gmap in (("JA1", J1A_GPIO), ("JB1", J1B_GPIO)):
    for pad, gpio in gmap.items():
        exp = expect_gpio_net.get(gpio, "?unmapped?")
        act = got.get((row, pad))
        if exp != act:
            errors.append(f"GPIO{gpio} ({row}.{pad}): config.h expects "
                          f"'{exp}', netlist has '{act}'")

# ---- 3. §2/§3 invariants ----------------------------------------------------
inv = [
    (("J2", "1"), "GND", "PCM5102A SCK must be tied to GND (PLL from BCK)"),
    (("J5", "2"), "BAT_MINUS", "B- must NOT be GND (DW01 low-side protection)"),
    (("J8", "2"), "BAT_MINUS", "JST B- must go to J5 B- only"),
    (("SW1", "2"), "VLOAD", "power switch COM feeds the load"),
    (("Q1", "3"), "VBAT_OUT", "P-FET drain at battery side"),
    (("D3", "2"), "VLOAD", "DNP naive-OR diode senses the load node (§4.2)"),
]
for key, expnet, why in inv:
    if got.get(key) != expnet:
        errors.append(f"invariant: {key[0]}.{key[1]} should be '{expnet}' ({why})")
if "BAT_MINUS" in netnames and \
        sorted(netnames["BAT_MINUS"]) != ["J5.2", "J8.2"]:
    errors.append(f"BAT_MINUS must be exactly J5.2+J8.2, got {sorted(netnames['BAT_MINUS'])}")

# ---- report -----------------------------------------------------------------
print(f"netlist: {len(netnames)} nets, {len(got)} connected pins; "
      f"design: {sum(1 for v in want.values() if v)} connected pins")
for name in sorted(netnames):
    print(f"  {name:12s} {' '.join(sorted(netnames[name]))}")
if errors:
    print(f"\nFAIL — {len(errors)} problems:")
    for e in errors:
        print("  !!", e)
    sys.exit(1)
print("\nOK — netlist matches design.py, config.h GPIO map, and §2/§3 invariants")
