#!/usr/bin/env python3
"""
airwire_stitch.py -- resolve Eagle airwires the way Eagle actually fuses copper (Step 5B).

THE RULES (each cost a debugging round to learn):
  * Eagle fuses two same-net wires only NODE-TO-NODE (a shared endpoint) or by a true body
    CROSSING. A thin wire that merely ENDS on another's body (a dangling T), or is stitched
    "past" an arc, does NOT fuse. Splitting a wire adds no connection on its own.
  * THT pads connect through the node at their center; the drill hole is irrelevant to
    connectivity (a stitch to a hole-center fuses fine).
  * Eagle has a MINIMUM FUSE LENGTH (~1-2 mil): a sub-mil wire collapses to nothing and
    makes no connection -- sub-fuse-length gaps must be closed by MOVING an endpoint onto
    the node, never by a short stitch.

  So every airwire (A,B are both Eagle nodes) is resolved by, in order:
    1. SNAP   -- gap below the fuse length: don't stitch (it would collapse); MOVE the loose
                 trace endpoint onto the node it should meet. Pure geometry, a sub-fuse-length
                 nudge correcting the Bezier->arc rounding.
    2. STITCH -- gap big enough and the A->B line stays hidden under copper: redraw the
                 airwire as a 6-mil node-to-node wire.
    3. PAD-RESCUE -- an airwire end is a pad: move too-close same-net trace endpoints onto
                 the pad's center node, stitch the nearest farther one. Both pad ends rescued.
    4. LADDER -- a loose trace endpoint sits on another same-net wire's INTERIOR (an on-arc
                 T): split that wire at the nearest on-wire point (sub-arcs stay on the same
                 circle), then SNAP the endpoint onto the split node -> a true 3-way node.
    5. LOCAL  -- loose trace-end -> nearest trace-end node within reach, if hidden under
                 copper (Eagle often anchors an airwire at a far pad while the real gap is a
                 nearby node).
    6. LOG    -- a hidden node->node path that must weave under fat copper, or a genuine
                 routing/pour gap; left to the human, with the target node reported.

Geometry edits (snap/ladder) move trace endpoints by at most a few mil and split arcs on
their own circle -- within the Bezier->arc conversion error bars.

Workflow (iterative; each pass changes connectivity, so later passes reach more):
  1. in Eagle: RATSNEST, then RUN export_airwires.ulp  -> writes <board>.airwires.txt
  2. python3 airwire_stitch.py "<board.brd>" [preview]
  3. in Eagle: File > Reload, RATSNEST, re-RUN the ULP; repeat 2-3 until only genuine
     pour/routing gaps remain.
ALWAYS re-export after a reload -- a stale .airwires.txt silently mis-resolves (the script
warns if the airwire file is older than the board).

Branch the board before the first pass; `preview` reports without writing.
"""
import argparse, math, os, re, sys

MIL = 0.0254; W_STITCH = 6*MIL; PAD_TOL = 0.30; NODE_TOL = 1.5*MIL
SNAP_BELOW = 3.0*MIL          # gaps under this are snapped (would collapse if stitched)
SNAP_REACH = 6.0*MIL          # a snap may move a trace-end at most this far onto a node
LOCAL_REACH = 25.0*MIL        # a local node-to-node stitch may reach this far
MOVE_BELOW = 5.0*MIL          # a pad endpoint this close is MOVED onto the center
STEP = 0.5*MIL

ap = argparse.ArgumentParser(description="Resolve Eagle airwires (snap/stitch/rescue/ladder)")
ap.add_argument('board', help="the .brd to edit (branch it first; Eagle closed)")
ap.add_argument('airwires', nargs='?', help="airwires file (default: <board>.airwires.txt "
                                            "from export_airwires.ulp)")
ap.add_argument('--preview', action='store_true', help="report only, write nothing")
a, extra = ap.parse_known_args()
PREVIEW = a.preview or 'preview' in extra

BRD = os.path.expanduser(a.board)
if not os.path.isfile(BRD):
    sys.exit(f"board not found: {BRD}")
AWF = a.airwires or os.path.splitext(BRD)[0] + ".airwires.txt"
if not os.path.exists(AWF):
    sys.exit(f"no airwire file: {AWF}\n(in Eagle: RATSNEST, then RUN export_airwires.ulp)")
