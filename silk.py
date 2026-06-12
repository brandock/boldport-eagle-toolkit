#!/usr/bin/env python3
"""
silk.py -- place the Boldport silkscreen art onto the board's silk layers
(Step 7), in the .brd frame so it registers with the outline and components.

What gets rendered, driven by the shapes' own attributes (shapes/silkscreen.json):
  * type=path, style=stroke  -> stroked <wire> chains at the shape's stroke-width
                                (outline art, legends);
  * type=path, style=fill    -> one arc-true <polygon> per subpath (pattern fills
                                like the Cuttle waves -- the LEAN way: an earlier
                                approach flattened the same waves to ~8,761 wires);
  * type=text, style=fill    -> FILLED wordmarks from the SVG font named by
                                font-family: glyph contours XOR'd so counters
                                become holes, holes bridged to the outside with
                                ~0.01 mm keyhole slits (invisible at silk
                                resolution); bottom-layer text is x-mirrored.

Plus the FOOTPRINT silkscreen art of placed components (components/<fp>.json ->
layout.silkscreen.shapes): path/text shapes only (e.g. hand-lettered pin labels)
-- in BOARD-absolute coordinates offset by the shape's own location, with the
DIRECT transform (rendering them through the component's placement+rotation puts
them in the wrong spots). type=rect body outlines are deliberately skipped: they
are the part-body documentation that lives on tDocu in the library packages, not
production silk.

Layers: top -> 21 (tPlace), bottom -> 22 (bPlace).

Placement conventions (each empirically verified on the first port):
  * stroke paths take the plain frame transform (x+OX, -y+OY, curve negated);
    their `location` attribute is NOT applied (matches PCBmodE's rendering);
  * fill paths are re-centred on the BOARD CENTRE (the outline bbox centre from
    layer 20) -- pattern fills are authored offset in the source;
  * text is authored y-UP, so it skips the y-flip, and is centred on the board
    centre plus its `location`.

Idempotent via the board's .inject.json manifest (feature "silk"), like
inject_brd.py: re-runs replace only what silk.py previously added.

Usage:
    python3 silk.py <repo-dir> "<board.brd>" [--ox N --oy N]

Branch the board first; Eagle closed; afterward File > Reload.
"""
import argparse, glob, json, math, os, re, sys
from functools import reduce

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arcfit
from svgfont import SvgFont

try:
    from shapely.geometry import Polygon, Point, LineString
    from shapely.ops import nearest_points
except ImportError:
    sys.exit("silk.py needs shapely (pip install --user shapely)")

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER = {'top': 21, 'bottom': 22}


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


def board_centre(brd_text):
    xs, ys = [], []
    plain = re.search(r'<plain>(.*?)</plain>', brd_text, re.S).group(1)
    for w in re.finditer(r'<wire [^>]*layer="20"[^>]*/>', plain):
        t = w.group(0)
        xs += [float(re.search(r'\bx1="(-?[\d.]+)"', t).group(1)),
               float(re.search(r'\bx2="(-?[\d.]+)"', t).group(1))]
        ys += [float(re.search(r'\by1="(-?[\d.]+)"', t).group(1)),
               float(re.search(r'\by2="(-?[\d.]+)"', t).group(1))]
    if not xs:
        sys.exit("no layer-20 outline on the board -- run the outline step first "
                 "(silk registers against it)")
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def W(x1, y1, x2, y2, cv, layer, width):
    tail = f' curve="{cv:.4f}"' if abs(cv) > 1e-6 else ''
    return (f'<wire x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" y2="{y2:.4f}" '
            f'width="{width}" layer="{layer}"{tail}/>')


def path_wires(value, layer, width, ox, oy, dx=0.0, dy=0.0):
    """stroke path -> wire chain; (dx,dy) = extra source-frame offset (footprint silk)"""
    out = []
    for verts, closed in arcfit.fit_path(value, tol=0.02)[0]:
        tv = [(x + dx + ox, -(y + dy) + oy, -cv) for x, y, cv in verts]
        n = len(tv)
        for i in (range(n) if closed else range(n - 1)):
            x1, y1, cv = tv[i]
            x2, y2, _ = tv[(i + 1) % n]
            out.append(W(x1, y1, x2, y2, cv, layer, width))
    return out


