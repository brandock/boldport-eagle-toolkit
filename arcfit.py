#!/usr/bin/env python3
"""
arcfit -- convert SVG cubic-Bezier paths to a tolerance-bounded arc/line spline,
for emitting Eagle .brd arcs (signed `curve` on wires / polygon vertices).

Design follows Brandon's brief (NURBS->G02/G03 / Meek-Walton / bezier2arc):
  1. parse with svgpathtools (CubicBezier/Line, .point/.derivative)
  2. split each cubic at INFLECTIONS (an arc has constant-sign curvature)
  3. ported bezier2arc tolerance fitter: greedily fit the largest arc whose max
     deviation from the original cubic is <= tol; min-radius + line fallback
  4. merge adjacent CO-CIRCULAR arcs (same centre+radius+direction) -> recovers
     the ~180 deg scallop arcs from co-circular ~77 deg cubic pieces (Tier-2 min)
Accept/reject driven by Hausdorff (max) deviation in mm, computed analytically
point-to-arc (faster than radialrange+fminbound across thousands of cubics).

Returns subpaths as lists of (x, y, curve_deg) vertices in the ORIGINAL svg
coordinate frame (y-down). curve_deg = signed Eagle sweep of the edge that
LEAVES this vertex (positive = CCW); 0 = straight. The caller applies the same
flip/register/mirror as other shapes -- and must negate curve_deg whenever it
negates x or y (a reflection reverses arc orientation).
"""
import numpy as np
from svgpathtools import parse_path, Path, CubicBezier, Line, QuadraticBezier, Arc

# ---------------- geometry helpers -------------------------------------------

def circle_from_points(p1, p2, p3):
    """Centre/radius of circle through 3 complex points (NumPy-2 safe)."""
    d = ((p3 - p1) * np.conj(p3 - p2)).imag
    if abs(d) < 1e-12:
        return None, None
    s = ((p3 - p1) * np.conj(p3 - p2)).real / d
    center = (p1 + p2) / 2.0 + 1j * s * (p1 - p2) / 2.0
    return center, abs(p1 - center)

def _ang(z):
    return np.angle(z)

def signed_sweep(center, p1, p2, p3):
    """Signed sweep (radians, +CCW) of the arc p1->p3 passing through p2."""
    a0 = _ang(p1 - center); am = _ang(p2 - center); a1 = _ang(p3 - center)
    ccw = (a1 - a0) % (2 * np.pi)                 # CCW arc length a0->a1
    ccw_m = (am - a0) % (2 * np.pi)
    return ccw if ccw_m <= ccw else -((2 * np.pi) - ccw)

def pt_arc_dist(P, center, radius, a0, sweep):
    """Min distance from complex point P to the arc(center,radius,a0,sweep rad)."""
    ang = _ang(P - center)
    if sweep >= 0:
        inside = ((ang - a0) % (2 * np.pi)) <= sweep + 1e-12
    else:
        inside = ((a0 - ang) % (2 * np.pi)) <= (-sweep) + 1e-12
    if inside:
        return abs(abs(P - center) - radius)
    e0 = center + radius * np.exp(1j * a0)
    e1 = center + radius * np.exp(1j * (a0 + sweep))
    return min(abs(P - e0), abs(P - e1))

# ---------------- inflection splitting ---------------------------------------

def _curv_cross(seg, t):
    d1 = seg.derivative(t); d2 = seg.derivative(t, n=2)
    return (np.conj(d1) * d2).imag                # ~ curvature sign

def inflection_ts(seg, n=40):
    if not isinstance(seg, CubicBezier):
        return []
    ts, prev = [], _curv_cross(seg, 1e-4)
    for i in range(1, n + 1):
        t = min(i / n, 1 - 1e-4)
        cur = _curv_cross(seg, t)
        if prev == 0 or (prev < 0) != (cur < 0):
            lo, hi = (i - 1) / n, t
            for _ in range(40):
                mid = (lo + hi) / 2
                if (_curv_cross(seg, lo) < 0) != (_curv_cross(seg, mid) < 0):
                    hi = mid
                else:
                    lo = mid
            ts.append((lo + hi) / 2)
        prev = cur
    return [t for t in ts if 1e-3 < t < 1 - 1e-3]