if os.path.getmtime(AWF) < os.path.getmtime(BRD):
    print("WARNING: the airwires file is OLDER than the board -- stale exports silently "
          "mis-resolve. In Eagle: RATSNEST + re-RUN export_airwires.ulp, then re-run this.")

airwires = []
for ln in open(AWF):
    t = ln.split()
    if len(t) >= 5:
        try:
            airwires.append((t[-1], (float(t[0]), float(t[1])), (float(t[2]), float(t[3]))))
        except ValueError:
            pass

brd = open(BRD).read()

# ---- pads ----
lib = re.search(r'<libraries>(.*?)</libraries>', brd, re.S).group(1)
pk = {}
for lm in re.finditer(r'<library name="([^"]+)"[^>]*>(.*?)</library>', lib, re.S):
    for pm in re.finditer(r'<package name="([^"]+)"[^>]*>(.*?)</package>', lm.group(2), re.S):
        for pt in re.findall(r'<pad [^>]*/>', pm.group(2)):
            nm = re.search(r'name="([^"]+)"', pt).group(1)
            x = float(re.search(r'\bx="(-?[\d.]+)"', pt).group(1))
            y = float(re.search(r'\by="(-?[\d.]+)"', pt).group(1))
            dm = re.search(r'diameter="([\d.]+)"', pt)
            pk[(lm.group(1), pm.group(1), nm)] = (x, y, float(dm.group(1)) if dm else 1.5)
elpads = []
for em in re.finditer(r'<element name="([^"]+)" library="([^"]+)"[^>]*? package="([^"]+)"([^>]*?)/?>', brd):
    nm, lb, pkg, at = em.groups()
    x = float(re.search(r'\bx="(-?[\d.]+)"', at).group(1))
    y = float(re.search(r'\by="(-?[\d.]+)"', at).group(1))
    rm = re.search(r'\brot="(M?)R(\d+)"', at)
    mir, deg = (rm.group(1), int(rm.group(2))) if rm else ('', 0)
    ang = math.radians(deg); cs, sn = math.cos(ang), math.sin(ang); mx = -1 if mir == 'M' else 1
    for (l, pkn, p), (ox, oy, dia) in pk.items():
        if l == lb and pkn == pkg:
            elpads.append((nm, p, x + mx*ox*cs - oy*sn, y + mx*ox*sn + oy*cs, dia/2))

# ---- wires per net: (A, B, curve, hw, layer, is_stitch, tag) ----
WIRE = {}; PAD = {}
for sm in re.finditer(r'<signal name="([^"]+)"[^>]*>(.*?)</signal>', brd, re.S):
    net = sm.group(1); L = []
    for w in re.findall(r'<wire [^>]*/>', sm.group(2)):
        wd = float(re.search(r'width="([\d.]+)"', w).group(1))
        lay = re.search(r'layer="(\d+)"', w).group(1)
        if lay not in ("1", "16"):
            continue
        x1, y1, x2, y2 = (float(z) for z in re.search(
            r'x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"', w).groups())
        cv = re.search(r'curve="(-?[\d.]+)"', w)
        L.append(((x1, y1), (x2, y2), float(cv.group(1)) if cv else 0.0, wd/2, lay,
                  abs(wd - W_STITCH) < 1e-3, w))
    WIRE[net] = L
    PAD[net] = [(e, p, cx, cy, R) for (e, p, cx, cy, R) in elpads
                if ('<contactref element="%s" pad="%s"' % (e, p)) in sm.group(2)]

def arc_geom(A, B, deg):
    th = math.radians(deg); c, s = math.cos(th), math.sin(th)
    m00, m01, m10, m11 = c-1, -s, s, c-1; det = m00*m11 - m01*m10
    bx, by = B[0]-A[0], B[1]-A[1]
    ax = (m11*bx - m01*by)/det; ay = (-m10*bx + m00*by)/det
    return (A[0]-ax, A[1]-ay), math.hypot(ax, ay)

