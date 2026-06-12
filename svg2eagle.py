#!/usr/bin/env python3
"""
PCBmodEagle -- transpile Boldport/PCBmodE SVG-path artwork into Eagle .brd geometry.

PCBmodE stores board art as SVG path strings inside JSON ("value": "m .. c .. ").
Eagle .brd (v6+) is XML whose drawing vocabulary is <wire> (straight or circular
arc) and <polygon> (straight/arc vertices). Eagle has NO Bezier primitive, so the
core job here is to FLATTEN every cubic/quadratic Bezier into many short straight
segments. With enough segments the result is visually faithful.

Round 1 goal: prove Eagle reads & displays injected free-form curvy geometry.
We emit into the board's <plain> section (free graphics), reusing a real board's
prologue (layer table etc.) so the file is valid in the user's exact Eagle version.

Coordinate notes:
  * PCBmodE/SVG units here are millimetres (Eagle .brd XML is also mm).
  * SVG y grows DOWN, Eagle y grows UP  -> we flip y (eagle_y = -svg_y).
  * Each shape carries a "location":[x,y] offset PCBmodE applies as translation.
These (flip/scale/offset) are exactly what round 1 calibrates against Eagle's view.
"""
import argparse, json, re, sys

# ---------- SVG path parsing -------------------------------------------------

_TOKEN = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?')

def tokenize(d):
    return _TOKEN.findall(d)

def parse_path(d):
    """Return list of subpaths; each subpath is a list of (x,y) points with
    Beziers already flattened by caller via FLATTEN. We flatten here directly."""
    toks = tokenize(d)
    i = 0
    cx = cy = 0.0          # current point
    sx = sy = 0.0          # subpath start (for Z)
    prev_cmd = None
    prev_c2 = None         # previous cubic 2nd control pt (for S smoothing)
    prev_qc = None         # previous quadratic control pt (for T smoothing)
    subpaths = []
    cur = []

    def num():
        nonlocal i
        v = float(toks[i]); i += 1; return v

    def emit(x, y):
        cur.append((x, y))

    while i < len(toks):
        t = toks[i]
        if re.match(r'[A-Za-z]', t):
            cmd = t; i += 1
        else:
            # implicit repeat of previous command (M->L, m->l)
            cmd = prev_cmd
            if cmd == 'M': cmd = 'L'
            elif cmd == 'm': cmd = 'l'
        rel = cmd.islower()
        C = cmd.upper()

        if C == 'M':
            if cur: subpaths.append(cur); cur = []
            x = num(); y = num()
            if rel: x += cx; y += cy
            cx, cy = x, y; sx, sy = x, y
            emit(cx, cy)
        elif C == 'L':
            x = num(); y = num()
            if rel: x += cx; y += cy
            cx, cy = x, y; emit(cx, cy)
        elif C == 'H':
            x = num()
            if rel: x += cx
            cx = x; emit(cx, cy)
        elif C == 'V':
            y = num()
            if rel: y += cy
            cy = y; emit(cx, cy)
        elif C == 'C':
            x1 = num(); y1 = num(); x2 = num(); y2 = num(); x = num(); y = num()
            if rel:
                x1 += cx; y1 += cy; x2 += cx; y2 += cy; x += cx; y += cy
            flatten_cubic(cx, cy, x1, y1, x2, y2, x, y, emit)
            prev_c2 = (x2, y2); cx, cy = x, y
        elif C == 'S':
            x2 = num(); y2 = num(); x = num(); y = num()
            if rel: x2 += cx; y2 += cy; x += cx; y += cy
            if prev_cmd and prev_cmd.upper() in ('C', 'S') and prev_c2:
                x1 = 2*cx - prev_c2[0]; y1 = 2*cy - prev_c2[1]
            else:
                x1, y1 = cx, cy
            flatten_cubic(cx, cy, x1, y1, x2, y2, x, y, emit)
            prev_c2 = (x2, y2); cx, cy = x, y
        elif C == 'Q':
            x1 = num(); y1 = num(); x = num(); y = num()
            if rel: x1 += cx; y1 += cy; x += cx; y += cy
            flatten_quad(cx, cy, x1, y1, x, y, emit)
            prev_qc = (x1, y1); cx, cy = x, y
        elif C == 'T':
            x = num(); y = num()
            if rel: x += cx; y += cy
            if prev_cmd and prev_cmd.upper() in ('Q', 'T') and prev_qc:
                x1 = 2*cx - prev_qc[0]; y1 = 2*cy - prev_qc[1]
            else:
                x1, y1 = cx, cy
            flatten_quad(cx, cy, x1, y1, x, y, emit)
            prev_qc = (x1, y1); cx, cy = x, y
        elif C == 'Z':
            if cur: emit(sx, sy); subpaths.append(cur); cur = []
            cx, cy = sx, sy
        else:
            # A (arc), T not expected in this art; skip a conservative arg count
            raise ValueError(f"unsupported path command: {cmd}")
        prev_cmd = cmd
    if cur: subpaths.append(cur)
    return subpaths