def crop(seg, t0, t1):
    return Path(seg).cropped(t0, t1)[0]

# ---------------- the tolerance fitter (ported bezier2arc) -------------------

def fit_piece(seg, tol, min_radius, max_radius, samples=24):
    """Fit one inflection-free cubic piece -> list of primitives.
    Primitive = ('arc', center, radius, a0, sweep, start, end) or ('line', s, e)."""
    pts = [seg.point(i / samples) for i in range(samples + 1)]
    # near-straight whole piece -> single line
    chord = pts[-1] - pts[0]
    if abs(chord) > 0:
        devs = [abs(((p - pts[0]) * np.conj(chord)).imag) / abs(chord) for p in pts]
        if max(devs) < tol:
            return [('line', pts[0], pts[-1])]
    prims, t0 = [], 0.0
    while t0 < 1.0 - 1e-9:
        t_arc = (1.0 - t0) * 2.0
        placed = None
        while t_arc > 1e-6:
            t_arc = min(t_arc / 2.0, 1.0 - t0)
            p1 = seg.point(t0); p3 = seg.point(t0 + t_arc); p2 = seg.point(t0 + t_arc / 2)
            center, radius = circle_from_points(p1, p2, p3)
            if center is None or radius < min_radius or radius > max_radius:
                continue
            a0 = _ang(p1 - center); sweep = signed_sweep(center, p1, p2, p3)
            ns = max(4, int(samples * t_arc))
            dev = max(pt_arc_dist(seg.point(t0 + t_arc * k / ns), center, radius, a0, sweep)
                      for k in range(ns + 1))
            if dev <= tol:
                placed = ('arc', center, radius, a0, sweep, p1, p3)
                break
        if placed is None:                         # fall back to a straight chord
            p1 = seg.point(t0); t_arc = min((1.0 - t0), 0.25); p3 = seg.point(t0 + t_arc)
            placed = ('line', p1, p3)
        prims.append(placed)
        t0 += t_arc
    return prims

# ---------------- co-circular merge (Tier-2 minimum) -------------------------

MAX_SWEEP = np.radians(175)   # cap merged edges below 180: R-method Gerber arcs
                              # are ambiguous at 180, and Eagle's edge handling
                              # gets dicey there. A 180 scallop -> two ~90 edges.

def merge_cocircular(prims, eps_c=0.02, eps_r=0.02):
    out = []
    for p in prims:
        if out and p[0] == 'arc' and out[-1][0] == 'arc':
            _, c0, r0, a00, s0, st0, en0 = out[-1]
            _, c1, r1, a01, s1, st1, en1 = p
            if (abs(c0 - c1) < eps_c and abs(r0 - r1) < eps_r and (s0 > 0) == (s1 > 0)
                    and abs(s0 + s1) < MAX_SWEEP):
                out[-1] = ('arc', (c0 + c1) / 2, (r0 + r1) / 2, a00, s0 + s1, st0, en1)
                continue
        out.append(p)
    return out

# ---------------- top level: path d-string -> arc subpaths -------------------

