#!/usr/bin/env python3
"""
bind_copper.py -- promote a board's free copper into routed <signal>s (Step 5A).

The model: a net can be a TREE -- branching like suburban streets rather than
running point-to-point -- so a trace end legitimately lands on ANOTHER trace
(a T-junction) or floats into the coming pour (a thermal), not always on a
pad. Whether and where a given board has such nets varies; the algorithm
handles both shapes. So:

  * work per ROUTE (the PCBmodE paths), never merge routes into groups;
  * bind a route to the net of whichever END hits a pad (tightest wins);
  * a route whose ends hit no pad but whose two ends' NEAREST pads agree on a
    net takes that net (crystal clusters, power stubs);
  * a still-unbound route inherits the net the MOST bound routes connect to it
    at a T-junction (majority vote, bidirectional, propagated until stable);
  * trust Eagle's linked-schematic DRC to flag any wrong bind -- don't try to
    be perfect up front. SEEDS are the human's authoritative overrides for the
    cases geometry can't decide ("lands on" vs "passes over").

Pad geometry comes from the .brd's OWN embedded <libraries> (the placed truth);
nets come from the signals' <contactref>s. OX/OY come from the board's
<board>.brd.transform.json sidecar (written by place.py); --ox/--oy override.

Usage (branch the board first; Eagle closed; afterward File > Reload):
    python3 bind_copper.py <repo-dir> "<board.brd>" report [top|bottom]
    python3 bind_copper.py <repo-dir> "<board.brd>" apply  [top|bottom]
    python3 bind_copper.py <repo-dir> "<board.brd>" show ROUTEKEY ... | --net=NET | --suspect

Seeds (sticky, stored in <board>.brd.seeds-<side>.json):
    ROUTEKEY=NET            bind this route to NET, locked
    MILX,MILY=NET           same, route resolved from an Eagle mil readout
    L<layer>=NET            answer a SHOW layer by number

`show` copies each named trace onto its own high layer (200+) so it can be
toggled visually in Eagle and answered by layer number; `apply` cleans the
SHOW layers back up. `apply` gathers every wire on the side's layer (free AND
already-bound) so re-runs self-correct.
"""
import argparse, glob, json, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arcfit

PAD_TOL = 0.95     # route end -> pad center (ends sit at pad EDGE; neighbours are farther)
JCT_TOL = 0.30     # route end -> another route's body = a real T-junction
SHOW_BASE = 200    # high layers for "show me this trace" copies
LAYER = {'top': 1, 'bottom': 16}


def find_master(path):
    path = os.path.expanduser(path.rstrip('/\\'))
    if os.path.isfile(path):
        return path
    cands = [p for p in sorted(glob.glob(os.path.join(path, "*.json"))) if _is_master(p)]
    if not cands:
        sys.exit(f"no master board json (with components + outline) found in {path}")
    if len(cands) > 1:
        sys.exit(f"multiple master candidates in {path} -- pass the .json explicitly")
    return cands[0]


def _is_master(p):
    try:
        return {'components', 'outline'} <= set(json.load(open(p)))
    except Exception:
        return False


def load_routing(master_path):
    base = os.path.basename(master_path).rsplit('.json', 1)[0]
    rp = os.path.join(os.path.dirname(master_path), f"{base}_routing.json")
    if not os.path.exists(rp):
        hits = glob.glob(os.path.join(os.path.dirname(master_path), "*_routing.json"))
        if len(hits) != 1:
            sys.exit("could not find a unique *_routing.json next to the master")
        rp = hits[0]
    return json.load(open(rp))


def load_offset(board_path, ox, oy):
    if ox is not None and oy is not None:
        return ox, oy
    sidecar = board_path + ".transform.json"
    if os.path.exists(sidecar):
        d = json.load(open(sidecar))
        return (ox if ox is not None else d['ox']), (oy if oy is not None else d['oy'])
    sys.exit(f"no {os.path.basename(sidecar)} and no --ox/--oy")


