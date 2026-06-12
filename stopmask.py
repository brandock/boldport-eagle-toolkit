#!/usr/bin/env python3
"""
stopmask.py -- write explicit soldermask openings to tStop/bStop (29/30), traced
from the board's own pad copper, with the NSMD expansion as a parameter.

Why: Eagle's Stop rule is a uniform DRC buffer and footprints can bake fixed
openings, but neither matches PCBmodE's freedom (per-shape mask geometry, e.g.
the path-scale openings on artistic pads). The PCBmodE-equivalent capability is
an explicit polygon per opening on the stop layers -- which Eagle honors -- so
this tool emits exactly that, for EVERY pad, both sides (PTH).

The opening shape is the pad's copper polygon traced from the board's embedded
libraries (the placed truth, same harvest as pour.py), buffered by --expand.
Pads represented only by a <pad> (no copper polygon) get a circular opening of
diameter + 2*expand.

Any smaller opening Eagle derives itself (DRC stop on pads, footprint stop
polygons) simply unions underneath the explicit one, so this tool's expansion
WINS whenever it is the largest -- no library surgery needed.

Default expansion 0.1 mm (~4 mil) per side: the NSMD ring measured on
Boldport's shipped boards (2x the config's soldermask buffer -- the measurement
rules over the config).

Idempotent via the board's .inject.json manifest (feature "stopmask").

Usage:
    python3 stopmask.py "<board.brd>" [--expand 0.1]

Branch the board first (copy ALL its sidecar .json files too); Eagle closed;
afterward File > Reload.
"""
import argparse, json, math, os, re, sys

try:
    from shapely.geometry import Polygon, Point
    from shapely import affinity
except ImportError:
    sys.exit("stopmask.py needs shapely (pip install --user shapely)")


def arc_center(x1, y1, x2, y2, deg):
    th = math.radians(deg); c, s = math.cos(th), math.sin(th)
    m00, m01, m10, m11 = c - 1, -s, s, c - 1
    det = m00 * m11 - m01 * m10
    bx, by = x2 - x1, y2 - y1
    return x1 - (m11 * bx - m01 * by) / det, y1 - (-m10 * bx + m00 * by) / det


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


def rmat(rot):
    if not rot:
        return (False, 0)
    return (rot.startswith('M'), int(re.search(r'R(\d+)', rot).group(1)) if 'R' in rot else 0)


def place(geom, ex, ey, rot):
    mir, ang = rmat(rot)
    if mir:
        geom = affinity.scale(geom, xfact=-1, yfact=1, origin=(0, 0))
    return affinity.translate(affinity.rotate(geom, ang, origin=(0, 0)), ex, ey)


def num(v):
    v = v + 0.0
    return "0" if abs(v) < 5e-5 else f"{v:.4f}".rstrip('0').rstrip('.')


def clean_ring(g):
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


def main():
    ap = argparse.ArgumentParser(description="Explicit NSMD mask openings on tStop/bStop")
    ap.add_argument('board', help="the .brd to edit (branch it first, with its sidecars)")
    ap.add_argument('--expand', type=float, default=0.1,
                    help="mask opening beyond the pad copper, mm per side (default 0.1)")
    a = ap.parse_args()
    import xml.etree.ElementTree as ET
    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    brd = ET.parse(board_path).getroot().find('.//board')

    # harvest per-package pad geometry from the board's own embedded libraries:
    # copper polygons on L1 (the traced pad art) + plain <pad> circles
    fp_polys, fp_pads = {}, {}
    for lib in brd.find('libraries'):
        for pkg in lib.find('packages'):
            key = (lib.get('name'), pkg.get('name'))
            fp_polys[key] = [Polygon(eagle_pts(po.findall('vertex')))
                             for po in pkg.findall('polygon')
                             if po.get('layer') == '1' and len(po.findall('vertex')) >= 3]
            fp_pads[key] = [(float(p.get('x')), float(p.get('y')),
                             float(p.get('diameter') or 0) or (float(p.get('drill')) + 0.6))
                            for p in pkg.findall('pad')]

    openings = []
    n_poly = n_circ = 0
    for e in brd.find('elements'):
        ex, ey, rot = float(e.get('x')), float(e.get('y')), e.get('rot')
        key = (e.get('library'), e.get('package'))
        polys = fp_polys.get(key, [])
        for poly in polys:
            openings.append(place(poly, ex, ey, rot).buffer(a.expand, quad_segs=8))
            n_poly += 1
        if not polys:                       # pad-only package: circular opening
            for px, py, dia in fp_pads.get(key, []):
                wx, wy = place(Point(px, py), ex, ey, rot).coords[0]
                openings.append(Point(wx, wy).buffer(dia / 2 + a.expand, quad_segs=8))
                n_circ += 1

    els = []
    for g in openings:
        gs = g.geoms if g.geom_type == 'MultiPolygon' else [g]
        for gg in gs:
            pts = clean_ring(gg)
            if not pts:
                continue
            vs = "\n".join(f'<vertex x="{num(x)}" y="{num(y)}"/>' for x, y in pts)
            for lay in (29, 30):            # PTH: open both sides
                els.append(f'<polygon width="0.0254" layer="{lay}">\n{vs}\n</polygon>')

    # manifest-idempotent inject (feature "stopmask")
    man_path = board_path + ".inject.json"
    t = open(board_path).read()
    man = json.load(open(man_path)) if os.path.exists(man_path) else {}
    removed = 0
    for old in man.get('stopmask', []):
        if '\n' + old in t:
            t = t.replace('\n' + old, '', 1); removed += 1
        elif old in t:
            t = t.replace(old, '', 1); removed += 1
    t = t.replace('</plain>', '\n'.join(els) + '\n</plain>', 1)
    import xml.dom.minidom as MD
    MD.parseString(t)
    open(board_path, 'w').write(t)
    man['stopmask'] = els
    json.dump(man, open(man_path, 'w'), indent=1)

    print(f"injected 'stopmask': removed {removed} prior, added {len(els)} polygons "
          f"({n_poly} traced pad shapes + {n_circ} circles, x2 sides) at expand {a.expand:g} mm")
    print("NEXT: File > Reload in Eagle. Openings union with any DRC/footprint stops, "
          "so the largest -- this one -- wins.")


if __name__ == '__main__':
    main()