def fit_path(d, tol=0.02, min_radius=0.05, max_radius=50.0):
    """Return (subpaths, stats). subpath = list of (x,y,curve_deg)."""
    path = parse_path(d)
    subpaths_out = []
    n_arcs = n_lines = 0
    for sub in path.continuous_subpaths():
        closed = sub.isclosed()          # open pen-strokes must NOT be wrap-closed
        prims = []
        for seg in sub:
            if isinstance(seg, Line):
                prims.append(('line', seg.start, seg.end)); continue
            if isinstance(seg, QuadraticBezier):
                seg = CubicBezier(seg.start, seg.start + 2/3*(seg.control - seg.start),
                                  seg.end + 2/3*(seg.control - seg.end), seg.end)
            if isinstance(seg, CubicBezier):
                ts = [0.0] + inflection_ts(seg) + [1.0]
                for t0, t1 in zip(ts, ts[1:]):
                    prims += fit_piece(crop(seg, t0, t1), tol, min_radius, max_radius)
            elif isinstance(seg, Arc):
                prims.append(('arc', seg.center, abs(seg.radius), np.radians(seg.theta),
                              np.radians(seg.delta), seg.start, seg.end))
        prims = merge_cocircular(prims)
        verts = []
        for p in prims:
            if p[0] == 'arc':
                center, radius, sweep, start = p[1], p[2], p[4], p[5]
                # shallow arc whose own sagitta is under tolerance -> emit as a
                # line. Eagle silently straightens tiny-sagitta polygon arc edges
                # (observed below curve~25), so we decide rather than let it.
                sagitta = radius * (1 - np.cos(sweep / 2))
                if sagitta < tol:
                    verts.append((start.real, start.imag, 0.0)); n_lines += 1
                else:
                    verts.append((start.real, start.imag, np.degrees(sweep))); n_arcs += 1
            else:
                verts.append((p[1].real, p[1].imag, 0.0)); n_lines += 1
        # open paths: keep the final endpoint (closed ones reuse the first vertex)
        if not closed and prims:
            last = prims[-1]
            endp = last[6] if last[0] == 'arc' else last[2]
            verts.append((endp.real, endp.imag, 0.0))
        subpaths_out.append((verts, closed))
    return subpaths_out, {'arcs': n_arcs, 'lines': n_lines}

# ---------------- validation: deviation of arc chain vs dense original -------

def max_deviation(d, subpaths, dense=4000):
    """Max distance (mm) from densely-sampled original path to emitted arc chain."""
    orig = parse_path(d)
    # build arc/line primitives back from vertices for distance test
    prims = []
    for verts, closed in subpaths:
        n = len(verts)
        for i in (range(n) if closed else range(n - 1)):
            x, y, cv = verts[i]
            x2, y2, _ = verts[(i + 1) % n]
            s = complex(x, y); e = complex(x2, y2)
            if abs(cv) < 1e-9:
                prims.append(('line', s, e))
            else:
                sweep = np.radians(cv)
                chord = e - s
                # reconstruct centre from chord + sweep
                half = sweep / 2
                if abs(np.sin(half)) < 1e-9:
                    prims.append(('line', s, e)); continue
                r = abs(chord) / (2 * np.sin(abs(half)))
                mid = (s + e) / 2
                hdir = 1j * chord / abs(chord) * (1 if sweep > 0 else -1)
                d_off = r * np.cos(half)
                center = mid + hdir * d_off
                a0 = _ang(s - center)
                prims.append(('arc', center, r, a0, sweep))
    def dist_to_prims(P):
        best = 1e9
        for p in prims:
            if p[0] == 'line':
                s, e = p[1], p[2]; v = e - s
                if abs(v) < 1e-12:
                    dd = abs(P - s)
                else:
                    u = max(0.0, min(1.0, ((P - s) * np.conj(v)).real / abs(v) ** 2))
                    dd = abs(P - (s + u * v))
            else:
                dd = pt_arc_dist(P, p[1], p[2], p[3], p[4])
            best = min(best, dd)
        return best
    return max(dist_to_prims(orig.point(i / dense)) for i in range(dense + 1))


if __name__ == '__main__':
    import json, os, time
    ct = os.path.expanduser("~/Dropbox/EAGLE/projects/Boldport/thecuttle")
    d = json.load(open(ct + "/shapes/silkscreen.json"))['layout']['silkscreen']['shapes'][3]['value']
    for tol in (0.05, 0.02, 0.01):
        t = time.time()
        subs, st = fit_path(d, tol=tol)
        dev = max_deviation(d, subs)
        nv = sum(len(v) for v, _ in subs)
        print(f"tol={tol:.2f}mm -> {st['arcs']:5d} arcs + {st['lines']:4d} lines "
              f"= {nv:5d} verts | max dev {dev*1000:6.1f} um | {time.time()-t:4.1f}s "
              f"(was ~60060 line-verts)")
