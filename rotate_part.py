#!/usr/bin/env python3
"""
rotate_part.py -- rotate one part in place by editing the .brd XML (Step 5C).

If you discover a part is reversed on the board after the copper is in, you
can't just rotate it in Eagle: you'd either have to delete the traces (losing
the faithful arcs) or drag them along with the rotation (making a mess). The
traces are the faithful artifact -- the PART must rotate under them. In the
.brd XML a rotation is just the element's `rot` attribute; signal wires are
separate elements and are untouched by construction. Smashed >NAME/>VALUE
labels are transformed along with the part.

The rotation is about the element's own origin. For a 2-pin part whose pads
are symmetric about the origin, +180 exactly exchanges the pads -- the
"sitting backwards" fix. Run RATSNEST in Eagle afterward to see the effect on
airwires.

Usage:
    python3 rotate_part.py "<board.brd>" REF DEGREES        # rotate BY degrees (CCW)
    python3 rotate_part.py "<board.brd>" REF DEGREES --set  # set rotation TO degrees

Branch the board first; Eagle closed; afterward File > Reload.
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from brdlib import element_span, read_placement, write_placement, num


def main():
    ap = argparse.ArgumentParser(description="Rotate one part in place in the .brd XML")
    ap.add_argument('board', help="the .brd to edit (branch it first)")
    ap.add_argument('ref', help="part refdes (e.g. R1)")
    ap.add_argument('degrees', type=float, help="degrees CCW (relative by default)")
    ap.add_argument('--set', action='store_true', help="treat DEGREES as the absolute rotation")
    a = ap.parse_args()

    board_path = os.path.expanduser(a.board)
    if not os.path.isfile(board_path):
        sys.exit(f"board not found: {board_path}")
    text = open(board_path).read()
    span = element_span(text, a.ref)
    if span is None:
        sys.exit(f"no element '{a.ref}' on the board")
    block = text[span[0]:span[1]]
    x, y, odeg, mir = read_placement(block)
    ndeg = a.degrees if a.set else odeg + a.degrees
    newblock = write_placement(block, x, y, ndeg, mir)
    text = text[:span[0]] + newblock + text[span[1]:]

    import xml.dom.minidom as MD
    MD.parseString(text)
    open(board_path, 'w').write(text)
    print(f"{a.ref}: R{num(odeg)} -> R{num(ndeg % 360)} at ({num(x)}, {num(y)})"
          + (" (mirrored)" if mir else ""))
    print("Traces untouched (signal wires are separate XML). "
          "NEXT: File > Reload in Eagle, then RATSNEST.")


if __name__ == '__main__':
    main()
