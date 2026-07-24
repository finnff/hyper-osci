#!/usr/bin/env python3
"""Route carrier.kicad_pcb: grid A* over exact pad geometry.

- widths per pcb.md §6.4 (power 1.0 / 3V3 0.6 / DAC 0.5 / signal 0.3), with
  automatic fallback to narrower widths at pinch points (logged; 0.6mm of
  1oz copper carries >1A — WiFi peaks are 0.35A, so fallback is safe)
- GND is not routed: the double-sided pour owns it (checked by DRC after)
- vias allowed (0.8/0.4); non-analog nets pay a penalty inside the AGND
  island, everything pays one inside the antenna keep-out
Run from hw/carrier/ AFTER gen_board.py:  python3 tools/route.py
"""
import heapq, math, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import pcbnew
from pcbnew import FromMM, VECTOR2I

BASE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PCB = os.path.join(BASE, "carrier.kicad_pcb")
board = pcbnew.LoadBoard(PCB)

GRID = 0.127
W_MM, H_MM = 70.0, 50.0
NX, NY = int(W_MM / GRID) + 1, int(H_MM / GRID) + 1
CLR = 0.26   # board DRC min is 0.25; socket row gaps are 0.27 legal
F, B = 0, 1
LAYER_ID = {F: pcbnew.F_Cu, B: pcbnew.B_Cu}

WIDTHS = {"BAT_PLUS": 1.0, "BAT_MINUS": 1.0, "VBAT_OUT": 1.0, "VSW": 1.0,
          "VLOAD": 1.0, "VBUS_CHG": 1.0, "3V3": 0.6,
          "DAC_L": 0.5, "DAC_R": 0.5, "SCOPE_X": 0.5, "SCOPE_Y": 0.5, "GND": 0.4}
FALLBACK = [1.0, 0.8, 0.6, 0.4, 0.3]
H_OF = {1.0: 0.5, 0.8: 0.4, 0.6: 0.3, 0.4: 0.2, 0.3: 0.15}
ANALOG = {"DAC_L", "DAC_R", "SCOPE_X", "SCOPE_Y", "MIC_OUT", "GND", "3V3"}
ISLAND = (32.0, 0.0, 70.0, 15.0)          # x1,y1,x2,y2 (soft for non-analog)
ANT = (0.0, 6.0, 6.0, 28.0)               # antenna region (soft for all)

# ---- collect pads -----------------------------------------------------------
pads = []           # (net, cx, cy, dx, dy, layers)
netpads = {}        # net -> [(cx, cy)]
for fp in board.GetFootprints():
    for p in fp.Pads():
        net = p.GetNetname()
        bb = p.GetBoundingBox()
        # bbox center, not GetPosition(): offset-drill pads (SW1) differ
        cx = (bb.GetLeft() + bb.GetRight()) / 2e6
        cy = (bb.GetTop() + bb.GetBottom()) / 2e6
        dx = bb.GetWidth() / 2e6
        dy = bb.GetHeight() / 2e6
        ls = set()
        if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
            if p.IsOnLayer(pcbnew.F_Cu):
                ls.add(F)
            if p.IsOnLayer(pcbnew.B_Cu):
                ls.add(B)
        else:
            ls = {F, B}
        pads.append((net, cx, cy, dx, dy, ls))
        if net:
            netpads.setdefault(net, []).append((cx, cy, ls, dx, dy))

# dedupe same-position pads (dual-footprint twins)
for net in netpads:
    seen, out = set(), []
    for cx, cy, ls, dx, dy in netpads[net]:
        k = (round(cx, 2), round(cy, 2))
        if k not in seen:
            seen.add(k)
            out.append((cx, cy, ls, dx, dy))
    netpads[net] = out

routed_segs = {F: [], B: []}   # (x1,y1,x2,y2,halfw,net)
vias_out = []                  # (x,y,net)

