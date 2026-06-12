#!/usr/bin/env python3
"""
gerber_compare.py -- render two Gerber sets to high-resolution rasters and
compare them pixel for pixel, layer by layer (Step 8: CAM verification).

Built for "Eagle CAM output vs make_gerber.py output" but works on any two
RS-274X sets that share layer extensions. Handles the dialect both producers
use: MOMM, FSLAX34 (3.4), circle apertures, D01/D02/D03, G01 lines, G02/G03
multi-quadrant arcs (I/J), G36/G37 regions, %LPD%/%LPC% polarity. Each file is
rendered by compositing its operations IN ORDER (clear really erases), so
polarity pours come out right.

Alignment: the two sets usually live in different frames (Eagle board frame vs
board-centred). Each set is aligned by the centre of its OUTLINE layer (.GKO)
bounding box; everything is rendered on a common canvas at --scale px/mm.

Outputs, per layer, into <out-dir>:
  <EXT>_diff.png    tri-colour: BLUE = both agree, RED = only set A,
                    GREEN = only set B (on dark background)
  <EXT>_a.png / _b.png   each set alone (copper on dark)
and prints an IoU (intersection over union) table. Drill files (.XLN) are
compared numerically: tool diameters, hit counts, worst nearest-hit distance.

Usage:
    python3 gerber_compare.py <setA-dir-or-zip> <setB-dir-or-zip> <out-dir>
                              [--scale 30] [--label-a Eagle --label-b make_gerber]
"""
import argparse, glob, math, os, re, sys, tempfile, zipfile

try:
    from PIL import Image, ImageDraw, ImageChops
except ImportError:
    sys.exit("gerber_compare.py needs Pillow (pip install --user Pillow)")

LAYERS = ['GKO', 'GTL', 'GBL', 'GTO', 'GBO', 'GTS', 'GBS']
ALIASES = {                      # alternate filename suffixes (e.g. OSHPark CAM job)
    'GKO': ['GKO', 'boardoutline.ger'],
    'GTL': ['GTL', 'toplayer.ger'],
    'GBL': ['GBL', 'bottomlayer.ger'],
    'GTO': ['GTO', 'topsilkscreen.ger'],
    'GBO': ['GBO', 'bottomsilkscreen.ger'],
    'GTS': ['GTS', 'topsoldermask.ger'],
    'GBS': ['GBS', 'bottomsoldermask.ger'],
    'XLN': ['XLN', 'drills.xln', 'xln'],
}


def setdir(path):
    path = os.path.expanduser(path)
    if path.lower().endswith('.zip'):
        d = tempfile.mkdtemp(prefix="gcmp_")
        with zipfile.ZipFile(path) as z:
            z.extractall(d)
        return d
    return path


def find_layer(d, ext, all_hits=False):
    hits = []
    for suf in ALIASES.get(ext, [ext]):
        hits += [p for p in glob.glob(os.path.join(d, "**", "*"), recursive=True)
                 if p.lower().endswith(suf.lower()) and os.path.isfile(p)]
    hits = sorted(set(hits))
    if all_hits:
        return hits
    return hits[0] if hits else None


def parse_gerber(path):
    """-> list of ops: ('line',pol,x1,y1,x2,y2,w) ('flash',pol,x,y,d) ('region',pol,pts)"""
    txt = open(path, errors='replace').read()
    ops = []
    apertures = {}
    cur_d = None
    pol = 1
    mode = 1                      # 1=linear, 2=cw, 3=ccw
    x = y = 0.0
    region = None                 # list of pts while in G36
    div = 10000.0
    unit = 1.0                    # mm; 25.4 if MOIN (inches)

    for raw in txt.replace('\r', '').split('*'):
        # a token may carry %-delimiters and newlines interleaved on either end
        # (e.g. '%\n%FSLAX36Y36'): strip the whole set at once or FS/MO/LP/ADD
        # never match
        tok = raw.strip('%\n\t ')
        if not tok or tok.startswith('G04') or tok.startswith('AM') or tok in ('G70', 'G71', 'G75', 'G90', 'G91', 'M02', 'M00'):
            continue
        if tok.startswith('FSLA'):
            m = re.search(r'X(\d)(\d)', tok)
            div = 10.0 ** int(m.group(2))
            continue
        if tok.startswith('MO'):
            unit = 25.4 if 'IN' in tok else 1.0
            continue
        if tok.startswith('TF') or tok.startswith('TA') or tok.startswith('TO') or tok.startswith('TD'):
            continue
        if tok.startswith('IN') or tok.startswith('IP') or tok.startswith('LN'):
            continue
        if tok.startswith('LP'):
            pol = 1 if 'D' in tok[2:3] else 0
            continue
        if tok.startswith('ADD'):
            m = re.match(r'ADD(\d+)([A-Z0-9]+)(?:,([\d.X]+))?', tok)
            if m:
                num, typ, params = int(m.group(1)), m.group(2), (m.group(3) or '')
                p = [float(v) for v in params.split('X') if v] if params else []
                apertures[num] = (typ, p, )

            continue
        if tok == 'G36':
            region = []
            continue
        if tok == 'G37':
            if region and len(region) >= 3:
                ops.append(('region', pol, region))
            region = None
            continue
        m = re.match(r'(?:G0?([123]))?'
                     r'(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?'
                     r'(?:D0?([123]))?$', tok)
        if not m:
            dm = re.match(r'(?:G54)?D(\d+)$', tok)
            if dm:
                cur_d = int(dm.group(1))
            continue
        g, mx, my, mi, mj, d = m.groups()
        if g:
            mode = int(g)
        nx = (int(mx) / div * unit) if mx else x
        ny = (int(my) / div * unit) if my else y
        if d == '3':
            ap = apertures.get(cur_d, ('C', [1.0]))
            dia = (ap[1][0] if ap[1] else 1.0) * unit
            ops.append(('flash', pol, nx, ny, dia))
        elif d == '1':
            if mode in (2, 3) and (mi or mj):
                cx = x + (int(mi) / div * unit if mi else 0.0)
                cy = y + (int(mj) / div * unit if mj else 0.0)
                pts = arc_pts(x, y, nx, ny, cx, cy, cw=(mode == 2))
            else:
                pts = [(x, y), (nx, ny)]
            if region is not None:
                if not region:
                    region.append(pts[0])
                region += pts[1:]
            else:
                ap = apertures.get(cur_d, ('C', [0.1]))
                w = (ap[1][0] if ap[1] else 0.1) * unit
                for k in range(len(pts) - 1):
                    ops.append(('line', pol, pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1], w))
        elif d == '2':
            if region is not None and region:
                if len(region) >= 3:
                    ops.append(('region', pol, region))
                region = []
        x, y = nx, ny
    return ops


