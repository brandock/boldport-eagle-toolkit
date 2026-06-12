#!/usr/bin/env python3
"""
flatten -- adaptive polyline flattening of arbitrary filled SVG paths, for Eagle
FILLED POLYGONS (the seigaiha waves and any other fill).

The generalized fill story (vs arcfit.py, which is for WIRES/strokes):
Eagle's CAM ALWAYS flattens a polygon's edges to straight Gerber draws on export
-- arc vertices buy nothing and Eagle's own re-tessellation can add kinks
("pointy-head" artifacts). So the right move for fills is to hand Eagle a
STRAIGHT-vertex polygon we built ourselves, adaptively: subdivide each Bezier
only where it's actually curved, to a Hausdorff tolerance. Then what we author
is exactly what Eagle ships -- deterministic, kink-free, smoothness bounded by
`tol`. Set `tol` to the fab's silkscreen imaging resolution (no point going
finer than the fab can render; e.g. OSHPark silk is comparatively coarse).

General: works on any fill, not tuned to the wave pattern.
"""
import numpy as np
from svgpathtools import parse_path, CubicBezier, Line, QuadraticBezier, Arc

def _flat_enough(p0, p1, p2, p3, tol):
    """Conservative cubic flatness: max distance of control pts to chord < tol."""
    chord = p3 - p0
    if abs(chord) < 1e-12:
        return abs(p1 - p0) < tol and abs(p2 - p0) < tol
    L = abs(chord)
    d1 = abs(((p1 - p0) * np.conj(chord)).imag) / L
    d2 = abs(((p2 - p0) * np.conj(chord)).imag) / L
    return max(d1, d2) < tol

def _flatten_cubic(p0, p1, p2, p3, tol, out, depth=0):
    if depth >= 20 or _flat_enough(p0, p1, p2, p3, tol):
        out.append(p3); return
    p01 = (p0 + p1) / 2; p12 = (p1 + p2) / 2; p23 = (p2 + p3) / 2
    p012 = (p01 + p12) / 2; p123 = (p12 + p23) / 2; m = (p012 + p123) / 2
    _flatten_cubic(p0, p01, p012, m, tol, out, depth + 1)
    _flatten_cubic(m, p123, p23, p3, tol, out, depth + 1)

def adaptive_flatten(d, tol=0.03):
    """Arbitrary filled SVG path -> straight-vertex subpaths [(x,y),...]."""
    path = parse_path(d)
    subpaths = []
    for sub in path.continuous_subpaths():
        pts = []
        for j, seg in enumerate(sub):
            if j == 0:
                pts.append(seg.start)
            if isinstance(seg, Line):
                pts.append(seg.end)
            elif isinstance(seg, QuadraticBezier):
                c = seg.control
                _flatten_cubic(seg.start, seg.start + 2/3*(c - seg.start),
                               seg.end + 2/3*(c - seg.end), seg.end, tol, pts)
            elif isinstance(seg, CubicBezier):
                _flatten_cubic(seg.start, seg.control1, seg.control2, seg.end, tol, pts)
            elif isinstance(seg, Arc):
                n = max(2, int(abs(seg.delta) / 6))
                for k in range(1, n + 1):
                    pts.append(seg.point(k / n))
        subpaths.append([(p.real, p.imag) for p in pts])
    return subpaths

def max_deviation(d, subpaths, dense=4000):
    """Max distance (mm) from densely-sampled original path to the polyline."""
    orig = parse_path(d)
    segs = []
    for pts in subpaths:
        for i in range(len(pts) - 1):
            segs.append((complex(*pts[i]), complex(*pts[i + 1])))
    def dist(P):
        best = 1e9
        for s, e in segs:
            v = e - s
            if abs(v) < 1e-12:
                dd = abs(P - s)
            else:
                u = max(0.0, min(1.0, ((P - s) * np.conj(v)).real / abs(v) ** 2))
                dd = abs(P - (s + u * v))
            if dd < best:
                best = dd
        return best
    return max(dist(orig.point(i / dense)) for i in range(dense + 1))


if __name__ == '__main__':
    import json, os, time
    ct = os.path.expanduser("~/Dropbox/EAGLE/projects/Boldport/thecuttle")
    d = json.load(open(ct + "/shapes/silkscreen.json"))['layout']['silkscreen']['shapes'][3]['value']
    for tol in (0.08, 0.05, 0.03, 0.02):
        t = time.time()
        subs = adaptive_flatten(d, tol=tol)
        nv = sum(len(s) for s in subs)
        dev = max_deviation(d, subs)
        print(f"tol={tol:.2f}mm -> {nv:6d} straight verts | max dev {dev*1000:5.1f} um "
              f"| {time.time()-t:4.1f}s   (uniform flatten=28 was ~60060; arc-fit was ~3045)")