# ---- owner maps: 0 = free, id = single net's zone, 255 = contested ---------
H_CLASSES = [0.15, 0.2, 0.3, 0.4, 0.5, 0.7]
NET_ID = {}
def net_id(n):
    if n not in NET_ID:
        NET_ID[n] = len(NET_ID) + 1
        assert NET_ID[n] < 254
    return NET_ID[n]
FOREIGN = 254          # NC pads: foreign to everyone

omap = {}

def rast_rect(bm, nid, cx, cy, dx, dy):
    ix0 = max(0, int((cx - dx) / GRID))
    ix1 = min(NX - 1, int((cx + dx) / GRID) + 1)
    iy0 = max(0, int((cy - dy) / GRID))
    iy1 = min(NY - 1, int((cy + dy) / GRID) + 1)
    for iy in range(iy0, iy1 + 1):
        base = iy * NX
        for ix in range(ix0, ix1 + 1):
            c = bm[base + ix]
            if c == 0:
                bm[base + ix] = nid
            elif c != nid:
                bm[base + ix] = 255

for h in H_CLASSES:
    for layer in (F, B):
        bm = bytearray(NX * NY)
        margin = 0.5 + h + 0.05
        i0 = int(margin / GRID) + 1
        for iy in range(NY):
            base = iy * NX
            if iy < i0 or iy > NY - 1 - i0:
                for ix in range(NX):
                    bm[base + ix] = 255
            else:
                for ix in range(i0):
                    bm[base + ix] = 255
                for ix in range(NX - i0, NX):
                    bm[base + ix] = 255
        omap[(h, layer)] = bm
        for pnet, cx, cy, dx, dy, ls in pads:
            if layer not in ls:
                continue
            nid = net_id(pnet) if pnet else FOREIGN
            rast_rect(bm, nid, cx, cy, dx + CLR + h, dy + CLR + h)

# §6.3: the AGND star-ground neck (only pour path between island and main
# GND) must stay clear on both layers, or a crossing trace severs the island
# region into its own connectivity cluster. Mark the neck strip and the
# gen_board neck-strap corridor as GND-OWNED: foreign nets are blocked, but
# GND stitch tracks may still thread the neck (keeping the single-crossing
# star topology if a stitch to the island region is ever needed).
NECKS = [(32.0, 13.0, 35.0, 15.0),      # pour neck strip
         (32.4, 8.5, 33.7, 18.1)]       # neck strap + vias (gen_board)
for h in H_CLASSES:
    for layer in (F, B):
        for nx1, ny1, nx2, ny2 in NECKS:
            rast_rect(omap[(h, layer)], net_id("GND"),
                      (nx1 + nx2) / 2, (ny1 + ny2) / 2,
                      (nx2 - nx1) / 2 + h, (ny2 - ny1) / 2 + h)

# holes (drills): vias must keep hole-to-hole >= 0.5 from every PTH drill
via_block = bytearray(NX * NY)
def rast_disc(bm, cx, cy, r):
    ix0, ix1 = max(0, int((cx - r) / GRID)), min(NX - 1, int((cx + r) / GRID) + 1)
    iy0, iy1 = max(0, int((cy - r) / GRID)), min(NY - 1, int((cy + r) / GRID) + 1)
    r2 = r * r
    for iy in range(iy0, iy1 + 1):
        for ix in range(ix0, ix1 + 1):
            if (ix * GRID - cx) ** 2 + (iy * GRID - cy) ** 2 <= r2:
                bm[iy * NX + ix] = 1
for fp0 in board.GetFootprints():
    for p0 in fp0.Pads():
        if p0.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
            dr = max(p0.GetDrillSizeX(), p0.GetDrillSizeY()) / 2e6
            if dr > 0:
                rast_disc(via_block, p0.GetPosition().x / 1e6,
                          p0.GetPosition().y / 1e6, dr + 0.75)