def fill_polys(value, layer, bcx, bcy, width=0.0254):
    # width is the polygon BOUNDARY stroke: Eagle fills the polygon AND strokes its
    # outline at this width, dilating the shape by width/2 all around. At the old
    # 0.1524 the wave crescents came out visibly fatter than Boldport's pure-region
    # fill; a 1-mil hairline keeps the dilation below fab resolution.
    """fill path -> one arc-true polygon per subpath, re-centred on the board centre"""
    subs = arcfit.fit_path(value, tol=0.02)[0]
    fl = [[(x, -y, -cv) for x, y, cv in v] for v, _ in subs]
    xs = [x for sp in fl for x, y, cv in sp]
    ys = [y for sp in fl for x, y, cv in sp]
    wcx, wcy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    out = []
    for sp in fl:
        if len(sp) < 3:
            continue
        vs = "\n".join(f'<vertex x="{x - wcx + bcx:.4f}" y="{y - wcy + bcy:.4f}"'
                       + (f' curve="{cv:.4f}"' if abs(cv) > 1e-6 else '') + '/>'
                       for x, y, cv in sp)
        out.append(f'<polygon width="{width}" layer="{layer}">\n{vs}\n</polygon>')
    return out


def _keyhole(poly, w=0.01):
    p = poly
    for _ in range(len(poly.interiors) * 2 + 5):
        if not p.interiors:
            break
        best, bd = None, 1e18
        for h in p.interiors:
            a = Point(h.coords[0]); b = nearest_points(a, p.exterior)[1]
            d = a.distance(b)
            if d < bd:
                bd, best = d, (a, b)
        a, b = best
        slit = LineString([(a.x, a.y), (b.x, b.y)]).buffer(w / 2, cap_style=2, join_style=2)
        q = p.difference(slit)
        cand = [q] if q.geom_type == 'Polygon' else [g for g in getattr(q, 'geoms', [])
                                                     if g.geom_type == 'Polygon']
        if not cand:
            break
        p = max(cand, key=lambda g: g.area)
    return p


def text_fill(shape, layer, mirror_x, font, bcx, bcy):
    """FILLED wordmark: glyph contours XOR'd (even-odd -> counters are holes),
    holes keyholed with tiny intra-letter slits (invisible at silk resolution).
    Text glyphs are y-up: no y-flip. Centred on the board centre + location."""
    size = float(re.match(r'[\d.]+', shape['font-size']).group())
    lx, ly = shape.get('location', [0, 0])
    cx, cy = bcx + lx, bcy - ly
    contours = []
    for sp in font.layout(shape['value'], size):
        pts = [((-x if mirror_x else x) + cx, y + cy) for x, y in sp]
        if len(pts) >= 3:
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.geom_type == 'Polygon' and poly.area > 1e-9:
                contours.append(poly)
    contours.sort(key=lambda p: p.area, reverse=True)
    filled = reduce(lambda acc, c: acc.symmetric_difference(c), contours, Polygon())
    polys = list(filled.geoms) if filled.geom_type == 'MultiPolygon' else [filled]
    out = []
    for p in polys:
        if p.is_empty or p.area < 1e-9:
            continue
        kh = _keyhole(p)
        vs = "\n".join(f'<vertex x="{x:.4f}" y="{y:.4f}"/>'
                       for x, y in list(kh.exterior.coords)[:-1])
        out.append(f'<polygon width="0.1" layer="{layer}">\n{vs}\n</polygon>')
    return out


def inject(board_path, feature, elements):
    man_path = board_path + ".inject.json"
    t = open(board_path).read()
    man = json.load(open(man_path)) if os.path.exists(man_path) else {}
    # silk OWNS the plain silk layers (21/22): board silk art comes only from this
    # tool (footprint silk lives in packages, labels in element attributes). So
    # removal is BY PATTERN over the <plain> section -- strip every plain wire and
    # polygon on 21/22 -- which survives Eagle re-saves. (Exact-string manifest
    # removal does NOT: an Eagle save reformats the XML -- trims trailing zeros
    # etc. -- and orphans the old elements, silently stacking re-runs.)
    m = re.search(r'<plain>.*?</plain>', t, re.S)
    plain = m.group(0)
    n0 = len(re.findall(r'<wire\b|<polygon\b', plain))
    plain2 = re.sub(r'\n?[ \t]*<wire\b[^>]*layer="2[12]"[^>]*/>', '', plain)
    plain2 = re.sub(r'\n?[ \t]*<polygon\b[^>]*layer="2[12]"[^>]*>.*?</polygon>', '',
                    plain2, flags=re.S)
    removed = n0 - len(re.findall(r'<wire\b|<polygon\b', plain2))
    t = t[:m.start()] + plain2 + t[m.end():]
    t = t.replace('</plain>', '\n'.join(elements) + '\n</plain>', 1)
    import xml.dom.minidom as MD
    MD.parseString(t)
    open(board_path, 'w').write(t)
    man[feature] = elements
    json.dump(man, open(man_path, 'w'), indent=1)
    return removed, len(elements)


