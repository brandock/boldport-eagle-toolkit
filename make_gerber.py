#!/usr/bin/env python3
"""
make_gerber.py -- generate the COMPLETE Gerber set for a Boldport repo entirely
from our own writer (gerber.py), no Eagle CAM. The PCBmodE pipeline, end to end:
  SVG art -> arcfit (true arcs) -> RS-274X with real G02/G03.

This renders the PUBLISHED REPO (the source of truth), not an Eagle board --
use it as the fidelity reference. Eagle CAM linearizes every arc to G01; this
writer keeps them. Layers (board-centred frame so they register):
  .GKO outline | .GTO/.GBO silk (strokes + pattern-fill regions + wordmarks,
  stroke-outline text) | .GTL/.GBL copper (traces + pads; the pour side is a
  literal polarity flood per the repo pour clearances) | .GTS/.GBS soldermask |
  .GTP/.GBP paste placeholders (PTH) | .XLN drills.

Usage:
    python3 make_gerber.py <repo-dir> <output-dir> [--base NAME]
"""
import argparse, glob, json, os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arcfit
from svgfont import SvgFont
from gerber import Gerber

HERE = os.path.dirname(os.path.abspath(__file__))
ARC_TOL = 0.02

def _is_master(p):
    try:
        return {'components', 'outline'} <= set(json.load(open(p)))
    except Exception:
        return False

ap = argparse.ArgumentParser(description="Boldport repo -> complete Gerber set (true arcs)")
ap.add_argument('repo', help="Boldport repo dir (master json found by content) or master .json")
ap.add_argument('out', help="output directory")
ap.add_argument('--base', help="output file basename (default: <board>)")
_a = ap.parse_args()
_mp = os.path.expanduser(_a.repo.rstrip('/\\'))
if not os.path.isfile(_mp):
    _c = [p for p in sorted(glob.glob(os.path.join(_mp, "*.json"))) if _is_master(p)]
    if len(_c) != 1:
        sys.exit(f"need exactly one master json in {_mp} (found {len(_c)})")
    _mp = _c[0]
CT = os.path.dirname(_mp)
stage = os.path.expanduser(_a.out)
board_name = os.path.basename(_mp).rsplit('.json', 1)[0]
base = _a.base or board_name

master = json.load(open(_mp))
sk = json.load(open(os.path.join(CT, "shapes", "silkscreen.json")))['layout']['silkscreen']['shapes']
_routing_hits = glob.glob(os.path.join(CT, "*_routing.json"))
_fam = next((s['font-family'] for s in sk if s.get('type') == 'text'), "GoodDog-Regular-webfont")
font = SvgFont(os.path.join(HERE, "fonts", _fam + ".svg"))

def flip_center(subs, ox, oy):
    out = []
    for verts, closed in subs:
        fl = [(x, -y, -cv) for x, y, cv in verts]
        out.append(([(x - ox, y - oy, cv) for x, y, cv in fl], closed))
    return out

def bbox_center(subs_flipped):
    xs = [x for v, _ in subs_flipped for x, y, cv in v]
    ys = [y for v, _ in subs_flipped for x, y, cv in v]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

def text_strokes(g, shape, mirror_x, width=0.12):
    size = float(re.match(r'[\d.]+', shape['font-size']).group())
    for sp in font.layout(shape['value'], size):          # centred at origin, y-up
        verts = [((-x if mirror_x else x), y, 0.0) for x, y in sp]
        g.stroke(verts, True, width)

# ---- board-centre origin (from the outline) --------------------------------
out_subs = arcfit.fit_path(master['outline']['shape']['value'], tol=ARC_TOL)[0]
ocx, ocy = bbox_center([([(x, -y, -cv) for x, y, cv in v], c) for v, c in out_subs])

# ---- .GKO board outline (arc strokes) --------------------------------------
gko = Gerber(f"{base} board outline", function="Profile,NP")
for verts, closed in flip_center(out_subs, ocx, ocy):
    gko.stroke(verts, closed, 0.15)

