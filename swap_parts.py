#!/usr/bin/env python3
"""
swap_parts.py -- exchange the placements of two parts by editing the .brd XML
(Step 5D).

When the repo placed two parts in each other's positions, exchange their
(x, y, rotation) in the XML -- the traces are the faithful artifact and stay
put; the parts move under them. Signal wires are separate XML elements and
are untouched by construction; smashed >NAME/>VALUE labels travel with their
parts.

The swap is exact: each part takes the other's position AND rotation (and
mirror). If a swapped pair also needs reorienting (e.g. mirror-image
footprints whose pads only line up after a half-turn), compose with
rotate_part.py afterward. Run RATSNEST in Eagle to see the effect.

Usage:
    python3 swap_parts.py "<board.brd>" REF1 REF2

Branch the board first; Eagle closed; afterward File > Reload.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brdlib import element_span, read_placement, write_placement, num


def main():
    ap = argparse.ArgumentParser(description="Exchange two parts' placements in the .brd XML")
    ap.add_argument('board', help="the .brd to edit (branch it first)")
    ap.add_argument('ref1', help="first part refdes (e.g. D1)")
    ap.add_argument('ref2', help="second part refdes (e.g. R1)")
    a = ap.parse_args()

    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    text = open(board_path).read()

    spans = {}
    for ref in (a.ref1, a.ref2):
        s = element_span(text, ref)
        if s is None:
            sys.exit(f"no element '{ref}' on the board")
        spans[ref] = s

    p1 = read_placement(text[spans[a.ref1][0]:spans[a.ref1][1]])
    p2 = read_placement(text[spans[a.ref2][0]:spans[a.ref2][1]])
    if p1[3] != p2[3]:
        print("WARNING: the parts differ in mirror state -- label positions for the "
              "mirror change are not transformed; eyeball them after reload.")

    # rewrite later-in-file first so the earlier span's offsets stay valid
    for ref, target in sorted(((a.ref1, p2), (a.ref2, p1)),
                              key=lambda rp: spans[rp[0]][0], reverse=True):
        s = spans[ref]
        block = text[s[0]:s[1]]
        nx, ny, ndeg, nmir = target
        text = text[:s[0]] + write_placement(block, nx, ny, ndeg, nmir) + text[s[1]:]

    import xml.dom.minidom as MD
    MD.parseString(text)
    open(board_path, 'w').write(text)
    print(f"swapped {a.ref1} <-> {a.ref2}:")
    print(f"  {a.ref1}: ({num(p1[0])}, {num(p1[1])}) R{num(p1[2])} -> ({num(p2[0])}, {num(p2[1])}) R{num(p2[2])}")
    print(f"  {a.ref2}: ({num(p2[0])}, {num(p2[1])}) R{num(p2[2])} -> ({num(p1[0])}, {num(p1[1])}) R{num(p1[2])}")
    print("Traces untouched. NEXT: File > Reload in Eagle, then RATSNEST; compose with "
          "rotate_part.py if the swapped pair also needs reorienting.")


if __name__ == '__main__':
    main()