def main():
    ap = argparse.ArgumentParser(description="Inject the repo silkscreen art into a board .brd")
    ap.add_argument('repo', help="Boldport repo dir (master json found by content) or master .json")
    ap.add_argument('board', help="the .brd to edit (branch it first)")
    ap.add_argument('--ox', type=float, help="x offset (default: from <board>.brd.transform.json)")
    ap.add_argument('--oy', type=float, help="y offset (default: from <board>.brd.transform.json)")
    a = ap.parse_args()

    master_path = find_master(a.repo)
    repo = os.path.dirname(master_path)
    master = json.load(open(master_path))
    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    ox, oy = load_offset(board_path, a.ox, a.oy)

    skfile = os.path.join(repo, "shapes", "silkscreen.json")
    if not os.path.exists(skfile):
        sys.exit(f"no {skfile}")
    sk = json.load(open(skfile))['layout']['silkscreen']['shapes']
    brd_text = open(board_path).read()
    bcx, bcy = board_centre(brd_text)
    print(f"master: {master_path}")
    print(f"board:  {board_path}")
    print(f"offset: OX={ox:g} OY={oy:g} | board centre ({bcx:.2f}, {bcy:.2f})")

    fonts = {}

    def get_font(family):
        if family not in fonts:
            fp = os.path.join(HERE, "fonts", family + ".svg")
            if not os.path.exists(fp):
                sys.exit(f"font '{family}' not found at {fp} -- add the SVG font to the "
                         f"toolkit fonts/ directory")
            fonts[family] = SvgFont(fp)
        return fonts[family]

    body = []
    n_stroke = n_fill = n_text = 0
    for s in sk:
        for side in s.get('layers', []):
            layer = LAYER.get(side)
            if layer is None:
                continue
            if s.get('type') == 'path' and s.get('style') == 'stroke':
                body += path_wires(s['value'], layer, s.get('stroke-width', 0.2), ox, oy)
                n_stroke += 1
            elif s.get('type') == 'path' and s.get('style') == 'fill':
                body += fill_polys(s['value'], layer, bcx, bcy)
                n_fill += 1
            elif s.get('type') == 'text':
                body += text_fill(s, layer, side == 'bottom',
                                  get_font(s['font-family']), bcx, bcy)
                n_text += 1

    # footprint silk (path/text only; rect body outlines live on tDocu)
    n_fp = 0
    placed_fps = {c['footprint'] for c in master['components'].values()}
    for fpname in sorted(placed_fps):
        fp = json.load(open(os.path.join(repo, "components", fpname + ".json")))
        for s in fp.get('layout', {}).get('silkscreen', {}).get('shapes', []):
            if s.get('type') != 'path':
                continue
            lx, ly = s.get('location', [0, 0])
            sides = s.get('layers', ['top'])
            for side in sides:
                layer = LAYER.get(side)
                if layer is None:
                    continue
                body += path_wires(s['value'], layer, s.get('stroke-width', 0.18),
                                   ox, oy, dx=lx, dy=ly)
                n_fp += 1

    removed, added = inject(board_path, 'silk', body)
    nw = sum(1 for e in body if e.startswith('<wire'))
    npoly = sum(1 for e in body if e.startswith('<polygon'))
    print(f"\ninjected 'silk': removed {removed} prior, added {added} elements "
          f"({nw} wires + {npoly} polygons) to {os.path.basename(board_path)}")
    print(f"  board shapes: {n_stroke} stroke paths, {n_fill} pattern fills, "
          f"{n_text} filled wordmarks; footprint silk paths: {n_fp}")
    print("NEXT: open (or File > Reload) the board in Eagle and eyeball the registration "
          "against the pads/outline.")


if __name__ == '__main__':
    main()