# ---- .GTO top silk ---------------------------------------------------------
gto = Gerber(f"{base} top silk", function="Legend,Top")
for sh in [s for s in sk if s.get('type') == 'path' and s.get('layers') == ['top'] and s.get('style') == 'stroke']:
    for verts, closed in flip_center(arcfit.fit_path(sh['value'], tol=ARC_TOL)[0], ocx, ocy):
        gto.stroke(verts, closed, float(sh.get('stroke-width') or 0.18))
for sh in [s for s in sk if s.get('type') == 'path' and s.get('layers') == ['top'] and s.get('style') == 'fill']:
    _fl = [([(x, -y, -cv) for x, y, cv in v], c) for v, c in arcfit.fit_path(sh['value'], tol=ARC_TOL)[0]]
    _cx, _cy = bbox_center(_fl)
    for verts, _ in _fl:
        if len(verts) >= 3:
            gto.region([(x - _cx, y - _cy, cv) for x, y, cv in verts])
for sh in [s for s in sk if s.get('type') == 'text' and s.get('layers') == ['top']]:
    text_strokes(gto, sh, mirror_x=False)

# ---- .GBO bottom silk: waves regions + boldport.club -----------------------
gbo = Gerber(f"{base} bottom silk", function="Legend,Bot")
for sh in [s for s in sk if s.get('type') == 'path' and s.get('layers') == ['bottom'] and s.get('style') == 'stroke']:
    for verts, closed in flip_center(arcfit.fit_path(sh['value'], tol=ARC_TOL)[0], ocx, ocy):
        gbo.stroke(verts, closed, float(sh.get('stroke-width') or 0.18))
for sh in [s for s in sk if s.get('type') == 'path' and s.get('layers') == ['bottom'] and s.get('style') == 'fill']:
    _fl = [([(x, -y, -cv) for x, y, cv in v], c) for v, c in arcfit.fit_path(sh['value'], tol=ARC_TOL)[0]]
    _cx, _cy = bbox_center(_fl)
    for verts, _ in _fl:
        if len(verts) >= 3:
            gbo.region([(x - _cx, y - _cy, cv) for x, y, cv in verts])
for sh in [s for s in sk if s.get('type') == 'text' and s.get('layers') == ['bottom']]:
    text_strokes(gbo, sh, mirror_x=True)

# ---- copper traces (.GTL/.GBL): routes are SVG-path strokes, like the art ---
routing = json.load(open(_routing_hits[0])) if len(_routing_hits) == 1 else sys.exit("no unique *_routing.json")
def copper(side):
    g = Gerber(f"{base} {side} copper traces", function=("Copper,L1,Top" if side == "top" else "Copper,L2,Bot"))
    for net_id, net in routing['routes'][side].items():
        w = float(net.get('stroke-width') or 0.25)
        for verts, closed in flip_center(arcfit.fit_path(net['value'], tol=ARC_TOL)[0], ocx, ocy):
            g.stroke(verts, closed, w)
    return g
gtl = copper('top')   # gbl (bottom) is built below as the pour

# ---- pads + drills: place footprints, auto-calibrate D-frame -> board -------
import math
from excellon import Excellon

