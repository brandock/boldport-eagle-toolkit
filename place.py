#!/usr/bin/env python3
"""
place.py -- position every part on a Boldport->Eagle board by editing the .brd
directly (replacement for make_place_scr.py's Eagle-script approach).

Reads the master <board>.json `components` (refdes -> {footprint, layer,
location, rotate}) and writes each matching <element>'s x / y / rot straight
into the .brd, through the board transform:

    x_brd =  x_repo + OX
    y_brd = -y_repo + OY          (the y-flip: PCBmodE/SVG is y-down, Eagle y-up)
    rot   = (-rotate) % 360       (sign flips with the y-flip)

Why edit the .brd instead of generating an Eagle MOVE/ROTATE script:
  * A part's position/rotation is BOARD-ONLY data -- it does not affect the
    schematic, netlist, or forward/back annotation (Eagle writes exactly this
    x/y/rot every time you drag a part). So writing it ourselves is safe and
    identical, and matches how the toolkit's other steps edit the .brd.
  * It sidesteps every Eagle-script quirk: no MOVE-sweep no-ops, no free-edition
    "can't ROTATE in the -x,-y quadrant", no warnings. The part is simply written
    where it belongs, in any frame.

Smashed parts: each part's >NAME / >VALUE label children are transformed with
their element (translated, and rotated about the element origin), so labels
follow the part.

NO BACKUP is made: branch the .brd/.sch (e.g. r0.1 -> r0.2) before running, as
you should before any step in this pipeline.

CHOOSING OX/OY -- same rule as the placement step of "Boldport to Eagle
Step-by-Step": auto-computed from the outline bbox + part locations so the board
lands snug in Eagle's positive (and free-edition-legal) area, or set explicitly.

Usage:
    python3 place.py <repo-dir> <board.brd>                  # auto OX/OY (needs svgpathtools)
    python3 place.py <repo-dir> <board.brd> --ox 50 --oy 40  # explicit offsets
    options: --margin 5.0

<repo-dir> may be the Boldport repo (master json found by content) or a direct
path to the master .json.
"""
import argparse, glob, json, math, os, re, sys


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


def source_bbox(master):
    try:
        from svgpathtools import parse_path
    except ImportError:
        sys.exit("auto OX/OY needs svgpathtools (pip install --user svgpathtools), "
                 "or pass --ox/--oy explicitly")
    xmin, xmax, ymin, ymax = parse_path(master['outline']['shape']['value']).bbox()
    for c in master['components'].values():
        x, y = c['location']
        xmin, xmax = min(xmin, x), max(xmax, x)
        ymin, ymax = min(ymin, y), max(ymax, y)
    return xmin, xmax, ymin, ymax


def num(v):
    v = v + 0.0
    if abs(v) < 5e-5:
        return "0"
    return f"{v:.4f}".rstrip('0').rstrip('.')


def get_attr(tag, key, default=None):
    m = re.search(rf'\b{key}="([^"]*)"', tag)
    return m.group(1) if m else default


def set_attr(tag, key, val):
    if re.search(rf'\b{key}="[^"]*"', tag):
        return re.sub(rf'\b{key}="[^"]*"', f'{key}="{val}"', tag, count=1)
    return re.sub(r'(\s*/?>)\s*$', rf' {key}="{val}"\1', tag, count=1)


def del_attr(tag, key):
    return re.sub(rf'\s+{key}="[^"]*"', '', tag, count=1)


def parse_rot(rotstr):
    """'MR90' -> (90.0, True); '' -> (0.0, False)"""
    if not rotstr:
        return (0.0, False)
    m = re.search(r'R(\d+(?:\.\d+)?)', rotstr)
    return (float(m.group(1)) if m else 0.0, 'M' in rotstr)


def element_span(text, ref):
    """(start, end) of the <element name="ref" ...> block, self-closing or paired."""
    m = re.search(rf'<element name="{re.escape(ref)}"(?=[\s/>])', text)
    if not m:
        return None
    i = m.start()
    gt = text.index('>', i)
    if text[gt - 1] == '/':
        return (i, gt + 1)
    return (i, text.index('</element>', gt) + len('</element>'))


