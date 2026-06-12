#!/usr/bin/env python3
"""
roll_call.py -- Step 1A of a Boldport->Eagle port: the parts roll call.

Reads a PCBmodE repo and builds a starter Eagle library named after your
schematic, plus an Eagle script that ADDs one instance of every part to the
sheet in a grid -- R1 D1 C1 C2 ... -- with a provenance comment per part
TYPE (one comment for C1..C7, not one per instantiation).

What each part gets:
  - PACKAGE: real from day one -- generated from components/<fp>.json
    (circle pads -> pad + filled copper polygon disk on L1+L16; rects ->
    square pad + square polygons; rounded rects -> round anchor pad +
    rounded-rect polygons + expanded stopmask; body outline on tDocu only).
  - SYMBOL + connect map:
      * safe guess: 2-pin parts with refdes prefix R or C borrow R-US / C-EU
        from Eagle's stock rcl library (identity 2-pin map). Everything else
        is NOT guessed --
      * placeholder: an auto box symbol with one pin per pad (named from the
        pad keys, so "9-PB6" reads as a port), identity connect map.
    Replacing placeholder symbols/maps with curated ones is Step 1B; wiring
    the schematic is Step 1C (by hand -- the repo has no netlist).

Usage:
    python3 roll_call.py <repo-dir> <sch-name-or-path> [-o out.lbr] [--scr out.scr] [--rcl rcl.lbr]

Example:
    python3 roll_call.py ~/Dropbox/EAGLE/projects/Boldport-masters/thecuttle the-cuttle-eagle
    # -> ~/Dropbox/EAGLE/libraries/the-cuttle-eagle.lbr
    # -> ./roll-call-the-cuttle-eagle.scr   (in Eagle: open the blank .sch, SCRIPT it)

Pad/package generation follows cuttle_lib.py (the original Cuttle library
builder); the placeholder box symbol follows emit_sch.py's auto-symbols.
"""
import argparse, glob, json, math, os, re, sys

RCL_CANDIDATES = [                      # glob patterns, macOS + Windows
    "~/Library/Application Support/Eagle/lbr/*/rcl.lbr",     # macOS managed-library cache
    "/Applications/EAGLE-*/cache/lbr/rcl.lbr",               # macOS app cache
    "~/AppData/Roaming/Eagle/lbr/*/rcl.lbr",                 # Windows managed-library cache
    "C:/Program Files/Autodesk/EAGLE*/lbr/rcl.lbr",          # Windows install dir
    "C:/EAGLE*/lbr/rcl.lbr",
]
SAFE = {'R': 'R-US', 'C': 'C-EU'}      # refdes prefix -> rcl symbol, 2-pin parts only


# ---- Eagle-flavored serialization helpers (from cuttle_lib.py) ---------------
def num(v):
    if v == 0:
        return "0"
    return f"{v:.4f}".rstrip('0').rstrip('.')


def rrect(cx, cy, w, h, radii, layer, width=0.0254):
    """Rounded-rect <polygon>, per-corner radii (keys tl/tr/bl/br are VISUAL
    corners -- placed literally, never rotated/flipped)."""
    a, b = w / 2, h / 2
    g = lambda k: (radii or {}).get(k, 0)
    segs = [((a, b), g('tr'), (a, b - g('tr')), (a - g('tr'), b)),
            ((-a, b), g('tl'), (-a + g('tl'), b), (-a, b - g('tl'))),
            ((-a, -b), g('bl'), (-a, -b + g('bl')), (-a + g('bl'), -b)),
            ((a, -b), g('br'), (a - g('br'), -b), (a, -b + g('br')))]
    V = []
    for corner, r, tin, tout in segs:
        if r > 0:
            V += [(tin[0], tin[1], 90), (tout[0], tout[1], 0)]
        else:
            V.append((corner[0], corner[1], 0))
    vx = ''.join(f'<vertex x="{num(cx+x)}" y="{num(cy+y)}"' + (f' curve="{num(c)}"' if c else '') + '/>'
                 for x, y, c in V)
    return f'<polygon width="{width}" layer="{layer}">{vx}</polygon>'


def circ_poly(cx, cy, r, layer):
    """Filled disk as a 4-arc POLYGON -- a bare <circle> on copper is graphics
    and never joins the net (the FlexyPin stripes lesson)."""
    pts = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    vs = ''.join(f'<vertex x="{num(px)}" y="{num(py)}" curve="90"/>' for px, py in pts)
    return f'<polygon width="0.0001" layer="{layer}">{vs}</polygon>'