PRISTINE = None   # set after via_block exists
def snapshot_pristine():
    global PRISTINE
    PRISTINE = ({k: bytes(v) for k, v in omap.items()}, bytes(via_block))
def restore_pristine():
    for k in omap:
        omap[k][:] = PRISTINE[0][k]
    via_block[:] = PRISTINE[1]

def add_seg_obstacle(layer, x1, y1, x2, y2, hw, net):
    L = math.hypot(x2 - x1, y2 - y1)
    steps = max(1, int(L / GRID))
    nid = net_id(net)
    for h in H_CLASSES:
        bm = omap[(h, layer)]
        r = hw + CLR + h
        for i in range(steps + 1):
            t = i / steps
            rast_rect(bm, nid, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, r, r)

# pre-existing GND copper from gen_board (neck strap, seeded stitch grid):
# rasterize as owned obstacles so routed nets keep clearance from it
for t0 in board.GetTracks():
    if t0.GetClass() == "PCB_VIA":
        px0, py0 = t0.GetPosition().x / 1e6, t0.GetPosition().y / 1e6
        for l0 in (F, B):
            add_seg_obstacle(l0, px0, py0, px0, py0,
                             t0.GetWidth() / 2e6, t0.GetNetname())
        rast_disc(via_block, px0, py0, 0.95)
    else:
        l0 = F if t0.GetLayer() == pcbnew.F_Cu else B
        add_seg_obstacle(l0, t0.GetStart().x / 1e6, t0.GetStart().y / 1e6,
                         t0.GetEnd().x / 1e6, t0.GetEnd().y / 1e6,
                         t0.GetWidth() / 2e6, t0.GetNetname())

def blocked_cell(net, h, layer, ix, iy):
    c = omap[(h, layer)][iy * NX + ix]
    return c != 0 and c != net_id(net)

def soft_cost(net, x, y):
    c = 0.0
    if net not in ANALOG and ISLAND[0] <= x <= ISLAND[2] and ISLAND[1] <= y <= ISLAND[3]:
        c += 4.0
    if ANT[0] <= x <= ANT[2] and ANT[1] <= y <= ANT[3]:
        c += 2.0
    return c

DIRS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
        (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]

def astar(net, h, sx, sy, sl, sdx, sdy, tx, ty, tlayers, tdx, tdy):
    def cell(x):
        return int(round(x / GRID))
    start = (cell(sx), cell(sy), sl)
    tcx, tcy = cell(tx), cell(ty)
    targets = {(tcx, tcy, l) for l in tlayers}
    openq = [(0.0, 0.0, start, None)]
    best, parent = {start: 0.0}, {}
    hit = None
    expanded = 0
    while openq:
        f, g, cur, par = heapq.heappop(openq)
        if cur in parent and best.get(cur, 1e9) < g - 1e-9:
            continue
        parent[cur] = par
        if cur in targets:
            hit = cur
            break
        expanded += 1
        if expanded > 800000:
            return None
        cx0, cy0, cl = cur
        for dx, dy, dc in DIRS:
            nx, ny = cx0 + dx, cy0 + dy
            nxt = (nx, ny, cl)
            x, y = nx * GRID, ny * GRID
            ng = g + dc + soft_cost(net, x, y)
            if nxt in best and best[nxt] <= ng:
                continue
            if blocked_cell(net, h, cl, nx, ny):
                inside_t = abs(x - tx) < tdx - 0.05 and abs(y - ty) < tdy - 0.05
                inside_s = abs(x - sx) < sdx - 0.05 and abs(y - sy) < sdy - 0.05
                if not (inside_t or inside_s):
                    continue
            best[nxt] = ng
            hh = math.hypot(nx - tcx, ny - tcy)
            heapq.heappush(openq, (ng + hh, ng, nxt, cur))
        # via
        ol = 1 - cl
        nxt = (cx0, cy0, ol)
        ng = g + 40.0
        if not (nxt in best and best[nxt] <= ng):
            if not via_block[cy0 * NX + cx0] and \
               not blocked_cell(net, 0.7, cl, cx0, cy0) and \
               not blocked_cell(net, 0.7, ol, cx0, cy0):
                best[nxt] = ng
                heapq.heappush(openq, (ng + math.hypot(cx0 - tcx, cy0 - tcy), ng, nxt, cur))
    if hit is None:
        return None
    path = []
    cur = hit
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    return path[::-1]

