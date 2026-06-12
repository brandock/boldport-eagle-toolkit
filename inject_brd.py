#!/usr/bin/env python3
"""
inject_brd.py -- inject NET-NEW geometry from the PCBmodE repo straight into a
board .brd: the board outline (Dimension, layer 20) and the copper traces
(top -> layer 1, bottom -> layer 16).

Generalized successor to the Cuttle-era inject_brd.py. Differences:
  * Takes the repo and the board on the command line (no hardcoded paths).
  * Reads the board's OX/OY from its `<board>.brd.transform.json` sidecar (written
    by place.py) so injected geometry uses the SAME frame as the placed parts --
    no more hardcoded 50/40. (--ox/--oy override; needed only if no sidecar.)
  * Edits the .brd directly (same approach as place.py / Steps 9, 10), minidom-
    validated, header/elements preserved byte-for-byte.

Change management (additive, never replace-all): each FEATURE is idempotent via a
manifest `<board>.brd.inject.json`. Re-running a feature removes only the elements
IT previously injected (exact string match) and adds the fresh set, so anything you
add by hand is never touched.

Geometry uses the §1 transform: x_brd = x + OX, y_brd = -y + OY, curve -> -curve
(arcfit.fit_path turns the SVG cubics into tolerance-bounded arcs first).

Usage:
    python3 inject_brd.py <repo-dir> "<board.brd>" dimension
    python3 inject_brd.py <repo-dir> "<board.brd>" top_copper
    python3 inject_brd.py <repo-dir> "<board.brd>" bottom_copper
    options: --ox N --oy N  (override the sidecar)

Branch the board first (e.g. r0.2 -> r0.3) and have Eagle closed on it. Afterward,
open (or File > Reload) the board in Eagle.
"""
import argparse, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arcfit


def find_master(path):
    path = os.path.expanduser(path.rstrip('/\\'))
    if os.path.isfile(path):
        return path
    cands = [p for p in sorted(glob.glob(os.path.join(path, "*.json"))) if _is_master(p)]
    if not cands:
        sys.exit(f"no master board json (with components + outline) found in {path}")
    if len(cands) > 1:
        sys.exit(f"multiple master candidates in {path}: {[os.path.basename(c) for c in cands]} "
                 f"-- pass the .json explicitly")
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
    sys.exit(f"no {os.path.basename(sidecar)} and no --ox/--oy. Run place.py first, "
             f"or pass the offsets explicitly so injected geometry matches the parts.")


def wires(d, layer, width, ox, oy):
    """arc-fit an SVG path, apply (x+OX, -y+OY, -curve), emit a <wire> chain."""
    subs, st = arcfit.fit_path(d, tol=0.02)
    out = []
    for verts, closed in subs:
        tv = [(x + ox, -y + oy, -cv) for x, y, cv in verts]
        n = len(tv)
        for i in (range(n) if closed else range(n - 1)):
            x1, y1, cv = tv[i]
            x2, y2, _ = tv[(i + 1) % n]
            if abs(x2 - x1) < 1e-7 and abs(y2 - y1) < 1e-7:
                continue
            tail = f' curve="{cv:.4f}"' if abs(cv) > 1e-6 else ''
            out.append(f'<wire x1="{x1:.4f}" y1="{y1:.4f}" x2="{x2:.4f}" '
                       f'y2="{y2:.4f}" width="{width:.4f}" layer="{layer}"{tail}/>')
    return out, st


def build_dimension(master, routing, ox, oy):
    ws, st = wires(master['outline']['shape']['value'], 20, 0.0, ox, oy)
    print(f"  outline arc-fit: {st['arcs']} arcs + {st['lines']} lines -> {len(ws)} wires (layer 20)")
    return ws


def build_copper(routing, side, layer, ox, oy):
    out, arcs, lines = [], 0, 0
    routes = routing['routes'][side]
    for net in routes.values():
        w = float(net.get('stroke-width') or 0.25)
        ws, st = wires(net['value'], layer, w, ox, oy)
        out += ws
        arcs += st['arcs']
        lines += st['lines']
    print(f"  {side} routes: {len(routes)} traces -> {arcs} arcs + {lines} lines "
          f"-> {len(out)} wires (layer {layer})")
    return out