def arc_pts(x1, y1, x2, y2, cx, cy, cw, seg_deg=4.0):
    r = math.hypot(x1 - cx, y1 - cy)
    a0 = math.atan2(y1 - cy, x1 - cx)
    a1 = math.atan2(y2 - cy, x2 - cx)
    if cw:
        while a1 >= a0 - 1e-12:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0 + 1e-12:
            a1 += 2 * math.pi
    n = max(2, int(abs(a1 - a0) / math.radians(seg_deg)))
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / n),
             cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]


def ops_bbox(ops, dark_only=True):
    xs, ys = [], []
    for op in ops:
        if dark_only and op[1] == 0:
            continue
        if op[0] == 'line':
            _, _, x1, y1, x2, y2, w = op
            xs += [x1 - w/2, x1 + w/2, x2 - w/2, x2 + w/2]
            ys += [y1 - w/2, y1 + w/2, y2 - w/2, y2 + w/2]
        elif op[0] == 'flash':
            _, _, x, y, dia = op
            xs += [x - dia/2, x + dia/2]; ys += [y - dia/2, y + dia/2]
        else:
            xs += [p[0] for p in op[2]]; ys += [p[1] for p in op[2]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def render(ops, w_px, h_px, tx, ty, scale):
    """composite ops in order onto an 'L' mask; (tx,ty) mm translation, y-up -> image y-down"""
    img = Image.new('L', (w_px, h_px), 0)
    d = ImageDraw.Draw(img)

    def P(x, y):
        return ((x + tx) * scale, h_px - (y + ty) * scale)

    for op in ops:
        v = 255 if op[1] else 0
        if op[0] == 'line':
            _, _, x1, y1, x2, y2, w = op
            r = w / 2 * scale
            p1, p2 = P(x1, y1), P(x2, y2)
            d.line([p1, p2], fill=v, width=max(1, int(round(w * scale))))
            for px, py in (p1, p2):
                d.ellipse([px - r, py - r, px + r, py + r], fill=v)
        elif op[0] == 'flash':
            _, _, x, y, dia = op
            r = dia / 2 * scale
            px, py = P(x, y)
            d.ellipse([px - r, py - r, px + r, py + r], fill=v)
        else:
            d.polygon([P(px, py) for px, py in op[2]], fill=v)
    return img


def compare_drills(fa, fb, off):
    def parse(paths):
        tools, hits = {}, []
        for p in paths:
            cur, unit, intdiv = None, 1.0, 1000.0
            for ln in open(p, errors='replace'):
                ln = ln.strip()
                if ln.startswith('INCH'):
                    unit, intdiv = 25.4, 10000.0
                    continue
                if ln.startswith('METRIC'):
                    unit, intdiv = 1.0, 1000.0
                    continue
                m = re.match(r'T(\d+)C([\d.]+)', ln)
                if m:
                    tools[int(m.group(1))] = round(float(m.group(2)) * unit, 3); continue
                m = re.match(r'T(\d+)$', ln)
                if m:
                    cur = int(m.group(1)); continue
                m = re.match(r'X(-?[\d.]+)Y(-?[\d.]+)', ln)
                if m and cur in tools:
                    gx, gy = m.group(1), m.group(2)
                    hits.append(((float(gx) if '.' in gx else int(gx) / intdiv) * unit,
                                 (float(gy) if '.' in gy else int(gy) / intdiv) * unit,
                                 tools[cur]))
        return tools, hits
    ta, ha = parse(fa)
    tb, hb = parse(fb)
    worst = 0.0
    for x, y, dia in ha:
        dmin = min((math.hypot(x - (bx + off[0]), y - (by + off[1])) for bx, by, _ in hb), default=9e9)
        worst = max(worst, dmin)
    return (sorted(set(ta.values())), len(ha), sorted(set(tb.values())), len(hb), worst)


def main():
    ap = argparse.ArgumentParser(description="Pixel-compare two Gerber sets, layer by layer")
    ap.add_argument('seta'); ap.add_argument('setb'); ap.add_argument('out')
    ap.add_argument('--scale', type=float, default=30.0, help="px per mm (default 30)")
    ap.add_argument('--label-a', default='A'); ap.add_argument('--label-b', default='B')
    a = ap.parse_args()
    da, db = setdir(a.seta), setdir(a.setb)
    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)

    # alignment from the outline layers
    oa = parse_gerber(find_layer(da, 'GKO'))
    ob = parse_gerber(find_layer(db, 'GKO'))
    ba, bb = ops_bbox(oa), ops_bbox(ob)
    ca = ((ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2)
    cb = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
    off_b = (ca[0] - cb[0], ca[1] - cb[1])          # add to set B coords -> set A frame
    M = 2.0
    w_mm, h_mm = ba[2] - ba[0] + 2 * M, ba[3] - ba[1] + 2 * M
    w_px, h_px = int(w_mm * a.scale), int(h_mm * a.scale)
    tx, ty = M - ba[0], M - ba[1]
    print(f"frame: {w_mm:.1f} x {h_mm:.1f} mm at {a.scale:g} px/mm -> {w_px} x {h_px} px; "
          f"set-B offset ({off_b[0]:+.3f}, {off_b[1]:+.3f}) mm")

    BG, BOTH, AONLY, BONLY = (12, 14, 18), (45, 70, 120), (210, 80, 60), (80, 200, 110)
    print(f"\n{'layer':6s} {'IoU':>6s} {'both':>9s} {'A-only':>8s} {'B-only':>8s}   "
          f"(A={a.label_a}, B={a.label_b})")
    for ext in LAYERS:
        fa, fb = find_layer(da, ext), find_layer(db, ext)
        if not fa or not fb:
            print(f"{ext:6s} -- missing in {'A' if not fa else 'B'}, skipped")
            continue
        ia = render(parse_gerber(fa), w_px, h_px, tx, ty, a.scale)
        opsb2 = []
        for op in parse_gerber(fb):
            if op[0] == 'line':
                opsb2.append(('line', op[1], op[2] + off_b[0], op[3] + off_b[1],
                              op[4] + off_b[0], op[5] + off_b[1], op[6]))
            elif op[0] == 'flash':
                opsb2.append(('flash', op[1], op[2] + off_b[0], op[3] + off_b[1], op[4]))
            else:
                opsb2.append(('region', op[1], [(px + off_b[0], py + off_b[1]) for px, py in op[2]]))
        ib = render(opsb2, w_px, h_px, tx, ty, a.scale)

        both = ImageChops.multiply(ia.point(lambda v: 255 if v else 0),
                                   ib.point(lambda v: 255 if v else 0))
        na, nb = ia.point(lambda v: 255 if v else 0), ib.point(lambda v: 255 if v else 0)
        aonly = ImageChops.subtract(na, nb)
        bonly = ImageChops.subtract(nb, na)
        h_both = both.histogram()[255]
        h_a, h_b = aonly.histogram()[255], bonly.histogram()[255]
        union = h_both + h_a + h_b
        iou = h_both / union if union else 1.0
        diff = Image.new('RGB', (w_px, h_px), BG)
        for mask, col in ((both, BOTH), (aonly, AONLY), (bonly, BONLY)):
            diff.paste(col, mask=mask)
        diff.save(os.path.join(out, f"{ext}_diff.png"))
        for img, suf in ((na, 'a'), (nb, 'b')):
            solo = Image.new('RGB', (w_px, h_px), BG)
            solo.paste((184, 115, 51), mask=img)
            solo.save(os.path.join(out, f"{ext}_{suf}.png"))
        print(f"{ext:6s} {iou:6.3f} {h_both:9d} {h_a:8d} {h_b:8d}")

    fa, fb = find_layer(da, 'XLN', all_hits=True), find_layer(db, 'XLN', all_hits=True)
    if fa and fb:
        ta, na, tb, nb, worst = compare_drills(fa, fb, off_b)
        print(f"\nXLN    A: {na} hits, tools {ta}")
        print(f"       B: {nb} hits, tools {tb}")
        print(f"       worst nearest-hit distance: {worst:.3f} mm")
    print(f"\nPNGs in {out}  (diff: BLUE=both, RED={a.label_a}-only, GREEN={a.label_b}-only)")


if __name__ == '__main__':
    main()