def closest_on(A, B, cv, P):
    """closest point on a wire (line or arc) to P; returns (point, interior?)"""
    if abs(cv) < 1e-6:
        dx, dy = B[0]-A[0], B[1]-A[1]; L2 = dx*dx + dy*dy
        t = 0.0 if L2 < 1e-12 else ((P[0]-A[0])*dx + (P[1]-A[1])*dy)/L2
        tc = max(0.0, min(1.0, t)); return (A[0]+tc*dx, A[1]+tc*dy), (0.02 < t < 0.98)
    C, R = arc_geom(A, B, cv); vx, vy = P[0]-C[0], P[1]-C[1]; Lp = math.hypot(vx, vy)
    if Lp < 1e-12:
        return A, False
    Pp = (C[0]+R*vx/Lp, C[1]+R*vy/Lp)
    ax, ay = A[0]-C[0], A[1]-C[1]; px, py = Pp[0]-C[0], Pp[1]-C[1]
    raw = math.degrees(math.atan2(ax*py-ay*px, ax*px+ay*py))
    if cv > 0 and raw < 0: raw += 360
    if cv < 0 and raw > 0: raw -= 360
    frac = raw/cv
    if frac < 0: return A, False
    if frac > 1: return B, False
    return Pp, (0.02 < frac < 0.98)

def split_angles(A, B, cv, Pp):
    C, R = arc_geom(A, B, cv)
    ax, ay = A[0]-C[0], A[1]-C[1]; px, py = Pp[0]-C[0], Pp[1]-C[1]
    raw = math.degrees(math.atan2(ax*py-ay*px, ax*px+ay*py))
    if cv > 0 and raw < 0: raw += 360
    if cv < 0 and raw > 0: raw -= 360
    return raw, cv-raw

def covered(net, P):
    for (A, B, cv, hw, lay, st, tag) in WIRE.get(net, []):
        q, _ = closest_on(A, B, cv, P)
        if math.hypot(P[0]-q[0], P[1]-q[1]) < hw:
            return True
    for (e, p, cx, cy, R) in PAD.get(net, []):
        if math.hypot(P[0]-cx, P[1]-cy) < R:
            return True
    return False

def hidden(net, A, B):
    n = max(2, int(math.hypot(A[0]-B[0], A[1]-B[1]) / STEP))
    return all(covered(net, (A[0]+i/n*(B[0]-A[0]), A[1]+i/n*(B[1]-A[1]))) for i in range(n+1))

def end_layer(net, P):
    b = None
    for (A, B, cv, hw, lay, st, tag) in WIRE.get(net, []):
        if st:
            continue
        for e in (A, B):
            d = math.hypot(P[0]-e[0], P[1]-e[1])
            if d <= NODE_TOL and (b is None or d < b[0]):
                b = (d, lay)
    return b[1] if b else None

def real_wires_ending_at(net, P):
    return [w for w in WIRE.get(net, []) if not w[5] and
            (math.hypot(P[0]-w[0][0], P[1]-w[0][1]) <= NODE_TOL or
             math.hypot(P[0]-w[1][0], P[1]-w[1][1]) <= NODE_TOL)]

def is_trace_end(net, P):
    return bool(real_wires_ending_at(net, P))

def host_wire(net, E):
    """same-net wire (not one E ends on) whose INTERIOR copper E sits inside -> (wire, Pp)"""
    best = None
    for w in WIRE.get(net, []):
        A, B, cv, hw, lay, st, tag = w
        if st:
            continue
        if math.hypot(E[0]-A[0], E[1]-A[1]) <= NODE_TOL or math.hypot(E[0]-B[0], E[1]-B[1]) <= NODE_TOL:
            continue
        q, interior = closest_on(A, B, cv, E)
        if not interior:
            continue
        d = math.hypot(E[0]-q[0], E[1]-q[1])
        if d < hw and (best is None or d < best[0]):
            best = (d, w, q)
    return (best[1], best[2]) if best else None

def nearest_trace_end(net, P, reach=SNAP_REACH):
    """nearest DISTINCT same-net real trace endpoint within reach (a node -- never a pad)"""
    best = None
    for (A, B, cv, hw, lay, st, tag) in WIRE.get(net, []):
        if st:
            continue
        for e in (A, B):
            d = math.hypot(P[0]-e[0], P[1]-e[1])
            if NODE_TOL < d <= reach and (best is None or d < best[0]):
                best = (d, e)
    return best[1] if best else None