FLATTEN = 16  # segments per Bezier; overridden by CLI

def flatten_cubic(x0, y0, x1, y1, x2, y2, x3, y3, emit):
    n = FLATTEN
    for k in range(1, n + 1):
        t = k / n; mt = 1 - t
        x = mt**3*x0 + 3*mt**2*t*x1 + 3*mt*t**2*x2 + t**3*x3
        y = mt**3*y0 + 3*mt**2*t*y1 + 3*mt*t**2*y2 + t**3*y3
        emit(x, y)

def flatten_quad(x0, y0, x1, y1, x2, y2, emit):
    n = FLATTEN
    for k in range(1, n + 1):
        t = k / n; mt = 1 - t
        x = mt**2*x0 + 2*mt*t*x1 + t**2*x2
        y = mt**2*y0 + 2*mt*t*y1 + t**2*y2
        emit(x, y)

# ---------- Eagle emission ---------------------------------------------------

def xf(pt, ox, oy, scale, flip_y):
    x, y = pt
    ex = (x + ox) * scale
    ey = (y + oy) * scale
    if flip_y: ey = -ey
    return ex, ey

def emit_wires(subpaths, layer, width, ox, oy, scale, flip_y):
    out = []
    for sp in subpaths:
        pts = [xf(p, ox, oy, scale, flip_y) for p in sp]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            out.append(f'<wire x1="{x1:.4f}" y1="{y1:.4f}" '
                       f'x2="{x2:.4f}" y2="{y2:.4f}" '
                       f'width="{width}" layer="{layer}"/>')
    return out

def emit_polygons(subpaths, layer, width, ox, oy, scale, flip_y):
    out = []
    for sp in subpaths:
        pts = [xf(p, ox, oy, scale, flip_y) for p in sp]
        if len(pts) < 3: continue
        out.append(f'<polygon width="{width}" layer="{layer}">')
        for x, y in pts:
            out.append(f'<vertex x="{x:.4f}" y="{y:.4f}"/>')
        out.append('</polygon>')
    return out

def prologue_from_template(path):
    """Everything up to and INCLUDING the <plain> line of a real .brd."""
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            out.append(line)
            if '<plain>' in line:
                return ''.join(out)
    raise SystemExit("template has no <plain> section")

EPILOGUE = """</plain>
<libraries>
</libraries>
<attributes>
</attributes>
<variantdefs>
</variantdefs>
<classes>
<class number="0" name="default" width="0" drill="0"/>
</classes>
<elements>
</elements>
<signals>
</signals>
</board>
</drawing>
</eagle>
"""

# ---------- main -------------------------------------------------------------

def main():
    global FLATTEN
    ap = argparse.ArgumentParser(description="PCBmodE SVG-path -> Eagle .brd")
    ap.add_argument('--json', required=True, help='PCBmodE shapes json (e.g. shapes/silkscreen.json)')
    ap.add_argument('--section', default='silkscreen', help='top-level layout key')
    ap.add_argument('--shape', type=int, action='append', help='shape index (repeatable); default all paths')
    ap.add_argument('--template', required=True, help='existing .brd to borrow prologue/layers from')
    ap.add_argument('--out', required=True)
    ap.add_argument('--layer', default='21', help='Eagle layer number (21=tPlace silk)')
    ap.add_argument('--flatten', type=int, default=16, help='segments per Bezier')
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--no-flip-y', action='store_true', help='disable SVG->Eagle y flip')
    ap.add_argument('--force-mode', choices=['wire', 'polygon'], help='override stroke/fill detection')
    args = ap.parse_args()
    FLATTEN = args.flatten

    data = json.load(open(args.json))
    shapes = data['layout'][args.section]['shapes']
    idxs = args.shape if args.shape else [i for i, s in enumerate(shapes) if s.get('type') == 'path']

    body = []
    for idx in idxs:
        s = shapes[idx]
        if s.get('type') != 'path':
            print(f"  skip [{idx}] type={s.get('type')}", file=sys.stderr); continue
        ox, oy = s.get('location', [0, 0])
        subpaths = parse_path(s['value'])
        npts = sum(len(sp) for sp in subpaths)
        mode = args.force_mode or ('polygon' if s.get('style') == 'fill' else 'wire')
        width = s.get('stroke-width') or 0.1524
        if mode == 'wire':
            body += emit_wires(subpaths, args.layer, width, ox, oy, args.scale, not args.no_flip_y)
        else:
            body += emit_polygons(subpaths, args.layer, 0.1524, ox, oy, args.scale, not args.no_flip_y)
        print(f"  shape [{idx}] {mode:7} subpaths={len(subpaths):3} pts={npts:5} loc=({ox},{oy})", file=sys.stderr)

    out = prologue_from_template(args.template) + '\n'.join(body) + '\n' + EPILOGUE
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(out)
    print(f"wrote {args.out}  ({len(body)} elements)", file=sys.stderr)

if __name__ == '__main__':
    main()