def emit(net, h, path):
    segs, via_pts = [], []
    i = 0
    while i < len(path) - 1:
        x0, y0, l0 = path[i]
        j = i + 1
        if path[j][2] != l0:
            via_pts.append((x0 * GRID, y0 * GRID))
            i = j
            continue
        dx, dy = path[j][0] - x0, path[j][1] - y0
        while j + 1 < len(path) and path[j + 1][2] == l0 and \
                (path[j + 1][0] - path[j][0], path[j + 1][1] - path[j][1]) == (dx, dy):
            j += 1
        segs.append((x0 * GRID, y0 * GRID, path[j][0] * GRID, path[j][1] * GRID, l0))
        i = j
    for x1, y1, x2, y2, l in segs:
        add_seg_obstacle(l, x1, y1, x2, y2, h, net)
    for x, y in via_pts:
        vias_out.append((x, y, net))
        rast_disc(via_block, x, y, 0.95)
        for l in (F, B):
            add_seg_obstacle(l, x, y, x, y, 0.4, net)
    return segs, via_pts

# regional most-constrained-first: NW escapes, then the power spines whose
# corridors signals would otherwise consume; remaining nets thin-before-fat
PRIORITY = ["POT_WIPER", "Q2_B", "VBAT_SENSE", "GPIO2_PU", "BTN_MODE",
            "DAC_L", "DAC_R", "SCOPE_X", "SCOPE_Y",
            "I2S_BCK", "I2S_LRCK", "I2S_DOUT",
            "VLOAD", "MIC_OUT", "BAT_PLUS", "BAT_MINUS",
            "VBAT_OUT", "VBUS_CHG", "VSW", "3V3", "VSW_SENSE", "TL431_K",
            "GATE", "DBG_TX", "LED_NET_A", "LED_MODE_A"]

snapshot_pristine()

def run_phase_a(order):
    all_tracks, fail = [], []
    vias_out.clear()
    for net in order:
        plist = netpads[net]
        if net == "GND" or len(plist) < 2:
            continue
        connected = [plist[0]]
        rest = list(plist[1:])
        while rest:
            bi, bd = 0, 1e18
            for i, pp in enumerate(rest):
                for qq in connected:
                    d = (pp[0] - qq[0]) ** 2 + (pp[1] - qq[1]) ** 2
                    if d < bd:
                        bd, bi = d, i
            px, py, pls, pdx, pdy = rest.pop(bi)
            want = WIDTHS.get(net, 0.3)
            done = False
            cands = sorted(connected,
                           key=lambda q: (px - q[0]) ** 2 + (py - q[1]) ** 2)[:4]
            for qx, qy, qls, qdx, qdy in cands:
                for w in [x for x in FALLBACK if x <= want]:
                    h = H_OF[w]
                    sl = F if F in pls else B
                    path = astar(net, h, px, py, sl, pdx, pdy,
                                 qx, qy, qls, qdx, qdy)
                    if path:
                        segs, vps = emit(net, h, path)
                        for x1, y1, x2, y2, l in segs:
                            all_tracks.append((x1, y1, x2, y2, w, l, net))
                        if w < want:
                            print(f"  note: {net} narrowed {want}->{w}mm")
                        done = True
                        break
                if done:
                    break
            if not done:
                fail.append(net)
                print(f"  FAIL: {net} {px:.1f},{py:.1f}")
            connected.append((px, py, pls, pdx, pdy))
    return all_tracks, fail

