#!/usr/bin/env python3
"""
pour.py -- the copper pour as Eagle's NATIVE cutout representation (Step 6).

Writes, inside the pour net's <signal>: one <polygon pour="solid"> for the board
interior (outline buffered inward) plus one <polygon pour="cutout"> per clearance
(each pad's copper, each hole, each foreign trace). Eagle then fills solid-minus-
cutouts, which reproduces PCBmodE's pour semantics:

  * NET-AGNOSTIC clearances (PCBmodE's `g`): uniform rings around ALL pads,
    drills, and other-net traces;
  * the pour net's OWN traces are NOT cut, so the fill merges through them --
    they are the hand-drawn spokes/thermals that connect the net to the pour
    (authored with buffer-to-pour: 0 in the source);
  * NO orphan removal: decorative floating islands survive (Eagle's own
    ratsnest pour would delete them).

Cutout shapes are TRACED from the board's own footprint copper polygons,
buffered by the clearance -- never guessed from pad parameters (guessed circles
over square pads leave peaks at the edge midpoints).

Clearances come from the repo master's `distances.from-pour-to` (pad / route /
drill / outline); the pour layer comes from `shapes/pours.json`; OX/OY from the
board's .transform.json sidecar. Everything is overridable on the CLI.

Eagle DRC gates (set these or the pour won't fill right):
  * pour width must be >= the DRC minimum copper width (default 0.2 mm here;
    below Eagle's minimum, Eagle silently refuses to fill);
  * the solid polygon carries isolate="0" thermals="no" orphans="yes" so Eagle
    reproduces the injected geometry instead of re-deciding it.

Usage:
    python3 pour.py <repo-dir> "<board.brd>" [--net GND] [--side bottom]
                    [--width 0.2] [--ox N --oy N]

Branch the board first; Eagle closed; afterward open it, RATSNEST, and watch the
remaining pour-thermal airwires resolve into the fill. Re-runs replace the prior
pour (any existing polygons in the pour net's signal on that layer are dropped).
"""
import argparse, glob, json, math, os, re, sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flatten

try:
    from shapely.geometry import Polygon, Point, LineString
    from shapely import affinity
except ImportError:
    sys.exit("pour.py needs shapely (pip install --user shapely)")

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


def load_offset(board_path, ox, oy):
    if ox is not None and oy is not None:
        return ox, oy
    sidecar = board_path + ".transform.json"
    if os.path.exists(sidecar):
        d = json.load(open(sidecar))
        return (ox if ox is not None else d['ox']), (oy if oy is not None else d['oy'])
    sys.exit(f"no {os.path.basename(sidecar)} and no --ox/--oy")


def pour_side(master_path, cli_side):
    if cli_side:
        return cli_side
    pj = os.path.join(os.path.dirname(master_path), "shapes", "pours.json")
    if os.path.exists(pj):
        layers = []
        for s in json.load(open(pj)).get('layout', {}).get('pours', {}).get('shapes', []):
            if s.get('type') == 'layer':
                layers += s.get('layers', [])
        if len(layers) == 1:
            return layers[0]
        if layers:
            sys.exit(f"repo pours multiple layers {layers} -- run once per side with --side")
    return 'bottom'


def arc_center(x1, y1, x2, y2, deg):
    th = math.radians(deg); c, s = math.cos(th), math.sin(th)
    m00, m01, m10, m11 = c - 1, -s, s, c - 1
    det = m00 * m11 - m01 * m10
    bx, by = x2 - x1, y2 - y1
    ax = (m11 * bx - m01 * by) / det
    ay = (-m10 * bx + m00 * by) / det
    return x1 - ax, y1 - ay


def rmat(rot):
    if not rot:
        return (False, 0)
    return (rot.startswith('M'), int(re.search(r'R(\d+)', rot).group(1)) if 'R' in rot else 0)


def eagle_pts(verts, seg=16):
    coords = [(float(v.get('x')), float(v.get('y'))) for v in verts]
    curves = [float(v.get('curve') or 0) for v in verts]
    n = len(coords); out = []
    for i in range(n):
        x1, y1 = coords[i]; x2, y2 = coords[(i + 1) % n]; cv = curves[i]
        out.append((x1, y1))
        if abs(cv) > 1e-9:
            cx, cy = arc_center(x1, y1, x2, y2, cv); R = math.hypot(x1 - cx, y1 - cy)
            a0 = math.atan2(y1 - cy, x1 - cx); sw = math.radians(cv)
            out += [(cx + R * math.cos(a0 + sw * k / seg), cy + R * math.sin(a0 + sw * k / seg))
                    for k in range(1, seg)]
    return out


