#!/usr/bin/env python3
"""
increment_version.py -- branch a board to its next revision, sidecars and all.

A ported board is a FAMILY of files sharing one stem ("<name> rN.M"): the .brd,
the .sch, and the sidecar .json files that keep the tools honest (.transform,
.inject, .seeds-*, .decor). Work on these boards proceeds by making a
next-version copy of the whole family before each step, so anything that
corrupts the relationship between the files can be reverted by going back to
the most recent stable revision.

Copying only SOME of the family is a trap: a branch without its .inject.json,
for example, makes the next silk/outline re-run silently stack new geometry on
top of the old instead of replacing it.

Usage:
    python3 increment_version.py "<name rN.M.brd>"            # -> rN.M+1
    python3 increment_version.py "<name rN.M.brd>" --to r2.0  # explicit target

Skips Eagle's own backup files (containing '#') and stale .airwires.txt exports
(re-export those per revision: RATSNEST + export_airwires.ulp).
"""
import argparse, glob, os, re, sys


def main():
    ap = argparse.ArgumentParser(description="Branch a board family to its next revision")
    ap.add_argument('board', help="the current .brd (e.g. \"myboard r1.1.brd\")")
    ap.add_argument('--to', help="target revision (e.g. r2.0; default: minor + 1)")
    a = ap.parse_args()

    board = os.path.expanduser(a.board)
    if not os.path.isfile(board):
        sys.exit(f"board not found: {board}")
    d = os.path.dirname(os.path.abspath(board))
    base = os.path.basename(board)
    m = re.match(r'(.+ r)(\d+)\.(\d+)\.brd$', base)
    if not m:
        sys.exit(f"'{base}' does not match the '<name> rN.M.brd' convention")
    prefix, major, minor = m.group(1), int(m.group(2)), int(m.group(3))
    old_stem = f"{prefix}{major}.{minor}"
    if a.to:
        tm = re.match(r'r(\d+)\.(\d+)$', a.to)
        if not tm:
            sys.exit("--to must look like rN.M")
        new_stem = f"{prefix}{tm.group(1)}.{tm.group(2)}"
    else:
        new_stem = f"{prefix}{major}.{minor + 1}"

    copied, skipped = [], []
    for src in sorted(glob.glob(os.path.join(d, old_stem + ".*"))):
        suffix = os.path.basename(src)[len(old_stem):]
        if '#' in suffix or suffix.endswith('.airwires.txt'):
            skipped.append(suffix)
            continue
        dst = os.path.join(d, new_stem + suffix)
        if os.path.exists(dst):
            sys.exit(f"refusing to overwrite existing {os.path.basename(dst)} -- "
                     f"is {new_stem} already in use?")
        with open(src, 'rb') as fi, open(dst, 'wb') as fo:
            fo.write(fi.read())
        copied.append(suffix)

    print(f"{old_stem}  ->  {new_stem}")
    for s in copied:
        print(f"  copied {s}")
    for s in skipped:
        print(f"  skipped {s} (regenerate per revision)")
    if not any(s == '.sch' for s in copied):
        print("WARNING: no .sch in the family -- the board/schematic link needs both.")
    print(f"\nWork on \"{new_stem}.brd\" now; \"{old_stem}.brd\" is the revert point.")


if __name__ == '__main__':
    main()