def strip_default_outline(t):
    """Remove Eagle's default board-outline rectangle from <plain> (layer-20 wires
    whose box touches the origin). Safe: a Boldport outline is shifted into the
    positive quadrant by OX/OY, so none of its wires has a 0 coordinate; only the
    default rectangle does. Returns (text, removed_count)."""
    def drop(m):
        w = m.group(0)
        coords = [float(v) for v in re.findall(r'(?:x1|y1|x2|y2)="([-\d.]+)"', w)]
        return '' if any(abs(c) < 1e-9 for c in coords) else w
    removed = [0]
    def sub(m):
        r = drop(m)
        if r == '':
            removed[0] += 1
        return r
    t = re.sub(r'\n?<wire [^>]*layer="20"[^>]*/>', sub, t)
    return t, removed[0]


def inject(board_path, feature, elements, strip_default=False):
    """Remove this feature's previously-injected elements (exact match), then add the
    new ones before </plain>. Manifest <board>.brd.inject.json tracks per feature.
    strip_default also clears Eagle's default outline rectangle (dimension only)."""
    man_path = board_path + ".inject.json"
    t = open(board_path).read()
    man = json.load(open(man_path)) if os.path.exists(man_path) else {}
    removed = 0
    for old in man.get(feature, []):
        if '\n' + old in t:
            t = t.replace('\n' + old, '', 1); removed += 1
        elif old in t:
            t = t.replace(old, '', 1); removed += 1
    stripped = 0
    if strip_default:
        t, stripped = strip_default_outline(t)
    if '</plain>' not in t:
        sys.exit("board has no <plain> section -- is this an Eagle .brd?")
    t = t.replace('</plain>', '\n'.join(elements) + '\n</plain>', 1)
    import xml.dom.minidom as MD
    MD.parseString(t)                            # well-formed check before writing
    open(board_path, 'w').write(t)
    man[feature] = elements
    json.dump(man, open(man_path, 'w'), indent=1)
    return removed, len(elements), stripped


def main():
    ap = argparse.ArgumentParser(description="Inject repo outline/copper geometry into a board .brd")
    ap.add_argument('repo', help="Boldport repo dir (master json found by content) or master .json")
    ap.add_argument('board', help="the .brd to edit in place (branch it first)")
    ap.add_argument('feature', choices=['dimension', 'top_copper', 'bottom_copper'])
    ap.add_argument('--ox', type=float, help="x offset (default: from <board>.brd.transform.json)")
    ap.add_argument('--oy', type=float, help="y offset (default: from <board>.brd.transform.json)")
    a = ap.parse_args()

    master_path = find_master(a.repo)
    master = json.load(open(master_path))
    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    ox, oy = load_offset(board_path, a.ox, a.oy)
    print(f"master: {master_path}")
    print(f"board:  {board_path}")
    print(f"offset: OX={ox:g} OY={oy:g}")

    routing = None
    if a.feature in ('top_copper', 'bottom_copper'):
        base = os.path.basename(master_path).rsplit('.json', 1)[0]
        rp = os.path.join(os.path.dirname(master_path), f"{base}_routing.json")
        if not os.path.exists(rp):
            hits = glob.glob(os.path.join(os.path.dirname(master_path), "*_routing.json"))
            if len(hits) != 1:
                sys.exit(f"could not find a unique *_routing.json next to the master")
            rp = hits[0]
        routing = json.load(open(rp))
        print(f"routing: {rp}")

    if a.feature == 'dimension':
        els = build_dimension(master, routing, ox, oy)
    elif a.feature == 'top_copper':
        els = build_copper(routing, 'top', 1, ox, oy)
    else:
        els = build_copper(routing, 'bottom', 16, ox, oy)

    removed, added, stripped = inject(board_path, a.feature, els, strip_default=(a.feature == 'dimension'))
    print(f"\ninjected '{a.feature}': removed {removed} prior, added {added} elements to "
          f"{os.path.basename(board_path)} (XML well-formed)")
    if stripped:
        print(f"  also removed {stripped} default-outline wire(s) (Eagle's (0,0)-anchored rectangle)")
    print("NEXT: open (or File > Reload) the board in Eagle.")


if __name__ == '__main__':
    main()