def pad_elements(name, x, y, padspec):
    """One PCBmodE pad -> Eagle <pad> plus matching filled copper on L1+L16."""
    sh = padspec['shapes'][0]
    drills = padspec.get('drills') or []
    drill = drills[0]['diameter'] if drills else None
    if sh['type'] == 'circle':
        d = sh['diameter']
        if drill is None:                                  # plain hole, no copper ring
            return [f'<hole x="{num(x)}" y="{num(y)}" drill="{d:g}"/>']
        return ([f'<pad name="{name}" x="{num(x)}" y="{num(y)}" drill="{drill:g}" diameter="{d:g}"/>']
                + [circ_poly(x, y, d / 2, L) for L in (1, 16)])
    w, h, radii = sh['width'], sh['height'], sh.get('radii')
    if not radii or all(v == 0 for v in radii.values()):
        return ([f'<pad name="{name}" x="{num(x)}" y="{num(y)}" drill="{drill:g}" '
                 f'diameter="{max(w,h):g}" shape="square"/>']
                + [rrect(x, y, w, h, {}, L) for L in (1, 16)])
    # stopmask expansion 0.1 mm (~4 mil) per side -- matches the NSMD ring
    # measured on Boldport's shipped boards (2x the config's 0.05 buffer)
    exp = {k: (v + 0.1 if v > 0 else 0) for k, v in radii.items()}
    return ([f'<pad name="{name}" x="{num(x)}" y="{num(y)}" drill="{drill:g}" '
             f'diameter="{min(w,h):g}" stop="no"/>']
            + [rrect(x, y, w, h, radii, L) for L in (1, 16)]          # copper
            + [rrect(x, y, w + 0.2, h + 0.2, exp, L) for L in (29, 30)])  # stopmask


def rect_wires(w, h, layer, width=0.127, cx=0.0, cy=0.0):
    a, b = w / 2, h / 2
    pts = [(cx - a, cy - b), (cx + a, cy - b), (cx + a, cy + b), (cx - a, cy + b), (cx - a, cy - b)]
    return [f'<wire x1="{num(x1)}" y1="{num(y1)}" x2="{num(x2)}" y2="{num(y2)}" '
            f'width="{width}" layer="{layer}"/>' for (x1, y1), (x2, y2) in zip(pts, pts[1:])]


def package_from_footprint(repo, fpname, pkgname=None):
    """components/<fp>.json -> Eagle <package> (SVG y-down -> Eagle y-up).
    Names UPPERCASE (Eagle's indexer requirement). Body outline on tDocu (L51)
    only -- Boldport silk is board art, not footprint silk. pkgname overrides
    the package name (used when a footprint is shared across refdes prefixes
    and each prefix gets its own divergeable copy)."""
    fp = json.load(open(os.path.join(repo, "components", fpname + ".json")))
    body = [f'<package name="{pkgname or fpname.upper()}">',
            f'<description>Boldport footprint, generated from components/{fpname}.json</description>']
    for pn, pin in fp.get('pins', {}).items():
        x, y = pin['layout']['location']
        padname = pn.split('-', 1)[0] if re.match(r'\d+-', pn) else pn
        body += pad_elements(padname, x, -y, fp['pads'][pin['layout']['pad']])
    lay = fp.get('layout', {})
    for s in (lay.get('assembly', {}).get('shapes', [])
              or lay.get('silkscreen', {}).get('shapes', [])):
        cx, cy = s.get('location', [0, 0])
        if s['type'] == 'rect':
            body += rect_wires(s['width'], s['height'], 51, cx=cx, cy=-cy)
        elif s['type'] == 'circle':
            body.append(f'<circle x="{num(cx)}" y="{num(-cy)}" '
                        f'radius="{num(s["diameter"]/2)}" width="0.127" layer="51"/>')
    body += ['<text x="0" y="2.2" size="1" layer="25" font="vector" align="bottom-center">&gt;NAME</text>',
             '<text x="0" y="-2.2" size="1" layer="27" font="vector" align="top-center">&gt;VALUE</text>',
             '</package>']
    return '\n'.join(body)


# ---- symbols -----------------------------------------------------------------
def pin_list(fp):
    """[(pad_name, pin_name)] sorted by pad number where numeric."""
    keys = list(fp.get('pins', {}).keys())
    def k(pn):
        m = re.match(r'(\d+)', pn)
        return (0, int(m.group(1))) if m else (1, pn)
    out = []
    for pn in sorted(keys, key=k):
        padname = pn.split('-', 1)[0] if re.match(r'\d+-', pn) else pn
        out.append((padname, pn.replace(' ', '_')))   # pin keeps the full key ("9-PB6")
    return out


