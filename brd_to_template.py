#!/usr/bin/env python3
"""
brd_to_template.py -- export EAGLE .brd layers to REGISTERED drawing templates
(SVG + PNG) for a round-trip with Illustrator/Photoshop.

Hands the human a template to draw ON, carrying a known scale (1 unit = 1 mm)
and fiducial crosses at known coordinates, so returned curves map back to board
mm exactly (no photo least-squares).

--origin lets you put (0,0) anywhere in board mm (e.g. on a symmetry axis) so the
human can design in all four quadrants. North-up (EAGLE y-up).

Everything drawn is read from the .brd -- nothing is hard-coded. Headers (J*) and
all copper are resolved from real element placement + package geometry.

Backgrounds are TRANSPARENT so the PNGs stack as Photoshop layers and you see
through to the layers below; only the actual ink (copper/lines/marks) is opaque.

Modes:
  (default)  CENTERLINE -- wires as thin polylines, parts as centers. One file,
             self-contained (grid + fiducials + legend).
  --copper   EXACT COPPER, LAYER-PER-FILE -- each requested copper layer (1, 16)
             becomes its own transparent PNG/SVG holding ONLY that layer's real
             filled copper (pads/SMDs/package-polygons as drawn, drill holes as
             true transparent holes, traces stroked at true width). Each requested
             non-copper layer (20 outline, 48/49/51 guides) gets its own file too.
             A separate 'registration' file carries grid + axes + fiducials + part
             labels. All files share one page size, so they re-register pixel-exact
             when stacked. PNG is a true Inkscape raster of the SVG.

Usage:
    python3 brd_to_template.py "<board>.brd" [--origin 50,26] [--layers 20,48,49,51]
    python3 brd_to_template.py "<board>.brd" --copper --layers 1,16,20 --origin 50,26
"""
import argparse, math, os, re, shutil, subprocess, hashlib, json

INKSCAPE = '/Applications/Inkscape.app/Contents/MacOS/inkscape'
COPPER_LAYERS = {'1', '16'}