def load_board(board_path):
    """Pads from the .brd's embedded <libraries> + placed elements; nets from contactrefs."""
    brd = open(board_path).read()
    libsec = re.search(r'<libraries>(.*?)</libraries>', brd, re.S).group(1)
    pkgpads = {}
    for lm in re.finditer(r'<library name="([^"]+)"[^>]*>(.*?)</library>', libsec, re.S):
        for pm in re.finditer(r'<package name="([^"]+)"[^>]*>(.*?)</package>', lm.group(2), re.S):
            pkgpads[(lm.group(1), pm.group(1))] = {p: (float(x), float(y))
                for p, x, y in re.findall(r'<pad name="([^"]+)" x="(-?[\d.]+)" y="(-?[\d.]+)"', pm.group(2))}
    padpos = {}
    # parse rot from ANYWHERE in the element tag -- a smashed element has
    # `... y="40" smashed="yes" rot="R180">`, so rot is NOT adjacent to y.
    for em in re.finditer(r'<element name="([^"]+)" library="([^"]+)"[^>]*? package="([^"]+)"([^>]*?)/?>', brd):
        nm, lib, pkg, attrs = em.groups()
        x = float(re.search(r'\bx="(-?[\d.]+)"', attrs).group(1))
        y = float(re.search(r'\by="(-?[\d.]+)"', attrs).group(1))
        rm = re.search(r'\brot="(M?)R(\d+)"', attrs)
        mir, deg = (rm.group(1), int(rm.group(2))) if rm else ('', 0)
        a = math.radians(deg); cs, sn = math.cos(a), math.sin(a); mx = -1 if mir == 'M' else 1
        for p, (px, py) in pkgpads.get((lib, pkg), {}).items():
            padpos[(nm, p)] = (x + mx*px*cs - py*sn, y + mx*px*sn + py*cs)
    pad2net = {}
    for sm in re.finditer(r'<signal name="([^"]+)"[^>]*>(.*?)</signal>', brd, re.S):
        for e, p in re.findall(r'<contactref element="([^"]+)" pad="([^"]+)"', sm.group(2)):
            pad2net[(e, p)] = sm.group(1)
    return brd, padpos, pad2net


def route_geom(routing, side, ox, oy):
    """Each PCBmodE route -> (key, polyline verts in .brd frame). Whole body kept
    so T-junction tests can use the BODY, not just the ends."""
    out = []
    for key, r in routing['routes'][side].items():
        subs, _ = arcfit.fit_path(r['value'], tol=0.02)
        pts = [(x + ox, -y + oy) for sub in subs for (x, y, cv) in sub[0]]
        out.append((key, pts))
    return out


def d2seg(p, a, b):
    ax, ay = a; bx, by = b; dx, dy = bx - ax, by - ay; L = dx*dx + dy*dy
    t = 0 if L == 0 else max(0, min(1, ((p[0]-ax)*dx + (p[1]-ay)*dy) / L))
    return math.hypot(p[0] - (ax + t*dx), p[1] - (ay + t*dy))


def near_body(pt, verts):
    return min((d2seg(pt, verts[k], verts[k+1]) for k in range(len(verts)-1)), default=9)


def classify(brd, padpos, pad2net, routes, seeds):
    ends = [(v[0], v[-1]) for _, v in routes]
    verts = [v for _, v in routes]
    idx = {key: i for i, (key, v) in enumerate(routes)}
    netof = [None] * len(routes); how = [None] * len(routes); locked = [False] * len(routes)

    def pad_at(pt):
        d, k = min((math.hypot(pt[0]-x, pt[1]-y), k) for k, (x, y) in padpos.items())
        return (d, k) if d < PAD_TOL and pad2net.get(k) else None

    def nearest_net(pt, tol=5.0):
        d, k = min((math.hypot(pt[0]-x, pt[1]-y), k) for k, (x, y) in padpos.items())
        return pad2net.get(k) if d < tol and pad2net.get(k) else None

    # 0) SEEDS -- authoritative, locked
    for key, net in seeds.items():
        if key in idx:
            i = idx[key]; netof[i] = net; how[i] = f"SEED={net}"; locked[i] = True
    # 1) pad-end binding: tightest pad end wins
    for i, (s, e) in enumerate(ends):
        if locked[i]:
            continue
        cand = [(c[0], pad2net[c[1]], c[1]) for c in (pad_at(s), pad_at(e)) if c]
        if cand:
            d, net, pad = min(cand); netof[i] = net; how[i] = f"pad {pad[0]}.{pad[1]} (d={d:.2f})"
    # 2) both-ends-agree on nearest net
    for i in range(len(routes)):
        if netof[i]:
            continue
        nn = [nearest_net(pt) for pt in ends[i]]
        if nn[0] and nn[0] == nn[1]:
            netof[i] = nn[0]; how[i] = f"both ends -> {nn[0]}"
    # 3) majority-vote T-junction propagation, until stable
    def tjunction(i, j):
        if min((near_body(p, verts[j]) for p in ends[i]), default=9) < JCT_TOL:
            return True
        if min((near_body(p, verts[i]) for p in ends[j]), default=9) < JCT_TOL:
            return True
        return False
    for _ in range(12):
        changed = False
        for i in range(len(routes)):
            if netof[i]:
                continue
            votes = {}
            for j in range(len(routes)):
                if j != i and netof[j] and tjunction(i, j):
                    votes[netof[j]] = votes.get(netof[j], 0) + 1
            if votes:
                netof[i] = max(votes, key=votes.get); how[i] = f"T-jct majority {votes}"; changed = True
        if not changed:
            break
    return verts, netof, how, idx