def place(geom, ex, ey, rot):
    mir, ang = rmat(rot)
    if mir:
        geom = affinity.scale(geom, xfact=-1, yfact=1, origin=(0, 0))
    return affinity.translate(affinity.rotate(geom, ang, origin=(0, 0)), ex, ey)


def wire_line(w, seg=12):
    x1, y1 = float(w.get('x1')), float(w.get('y1'))
    x2, y2 = float(w.get('x2')), float(w.get('y2'))
    cv = float(w.get('curve') or 0)
    if abs(cv) < 1e-9:
        return LineString([(x1, y1), (x2, y2)])
    cx, cy = arc_center(x1, y1, x2, y2, cv); R = math.hypot(x1 - cx, y1 - cy)
    a0 = math.atan2(y1 - cy, x1 - cx); sw = math.radians(cv)
    return LineString([(cx + R * math.cos(a0 + sw * k / seg), cy + R * math.sin(a0 + sw * k / seg))
                       for k in range(seg + 1)])


def main():
    ap = argparse.ArgumentParser(description="Inject the pour (solid + cutouts) into a board .brd")
    ap.add_argument('repo', help="Boldport repo dir (master json found by content) or master .json")
    ap.add_argument('board', help="the .brd to edit (branch it first)")
    ap.add_argument('--net', default='GND', help="pour net (default GND)")
    ap.add_argument('--side', choices=['top', 'bottom'],
                    help="pour side (default: from shapes/pours.json, else bottom)")
    ap.add_argument('--width', type=float, default=0.2,
                    help="pour stroke width, mm (default 0.2; must be >= DRC min copper width)")
    ap.add_argument('--decor', action='append', default=[],
                    help="routing.json route key of DECORATIVE pour-net copper (e.g. a logo), "
                         "so the pour doesn't swallow it. KEY or KEY:STYLE where STYLE is "
                         "'silhouette' (default: one cutout on the outer shape -- whole motif "
                         "on bare substrate) or 'traced' (clearance capsules along the strokes "
                         "-- fill threads through the motif's negative space). An artistic "
                         "choice. Repeatable; sticky in <board>.brd.decor.json")
    ap.add_argument('--ox', type=float, help="x offset (default: from <board>.brd.transform.json)")
    ap.add_argument('--oy', type=float, help="y offset (default: from <board>.brd.transform.json)")
    a = ap.parse_args()

    master_path = find_master(a.repo)
    master = json.load(open(master_path))
    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    ox, oy = load_offset(board_path, a.ox, a.oy)
    side = pour_side(master_path, a.side)
    layer = str(LAYER[side])

    D = master.get('distances', {}).get('from-pour-to', {})
    cl_pad = D.get('pad', 0.25)
    cl_route = D.get('route', 0.25)
    cl_drill = D.get('drill', 0.4)
    cl_outline = D.get('outline', 0.25)
    print(f"master: {master_path}")
    print(f"board:  {board_path}")
    print(f"pour:   net={a.net} side={side} (layer {layer}) width={a.width:g}")
    print(f"clearances (from-pour-to): pad={cl_pad:g} route={cl_route:g} "
          f"drill={cl_drill:g} outline={cl_outline:g}")

    brd = ET.parse(board_path).getroot().find('.//board')

    # outer solid = board outline buffered inward
    subs = flatten.adaptive_flatten(master['outline']['shape']['value'], tol=0.05)
    ring = max(subs, key=lambda s: Polygon(s).area if len(s) > 2 else 0)
    outer = Polygon([(x + ox, -y + oy) for x, y in ring]).buffer(-cl_outline)

    # footprint copper polys on the pour layer + hole drills, from the board's
    # own embedded libraries (the placed truth)
    fp_poly, fp_hole, fp_drillpads = {}, {}, {}
    for lib in brd.find('libraries'):
        for pkg in lib.find('packages'):
            key = (lib.get('name'), pkg.get('name'))
            fp_poly[key] = [Polygon(eagle_pts(po.findall('vertex')))
                            for po in pkg.findall('polygon')
                            if po.get('layer') == layer and len(po.findall('vertex')) >= 3]
            fp_hole[key] = [(float(h.get('x')), float(h.get('y')), float(h.get('drill')))
                            for h in pkg.findall('hole') if h.get('drill')]
            # pads with drills but NO copper polygon on this layer still need a
            # drill clearance (belt and braces; most pads have traced copper)
            fp_drillpads[key] = [(float(p.get('x')), float(p.get('y')), float(p.get('drill')))
                                 for p in pkg.findall('pad') if p.get('drill')]

    cutouts = []
    n_pad = n_hole = 0
    for e in brd.find('elements'):
        ex, ey, rot = float(e.get('x')), float(e.get('y')), e.get('rot')
        key = (e.get('library'), e.get('package'))
        for poly in fp_poly.get(key, []):
            cutouts.append(place(poly, ex, ey, rot).buffer(cl_pad)); n_pad += 1
        covered = [place(Point(px, py), ex, ey, rot).coords[0]
                   for px, py, _ in fp_drillpads.get(key, [])] if fp_poly.get(key) else []
        for hx, hy, drl in fp_hole.get(key, []):
            wx, wy = place(Point(hx, hy), ex, ey, rot).coords[0]
            cutouts.append(Point(wx, wy).buffer(drl / 2 + cl_drill)); n_hole += 1
        if not fp_poly.get(key):
            for px, py, drl in fp_drillpads.get(key, []):
                wx, wy = place(Point(px, py), ex, ey, rot).coords[0]
                cutouts.append(Point(wx, wy).buffer(drl / 2 + cl_drill)); n_hole += 1

    n_trace = n_spoke = 0
    for sig in brd.find('signals'):
        spoke = sig.get('name') == a.net
        for w in sig.findall('wire'):
            if w.get('layer') != layer:
                continue
            if spoke:
                n_spoke += 1
                continue
            cutouts.append(wire_line(w).buffer(float(w.get('width')) / 2 + cl_route))
            n_trace += 1

    # ---- decorative pour-net copper (e.g. a logo drawn as route-art) ----
    # The pour normally merges into its own net's traces -- which makes decorative
    # copper invisible. Designated routes get clearance cutouts in one of two
    # STYLES (an artistic choice):
    #   silhouette -- ONE cutout on the outer shape (sub-paths merged, counters
    #                 filled): the whole motif sits on bare substrate;
    #   traced     -- clearance capsules along each stroke segment: the fill
    #                 threads through the motif's negative space. (Per-SEGMENT
    #                 capsules, because a traced closed ring would need a
    #                 cutout-with-a-hole, which Eagle polygons can't express.)
    # Designations are sticky per board in <board>.brd.decor.json.
    decorfile = board_path + ".decor.json"
    decor = {}
    if os.path.exists(decorfile):
        loaded = json.load(open(decorfile))
        decor = {k: 'silhouette' for k in loaded} if isinstance(loaded, list) else dict(loaded)
    for d in a.decor:
        for item in d.split(','):
            item = item.strip()
            if not item:
                continue
            key, _, style = item.partition(':')
            style = style or 'silhouette'
            if style not in ('silhouette', 'traced'):
                sys.exit(f"--decor {item}: style must be 'silhouette' or 'traced'")
            decor[key] = style
    n_decor = 0
    if decor:
        base = os.path.basename(master_path).rsplit('.json', 1)[0]
        rp = os.path.join(os.path.dirname(master_path), f"{base}_routing.json")
        if not os.path.exists(rp):
            hits = glob.glob(os.path.join(os.path.dirname(master_path), "*_routing.json"))
            rp = hits[0] if len(hits) == 1 else sys.exit("no unique *_routing.json for --decor")
        routing = json.load(open(rp))
        from shapely.ops import unary_union
        for key, style in sorted(decor.items()):
            r = routing['routes'].get(side, {}).get(key)
            if not r:
                print(f"  !! decor route '{key}' not in routes.{side} -- skipped")
                continue
            w = float(r.get('stroke-width') or 0.25)
            subs = [[(x + ox, -y + oy) for x, y in sp]
                    for sp in flatten.adaptive_flatten(r['value'], tol=0.02)]
            if style == 'traced':
                for sp in subs:
                    for i in range(len(sp) - 1):
                        cutouts.append(LineString([sp[i], sp[i + 1]])
                                       .buffer(w / 2 + cl_route, quad_segs=8))
            else:
                polys = []
                for sp in subs:
                    if len(sp) >= 3:
                        p = Polygon(sp).buffer(0)
                        if not p.is_empty:
                            polys += list(p.geoms) if p.geom_type == 'MultiPolygon' else [p]
                u = unary_union(polys)
                geoms = list(u.geoms) if u.geom_type == 'MultiPolygon' else [u]
                # NB: do not name this 'outer' -- that is the board-interior solid
                motif = unary_union([Polygon(g.exterior) for g in geoms])  # fill counters
                cutouts.append(motif.buffer(cl_route, quad_segs=8))
            n_decor += 1
            print(f"  decor: {key} ({style})")
        json.dump(decor, open(decorfile, 'w'), indent=1)
    # hint: a pour-net route that bind_copper could only seed (no pad ends, no
    # T-junction) is the classic decorative-copper candidate
    seedfile = f"{board_path}.seeds-{side}.json"
    if os.path.exists(seedfile):
        hints = [k for k, n in json.load(open(seedfile)).items()
                 if n == a.net and k not in decor]
        if hints:
            print(f"NOTE: seeded {a.net} route(s) {hints} were unbindable by geometry -- "
                  f"if decorative (logo art), re-run with --decor {hints[0]}")

    def clean_ring(g):
        """Vertex list Eagle will accept: simplify sub-visual wobble (5 um), round
        to 4 decimals, and drop micro-edges -- adjacent vertices that land closer
        than ~2 um after rounding make Eagle's integer-grid validity check fail
        ("Invalid polygon") even though shapely calls the ring valid."""
        g = g.simplify(0.005, preserve_topology=True)
        pts = [(round(x, 4), round(y, 4)) for x, y in list(g.exterior.coords)[:-1]]
        out = []
        for p in pts:
            if out and math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) < 0.002:
                continue
            out.append(p)
        while len(out) > 2 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < 0.002:
            out.pop()
        return out if len(out) >= 3 else None

    def poly_xml(geom, pour, width, attrs=""):
        gs = geom.geoms if geom.geom_type == 'MultiPolygon' else [geom]
        out = []
        for g in gs:
            if g.is_empty or g.area < 1e-6:
                continue
            pts = clean_ring(g)
            if not pts:
                continue
            vs = "\n".join(f'<vertex x="{x:.4f}" y="{y:.4f}"/>' for x, y in pts)
            out.append(f'<polygon width="{width}" layer="{layer}" pour="{pour}" rank="3"{attrs}>'
                       f'\n{vs}\n</polygon>')
        return out

    # sanity: the solid must still be the board interior (catches any accidental
    # clobbering of `outer` between computation and emission)
    if outer.area < 0.5 * Polygon([(x + ox, -y + oy) for x, y in ring]).area:
        sys.exit(f"INTERNAL ERROR: solid area {outer.area:.0f} mm^2 is not the board "
                 f"interior -- refusing to write")
    # Eagle fills a polygon AND strokes its boundary at the polygon width, so the
    # copper extends width/2 OUTSIDE the vertex path. Deflate the solid by width/2
    # so the filled edge lands exactly on PCBmodE's pour edge. (Cutouts are emitted
    # at width 0, so they need no compensation.)
    xml = poly_xml(outer.buffer(-a.width / 2), "solid", a.width,
                   ' isolate="0" thermals="no" orphans="yes"')
    for c in cutouts:
        xml += poly_xml(c, "cutout", "0")
    print(f"  solid area {outer.area:.0f} mm^2 (board interior)")
    block = "\n".join(xml)

    # inject into the pour net's signal, replacing any prior pour polygons there
    t = open(board_path).read()
    m = re.search(rf'(<signal name="{re.escape(a.net)}"[^>]*>)(.*?)(</signal>)', t, re.S)
    if not m:
        sys.exit(f"no <signal name=\"{a.net}\"> on the board -- is the schematic wired and bound?")
    body = re.sub(rf'\s*<polygon[^>]*layer="{layer}".*?</polygon>', '', m.group(2), flags=re.S)
    t = t[:m.start()] + m.group(1) + body + '\n' + block + '\n' + m.group(3) + t[m.end():]
    import xml.dom.minidom as MD
    MD.parseString(t)
    open(board_path, 'w').write(t)

    print(f"\nwrote {os.path.basename(board_path)}")
    print(f"  {a.net} signal: 1 solid (width {a.width:g}) + {len(xml)-1} cutouts "
          f"({n_pad} pad copper + {n_hole} drills + {n_trace} foreign traces"
          + (f" + {n_decor} decorative motif(s)" if n_decor else "")
          + f"); {n_spoke} {a.net} spokes left to merge")
    print(f"""
EAGLE DRC GATES (once per board): DRC minimum copper width must be <= {a.width:g} mm
or Eagle refuses to fill; leave the injected polygon's thermals/isolate as written.
NEXT: open the board in Eagle, RATSNEST -- the fill computes, the {a.net} thermal
airwires resolve into it, and decorative islands stay.""")


if __name__ == '__main__':
    main()