def nearest_node(net, P):
    best = None
    for (A, B, cv, hw, lay, st, tag) in WIRE.get(net, []):
        if st:
            continue
        for e in (A, B):
            d = math.hypot(P[0]-e[0], P[1]-e[1])
            if d > NODE_TOL and (best is None or d < best[0]):
                best = (d, e, 'trace-end')
    for (e, p, cx, cy, R) in PAD.get(net, []):
        d = math.hypot(P[0]-cx, P[1]-cy)
        if d > NODE_TOL and (best is None or d < best[0]):
            best = (d, (cx, cy), 'pad %s.%s' % (e, p))
    return best

# ---- tag editing (collected, applied at the end) ----
repl = {}; stitches = {}                     # oldtag -> newtext ; net -> [wirestrings]

def set_end(tag, oldP, newP):
    m = re.search(r'x1="(-?[\d.]+)" y1="(-?[\d.]+)" x2="(-?[\d.]+)" y2="(-?[\d.]+)"', tag)
    x1, y1, x2, y2 = (float(z) for z in m.groups())
    if math.hypot(x1-oldP[0], y1-oldP[1]) <= math.hypot(x2-oldP[0], y2-oldP[1]):
        nx1, ny1, nx2, ny2 = newP[0], newP[1], x2, y2
    else:
        nx1, ny1, nx2, ny2 = x1, y1, newP[0], newP[1]
    return tag[:m.start()] + 'x1="%.4f" y1="%.4f" x2="%.4f" y2="%.4f"' % (nx1, ny1, nx2, ny2) + tag[m.end():]

def queue_repl(tag, newtext):
    if tag in repl:
        return False                         # already edited -> conflict
    repl[tag] = newtext
    return True

def snap(net, E, T):                         # move every real wire ending at E so that end -> T
    ws = real_wires_ending_at(net, E)
    if not ws:
        return False
    ok = False
    for w in ws:
        if queue_repl(w[6], set_end(w[6], E, T)):
            ok = True
    return ok

def ladder(net, host, E):                    # split host at Pp, snap E's wire end onto Pp
    w, Pp = host; A, B, cv, hw, lay, st, tag = w
    if abs(cv) < 1e-6:
        t1 = set_end(tag, B, Pp); t2 = set_end(tag, A, Pp)
    else:
        a1, a2 = split_angles(A, B, cv, Pp)
        if min(abs(a1), abs(a2)) < 1.0:
            return False                     # degenerate split (Pp at an end)
        t1 = re.sub(r'curve="-?[\d.]+"', 'curve="%.5f"' % a1, set_end(tag, B, Pp))
        t2 = re.sub(r'curve="-?[\d.]+"', 'curve="%.5f"' % a2, set_end(tag, A, Pp))
    if not queue_repl(tag, t1 + "\n" + t2):
        return False
    return snap(net, E, Pp)

def add_stitch(net, A, B, lay):
    s = ('<wire x1="%.4f" y1="%.4f" x2="%.4f" y2="%.4f" width="%.4f" layer="%s"/>'
         % (A[0], A[1], B[0], B[1], W_STITCH, lay))
    if s in brd or s in stitches.get(net, []):
        return False
    stitches.setdefault(net, []).append(s)
    return True

def pad_RC(net, P):
    for (e, p, cx, cy, R) in PAD.get(net, []):
        if math.hypot(P[0]-cx, P[1]-cy) <= PAD_TOL:
            return (cx, cy, R)
    return None

def pad_rescue(net, center, R):
    """Connect a pad to its net. A pad's trace often passes within its copper but never
    reaches the center node, so Eagle calls it unconnected. For every same-net trace
    ENDPOINT inside the pad: too-close ones are MOVED onto the center (a stitch that
    short would collapse), the nearest farther one gets a stitch (a real spoke, hidden
    in pad copper). `center` is the pad's center node."""
    cx, cy = center; cands = []
    for (A, B, cv, hw, lay, st, tag) in WIRE.get(net, []):
        if st:
            continue
        for e in (A, B):
            d = math.hypot(e[0]-cx, e[1]-cy)
            if 0.2*MIL < d < R:
                cands.append((d, e, lay))
    cands.sort()
    did = False; far = None
    for d, e, lay in cands:
        if d <= MOVE_BELOW:
            if snap(net, e, center):
                did = True
        elif far is None:
            far = (e, lay)
    if far and add_stitch(net, far[0], center, far[1]):
        did = True
    return did