def strip_show_layers(brd):
    brd = re.sub(r'\n?<wire [^>]*layer="2\d\d"[^>]*/>', '', brd)
    brd = re.sub(r'\n?<layer number="2\d\d"[^>]*/>', '', brd)
    return brd


def collect_route_wires(brd, routes, layer):
    rw = {}
    for w in re.findall(rf'<wire [^>]*layer="{layer}"[^>]*/>', brd):
        x1, y1, x2, y2 = (float(z) for z in re.search(
            r'x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"', w).groups())
        best = (0.12, None)
        for k, v in routes:
            d = max(near_body((x1, y1), v), near_body((x2, y2), v))
            if d < best[0]:
                best = (d, k)
        if best[1]:
            rw.setdefault(best[1], []).append(w)
    return rw


def main():
    ap = argparse.ArgumentParser(description="Bind a board's free copper into <signal>s")
    ap.add_argument('repo', help="Boldport repo dir (master json found by content) or master .json")
    ap.add_argument('board', help="the .brd to edit (branch it first)")
    ap.add_argument('args', nargs='*',
                    help="report|apply|show, top|bottom, seeds (KEY=NET, MILX,MILY=NET, L<n>=NET), "
                         "show targets (ROUTEKEY, --net=NET, --suspect)")
    ap.add_argument('--ox', type=float, help="x offset (default: from <board>.brd.transform.json)")
    ap.add_argument('--oy', type=float, help="y offset (default: from <board>.brd.transform.json)")
    # parse_known_args so command words like --suspect / --net=NET aren't eaten by
    # argparse as (unknown) options of this script -- they belong to the command stream
    a, extra = ap.parse_known_args()
    a.args = a.args + extra

    master_path = find_master(a.repo)
    routing = load_routing(master_path)
    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    ox, oy = load_offset(board_path, a.ox, a.oy)

    cmd, side = 'report', 'top'
    coord_seeds, show_targets, seed_args = [], [], []
    for arg in a.args:
        if arg in ('report', 'apply', 'show'):
            cmd = arg
        elif arg in ('top', 'bottom'):
            side = arg
        elif arg.startswith('--net='):
            show_targets.append(('net', arg.split('=', 1)[1]))
        elif arg == '--suspect':
            show_targets.append(('suspect', None))
        elif '=' in arg:
            seed_args.append(arg)
        elif cmd == 'show':
            show_targets.append(('key', arg))

    layer = LAYER[side]
    SEEDFILE = f"{board_path}.seeds-{side}.json"
    SHOWFILE = f"{board_path}.show-{side}.json"
    seeds = json.load(open(SEEDFILE)) if os.path.exists(SEEDFILE) else {}
    showmap = json.load(open(SHOWFILE)) if os.path.exists(SHOWFILE) else {}

    routes = route_geom(routing, side, ox, oy)
    for arg in seed_args:
        lhs, net = arg.split('=', 1)
        if lhs[:1] == 'L' and lhs[1:].isdigit():
            key = showmap.get(lhs[1:])
            if key:
                seeds[key] = net; print(f"seed: layer {lhs[1:]} -> {key} = {net}")
            else:
                print(f"!! no SHOW route on layer {lhs[1:]}")
        elif ',' in lhs:
            mx, my = (float(z) for z in lhs.split(','))
            x, y = mx * 0.0254, my * 0.0254
            d, key = min((near_body((x, y), v), k) for k, v in routes)
            seeds[key] = net; print(f"seed: {key} = {net}  (from {mx:.0f},{my:.0f} mil, d={d:.2f}mm)")
        else:
            seeds[lhs] = net
    json.dump(seeds, open(SEEDFILE, 'w'), indent=1)
    if seeds:
        print("active seeds:", ", ".join(f"{k}={v}" for k, v in seeds.items()))

    brd, padpos, pad2net = load_board(board_path)
    if not padpos:
        sys.exit("no pads found in the board's embedded libraries -- is the board populated?")
    verts, netof, how, idx = classify(brd, padpos, pad2net, routes, seeds)

    from collections import Counter
    by = Counter(n for n in netof if n)
    unbound = [routes[i][0] for i in range(len(routes)) if not netof[i]]
    print(f"board: {os.path.basename(board_path)} | side: {side} (layer {layer}) | "
          f"offset OX={ox:g} OY={oy:g}")
    print(f"routes: {len(routes)} | bound: {len(routes)-len(unbound)} | "
          f"nets: {', '.join(f'{n}x{c}' for n, c in sorted(by.items()))}")
    import xml.dom.minidom as MD

    if cmd == 'report':
        for i, (k, v) in enumerate(routes):
            print(f"  {k:14s} -> {str(netof[i]):8s} [{how[i]}]")
        if unbound:
            print(f"\nUNBOUND ({len(unbound)}): {unbound}")
            print("  bind with seeds (KEY=NET) or inspect with: show --suspect")

    elif cmd == 'show':
        keys = []
        for kind, val in show_targets:
            if kind == 'key' and val in idx:
                keys.append(val)
            elif kind == 'net':
                keys += [k for i, (k, v) in enumerate(routes) if netof[i] == val]
            elif kind == 'suspect':
                keys += [k for i, (k, v) in enumerate(routes) if not (how[i] or '').startswith('pad')]
        keys = list(dict.fromkeys(keys))
        brd = strip_show_layers(brd)
        rw = collect_route_wires(brd, routes, layer)
        layerdefs, addwires, showmap = [], [], {}
        for n, key in enumerate(keys):
            L = SHOW_BASE + n
            layerdefs.append(f'<layer number="{L}" name="SHOW{L}" color="{2+n%14}" fill="1" '
                             f'visible="yes" active="yes"/>')
            addwires += [re.sub(r'layer="\d+"', f'layer="{L}"', w) for w in rw.get(key, [])]
            showmap[str(L)] = key
        if layerdefs and '<layer number="200"' not in brd:
            brd = brd.replace('</layers>', '\n'.join(layerdefs) + '\n</layers>', 1)
        if addwires:
            brd = brd.replace('</plain>', '\n'.join(addwires) + '\n</plain>', 1)
        MD.parseString(brd); open(board_path, 'w').write(brd)
        json.dump(showmap, open(SHOWFILE, 'w'), indent=1)
        print(f"\nSHOWING {len(keys)} trace(s) on layers {SHOW_BASE}-{SHOW_BASE+len(keys)-1} -- "
              f"reload in Eagle, toggle each layer, then answer:  L<layer>=NET")
        for L, key in showmap.items():
            i = idx[key]
            print(f"  Layer {L}: {key}  (currently {netof[i]})  [{how[i]}]")

    elif cmd == 'apply':
        brd = strip_show_layers(brd)
        if os.path.exists(SHOWFILE):
            os.remove(SHOWFILE)

        def match_net(x1, y1, x2, y2):
            best = (0.12, None)
            for i, (k, v) in enumerate(routes):
                if not netof[i]:
                    continue
                d = max(near_body((x1, y1), v), near_body((x2, y2), v))
                if d < best[0]:
                    best = (d, netof[i])
            return best[1]

        moved, unmatched = {}, 0
        for w in re.findall(rf'<wire [^>]*layer="{layer}"[^>]*/>', brd):
            x1, y1, x2, y2 = (float(x) for x in re.search(
                r'x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"', w).groups())
            net = match_net(x1, y1, x2, y2)
            if net:
                moved.setdefault(net, []).append(w)
                brd = brd.replace('\n' + w, '', 1) if ('\n' + w) in brd else brd.replace(w, '', 1)
            else:
                unmatched += 1
        for net, ws in moved.items():
            m = re.search(rf'(<signal name="{re.escape(net)}"[^>]*>)(.*?)(</signal>)', brd, re.S)
            if not m:
                print(f"  !! no <signal> for {net}, skipping {len(ws)} wires"); continue
            brd = brd[:m.end(2)] + '\n' + '\n'.join(ws) + brd[m.end(2):]
        MD.parseString(brd); open(board_path, 'w').write(brd)
        print(f"\nAPPLIED: (re)placed {sum(len(v) for v in moved.values())} wires into "
              f"{len(moved)} signals; {unmatched} unmatched left in place.")
        print("NEXT: open (or File > Reload) the board in Eagle, run RATSNEST, and review.")


if __name__ == '__main__':
    main()