promote = []
all_tracks = []
for attempt in range(8):
    restore_pristine()
    order = promote + [n for n in PRIORITY if n not in promote] +         sorted([n for n in netpads if n not in PRIORITY and n not in promote
                and n != "GND"], key=lambda n: WIDTHS.get(n, 0.3))
    all_tracks, fails = run_phase_a(order)
    print(f"attempt {attempt + 1}: {len(all_tracks)} segments, "
          f"{len(vias_out)} vias, {len(fails)} failures")
    if not fails:
        break
    promote = [n for n in fails if n not in promote] + promote
fail = fails

# ---- write into board -------------------------------------------------------
nets_by_name = {ni.GetNetname(): ni for ni in board.GetNetInfo().NetsByNetcode().values()}
for x1, y1, x2, y2, w, l, net in all_tracks:
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(VECTOR2I(FromMM(x1), FromMM(y1)))
    t.SetEnd(VECTOR2I(FromMM(x2), FromMM(y2)))
    t.SetWidth(FromMM(w))
    t.SetLayer(LAYER_ID[l])
    t.SetNet(nets_by_name[net])
    board.Add(t)
seen_via = set()
for x, y, net in vias_out:
    k = (round(x, 2), round(y, 2))
    if k in seen_via:
        continue
    seen_via.add(k)
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(VECTOR2I(FromMM(x), FromMM(y)))
    v.SetDrill(FromMM(0.4))
    v.SetWidth(FromMM(0.8))
    v.SetNet(nets_by_name[net])
    for setter, args in [("SetFrontTentingMode", (pcbnew.TENTING_MODE_TENTED,)),
                         ("SetBackTentingMode", (pcbnew.TENTING_MODE_TENTED,))]:
        if hasattr(v, setter) and hasattr(pcbnew, "TENTING_MODE_TENTED"):
            getattr(v, setter)(*args)
    board.Add(v)

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)

# ---- phase B: stitch only the GND pads the pour cannot reach ----------------
import json, subprocess, tempfile
def orphan_gnd_pads():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        rep = tf.name
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "-o", rep, PCB],
                   capture_output=True)
    r = json.load(open(rep))
    orphans = []
    for u in r.get("unconnected_items", []):
        for it in u.get("items", []):
            d = it["description"]
            if d.startswith("PTH pad") and "[GND]" in d:
                orphans.append((it["pos"]["x"], it["pos"]["y"]))
    # snap to actual GND pads
    out = []
    for ox, oy in orphans:
        for pnet, cx, cy, dx, dy, ls in pads:
            if pnet == "GND" and abs(cx - ox) < 0.6 and abs(cy - oy) < 0.6:
                out.append((cx, cy, dx, dy))
                break
    return list({(round(x, 2), round(y, 2)): (x, y, dx, dy)
                 for x, y, dx, dy in out}.values())

orphans = orphan_gnd_pads()
print(f"phase B: {len(orphans)} pour-orphaned GND pads")
gnd_all = [(cx, cy, dx, dy) for pnet, cx, cy, dx, dy, ls in pads if pnet == "GND"]
stitch_fail = []
stitched = []
for px, py, pdx, pdy in orphans:
    cands = sorted([g for g in gnd_all if abs(g[0] - px) > 0.1 or abs(g[1] - py) > 0.1],
                   key=lambda g: (g[0] - px) ** 2 + (g[1] - py) ** 2)[:6]
    ok = False
    for qx, qy, qdx, qdy in cands:
        if any(abs(qx - ox) < 0.1 and abs(qy - oy) < 0.1 for ox, oy, _, _ in orphans):
            continue
        for w in (0.4, 0.3):
            h = H_OF[w]
            path = astar("GND", h, px, py, F, pdx, pdy, qx, qy, {F, B}, qdx, qdy)
            if path:
                segs, vps = emit("GND", h, path)
                for x1, y1, x2, y2, l in segs:
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(VECTOR2I(FromMM(x1), FromMM(y1)))
                    t.SetEnd(VECTOR2I(FromMM(x2), FromMM(y2)))
                    t.SetWidth(FromMM(w))
                    t.SetLayer(LAYER_ID[l])
                    t.SetNet(nets_by_name["GND"])
                    board.Add(t)
                for vx, vy in vps:      # emit only queues vias — place them
                    v = pcbnew.PCB_VIA(board)
                    v.SetPosition(VECTOR2I(FromMM(vx), FromMM(vy)))
                    v.SetDrill(FromMM(0.4))
                    v.SetWidth(FromMM(0.8))
                    v.SetNet(nets_by_name["GND"])
                    board.Add(v)
                stitched.append((px, py))
                ok = True
                break
        if ok:
            break
    if not ok:
        stitch_fail.append((px, py))
        print(f"  STITCH FAIL: GND pad {px:.1f},{py:.1f}")
