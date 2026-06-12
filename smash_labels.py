#!/usr/bin/env python3
"""
smash_labels.py -- generate an Eagle script that SMASHes every part so its
NAME (and VALUE) labels become visible, movable tName/bName text.

A freshly-placed board shows parts but is hard to read: the refdes labels sit
at the package default and often hide under the body. SMASH-ing a part detaches
its >NAME / >VALUE text so Eagle renders it at the position defined in the
library package (the generated packages put >NAME on tNames at a small offset
in the part's local +Y, so after the element's rotation it lands consistently
beside each part) -- and you can then nudge any that overlap.

This emits one `SMASH '<ref>';` per part, read from the master <board>.json
`components`. SMASH maps top parts to tNames (25) and bottom parts to bNames
(26) automatically, and is board-only (no schematic annotation touched).

Usage:
    python3 smash_labels.py <repo-dir> [-o out.scr]

<repo-dir> is the Boldport repo (the master <board>.json is found by content,
the root json holding both `components` and `outline` -- same rule as
roll_call.py / make_place_scr.py). A direct path to the master .json works too.

Then, in the Eagle BOARD editor (after placement): File > Execute Script...
To move a label afterward: MOVE the >NAME text, or SMASH is reversible per
part (smashing again re-attaches).
"""
import argparse, glob, json, os, sys


def find_master(path):
    """Accept a repo dir or a direct master.json path; return the master path.
    The master is the repo-root json holding both `components` and `outline`."""
    path = os.path.expanduser(path.rstrip('/\\'))
    if os.path.isfile(path):
        return path
    cands = [p for p in sorted(glob.glob(os.path.join(path, "*.json")))
             if _is_master(p)]
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


def refkey(r):
    return (''.join(c for c in r if not c.isdigit()),
            int(''.join(c for c in r if c.isdigit()) or 0))


def main():
    ap = argparse.ArgumentParser(
        description="PCBmodE master JSON -> Eagle script that SMASHes every part "
                    "(exposes NAME/VALUE labels)")
    ap.add_argument('master', help="Boldport repo dir (master json found by content) "
                                   "or a direct path to the master <board>.json")
    ap.add_argument('-o', '--out', help="output .scr (default: smash-<board>.scr beside the JSON)")
    a = ap.parse_args()

    master_path = find_master(a.master)
    master = json.load(open(master_path))
    comps = master['components']
    board = os.path.basename(master_path).rsplit('.json', 1)[0]
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(master_path)), f"smash-{board}.scr")
    print(f"master: {master_path}")

    lines = [
        f"# SMASH every {board} part so its NAME/VALUE labels show as movable tName/bName text.",
        "# Run from the .brd (after placement):  Board > File > Execute Script...",
        "GRID MM;",
    ]
    for ref in sorted(comps, key=refkey):
        lines.append(f"SMASH '{ref}';")
    lines.append("GRID LAST;")

    open(out, 'w').write('\n'.join(lines) + '\n')
    print(f"wrote {out}  (SMASH for {len(comps)} parts)")
    print(f"""
NEXT STEP: in the Eagle BOARD editor (parts already placed),
File > Execute Script...  ->  {out}
Each part's refdes appears as tName/bName text beside it; MOVE any that overlap.""")


if __name__ == '__main__':
    main()
