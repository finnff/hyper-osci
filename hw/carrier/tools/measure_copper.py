#!/usr/bin/env python3
"""What the routed copper actually is — width breakdown and pad-to-pad DC.

pcb.md 6.4 carries a table of "as built" trace widths and an end-to-end
resistance for VLOAD, and every time the board is re-routed that table is
silently wrong.  It has been wrong twice.  This regenerates it from
carrier.kicad_pcb so the doc can be updated from a measurement instead of a
memory.

Resistance is a nodal solve over the real track graph, not a longest-path
guess: the router leaves parallel runs and via stitches on the power nets, and
a single path over-states them.  Tracks are 1 oz copper (0.4926 mohm/square);
a via barrel is taken as 1 mohm, which is the usual figure for a 0.4 mm drill
through 1.6 mm of FR4 at 25 um of plating and is small enough not to matter.
The GND pour is NOT modelled -- ask this about the routed nets only.

Run from hw/carrier/:  /usr/bin/python3 tools/measure_copper.py [--json]
"""
import collections
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np                                      # noqa: E402
import pcbnew                                           # noqa: E402

BASE = os.environ.get("CARRIER_BASE") or os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))
PCB = os.path.join(BASE, "carrier.kicad_pcb")

RHO_SQ_MOHM = 0.4926        # 1 oz (35 um) copper, milliohms per square
VIA_MOHM = 1.0

# The pad pairs pcb.md 6.4 quotes a number for.  (net, from, to, what).
PROBES = [
    ("VLOAD", ("SW1", "2"), ("JA1", "1"), "SW1 -> SuperMini 5V pin"),
    ("VLOAD", ("C1", "1"), ("JA1", "1"), "C1 + -> SuperMini 5V pin"),
    ("VSW", ("Q1", "2"), ("SW1", "1"), "Q1 source -> SW1"),
    ("BAT_PLUS", ("J8", "1"), ("J6", "1"), "J8 + -> TP4056 B+"),
    ("BAT_MINUS", ("J8", "2"), ("J5", "2"), "J8 - -> TP4056 B-"),
    ("VBAT_OUT", ("J6", "2"), ("Q1", "3"), "TP4056 OUT+ -> Q1 drain"),
    ("VBUS_CHG", ("J9", "1"), ("D2", "2"), "TP4056 IN+ -> D2 anode"),
    ("3V3", ("JA1", "3"), ("J2", "6"), "SuperMini 3V3 -> DAC VIN"),
]
# Nets whose width breakdown the doc tabulates.
TABLE = ["BAT_PLUS", "BAT_MINUS", "VBAT_OUT", "VBUS_CHG", "VSW", "VLOAD",
         "3V3", "DAC_L", "DAC_R", "SCOPE_X", "SCOPE_Y", "MIC_OUT"]

Q = 1000                    # node quantisation, 1 um


def key(p, layer):
    return (round(p.x / Q), round(p.y / Q), layer)


def load():
    b = pcbnew.LoadBoard(PCB)
    if b is None:
        sys.exit("could not load " + PCB)
    return b


def widths(board):
    """net -> {width_mm: length_mm}, plus via counts."""
    out = collections.defaultdict(lambda: collections.defaultdict(float))
    vias = collections.Counter()
    for t in board.GetTracks():
        net = t.GetNetname()
        if t.GetClass() == "PCB_VIA":
            vias[net] += 1
            continue
        L = t.GetLength() / 1e6
        if L <= 0:
            continue
        out[net][round(t.GetWidth() / 1e6, 3)] += L
    return out, vias