print(f"phase B: stitched {len(stitched)}, failed {len(stitch_fail)}")

# ---- phase C: pour-stitching vias every ~8mm (pcb.md par.6.2) ---------------
AVOID = [(0, 6, 6.5, 28),            # antenna keepout (no pour)
         (30.5, 0, 32.5, 14),        # agnd moat W
         (34.5, 13, 59.5, 15),       # agnd moat S
         (58.5, 13, 70, 16.5)]       # agnd moat SE

# a stitch via is only useful (and never harmful) landing on MAIN pour on
# both layers — a via in a pour pocket welds an isolated F+B scrap together,
# which island removal then keeps ("connected to a via") as floating copper
def largest_outline(L):
    best = None
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        polys = z.GetFilledPolysList(LAYER_ID[L])
        for i in range(polys.OutlineCount()):
            o = polys.Outline(i)
            if best is None or o.Area() > best.Area():
                best = o
    return best
MAIN_F, MAIN_B = largest_outline(F), largest_outline(B)
nstitch = 0
yv = 5.0
while yv < 47:
    xv = 5.0
    while xv < 67:
        bad = any(a <= xv <= c and b <= yv <= d for a, b, c, d in AVOID)
        cxv, cyv = int(round(xv / GRID)), int(round(yv / GRID))
        pv = VECTOR2I(FromMM(xv), FromMM(yv))
        if not bad and not via_block[cyv * NX + cxv] and \
           omap[(0.7, F)][cyv * NX + cxv] == 0 and \
           omap[(0.7, B)][cyv * NX + cxv] == 0 and \
           MAIN_F.PointInside(pv) and MAIN_B.PointInside(pv):
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(VECTOR2I(FromMM(xv), FromMM(yv)))
            v.SetDrill(FromMM(0.4))
            v.SetWidth(FromMM(0.8))
            v.SetNet(nets_by_name["GND"])
            board.Add(v)
            rast_disc(via_block, xv, yv, 0.95)
            nstitch += 1
        xv += 8.0
    yv += 8.0
print(f"phase C: {nstitch} GND stitching vias")
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())