def _sha256(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def _version_of(brd):
    m = re.search(r'r\d+\.\d+[a-z]?', os.path.basename(brd))
    return m.group(0) if m else os.path.splitext(os.path.basename(brd))[0]


def _nn_pitch_mm(parts):
    """median nearest-neighbour spacing among part centers (a generic placement pitch)."""
    pts = [(x, y) for _, x, y in parts]
    ds = []
    for i, (x, y) in enumerate(pts):
        nn = min((math.hypot(x - X, y - Y) for j, (X, Y) in enumerate(pts) if j != i), default=0)
        if nn: ds.append(nn)
    ds.sort()
    return ds[len(ds) // 2] if ds else 0.0


def _params(brd, b, parts, oy, gendir, gen_list):
    """Generic, machine-extractable knobs only -- no project-specific geometry. Curate any
    project specifics (bowline/projection/etc.) into this file by hand afterwards."""
    widths = sorted({round(float(a['width']), 4) for a in
                     (attrs(t) for t in re.findall(r'<wire [^>]*/>', b))
                     if a.get('layer') in ('1', '16') and 'width' in a})
    vias = {(round(float(a.get('drill', 0)), 3), round(float(a.get('diameter', 0)), 3))
            for a in (attrs(t) for t in re.findall(r'<via [^>]*/>', b))}
    pitch = _nn_pitch_mm(parts)
    return {
        'version': _version_of(brd),
        'auto_extracted': True,
        'note': 'machine-extracted from the .brd; CURATE project-specific geometry by hand (see CONSTRUCTION.md / SOURCE_OF_TRUTH.md)',
        'frame': {'symmetry_axis_y_mm': oy, 'units': 'mm (1 SVG unit = 1 mm, EAGLE-coords)'},
        'parts': {'count': len(parts), 'pitch_mm': round(pitch, 4),
                  'pitch_mil': round(pitch / 0.0254, 1) if pitch else 0},
        'traces': {'copper_widths_mm': widths, 'vias_drill_pad_mm': sorted(list(vias))},
        'frozen_generators': {f: _sha256(os.path.join(gendir, f))
                              for f in gen_list if os.path.exists(os.path.join(gendir, f))},
    }


CONSTRUCTION_SKELETON = """# {ver} — Construction record (what came from where)

Auto-generated skeleton by `brd_to_template.py --folder`. Fill in the provenance of each source
curve and decision. See `SOURCE_OF_TRUTH.md` for the doctrine (the .brd is a render; the curves live
in `source.svg` + the frozen `gen/` generators + `params.json`).

## Files in this folder
| File | What it is | Source of truth? |
|---|---|---|
| `{base}.brd` / `.sch` | EAGLE **render** (curves atomized to arcs) | **No — output.** |
| `source.svg` | Inkscape-layer SVG; clean source curves on named layers | **Yes** — geometry |
| `gen/*.py` | **frozen** generators used this rev (sha256 in params.json) | **Yes** |
| `params.json` | auto-extracted knobs + frozen-script pins | the index |

## Lineage (FILL IN)
- Source curves: _which `source.svg` layers, and where each came from._
- Generators: _what each `gen/` script produced this rev._
- What changed vs the prior rev: _describe here._
"""


def make_folder(brd, ink_svg, parts, b, oy, gen_list):
    """Assemble a per-version SOURCE-OF-TRUTH folder around an already-written inkscape-layers
    SVG: copy brd/sch, move the SVG in as source.svg, freeze the named generator scripts into
    gen/, write params.json + a CONSTRUCTION.md skeleton. Project-AGNOSTIC -- any project-specific
    post-step (e.g. injecting computed curves into source.svg) runs separately, after this."""
    base = os.path.splitext(os.path.basename(brd))[0]
    folder = os.path.join(os.path.dirname(os.path.abspath(brd)), base)
    gendir = os.path.join(folder, 'gen')
    os.makedirs(gendir, exist_ok=True)
    shutil.copy2(brd, folder)
    sch = os.path.splitext(brd)[0] + '.sch'
    if os.path.exists(sch): shutil.copy2(sch, folder)
    src_svg = os.path.join(folder, 'source.svg')
    shutil.move(ink_svg, src_svg)                       # the combined doc becomes source.svg
    here = os.path.dirname(os.path.abspath(__file__))
    for f in gen_list:                                  # freeze exactly the named generators
        p = os.path.join(here, f)
        if os.path.exists(p): shutil.copy2(p, os.path.join(gendir, f))
    shutil.rmtree(os.path.join(gendir, '__pycache__'), ignore_errors=True)
    json.dump(_params(brd, b, parts, oy, gendir, gen_list),
              open(os.path.join(folder, 'params.json'), 'w'), indent=2)
    cm = os.path.join(folder, 'CONSTRUCTION.md')
    if not os.path.exists(cm):
        open(cm, 'w').write(CONSTRUCTION_SKELETON.format(ver=_version_of(brd), base=base))
    return folder

LAYER_STYLE = {
    '1':  ('L1 Top copper',             '#c08020', 0.15),
    '16': ('L16 Bottom copper',         '#2050c0', 0.15),
    '20': ('L20 Dimension (board edge)','#000000', 0.30),
    '49': ('L49 Reference (Cuttle dim)','#d62728', 0.15),
    '51': ('L51 tDocu (master bowline)','#1f77b4', 0.22),
    '48': ('L48 Document (bowline parts)','#2ca02c', 0.12),
    '21': ('L21 tPlace (top silk)',       '#cc00cc', 0.12),
    '22': ('L22 bPlace (bottom silk)',    '#882288', 0.12),
    '52': ('L52 bDocu',                   '#88aacc', 0.20),
    '29': ('L29 tStop (mask)',            '#00897b', 0.10),
    '30': ('L30 bStop (mask)',            '#00695c', 0.10),
    '200':('L200 Bowline',                '#ff7f0e', 0.18),
    '201':('L201 Bowline edit',           '#7f7f7f', 0.15),
}
LAYER_SLUG = {'1':'L1 copper','16':'L16 copper','20':'L20 outline',
              '48':'L48 guides','49':'L49 reference','51':'L51 tDocu',
              '21':'L21 tPlace','22':'L22 bPlace','52':'L52 bDocu',
              '29':'L29 tStop','30':'L30 bStop','200':'L200 Bowline','201':'L201 edit'}

def attrs(s): return dict(re.findall(r'(\w+)="([^"]*)"', s))

def arc_pts(x1, y1, x2, y2, C, n=48):
    Cr = math.radians(C); dx, dy = x2 - x1, y2 - y1; d = math.hypot(dx, dy)
    if d == 0 or abs(C) < 1e-9: return [(x1, y1), (x2, y2)]
    h = (d / 2) / math.tan(Cr / 2); px, py = -dy / d, dx / d
    cx, cy = (x1 + x2) / 2 + px * h, (y1 + y2) / 2 + py * h
    r = math.hypot(x1 - cx, y1 - cy); a0 = math.atan2(y1 - cy, x1 - cx)
    return [(cx + r*math.cos(a0+Cr*t/n), cy + r*math.sin(a0+Cr*t/n)) for t in range(n+1)]

def parse(brd, layers):
    """Wires (with arcs) on the requested layers, from <plain>/<signals>
    (NOT <libraries> -- those are local package coords). Plus LED centers."""
    b = open(brd).read()
    body = re.sub(r'<libraries>.*?</libraries>', '', b, flags=re.S)
    polys = {L: [] for L in layers}
    for tag in re.findall(r'<wire [^>]*/>', body):
        a = attrs(tag); L = a.get('layer')
        if L not in layers: continue
        x1, y1, x2, y2 = (float(a[k]) for k in ('x1','y1','x2','y2'))
        polys[L].append(arc_pts(x1,y1,x2,y2,float(a['curve'])) if 'curve' in a else [(x1,y1),(x2,y2)])
    leds = [(m.group(1), float(m.group(2)), float(m.group(3)))
            for m in re.finditer(r'<element name="(LED\d+)"[^>]*x="([-0-9.]+)" y="([-0-9.]+)"', b)]
    return b, polys, leds

def package_block(b, pkg):
    m = re.search(r'<package name="' + re.escape(pkg) + r'">(.*?)</package>', b, re.S)
    return m.group(1) if m else ''

def elem_affine(a):
    """shapely affine [a,b,d,e,xoff,yoff] for an element's placement (rot+mirror)."""
    ex, ey = float(a['x']), float(a['y']); rot = a.get('rot', 'R0')
    mir = 'M' in rot; ang = math.radians(float(re.sub('[MR]', '', rot) or 0))
    ca, sa = math.cos(ang), math.sin(ang)
    M = [-ca, -sa, -sa, ca, ex, ey] if mir else [ca, -sa, sa, ca, ex, ey]
    return M, mir, (ex, ey)

def xform_pts(pts, M):
    return [(M[0]*x + M[1]*y + M[4], M[2]*x + M[3]*y + M[5]) for x, y in pts]

def headers(b, names=('J1','J2')):
    pads, elems = [], {}
    for em in re.finditer(r'<element [^>]*>', b):
        a = attrs(em.group(0))
        if a.get('name') not in names: continue
        M, mir, (ex, ey) = elem_affine(a); elems[a['name']] = (ex, ey)
        for pm in re.finditer(r'<pad [^>]*/>', package_block(b, a.get('package',''))):
            pa = attrs(pm.group(0))
            (bx, by), = xform_pts([(float(pa['x']), float(pa['y']))], M)
            pads.append((a['name'], pa.get('name'), bx, by, float(pa.get('diameter', 1.7))))
    return pads, elems

def rounded_box(dx, dy, rn):
    """EAGLE smd as a shapely polygon. roundness% of the short half-side; at 100%
    it's a true stadium (semicircular ends) -- the erode/dilate trick collapses
    there, so build the stadium from the centerline segment instead."""
    from shapely.geometry import box, LineString, Point
    if rn <= 0:
        return box(-dx/2, -dy/2, dx/2, dy/2)
    r = rn/100.0 * min(dx, dy)/2.0
    if r >= min(dx, dy)/2.0 - 1e-9:                       # stadium / circle
        rr = min(dx, dy)/2.0
        if abs(dx - dy) < 1e-9: return Point(0, 0).buffer(rr, resolution=24)
        if dx > dy:
            h = dx/2.0 - rr; return LineString([(-h, 0), (h, 0)]).buffer(rr, resolution=24)
        h = dy/2.0 - rr; return LineString([(0, -h), (0, h)]).buffer(rr, resolution=24)
    return box(-dx/2, -dy/2, dx/2, dy/2).buffer(-r, join_style=2).buffer(r, join_style=1)

def copper_geoms(b, L):
    """Exact copper for board layer L: (fills, traces).
    fills = shapely polygons (THT pads with drill knocked out as a real hole, SMDs,
    artistic package polygons); traces = [(points, width)] stroked wires.
    Traced from the board's own embedded packages (placed truth, same harvest as
    stopmask.py/pour.py), transformed per element; mirror swaps copper 1<->16."""
    from shapely.geometry import Polygon, Point
    from shapely.affinity import rotate as _rot, translate as _tr, affine_transform as _aff
    OTHER = {'1': '16', '16': '1'}
    fills, traces = [], []
    body = re.sub(r'<libraries>.*?</libraries>', '', b, flags=re.S)
    for tag in re.findall(r'<wire [^>]*/>', body):                 # routed copper
        a = attrs(tag)
        if a.get('layer') != L: continue
        x1, y1, x2, y2 = (float(a[k]) for k in ('x1','y1','x2','y2'))
        pts = arc_pts(x1,y1,x2,y2,float(a['curve'])) if 'curve' in a else [(x1,y1),(x2,y2)]
        traces.append((pts, float(a.get('width', 0.2))))
    for em in re.finditer(r'<element [^>]*>', b):
        a = attrs(em.group(0)); pkg = a.get('package')
        if not pkg: continue
        M, mir, _ = elem_affine(a); blk = package_block(b, pkg)
        eff = (lambda l: OTHER.get(l, l)) if mir else (lambda l: l)
        for pm in re.finditer(r'<pad [^>]*/>', blk):               # THT -> both layers
            pa = attrs(pm.group(0)); px, py = float(pa['x']), float(pa['y'])
            pad = Point(px, py).buffer(float(pa.get('diameter', 1.7))/2, resolution=24)
            dr = float(pa.get('drill', 0))
            if dr: pad = pad.difference(Point(px, py).buffer(dr/2, resolution=16))
            fills.append(_aff(pad, M))
        for sm in re.finditer(r'<smd [^>]*/>', blk):               # SMD -> element side
            pa = attrs(sm.group(0))
            if eff(pa.get('layer', '1')) != L: continue
            g = rounded_box(float(pa['dx']), float(pa['dy']), float(pa.get('roundness', 0)))
            srot = float(re.sub('[MR]', '', pa.get('rot', 'R0')) or 0)
            g = _tr(_rot(g, srot, origin=(0, 0)), float(pa['x']), float(pa['y']))
            fills.append(_aff(g, M))
        for pgm in re.finditer(r'<polygon [^>]*layer="(\d+)"[^>]*>(.*?)</polygon>', blk, re.S):
            if eff(pgm.group(1)) != L: continue
            vv = [(float(v['x']), float(v['y']), float(v.get('curve', 0)))
                  for v in (attrs(t) for t in re.findall(r'<vertex [^/]*/>', pgm.group(2)))]
            ring = []
            for i, (vx, vy, cv) in enumerate(vv):
                nx, ny, _ = vv[(i+1) % len(vv)]
                ring += arc_pts(vx, vy, nx, ny, cv)[:-1] if cv else [(vx, vy)]
            if len(ring) >= 3: fills.append(_aff(Polygon(ring), M))
        for wm in re.finditer(r'<wire [^>]*/>', blk):              # package copper wires
            pa = attrs(wm.group(0))
            if eff(pa.get('layer', '')) != L: continue
            x1, y1, x2, y2 = (float(pa[k]) for k in ('x1','y1','x2','y2'))
            pts = arc_pts(x1,y1,x2,y2,float(pa['curve'])) if 'curve' in pa else [(x1,y1),(x2,y2)]
            traces.append((xform_pts(pts, M), float(pa.get('width', 0.2))))
    fills = [g for g in fills if not g.is_empty]
    return fills, traces

def graphics_geoms(b, L):
    """Non-copper graphics on board layer L: (fills, strokes). Harvests <wire>
    (with arcs), <circle>, <rectangle>, <polygon> from <plain> (board-level) AND
    from every element's package (transformed by placement). Mirror swaps paired
    layers (21<->22, 51<->52, ...) so a flipped part's silk lands on the right side.
    Text is intentionally skipped for now (outlines only)."""
    from shapely.geometry import Polygon
    from shapely.affinity import affine_transform as _aff
    SWAP = {'1':'16','16':'1','21':'22','22':'21','25':'26','26':'25',
            '27':'28','28':'27','29':'30','30':'29','51':'52','52':'51'}
    fills, strokes = [], []
    def put(txt, M, eff):
        tx = (lambda P: xform_pts(P, M)) if M else (lambda P: P)
        ax = (lambda g: _aff(g, M)) if M else (lambda g: g)
        for wm in re.finditer(r'<wire [^>]*/>', txt):
            a = attrs(wm.group(0))
            if eff(a.get('layer','')) != L: continue
            x1,y1,x2,y2 = (float(a[k]) for k in ('x1','y1','x2','y2'))
            pts = arc_pts(x1,y1,x2,y2,float(a['curve'])) if 'curve' in a else [(x1,y1),(x2,y2)]
            strokes.append((tx(pts), float(a.get('width') or 0) or 0.1))
        for cm in re.finditer(r'<circle [^>]*/>', txt):
            a = attrs(cm.group(0))
            if eff(a.get('layer','')) != L: continue
            cx,cy,r = float(a['x']),float(a['y']),float(a['radius']); w = float(a.get('width',0))
            ring = [(cx+r*math.cos(math.tau*i/48), cy+r*math.sin(math.tau*i/48)) for i in range(49)]
            if w > 0: strokes.append((tx(ring), w))
            else: fills.append(ax(Polygon(ring)))
        for rm in re.finditer(r'<rectangle [^>]*/>', txt):
            a = attrs(rm.group(0))
            if eff(a.get('layer','')) != L: continue
            x1,y1,x2,y2 = (float(a[k]) for k in ('x1','y1','x2','y2'))
            cx,cy = (x1+x2)/2,(y1+y2)/2; ang = math.radians(float(re.sub('[MR]','',a.get('rot','R0')) or 0))
            ca,sa = math.cos(ang),math.sin(ang)
            rect = [(cx+(x-cx)*ca-(y-cy)*sa, cy+(x-cx)*sa+(y-cy)*ca) for x,y in [(x1,y1),(x2,y1),(x2,y2),(x1,y2)]]
            fills.append(ax(Polygon(rect)))
        for pgm in re.finditer(r'<polygon [^>]*layer="(\d+)"[^>]*>(.*?)</polygon>', txt, re.S):
            if eff(pgm.group(1)) != L: continue
            vv = [(float(v['x']),float(v['y']),float(v.get('curve',0)))
                  for v in (attrs(t2) for t2 in re.findall(r'<vertex [^/]*/>', pgm.group(2)))]
            ring = []
            for i,(vx,vy,cv) in enumerate(vv):
                nx,ny,_ = vv[(i+1)%len(vv)]
                ring += arc_pts(vx,vy,nx,ny,cv)[:-1] if cv else [(vx,vy)]
            if len(ring) >= 3: fills.append(ax(Polygon(ring)))
    pm = re.search(r'<plain>(.*?)</plain>', b, re.S)
    if pm: put(pm.group(1), None, lambda l: l)
    for em in re.finditer(r'<element [^>]*>', b):
        a = attrs(em.group(0)); pkg = a.get('package')
        if not pkg: continue
        M, mir, _ = elem_affine(a)
        eff = (lambda l: SWAP.get(l, l)) if mir else (lambda l: l)
        put(package_block(b, pkg), M, eff)
    return [g for g in fills if not g.is_empty], strokes

def path_d(geom, SX, SY):
    def ring(coords):
        p = list(coords)
        return ('M' + ' '.join(f'{SX(x)},{SY(y)}' for x, y in p[:1]) +
                ' L' + ' '.join(f'{SX(x)},{SY(y)}' for x, y in p[1:]) + ' Z')
    polys = list(geom.geoms) if geom.geom_type.startswith('Multi') else [geom]
    out = []
    for p in polys:
        if p.is_empty: continue
        out.append(ring(p.exterior.coords))
        out += [ring(h.coords) for h in p.interiors]
    return ' '.join(out)

def rasterize(svg, png, dpi):
    """True transparent raster of the full page via Inkscape."""
    if dpi <= 0: return '(png skipped --no-png; open the .svg in Inkscape)'
    if not os.path.exists(INKSCAPE): return f'(no Inkscape at {INKSCAPE}; SVG only)'
    r = subprocess.run([INKSCAPE, svg, '--export-type=png', '--export-area-page',
                        '--export-background-opacity=0', '--export-filename=' + png,
                        f'--export-dpi={dpi}'], capture_output=True, text=True)
    return png if os.path.exists(png) else f'(inkscape failed: {(r.stderr or r.stdout)[:200]})'

SVG_NS = ('<svg xmlns="http://www.w3.org/2000/svg" '
          'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
          'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd" '
          'width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">')

def write_file(out, W, H, ox, oy, body, dpi):
    """Wrap body lines in a transparent SVG page (no white rect) and rasterize."""
    S = [SVG_NS.format(W=W, H=H),
         f'<!-- 1 unit = 1 mm. North up. origin = board mm ({ox},{oy}); board = drawn + ({ox},{oy}). '
         f'Transparent background. Do NOT move the magenta fiducials. -->'] + body + ['</svg>']
    open(out + '.svg', 'w').write('\n'.join(S))
    return out + '.svg', rasterize(out + '.svg', out + '.png', dpi)

def ink_layer(label, id_, body, locked=False, hidden=False, transform=None):
    """Wrap body in an Inkscape LAYER group -- opens with an eye icon / lock in
    Inkscape (and Illustrator). The inkscape:label is the round-trip routing handle:
    the importer reads it to send drawn paths back to the right EAGLE layer.
    transform (e.g. a Y-flip) makes paths drawn in this layer store in layer-local
    coordinates -- used by --eagle-coords so drawn paths read back as raw EAGLE mm."""
    style = 'display:none' if hidden else 'display:inline'
    lock = ' sodipodi:insensitive="true"' if locked else ''
    tr = f' transform="{transform}"' if transform else ''
    return ([f'<g inkscape:groupmode="layer" inkscape:label="{label}" id="{id_}"{tr} style="{style}"{lock}>']
            + body + ['</g>'])

def board_bounds(b, layers=None):
    """(minx,miny,maxx,maxy) over placed wires on the REQUESTED layers (plain+signals)
    plus element origins -- enough to size the page; pad/silk extents are covered by the
    caller's margin. Layer-filtered so construction guides (e.g. L48) you didn't ask for
    don't inflate the page."""
    body = re.sub(r'<libraries>.*?</libraries>', '', b, flags=re.S)
    xs, ys = [], []
    for m in re.finditer(r'<wire [^>]*/>', body):
        a = attrs(m.group(0))
        if layers and a.get('layer') not in layers: continue
        xs += [float(a[k]) for k in ('x1','x2') if k in a]
        ys += [float(a[k]) for k in ('y1','y2') if k in a]
    for m in re.finditer(r'<element [^>]*>', b):
        a = attrs(m.group(0))
        if 'x' in a and 'y' in a: xs.append(float(a['x'])); ys.append(float(a['y']))
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0,0,100,100)

def grid_axes(SX, SY, ox, oy, NX0, NX1, NY0, NY1, W, H):
    S = ['<g stroke="#cfcfcf" stroke-width="0.05">']
    nx = math.ceil(NX0/10)*10
    while nx <= NX1: S.append(f'<line x1="{SX(nx+ox)}" y1="0" x2="{SX(nx+ox)}" y2="{H}"/>'); nx += 10
    ny = math.ceil(NY0/10)*10
    while ny <= NY1: S.append(f'<line x1="0" y1="{SY(ny+oy)}" x2="{W}" y2="{SY(ny+oy)}"/>'); ny += 10
    S.append('</g>')
    S.append(f'<line x1="0" y1="{SY(oy)}" x2="{W}" y2="{SY(oy)}" stroke="#444" stroke-width="0.2"/>')
    S.append(f'<line x1="{SX(ox)}" y1="0" x2="{SX(ox)}" y2="{H}" stroke="#444" stroke-width="0.2"/>')
    S.append('<g font-size="1.1" fill="#666" font-family="sans-serif">')
    nx = math.ceil(NX0/10)*10
    while nx <= NX1:
        if nx != 0: S.append(f'<text x="{SX(nx+ox)+0.2}" y="{SY(oy)-0.4}">{nx:g}</text>')
        nx += 10
    ny = math.ceil(NY0/10)*10
    while ny <= NY1:
        if ny != 0: S.append(f'<text x="{SX(ox)+0.3}" y="{SY(ny+oy)-0.3}">{ny:g}</text>')
        ny += 10
    S.append(f'<text x="{SX(ox)+0.4}" y="{SY(oy)-0.5}" font-size="1.4" fill="#000">(0,0)</text></g>')
    return S

def fiducials(fids, SX, SY, ox, oy):
    S = ['<g id="fiducials" stroke="#e000e0" stroke-width="0.15" fill="#e000e0">']
    for (mx, my) in fids:
        cx, cy = SX(mx+ox), SY(my+oy)
        S.append(f'<g id="fid_{mx:g}_{my:g}">'
                 f'<line x1="{cx-2}" y1="{cy}" x2="{cx+2}" y2="{cy}"/>'
                 f'<line x1="{cx}" y1="{cy-2}" x2="{cx}" y2="{cy+2}"/>'
                 f'<circle cx="{cx}" cy="{cy}" r="0.25"/>'
                 f'<text x="{cx+2.3}" y="{cy+0.4}" font-size="1.4" stroke="none">({mx:g},{my:g})</text></g>')
    S.append('</g>')
    return S

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('brd'); ap.add_argument('--out-base')
    ap.add_argument('--layers', default='20,48,49,51')
    ap.add_argument('--origin', default='50,26', help='board mm point that becomes new (0,0)')
    ap.add_argument('--fidhalf', default='48,18', help='fiducial half-extents in NEW coords: fx,fy')
    ap.add_argument('--copper', action='store_true',
                    help='exact-copper, one transparent file per layer + a registration file')
    ap.add_argument('--dpi', type=float, default=254, help='PNG raster dpi (254 = 10 px/mm)')
    ap.add_argument('--no-png', action='store_true', help='write SVGs only, skip the (slow) Inkscape PNG raster')
    ap.add_argument('--out-dir', help='output folder (default: "<board> template/" next to the .brd)')
    ap.add_argument('--per-layer', action='store_true',
                    help='also emit the individual per-layer SVGs (Photoshop-style); default = the single combined Inkscape doc only')
    ap.add_argument('--eagle-coords', action='store_true',
                    help='author in raw EAGLE board mm (origin 0,0, no centering); draw layers carry a Y-flip transform so paths drawn in Inkscape (enable Y-axis-up) read back as exact EAGLE coords -- no transform needed')
    ap.add_argument('--draw', default='1,16',
                    help='comma list of EAGLE layer numbers to create empty unlocked DRAW layers for (default 1,16; e.g. 1,16,20 to draw the board outline)')
    ap.add_argument('--folder', action='store_true',
                    help='build the per-version SOURCE-OF-TRUTH folder ("<board>/"): source.svg (this '
                         'doc) + brd/sch + frozen gen/ (see --gen) + params.json + CONSTRUCTION.md '
                         'skeleton. Implies --copper --eagle-coords. See SOURCE_OF_TRUTH.md. Run any '
                         'project-specific source.svg injection as a separate step afterwards.')
    ap.add_argument('--gen', default='',
                    help='comma list of generator-script basenames (in this toolkit dir) to FREEZE into '
                         'the folder gen/ (sha256-pinned in params.json). Only used with --folder. '
                         'Pass the project\'s own generator scripts, e.g. place.py,route.py.')
    a = ap.parse_args()
    if a.folder:                    # the folder is built from the eagle-coords combined doc
        a.copper = True; a.eagle_coords = True; a.no_png = True
    if a.no_png: a.dpi = 0          # SVG-only: skip the slow Inkscape raster
    layers = a.layers.split(',')
    ox, oy = (float(v) for v in a.origin.split(','))
    fx, fy = (float(v) for v in a.fidhalf.split(','))
    base = a.out_base or os.path.splitext(a.brd)[0]
    b, polys, leds = parse(a.brd, layers)
    hpads, helems = headers(b)

    if a.eagle_coords:
        ox, oy = 0.0, 0.0                                  # raw EAGLE mm, origin at EAGLE (0,0)
        mnx, mny, mxx, mxy = board_bounds(b, set(layers)); m = 6.0
        NX0, NY0, NX1, NY1 = 0.0, 0.0, mxx + m, mxy + m    # page = EAGLE origin -> board extent + margin
    else:
        NX0, NX1, NY0, NY1 = -fx-7, fx+7, -fy-7, fy+7
    W, H = NX1-NX0, NY1-NY0
    SX = lambda x: round((x-ox) - NX0, 4)                  # eagle: -> x   (svg_x = EAGLE x)
    SY = lambda y: round(NY1 - (y-oy), 4)                  # eagle: -> H-y (pre-flip; draw layers re-flip)
    fids = ([(round(NX0+10), round(NY0+10)), (round(NX1-10), round(NY0+10)),
             (round(NX1-10), round(NY1-10)), (round(NX0+10), round(NY1-10))]
            if a.eagle_coords else [(-fx,-fy),(fx,-fy),(fx,fy),(-fx,fy)])
    scale_bar = (f'<g font-size="1.6" font-family="sans-serif">'
                 f'<line x1="2" y1="{H-2}" x2="12" y2="{H-2}" stroke="#000" stroke-width="0.3"/>'
                 f'<text x="2" y="{H-3}" fill="#000">10 mm  (1 unit = 1 mm) · origin = board ({ox:g},{oy:g})</text></g>')

    if a.copper:
        outdir = a.out_dir or (os.path.splitext(a.brd)[0] + ' template')
        os.makedirs(outdir, exist_ok=True)
        bname = os.path.basename(os.path.splitext(a.brd)[0])
        per = []; bodies = {}
        for L in layers:
            if L not in COPPER_LAYERS and L not in LAYER_STYLE: continue
            col = LAYER_STYLE.get(L, (None, '#808080', 0.12))[1]
            # exact copper (pads/SMDs + traces) OR general graphics (silk/docu/ref):
            # both return (filled polygons, stroked polylines-with-width) and render alike
            fills, strokes = copper_geoms(b, L) if L in COPPER_LAYERS else graphics_geoms(b, L)
            body = [f'<g fill="{col}" fill-rule="evenodd" stroke="none">']
            body += [f'<path d="{path_d(g, SX, SY)}"/>' for g in fills if path_d(g, SX, SY)]
            body.append('</g>')
            if strokes:
                body.append(f'<g stroke="{col}" fill="none" stroke-linecap="round" stroke-linejoin="round">')
                body += ['<polyline stroke-width="%g" points="%s"/>' %
                         (w, ' '.join(f'{SX(x)},{SY(y)}' for x, y in pts)) for pts, w in strokes]
                body.append('</g>')
            bodies[L] = body
            if a.per_layer:                                   # optional Photoshop-style per-layer files
                svg, _ = write_file(os.path.join(outdir, f'{bname} {LAYER_SLUG.get(L, "L"+L)}'), W, H, ox, oy, body, a.dpi)
                per.append(os.path.basename(svg))
        # registration layer: grid, axes, fiducials, part labels (toggle on while drawing)
        reg = grid_axes(SX, SY, ox, oy, NX0, NX1, NY0, NY1, W, H)
        reg.append('<g fill="#555" font-family="sans-serif" font-size="0.9">')
        reg += [f'<text x="{SX(lx)+0.4}" y="{SY(ly)-0.4}">{name[3:]}</text>' for name, lx, ly in leds]
        reg.append('</g>')
        reg.append('<g fill="#004d4d" font-family="sans-serif" font-size="2.2" font-weight="bold">')
        for en in helems:
            exs = [bx for e,_,bx,_,_ in hpads if e == en]
            lx = min(exs) if exs else helems[en][0]
            reg.append(f'<text x="{SX(lx)-4.5}" y="{SY(helems[en][1])+0.7}">{en}</text>')
        reg.append('</g>')
        reg += fiducials(fids, SX, SY, ox, oy); reg.append(scale_bar)
        if a.per_layer:
            svg, _ = write_file(os.path.join(outdir, f'{bname} registration'), W, H, ox, oy, reg, a.dpi)
            per.append(os.path.basename(svg))

        # THE deliverable: ONE combined Inkscape doc holding every layer (it is how
        # Inkscape stores layers -- all inside one SVG; this is the only file you open).
        combined = []
        for L in [x for x in ('48','20','52','51','22','21','30','29','16','1','49','200','201') if x in bodies]:
            label = LAYER_STYLE.get(L, (LAYER_SLUG.get(L, 'L'+L),))[0]
            combined += ink_layer(label, 'layer'+L, bodies[L], locked=True)
        combined += ink_layer('registration (grid · fiducials · labels)', 'layerReg', reg, locked=True)
        dtr = f'matrix(1 0 0 -1 0 {H:g})' if a.eagle_coords else None   # Y-flip -> draw-layer coords = EAGLE mm
        for L in a.draw.split(','):
            combined += ink_layer(f'draw → {LAYER_SLUG.get(L, "L"+L)}',
                                  'layerDraw'+L, [], locked=False, transform=dtr)
        if a.folder:                                   # write the doc where make_folder expects it
            outdir = os.path.dirname(os.path.abspath(a.brd))
        ink = os.path.join(outdir, f'{bname} (inkscape layers)')
        note = (f'<!-- EAGLE-COORDS MODE: enable "Y axis points up" in Inkscape (Document Properties). '
                f'Coordinates then equal raw EAGLE board mm: a part at EAGLE (x,y) shows at (x,y). '
                f'Draw in a "draw to L#" layer (it carries a Y-flip) and the path d is already EAGLE mm; '
                f'paste it back with no transform. Reference layers are locked/view-only. -->'
                if a.eagle_coords else
                f'<!-- Open in Inkscape: each EAGLE layer is a named, LOCKED layer (eye+lock). Draw in a '
                f'"draw -> L#" layer; importer reads inkscape:label. origin=board ({ox},{oy}); '
                f'board=drawn+({ox},{oy}); keep the magenta fiducials. -->')
        S = [SVG_NS.format(W=W, H=H), note] + combined + ['</svg>']
        open(ink + '.svg', 'w').write('\n'.join(S))
        rasterize(ink + '.svg', ink + '.png', a.dpi)

        if a.folder:
            sym_y = float(a.origin.split(',')[1])      # true symmetry axis (eagle-coords zeroed oy)
            gen_list = [g for g in a.gen.split(',') if g]
            folder = make_folder(a.brd, ink + '.svg', leds, b, sym_y, gen_list)
            print(f"built source-of-truth folder: {folder}/")
            for f in sorted(os.listdir(folder)):
                print(f"  {f}" + ('/' if os.path.isdir(os.path.join(folder, f)) else ''))
            gd = os.path.join(folder, 'gen')
            if os.path.isdir(gd): print(f"  gen/: {', '.join(sorted(os.listdir(gd))) or '(none — pass --gen)'}")
            print("NEXT: run any project-specific source.svg injection; fill CONSTRUCTION.md; curate params.json")
            return

        print(f"wrote {outdir}/")
        print(f"  {bname} (inkscape layers).svg   ({len(bodies)} reference layers + registration + {len(a.draw.split(','))} draw layers)" +
              ('' if a.dpi <= 0 else '  [+png]'))
        if per: print(f"  + {len(per)} per-layer files (--per-layer)")
        print(f"origin = board mm ({ox},{oy}); headers: {', '.join(sorted(helems)) or '(none)'}; LEDs: {len(leds)}")
        return

    # ---- CENTERLINE mode (single Inkscape-layered transparent file) ----
    doc = []
    # registration layer (grid + axes + fiducials + legend), locked, at the bottom
    reg = grid_axes(SX, SY, ox, oy, NX0, NX1, NY0, NY1, W, H)
    reg += fiducials(fids, SX, SY, ox, oy)
    reg.append('<g font-size="1.6" font-family="sans-serif">')
    for i,L in enumerate(layers):
        if L not in LAYER_STYLE: continue
        lbl,col,wd = LAYER_STYLE[L]; yy = 2+i*2.2
        reg.append(f'<line x1="2" y1="{yy}" x2="6" y2="{yy}" stroke="{col}" stroke-width="0.5"/>'
                   f'<text x="7" y="{yy+0.6}" fill="#333">{lbl}</text>')
    reg.append('</g>'); reg.append(scale_bar)
    doc += ink_layer('registration (grid · fiducials · legend)', 'layerReg', reg, locked=True)
    # one named layer per EAGLE layer
    for L in layers:
        if L not in LAYER_STYLE: continue
        lbl, col, wd = LAYER_STYLE[L]
        g = [f'<g stroke="{col}" stroke-width="{wd}" fill="none" stroke-linecap="round" stroke-linejoin="round">']
        g += ['<polyline points="' + ' '.join(f'{SX(px)},{SY(py)}' for px,py in pl) + '"/>' for pl in polys[L]]
        g.append('</g>')
        doc += ink_layer(lbl, 'layer'+L, g, locked=True)
    # parts layer (LED centers + J1/J2 pads)
    parts = ['<g id="LEDs" fill="#ff7f0e">']
    for name, lx, ly in leds:
        parts.append(f'<circle cx="{SX(lx)}" cy="{SY(ly)}" r="0.5"/>')
        parts.append(f'<text x="{SX(lx)+0.6}" y="{SY(ly)+0.4}" font-size="0.9" fill="#b35a00">{name[3:]}</text>')
    parts.append('</g><g id="headers">')
    for en, pn, bx, by, dia in hpads:
        cx, cy = SX(bx), SY(by); r = dia/2
        if pn == '1':
            parts.append(f'<rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" fill="#00a0a0" stroke="#004d4d" stroke-width="0.12"/>')
        else:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#00a0a0" stroke="#004d4d" stroke-width="0.12"/>')
    for en in helems:
        exs = [bx for e,_,bx,_,_ in hpads if e == en]
        lx = min(exs) if exs else helems[en][0]; ly = helems[en][1]
        parts.append(f'<text x="{SX(lx)-4.5}" y="{SY(ly)+0.7}" font-size="2.2" fill="#004d4d" font-family="sans-serif" font-weight="bold">{en}</text>')
    parts.append('</g>')
    doc += ink_layer('parts (LEDs · J1/J2)', 'layerParts', parts, locked=True)
    doc += ink_layer('draw', 'layerDraw', [], locked=False)
    out = base + ' template (centered)'
    S = [SVG_NS.format(W=W, H=H),
         f'<!-- Inkscape layers (eye+lock per layer); draw in the unlocked "draw" layer. '
         f'origin=board ({ox},{oy}); board=drawn+({ox},{oy}); keep the magenta fiducials. -->'] + doc + ['</svg>']
    open(out + '.svg', 'w').write('\n'.join(S))
    png = rasterize(out + '.svg', out + '.png', a.dpi)
    print(f"wrote {out}.svg\nwrote {png}")
    print(f"origin = board mm ({ox},{oy}); headers found: {', '.join(sorted(helems)) or '(none)'} ({len(hpads)} pads)")

if __name__ == '__main__':
    main()