# ---- resolve ----
n_snap = n_stitch = n_ladder = n_pad = 0; logs = []
for net, A, B in airwires:
    gap = math.hypot(A[0]-B[0], A[1]-B[1])
    # 1. sub-fuse-length gap -> snap the loose trace-end onto the nearest DISTINCT trace-end
    #    node. (Snapping to a pad center backfired -- the center is in the drill hole and the
    #    move breaks the endpoint's junction -- so trace-end targets only; pad-center cases
    #    fall through to LOG.)
    if gap < SNAP_BELOW and not pad_RC(net, A) and not pad_RC(net, B):
        did = False
        for E in (A, B):
            if not is_trace_end(net, E):
                continue
            N = nearest_trace_end(net, E)
            if N and snap(net, E, N):
                n_snap += 1; did = True; break
        if did:
            continue
    # 2. node-to-node stitch if hidden
    if gap >= SNAP_BELOW and hidden(net, A, B):
        add_stitch(net, A, B, end_layer(net, A) or end_layer(net, B) or "1")
        n_stitch += 1
        continue
    # 3. pad rescue: airwire anchored at a pad -> rescue BOTH pad ends
    pads_done = 0
    for P in (A, B):
        rc = pad_RC(net, P)
        if rc and pad_rescue(net, (rc[0], rc[1]), rc[2]):
            pads_done += 1
    if pads_done:
        n_pad += pads_done
        continue
    # 4. arc-ladder: a loose trace-end sits on another same-net wire's interior
    done = False
    for E in (A, B):
        if not is_trace_end(net, E):
            continue
        h = host_wire(net, E)
        if h and ladder(net, h, E):
            n_ladder += 1; done = True; break
    if done:
        continue
    # 5. local node-to-node: loose trace-end -> nearest trace-end node within reach, if hidden
    for E in (A, B):
        if not is_trace_end(net, E):
            continue
        N = nearest_trace_end(net, E, LOCAL_REACH)
        if N and math.hypot(E[0]-N[0], E[1]-N[1]) > SNAP_BELOW and hidden(net, E, N):
            add_stitch(net, E, N, end_layer(net, E) or "1")
            n_stitch += 1; done = True; break
    if done:
        continue
    logs.append((net, A, B))

# ---- apply ----
if not PREVIEW:
    for old, new in repl.items():
        if brd.count(old) == 1:
            brd = brd.replace(old, new, 1)
        else:
            print("  !! skip edit (tag not unique): %s" % old[:60])
    for net, ws in stitches.items():
        m = re.search(r'(<signal name="%s"[^>]*>)(.*?)(</signal>)' % re.escape(net), brd, re.S)
        brd = brd[:m.end(2)] + '\n' + '\n'.join(ws) + brd[m.end(2):]
    import xml.dom.minidom as MD
    MD.parseString(brd)
    open(BRD, 'w').write(brd)

print("%sairwire_stitch: %d airwires | %d snapped | %d stitched | %d pad-rescued | "
      "%d arc-laddered | %d LOGGED"
      % ("[PREVIEW] " if PREVIEW else "", len(airwires), n_snap, n_stitch, n_pad, n_ladder, len(logs)))
for net, A, B in logs:
    nn = nearest_node(net, A) or nearest_node(net, B)
    tgt = ("aim for %s, %.0f mil away" % (nn[2], nn[0]/MIL)) if nn else "no node nearby"
    print("  LOG %-4s gap=%.0f mil  loose end (%.0f,%.0f) -> %s"
          % (net, math.hypot(A[0]-B[0], A[1]-B[1])/MIL, A[0]/MIL, A[1]/MIL, tgt))
if not PREVIEW:
    print("NEXT: in Eagle File > Reload, RATSNEST, re-RUN export_airwires.ulp, and repeat "
          "until only genuine pour/routing gaps remain.")