def placeholder_symbol(fpname, pins):
    """Auto box symbol: one pin per pad, <=8 pins on the left, else split into
    two columns (left top->bottom, right top->bottom). Pins on the 2.54 grid."""
    name = 'PH_' + fpname.upper()
    n = len(pins)
    split = n > 8
    left = pins[:math.ceil(n / 2)] if split else pins
    right = pins[math.ceil(n / 2):] if split else []
    ncol = max(len(left), len(right), 1)
    ytop = ((ncol - 1) // 2 + 1) * 2.54
    XP, BOX = 12.7, 10.16
    sp = [f'<symbol name="{name}">']
    for col, sgn, rot in ((left, -1, 'R0'), (right, 1, 'R180')):
        for i, (_, pinname) in enumerate(col):
            sp.append(f'<pin name="{pinname}" x="{num(sgn*XP)}" y="{num(ytop - i*2.54)}" '
                      f'visible="both" length="middle" direction="pas" rot="{rot}"/>')
    btop, bbot = ytop + 2.54, ytop - ncol * 2.54
    for (x1, y1), (x2, y2) in [((-BOX, btop), (BOX, btop)), ((BOX, btop), (BOX, bbot)),
                               ((BOX, bbot), (-BOX, bbot)), ((-BOX, bbot), (-BOX, btop))]:
        sp.append(f'<wire x1="{num(x1)}" y1="{num(y1)}" x2="{num(x2)}" y2="{num(y2)}" '
                  f'width="0.254" layer="94"/>')
    sp += [f'<text x="{num(-BOX)}" y="{num(btop+0.5)}" size="1.778" layer="95">&gt;NAME</text>',
           f'<text x="{num(-BOX)}" y="{num(bbot-2.3)}" size="1.778" layer="96">&gt;VALUE</text>',
           '</symbol>']
    height = (ncol + 2) * 2.54
    return name, '\n'.join(sp), height


def strip_urn(s):
    s = re.sub(r'<package3dinstances>.*?</package3dinstances>', '', s, flags=re.S)
    s = re.sub(r'<package3dinstance[^>]*/?>', '', s)
    s = re.sub(r'\s+library_version="[^"]*"', '', s)
    return re.sub(r'\s+[\w]*urn="[^"]*"', '', s)


def rcl_symbol(rcl_text, symname):
    m = re.search(rf'<symbol name="{re.escape(symname)}"[^>]*>.*?</symbol>', rcl_text, re.S)
    return strip_urn(m.group(0)) if m else None


def deviceset(dsn, prefix, symname, pkgname, connects):
    cx = ''.join(f'<connect gate="G$1" pin="{p}" pad="{d}"/>' for p, d in connects)
    return (f'<deviceset name="{dsn}" prefix="{prefix}" uservalue="yes">'
            f'<gates><gate name="G$1" symbol="{symname}" x="0" y="0"/></gates>'
            f'<devices><device name="" package="{pkgname}"><connects>{cx}</connects>'
            f'<technologies><technology name=""/></technologies></device></devices></deviceset>')


# ---- main ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Step 1A of a Boldport->Eagle port: read a PCBmodE repo, build a "
                    "starter Eagle library (real footprints, guessed/placeholder symbols), "
                    "and write an Eagle script that ADDs every part to your schematic "
                    "in a commented roll-call grid.",
        epilog="""example:
  py roll_call.py ..\\..\\Boldport-masters\\thecuttle "..\\the-cuttle-test\\the-cuttle-test r0.1.sch"

outputs:
  <libname>.lbr            -> ~/Dropbox/EAGLE/libraries/   (must be a dir Eagle indexes)
  roll-call-<libname>.scr  -> next to the .sch             (run it FROM EAGLE, see below)

then, IN EAGLE: open the (blank) .sch and File > Execute Script... the .scr.
This python script only GENERATES the files; nothing appears in the schematic
until Eagle executes the script.""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('repo', help="PCBmodE repo dir (holds <board>.json + components/)")
    ap.add_argument('sch', help="schematic name or path -- names the .lbr and the .scr "
                                "(a trailing rev like 'r0.1' is dropped from the library name)")
    ap.add_argument('-o', '--lbr', help="output .lbr (default ~/Dropbox/EAGLE/libraries/<libname>.lbr)")
    ap.add_argument('--scr', help="output .scr (default: roll-call-<libname>.scr beside the .sch)")
    ap.add_argument('--rcl', help="path to stock rcl.lbr (for the R/C symbol guess)")
    a = ap.parse_args()

    repo = os.path.expanduser(a.repo.rstrip('/\\'))
    schname = os.path.basename(a.sch).rsplit('.sch', 1)[0]
    # Library name: the .sch may be rev-named ("the-cuttle-test r0.1.sch") but the
    # library serves ALL revs -- strip a trailing rev token. And Eagle's ADD command
    # parses tokens on whitespace, so a library name must carry no spaces.
    libname = re.sub(r'\s+r\d+\.\d+$', '', schname)
    libname = re.sub(r'[^A-Za-z0-9_.-]+', '-', libname)
    if libname != schname:
        print(f"library name: {libname}  (rev suffix/spaces dropped from '{schname}')")
    lbr_out = os.path.expanduser(a.lbr or f"~/Dropbox/EAGLE/libraries/{libname}.lbr")
    # the .scr is PROJECT-specific (unlike this toolkit script): default it into
    # the project directory, beside the .sch it serves
    schdir = os.path.dirname(os.path.expanduser(a.sch))
    scr_out = a.scr or os.path.join(schdir or '.', f"roll-call-{libname}.scr")

    # master = the repo-root json that has components + outline
    masters = [p for p in glob.glob(os.path.join(repo, "*.json"))
               if {'components', 'outline'} <= set(json.load(open(p)))]
    if not masters:
        sys.exit(f"no master board json found in {repo}")
    master = json.load(open(masters[0]))

    rcl_text = None
    for cand in ([a.rcl] if a.rcl else RCL_CANDIDATES):
        hits = sorted(glob.glob(os.path.expanduser(cand)))
        if hits:
            rcl_text = open(hits[-1], encoding='utf-8', errors='ignore').read()
            print(f"rcl symbols from: {hits[-1]}")
            break
    if rcl_text is None:
        print("WARNING: stock rcl.lbr not found -- R/C parts get placeholders too "
              "(point --rcl at your rcl.lbr)", file=sys.stderr)

    # group refdes by (prefix, footprint): C1..C7 -> one group, but D1 and R1
    # (same footprint, different prefix) -> SEPARATE groups. The repo carries no
    # part-type semantics at all -- the refdes prefix is the ONLY semantic clue
    # (D=diode, R=resistor...), so honor it: each prefix gets its own copy of a
    # shared footprint, free to diverge in 1B (the Cuttle's D1 eventually grew
    # a mirror-image package with a direction arrow).
    def refkey(r):
        m = re.match(r'([A-Za-z]+)(\d+)$', r)
        return (m.group(1), int(m.group(2))) if m else (r, 0)
    groups = {}                       # (prefix, fpname) -> [refdes...]
    for ref in sorted(master['components'], key=refkey):
        groups.setdefault((refkey(ref)[0], master['components'][ref]['footprint']), []).append(ref)
    fp_prefixes = {}                  # fpname -> [prefix...] (who shares it)
    for prefix, fpname in groups:
        fp_prefixes.setdefault(fpname, []).append(prefix)

    packages, symbols, devicesets, plan = {}, {}, {}, []
    for (prefix, fpname), refs in groups.items():
        fp = json.load(open(os.path.join(repo, "components", fpname + ".json")))
        pins = pin_list(fp)
        shared = len(fp_prefixes[fpname]) > 1
        pkgname = f"{fpname.upper()}-{prefix}" if shared else fpname.upper()
        packages[pkgname] = package_from_footprint(repo, fpname, pkgname)
        note = ""
        if shared:
            others = [p for p in fp_prefixes[fpname] if p != prefix]
            note = f" | footprint shared with prefix {','.join(others)} - own copy, may diverge"

        sym = SAFE.get(prefix) if (len(pins) == 2 and rcl_text) else None
        if sym and rcl_symbol(rcl_text, sym):
            symbols.setdefault(sym, rcl_symbol(rcl_text, sym))
            dsn = sym if sym not in devicesets else f"{sym}-{pkgname}"
            connects = [('1', pins[0][0]), ('2', pins[1][0])]
            status = f"symbol {sym} (rcl guess), map identity 2-pin UNVERIFIED{note}"
            height = 12.7
        else:
            symn, symxml, height = placeholder_symbol(fpname, pins)
            symbols.setdefault(symn, symxml)
            sym, dsn = symn, pkgname
            connects = [(pinname, padname) for padname, pinname in pins]
            status = f"PLACEHOLDER symbol -- replace in step 1B{note}"
        devicesets[dsn] = deviceset(dsn, prefix, sym, pkgname, connects)
        plan.append(dict(refs=refs, fp=fpname, dsn=dsn, status=status, height=height))

    # ---- write the .lbr ----
    header = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "eagle_lib_header.xml")).read()
    NL = chr(10)
    LBR = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE eagle SYSTEM "eagle.dtd">
<eagle version="9.6.2">
<drawing>
{header}
<library>
<description>&lt;b&gt;{libname}&lt;/b&gt;&lt;p&gt;
Roll-call library generated by roll_call.py from {os.path.basename(masters[0])}.
Footprints from the repo component JSON; symbols are rcl guesses or placeholders.</description>
<packages>
{NL.join(packages.values())}
</packages>
<symbols>
{NL.join(symbols.values())}
</symbols>
<devicesets>
{NL.join(devicesets.values())}
</devicesets>
</library>
</drawing>
</eagle>
'''
    LBR = LBR.replace('><', '>\n<')   # one element per line: Eagle's indexer rejects long lines
    os.makedirs(os.path.dirname(lbr_out), exist_ok=True)
    open(lbr_out, 'w').write(LBR)
    import xml.dom.minidom as MD
    MD.parseString(LBR)

    # ---- write the roll-call .scr ----
    snap = lambda v: round(v / 2.54) * 2.54
    rows, y = [], 0.0                      # lay out from 0 downward, then shift positive
    for p in plan:
        ypart = snap(y - 7.62 - p['height'] / 2)
        rows.append((p, y, ypart))
        y = snap(ypart - p['height'] / 2 - 10.16)
    shift = snap(-y + 7.62)                # lowest row lands at y >= ~7.62
    # ADD resolves @library against Eagle's configured library DIRECTORIES --
    # NOT against USE'd files (verified empirically on 9.6.2: ADD after USE
    # fails; ADD @name with the .lbr in a library directory works). The bare
    # name is also the only cross-platform-safe token (a Windows full path
    # puts a drive-letter colon inside the command token). So: the .lbr MUST
    # be in one of Eagle's library directories (Options > Directories >
    # Libraries) -- the default output location ~/Dropbox/EAGLE/libraries
    # qualifies if Eagle's ADD dialog already lists your other libraries from
    # there.
    libref = libname
    lines = [f"# Roll call for {libname}: one ADD per part, a provenance TEXT per part type.",
             f"# Generated by roll_call.py from {os.path.basename(masters[0])}.",
             f"# REQUIRES: {os.path.basename(lbr_out)} in one of Eagle's library directories",
             "# (Options > Directories > Libraries). Then: open the blank .sch and",
             "# File > Execute Script... this file.",
             "GRID MM;",
             "LAYER 97;", "CHANGE SIZE 1.778;"]
    for p, ytext, ypart in rows:
        lines.append(f"TEXT '{' '.join(p['refs'])} = {p['fp']} | {p['status']}' "
                     f"({num(12.7)} {num(snap(ytext + shift))});")
        for i, ref in enumerate(p['refs']):
            # the refdes MUST be quoted: an unquoted R1 parses as orientation
            # "rotate 1 degree" -> "Non orthogonal orientations can only be
            # used in a board or footprint!" (any R<digits> name collides)
            lines.append(f"ADD {p['dsn']}@{libref} '{ref}' "
                         f"({num(snap(15.24 + i * 33.02))} {num(snap(ypart + shift))});")
    lines += ["GRID LAST;", "WINDOW FIT;"]
    open(scr_out, 'w').write('\n'.join(lines) + '\n')

    nparts = sum(len(p['refs']) for p in plan)
    print(f"wrote {lbr_out}  ({len(packages)} packages, {len(symbols)} symbols, "
          f"{len(devicesets)} devicesets; XML well-formed)")
    print(f"wrote {scr_out}  ({nparts} parts in {len(plan)} roll-call groups)")
    for p in plan:
        print(f"  {','.join(p['refs']):28s} {p['fp']:28s} {p['status']}")
    print(f"""
NEXT STEPS (this script only generates files -- Eagle does the placing):
  1. In Eagle, open your schematic on a BLANK sheet.
  2. File > Execute Script...  ->  {scr_out}
     Expect {nparts} parts in {len(plan)} commented rows, then WINDOW FIT.
  3. Read each row's provenance comment:
       - 'rcl guess' symbols: verify the 2-pin connect map against the board.
       - 'PLACEHOLDER' symbols: replace with a curated symbol + hand-verified
         pin->pad map (step 1B).
  4. Wire the schematic by hand (step 1C) -- a PCBmodE repo has no netlist.
Re-running? ADD collides with existing part names: run only on a blank sheet
(close without saving, or delete all parts first).""")


if __name__ == '__main__':
    main()