def place_element(block, comp, ox, oy):
    gt = block.index('>')
    self_close = block[gt - 1] == '/'
    open_tag, rest = block[:gt + 1], block[gt + 1:]

    ox_old = float(get_attr(open_tag, 'x'))
    oy_old = float(get_attr(open_tag, 'y'))
    odeg, _ = parse_rot(get_attr(open_tag, 'rot', ''))

    x_repo, y_repo = comp['location']
    nx, ny = x_repo + ox, -y_repo + oy
    ndeg = int((-float(comp.get('rotate', 0))) % 360)
    nmir = comp.get('layer', 'top') == 'bottom'

    open_tag = set_attr(open_tag, 'x', num(nx))
    open_tag = set_attr(open_tag, 'y', num(ny))
    rotstr = ('M' if nmir else '') + f'R{ndeg}'
    open_tag = del_attr(open_tag, 'rot')
    if ndeg != 0 or nmir:
        open_tag = set_attr(open_tag, 'rot', rotstr)

    # transform smashed label children: rigid rotate about the element origin + translate
    d = math.radians(ndeg - odeg)
    cosd, sind = math.cos(d), math.sin(d)

    def xform(am):
        atag = am.group(0)
        rx = float(get_attr(atag, 'x')) - ox_old
        ry = float(get_attr(atag, 'y')) - oy_old
        atag = set_attr(atag, 'x', num(nx + rx * cosd - ry * sind))
        atag = set_attr(atag, 'y', num(ny + rx * sind + ry * cosd))
        adeg, amir = parse_rot(get_attr(atag, 'rot', ''))
        adeg = int((adeg + (ndeg - odeg)) % 360)
        atag = del_attr(atag, 'rot')
        if adeg != 0 or amir:
            atag = set_attr(atag, 'rot', ('M' if amir else '') + f'R{adeg}')
        return atag

    if not self_close:
        rest = re.sub(r'<attribute\b[^>]*/>', xform, rest)
    return open_tag + rest, nx, ny, ndeg, nmir


def main():
    ap = argparse.ArgumentParser(
        description="Position every part by editing the .brd directly (master JSON -> element x/y/rot)")
    ap.add_argument('master', help="Boldport repo dir (master json found by content) or master .json")
    ap.add_argument('board', help="the .brd to edit in place (branch it first)")
    ap.add_argument('--ox', type=float, help="x offset (default: computed from outline)")
    ap.add_argument('--oy', type=float, help="y offset (default: computed from outline)")
    ap.add_argument('--margin', type=float, default=5.0,
                    help="clearance (mm) between board extents and axes in auto mode (default 5)")
    a = ap.parse_args()

    master_path = find_master(a.master)
    master = json.load(open(master_path))
    comps = master['components']
    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    print(f"master: {master_path}")
    print(f"board:  {board_path}")

    snap_up = lambda v: math.ceil(v / 2.54) * 2.54
    if a.ox is None or a.oy is None:
        xmin, xmax, ymin, ymax = source_bbox(master)
        ox = a.ox if a.ox is not None else snap_up(a.margin - xmin)
        oy = a.oy if a.oy is not None else snap_up(a.margin + ymax)
        print(f"source bbox (outline + parts): x [{xmin:.2f}, {xmax:.2f}]  y [{ymin:.2f}, {ymax:.2f}]")
        print(f"auto transform (margin {a.margin:g}, snapped up to 2.54):  OX={ox:g}  OY={oy:g}")
        print(f"board lands at:  x [{xmin+ox:.2f}, {xmax+ox:.2f}]  y [{oy-ymax:.2f}, {oy-ymin:.2f}]")
    else:
        ox, oy = a.ox, a.oy

    text = open(board_path).read()
    placed, missing_in_board, bottom = [], [], []
    for ref in sorted(comps, key=lambda r: (''.join(c for c in r if not c.isdigit()),
                                            int(''.join(c for c in r if c.isdigit()) or 0))):
        span = element_span(text, ref)
        if span is None:
            missing_in_board.append(ref)
            continue
        block = text[span[0]:span[1]]
        newblock, nx, ny, ndeg, nmir = place_element(block, comps[ref], ox, oy)
        text = text[:span[0]] + newblock + text[span[1]:]
        placed.append(ref)
        if nmir:
            bottom.append(ref)

    import xml.dom.minidom as MD
    MD.parseString(text)                      # well-formed check before writing
    open(board_path, 'w').write(text)

    sidecar = board_path + ".transform.json"
    json.dump({'master': os.path.basename(master_path), 'ox': ox, 'oy': oy, 'margin': a.margin,
               'transform': 'x_brd = x_src + ox; y_brd = -y_src + oy; rot_brd = -rot_src'},
              open(sidecar, 'w'), indent=1)

    print(f"\nplaced {len(placed)} parts directly into {os.path.basename(board_path)} (XML well-formed)")
    print(f"wrote {os.path.basename(sidecar)}")
    if bottom:
        print(f"NOTE: bottom-layer parts mirrored (MR): {bottom} -- eyeball these.")
    if missing_in_board:
        print(f"WARNING: in master but not on the board (not placed): {missing_in_board}")
    extra = set()
    for m in re.finditer(r'<element name="([^"]+)"', text):
        if m.group(1) not in comps:
            extra.add(m.group(1))
    if extra:
        print(f"NOTE: on the board but not in master (left as-is): {sorted(extra)}")
    print(f"\nTRANSFORM: x -> x + {ox:g},  y -> -y + {oy:g}  (also in {os.path.basename(sidecar)})")
    print("NEXT: open (or reload) the board in Eagle -- parts are already in position.")


if __name__ == '__main__':
    main()