# ---- phase D: union-find the pour clusters, via-bridge each one to main -----
# The dense routing slices the pour into pad-attached ribbons; KiCad reports
# every severed cluster as an unconnected item (with useless zone-origin
# anchor positions, so we cluster the filled outlines ourselves). Outlines
# are linked by GND through-holes/vias and by GND tracks (the neck strap);
# each stray cluster gets a via where the opposite layer is main copper.
nD = 0
gnd_single = False
for rnd in range(5):
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    outls = []
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for L in (F, B):
            polys = z.GetFilledPolysList(LAYER_ID[L])
            for i in range(polys.OutlineCount()):
                o = polys.Outline(i)
                if o.Area() > 5e8:
                    outls.append((L, o, o.BBox()))
    parent = list(range(len(outls)))
    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    def union(a, b):
        parent[find(a)] = find(b)
    def owner(L, pt):
        for idx, (l2, o2, bb2) in enumerate(outls):
            if l2 == L and bb2.Contains(pt) and o2.PointInside(pt):
                return idx
        return None
    thru = []                       # points with copper on both layers
    for fp0 in board.GetFootprints():
        for p0 in fp0.Pads():
            if p0.GetNetname() == "GND" and \
               p0.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                thru.append(p0.GetPosition())
    straps = []                     # GND tracks: sample along their length
    for t0 in board.GetTracks():
        if t0.GetNetname() != "GND":
            continue
        if t0.GetClass() == "PCB_VIA":
            thru.append(t0.GetPosition())
        else:
            L = F if t0.GetLayer() == pcbnew.F_Cu else B
            s0, e0 = t0.GetStart(), t0.GetEnd()
            n = max(1, int(math.hypot(e0.x - s0.x, e0.y - s0.y) / 1e6))
            straps.append((L, [VECTOR2I(s0.x + (e0.x - s0.x) * k // n,
                                        s0.y + (e0.y - s0.y) * k // n)
                               for k in range(n + 1)]))
    for pt in thru:
        a, b2 = owner(F, pt), owner(B, pt)
        if a is not None and b2 is not None:
            union(a, b2)
    for L, pts in straps:
        own = [o2 for o2 in (owner(L, pt) for pt in pts) if o2 is not None]
        for o2 in own[1:]:
            union(own[0], o2)
    clusters = {}
    for i in range(len(outls)):
        clusters.setdefault(find(i), []).append(i)
    if len(clusters) == 1:
        print(f"phase D round {rnd}: single GND cluster")
        gnd_single = True
        break
    main_root = max(clusters,
                    key=lambda r: sum(outls[i][1].Area() for i in clusters[r]))
    print(f"phase D round {rnd}: {len(clusters)} clusters "
          f"({len(outls)} outlines)")
    placed = 0
    for root, members in clusters.items():
        if root == main_root:
            continue
        # via specs: normal 0.8/0.4 checked at 0.7-class; small 0.65/0.3 at
        # 0.4-class (free cell there still guarantees >=0.36mm copper
        # clearance for the 0.325 via radius) for power-walled pockets
        spot = fallback = None
        for vw, vd, vcls in ((0.8, 0.4, 0.7), (0.65, 0.3, 0.4)):
            for idx in members:
                L, o, bb = outls[idx]
                OL = B if L == F else F
                yy = bb.GetTop() / 1e6 + 0.4
                while yy < bb.GetBottom() / 1e6 and spot is None:
                    xx = bb.GetLeft() / 1e6 + 0.4
                    while xx < bb.GetRight() / 1e6 and spot is None:
                        cx0, cy0 = int(round(xx / GRID)), int(round(yy / GRID))
                        pt = VECTOR2I(FromMM(xx), FromMM(yy))
                        if 0 <= cx0 < NX and 0 <= cy0 < NY and \
                           not via_block[cy0 * NX + cx0] and \
                           not blocked_cell("GND", vcls, F, cx0, cy0) and \
                           not blocked_cell("GND", vcls, B, cx0, cy0) and \
                           o.PointInside(pt):
                            oidx = owner(OL, pt)
                            if oidx is not None and find(oidx) != find(root):
                                if find(oidx) == main_root:
                                    spot = (xx, yy, oidx, vw, vd)
                                elif fallback is None:
                                    fallback = (xx, yy, oidx, vw, vd)
                        xx += 0.508
                    yy += 0.508
                if spot:
                    break
            if spot:
                break
        use = spot or fallback
        if use:
            xx, yy, oidx, vw, vd = use
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(VECTOR2I(FromMM(xx), FromMM(yy)))
            v.SetDrill(FromMM(vd))
            v.SetWidth(FromMM(vw))
            v.SetNet(nets_by_name["GND"])
            board.Add(v)
            rast_disc(via_block, xx, yy, 0.95)
            union(root, oidx)
            nD += 1
            placed += 1
            continue
        # no via spot (stacked F/B pockets): A*-route a GND track from a
        # pad inside this cluster to the nearest main-cluster GND pad
        def pad_in(cx, cy, idxs):
            pt = VECTOR2I(FromMM(cx), FromMM(cy))
            return any(outls[i][2].Contains(pt) and outls[i][1].PointInside(pt)
                       for i in idxs)
        spads = [(cx, cy, dx, dy) for pnet, cx, cy, dx, dy, ls in pads
                 if pnet == "GND" and pad_in(cx, cy, members)]
        mpads = [(cx, cy, dx, dy) for pnet, cx, cy, dx, dy, ls in pads
                 if pnet == "GND" and pad_in(cx, cy, clusters[main_root])]
        ok = False
        for px, py, pdx, pdy in spads:
            for qx, qy, qdx, qdy in sorted(
                    mpads, key=lambda g: (g[0] - px) ** 2 + (g[1] - py) ** 2)[:8]:
                for w in (0.4, 0.3):
                    path = astar("GND", H_OF[w], px, py, F, pdx, pdy,
                                 qx, qy, {F, B}, qdx, qdy)
                    if not path:
                        continue
                    segs, vps = emit("GND", H_OF[w], path)
                    for x1, y1, x2, y2, l in segs:
                        t = pcbnew.PCB_TRACK(board)
                        t.SetStart(VECTOR2I(FromMM(x1), FromMM(y1)))
                        t.SetEnd(VECTOR2I(FromMM(x2), FromMM(y2)))
                        t.SetWidth(FromMM(w))
                        t.SetLayer(LAYER_ID[l])
                        t.SetNet(nets_by_name["GND"])
                        board.Add(t)
                    for vx, vy in vps:
                        v = pcbnew.PCB_VIA(board)
                        v.SetPosition(VECTOR2I(FromMM(vx), FromMM(vy)))
                        v.SetDrill(FromMM(0.4))
                        v.SetWidth(FromMM(0.8))
                        v.SetNet(nets_by_name["GND"])
                        board.Add(v)
                    nD += 1
                    placed += 1
                    ok = True
                    break
                if ok:
                    break
            if ok:
                break
        if not ok:
            L0, o0, bb0 = outls[members[0]]
            print(f"phase D: cluster at ({bb0.GetCenter().x/1e6:.1f},"
                  f"{bb0.GetCenter().y/1e6:.1f}) UNBRIDGEABLE")
    if placed == 0:
        print(f"phase D: STUCK — {len(clusters)} clusters left, "
              "no legal bridge spot; manual fix needed")
        break
print(f"phase D: {nD} cluster-bridging vias")

# ---- phase E: prune GND vias the pour never reached -------------------------
# a seeded via can land where pad clearances leave no fill on one layer;
# DRC flags it via_dangling/unconnected. Connected on both layers or gone.
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
fout = {F: [], B: []}
for z in board.Zones():
    if z.GetIsRuleArea():
        continue
    for L in (F, B):
        polys = z.GetFilledPolysList(LAYER_ID[L])
        for i in range(polys.OutlineCount()):
            o = polys.Outline(i)
            if o.Area() > 5e8:
                fout[L].append((o, o.BBox()))
drop = []
for t0 in list(board.GetTracks()):
    if t0.GetClass() == "PCB_VIA" and t0.GetNetname() == "GND":
        pt = t0.GetPosition()
        if not all(any(bb.Contains(pt) and o.PointInside(pt)
                       for o, bb in fout[L]) for L in (F, B)):
            drop.append(t0)
for t0 in drop:
    board.Remove(t0)
print(f"phase E: pruned {len(drop)} dangling GND vias")

filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
pcbnew.SaveBoard(PCB, board)
print("saved", PCB)
sys.exit(1 if (fail or stitch_fail or not gnd_single) else 0)