def graph(board, net):
    """Nodes and resistive edges for one net.  Returns (edges, pad_nodes)."""
    edges = []                      # (node_a, node_b, mohm)
    pad_nodes = collections.defaultdict(set)     # (ref, padnum) -> {node}

    # Pads first: every pad is one node per copper layer it touches, and a
    # through-hole pad shorts its own layers.
    pads = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != net:
                continue
            ref = (fp.GetReference(), p.GetNumber())
            bb = p.GetBoundingBox()
            box = (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom())
            thru = p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD
            layers = [pcbnew.F_Cu, pcbnew.B_Cu] if thru else [p.GetLayer()]
            nodes = [("pad", ref, l) for l in layers]
            for a, b in zip(nodes, nodes[1:]):
                edges.append((a, b, 0.01))      # barrel of a plated pad
            pads.append((ref, box, layers, nodes))
            # A footprint may carry the same pad number twice — D2's dual
            # SMA/DO-41 landing, SW2's twin tactile pins.  KiCad treats those
            # as one net node, so short them here too or the solve sees an
            # open where the board has none.
            if pad_nodes[ref]:
                edges.append((sorted(pad_nodes[ref], key=repr)[0], nodes[0], 0.01))
            for n in nodes:
                pad_nodes[ref].add(n)

    def attach(pt, layer):
        """Node for a track end: the pad it lands in, else the point itself."""
        for ref, box, layers, nodes in pads:
            if layer in layers and box[0] <= pt.x <= box[2] \
                    and box[1] <= pt.y <= box[3]:
                return ("pad", ref, layer)
        return key(pt, layer)

    for t in board.GetTracks():
        if t.GetNetname() != net:
            continue
        if t.GetClass() == "PCB_VIA":
            a = attach(t.GetPosition(), pcbnew.F_Cu)
            b = attach(t.GetPosition(), pcbnew.B_Cu)
            edges.append((a, b, VIA_MOHM))
            continue
        L = t.GetLength() / 1e6
        w = t.GetWidth() / 1e6
        if L <= 0 or w <= 0:
            continue
        lay = t.GetLayer()
        edges.append((attach(t.GetStart(), lay), attach(t.GetEnd(), lay),
                      RHO_SQ_MOHM * L / w))
    return edges, pad_nodes


def resistance(edges, pad_nodes, a, b):
    """DC resistance between two pads, by nodal analysis.  None if open."""
    if a not in pad_nodes or b not in pad_nodes:
        return None, "no such pad"
    nodes = sorted({n for e in edges for n in e[:2]}, key=repr)
    if not nodes:
        return None, "net has no copper"
    idx = {n: i for i, n in enumerate(nodes)}
    N = len(nodes)
    G = np.zeros((N, N))
    for u, v, r in edges:
        if u == v or r <= 0:
            continue
        g = 1.0 / r
        i, j = idx[u], idx[v]
        G[i, i] += g
        G[j, j] += g
        G[i, j] -= g
        G[j, i] -= g
    # inject 1 mA at any node of `a`, sink at any node of `b`, ground `b`
    src, snk = sorted(pad_nodes[a], key=repr)[0], sorted(pad_nodes[b], key=repr)[0]
    if src == snk:
        return 0.0, "same node"
    keep = [i for i in range(N) if i != idx[snk]]
    Gr = G[np.ix_(keep, keep)]
    I = np.zeros(len(keep))
    I[keep.index(idx[src])] = 1.0
    try:
        V = np.linalg.solve(Gr, I)
    except np.linalg.LinAlgError:
        return None, "not connected (singular)"
    if not np.all(np.isfinite(V)):
        return None, "not connected"
    return V[keep.index(idx[src])], ""


def main():
    board = load()
    wid, vias = widths(board)
    as_json = "--json" in sys.argv
    result = {"widths": {}, "probes": []}

    if not as_json:
        print("as-built track widths (mm of track at each width)\n")
        print(f"  {'net':<11} {'total':>7}  breakdown")
    for net in TABLE:
        w = wid.get(net)
        if not w:
            continue
        tot = sum(w.values())
        parts = ", ".join(f"{L:.1f} mm @ {ww:.2f}"
                          for ww, L in sorted(w.items(), reverse=True))
        result["widths"][net] = {"total_mm": round(tot, 2),
                                 "by_width": {str(k): round(v, 2)
                                              for k, v in sorted(w.items())},
                                 "vias": vias.get(net, 0),
                                 "min_mm": min(w)}
        if not as_json:
            print(f"  {net:<11} {tot:7.1f}  {parts}"
                  + (f"   +{vias[net]} vias" if vias.get(net) else ""))

    if not as_json:
        print("\npad-to-pad DC resistance (1 oz copper, nodal solve)\n")
    for net, a, b, what in PROBES:
        edges, pads = graph(board, net)
        r, why = resistance(edges, pads, a, b)
        rec = {"net": net, "from": ".".join(a), "to": ".".join(b),
               "what": what, "mohm": None if r is None else round(r, 2),
               "note": why}
        result["probes"].append(rec)
        if not as_json:
            val = f"{r:8.2f} mohm" if r is not None else f"{'--':>8}  ({why})"
            print(f"  {net:<11} {a[0]}.{a[1]:<3} -> {b[0]}.{b[1]:<3} {val}"
                  f"   {what}")
    if as_json:
        print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