def rot(px, py, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return px * c - py * s, px * s + py * c

def xf(px, py, flip):
    return (px - ocx, (-py if flip else py) - ocy)

def rounded_rect_pts(w, h, radii, n=6):
    a, b = w / 2, h / 2
    co = [(a - radii.get('tr', 0), b - radii.get('tr', 0), 0,   radii.get('tr', 0)),
          (-a + radii.get('tl', 0), b - radii.get('tl', 0), 90,  radii.get('tl', 0)),
          (-a + radii.get('bl', 0), -b + radii.get('bl', 0), 180, radii.get('bl', 0)),
          (a - radii.get('br', 0), -b + radii.get('br', 0), 270, radii.get('br', 0))]
    pts = []
    for cx, cy, a0, r in co:
        if r <= 1e-9:
            pts.append((cx, cy))
        else:
            for k in range(n + 1):
                ang = math.radians(a0 + 90 * k / n)
                pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts

fp_cache = {}
def footprint(name):
    if name not in fp_cache:
        fp_cache[name] = json.load(open(os.path.join(CT, "components", name + ".json")))
    return fp_cache[name]

def iter_pads(rsign):
    """Yield (world_x, world_y, shape, shape_rot, drill) in PCBmodE D-frame."""
    for comp in master['components'].values():
        fp = footprint(comp['footprint']); crot = comp.get('rotate', 0) * rsign
        clx, cly = comp['location']
        for pin in fp['pins'].values():
            lay = pin['layout']
            rx, ry = rot(lay['location'][0], lay['location'][1], crot)
            paddef = fp['pads'][lay['pad']]
            yield (clx + rx, cly + ry, paddef['shapes'][0],
                   crot + lay.get('rotate', 0) * rsign,
                   (paddef.get('drills') or [{}])[0].get('diameter'))

# Pads share the SVG->board transform already validated on the silk/traces:
# flip_y=True, no rotation-sign change. (Auto-search is defeated here -- the fish
# is top/bottom symmetric so all flip/rot combos tie.) PCBmodE traces terminate
# at pad EDGES (~one pad radius from centre) and ~40% of route ends are trace
# junctions, so we report a generous connectivity sanity check, not centre-match.
flip, rsign = True, 1
endpoints = []
for side in ('top', 'bottom'):
    for net in routing['routes'][side].values():
        sub = arcfit.fit_path(net['value'], tol=ARC_TOL)[0]
        if sub:
            endpoints += [sub[0][0][0], sub[-1][0][-1]]
pc = [xf(wx, wy, flip) for wx, wy, *_ in iter_pads(rsign)]
conn = sum(any((bx - px)**2 + (by - py)**2 < 0.8**2 for px, py in pc)
           for bx, by in (xf(e[0], e[1], flip) for e in endpoints))
print(f"  pads use silk transform (flip_y=True); connectivity sanity: "
      f"{conn}/{len(endpoints)} trace ends within 0.8mm of a pad")

# emit TOP copper pads + soldermask openings now; collect records for the pour
gts = Gerber(f"{base} top mask", function="Soldermask,Top"); gbs = Gerber(f"{base} bottom mask", function="Soldermask,Bot")
exc = Excellon()
# PCBmodE's mask model (distances.soldermask): rect/circle pads get an ABSOLUTE
# buffer per side (typ. 0.05 mm), NOT a proportional scale. (path-type pads would
# use path-scale 1.05, a centroid scale -- none on boards whose pads are all
# circle/rect.) The old pad_scale_factor multiplicative model made big pads' gaps
# too wide and small pads' too tight.
_SM = master.get('distances', {}).get('soldermask', {})
# EMPIRICAL x2: the config buffer (0.05) produces HALF the NSMD ring measured on
# Boldport's shipped boards (~4 mil/side). Hypothesis: PCBmodE renders the mask
# shape as the pad path STROKED 2*buffer centred on the edge, so the printed
# opening grows by 2*buffer per side. Whatever the mechanism, the measurement
# rules: effective expansion = 2 x buffer.
MASK_BUF = 2 * _SM.get('circle-buffer', _SM.get('rect-buffer', 0.05))
pad_recs = []
for wx, wy, shape, srot, dr in iter_pads(rsign):
    bx, by = xf(wx, wy, flip)
    if dr:
        exc.hit(bx, by, dr)
    pad_recs.append((wx, wy, shape, srot))
    if shape['type'] == 'circle':
        d = shape['diameter']
        gtl.flash(bx, by, d)
        gts.flash(bx, by, d + 2 * MASK_BUF); gbs.flash(bx, by, d + 2 * MASK_BUF)
    else:                                   # rect / rounded-rect -> region
        def board_rect(buf):
            pts = rounded_rect_pts(shape['width'] + 2 * buf, shape['height'] + 2 * buf,
                                   {k: v + (buf if v > 0 else 0)
                                    for k, v in shape.get('radii', {}).items()})
            return [(*xf(wx + rot(lx, ly, srot)[0], wy + rot(lx, ly, srot)[1], flip), 0.0)
                    for lx, ly in pts]
        gtl.region(board_rect(0.0))
        gts.region(board_rect(MASK_BUF)); gbs.region(board_rect(MASK_BUF))

# ---- bottom copper = literal pours.json ground plane -----------------------
# Flood the fish with copper (LPD), clear (LPC) a border + a 0.25mm moat around
# every signal pad and bottom trace, then lay the signal copper back in (LPD).
# Clearances per distances.from-pour-to (mm). (Drills sit inside pads, whose
# wider clearance subsumes them, so no separate drill moat needed.)
D = master['distances']['from-pour-to']

def pad_shape_pts(wx, wy, shape, srot, grow):
    pts = rounded_rect_pts(shape['width'] + 2*grow, shape['height'] + 2*grow,
                           {k: v + grow for k, v in shape.get('radii', {}).items()})
    return [(*xf(wx + rot(lx, ly, srot)[0], wy + rot(lx, ly, srot)[1], flip), 0.0) for lx, ly in pts]

def emit_pad(g, wx, wy, shape, srot, grow=0.0):
    if shape['type'] == 'circle':
        g.flash(*xf(wx, wy, flip), shape['diameter'] + 2*grow)
    else:
        g.region(pad_shape_pts(wx, wy, shape, srot, grow))

def bottom_traces(g, grow):
    for net in routing['routes']['bottom'].values():
        w = float(net.get('stroke-width') or 0.25) + 2*grow
        for verts, closed in flip_center(arcfit.fit_path(net['value'], tol=ARC_TOL)[0], ocx, ocy):
            g.stroke(verts, closed, w)

outline_board = flip_center(out_subs, ocx, ocy)        # the fish, closed, arcs
gbl = Gerber(f"{base} bottom copper + pour", function="Copper,L2,Bot")
for verts, _ in outline_board:                         # 1. flood the fish (LPD)
    gbl.region(verts)
gbl.clear()                                            # 2. clearances (LPC)
for verts, closed in outline_board:
    gbl.stroke(verts, closed, 2 * D['outline'])        #    pull copper in from edge
for wx, wy, shape, srot in pad_recs:
    emit_pad(gbl, wx, wy, shape, srot, grow=D['pad'])  #    moat around pads
bottom_traces(gbl, grow=D['route'])                    #    moat around bottom traces
gbl.dark()                                             # 3. lay signal copper back (LPD)
bottom_traces(gbl, grow=0.0)
for wx, wy, shape, srot in pad_recs:
    emit_pad(gbl, wx, wy, shape, srot, grow=0.0)

NOPASTE = "%FSLAX34Y34*%\n%MOMM*%\nG04 no paste (PTH)*\nM02*\n"
files = {
    f"{base}.GKO": gko.render(), f"{base}.GTO": gto.render(), f"{base}.GBO": gbo.render(),
    f"{base}.GTL": gtl.render(), f"{base}.GBL": gbl.render(),   # copper: traces + pads
    f"{base}.GTS": gts.render(), f"{base}.GBS": gbs.render(),   # soldermask openings
    f"{base}.GTP": NOPASTE, f"{base}.GBP": NOPASTE,             # no paste (PTH)
    f"{base}.XLN": exc.render(),                                # drills
}
os.makedirs(stage, exist_ok=True)
for name, content in files.items():
    open(os.path.join(stage, name), 'w').write(content)

for g, lbl in ((gko, '.GKO outline'), (gto, '.GTO top silk'), (gbo, '.GBO bottom silk'),
               (gtl, '.GTL top copper'), (gbl, '.GBL bot copper'),
               (gts, '.GTS top mask'), (gbs, '.GBS bot mask')):
    r = g.render()
    print(f"  {lbl:16} {len(r):7d} B | {g.n_arcs:5d} arcs | {g.n_flashes:4d} flashes | {r.count('G36'):3d} regions")
print(f"  drills: {exc.n_hits} hits across {len(exc.tools)} tools (mm): {sorted(exc.tools)}")
print(f"  routes: top {len(routing['routes']['top'])}, bottom {len(routing['routes']['bottom'])} nets")
print(f"  wrote {len(files)} files to {stage}")
