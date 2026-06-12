#!/usr/bin/env python3
"""
strip_stitches.py -- remove all 6-mil copper wires (airwire stitches) from a .brd
for a clean restart of the stitching pass.

The airwire-resolution stitches (and equivalent hand-fixes) are 6-mil (0.1524 mm)
wires on copper layers 1/16. Boldport traces are wider, so width is a clean
discriminator. This strips every 6-mil wire on layers 1 and 16 and leaves
everything else untouched, reporting a per-layer count first so you can
sanity-check (a board whose REAL traces include 6-mil work is not strippable
this way -- check the report before trusting it).

NOTE: removes stitch-type fixes only. Arc-ladder splits (sub-arcs at the original
wide width) and snapped/moved endpoints are NOT undone -- for pristine geometry
go back to the pre-stitch branch of the board.

Usage:
    python3 strip_stitches.py "<board.brd>"     # branch first; Eagle closed
"""
import re, os, sys
from collections import Counter

W6, TOL, COPPER = 0.1524, 0.0006, ("1", "16")
if len(sys.argv) < 2:
    sys.exit("usage: strip_stitches.py <board.brd>")
BRD = os.path.expanduser(sys.argv[1])
if not os.path.exists(BRD):
    sys.exit("no such board: %s" % BRD)
brd = open(BRD).read()

def parse(w):
    wd = float(re.search(r'width="([\d.]+)"', w).group(1))
    lay = re.search(r'layer="(\d+)"', w).group(1)
    return wd, lay

# report all 6-mil wires by layer (so anything outside 1/16 is visible too)
by = Counter()
for w in re.findall(r'<wire [^>]*/>', brd):
    wd, lay = parse(w)
    if abs(wd - W6) < TOL:
        by[lay] += 1
print("6-mil wires in %s by layer: %s" % (os.path.basename(BRD), dict(by)))
off = {l: n for l, n in by.items() if l not in COPPER}
if off:
    print("  (NOT removing %s -- only layers 1/16 are stripped)" % off)

removed = [0]
def repl(m):
    w = m.group(0).lstrip("\n \t")
    wd, lay = parse(w)
    if abs(wd - W6) < TOL and lay in COPPER:
        removed[0] += 1
        return ""
    return m.group(0)
new = re.sub(r'\n?[ \t]*<wire [^>]*/>', repl, brd)

import xml.dom.minidom as MD
MD.parseString(new)                          # validate before writing
open(BRD, 'w').write(new)
print("removed %d six-mil copper wires (layers 1/16)." % removed[0])
print("remaining <wire> count: %d (was %d)" % (new.count('<wire '), brd.count('<wire ')))
