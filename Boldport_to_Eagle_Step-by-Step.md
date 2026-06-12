# Boldport → Eagle: Step-by-Step

A how-to for porting a Boldport (PCBmodE) board into Eagle CAD with faithful
curves and art, as well as a linked schematic, and the ability to run Eagle CAM to produce
valid Gerber files.

## Overview of the Boldport → Eagle Script Library and Board Structure

Boards designed for PCBmodE (https://boldport.com/pcbmode, https://github.com/boldport/pcbmode)
are defined by a series of json files for components, shapes, copper pours, etc.

Eagle boards are defined by a .sch (the schematic) and a .brd (the board design).


**Boldport board anatomy (what to read out of a Boldport project)**

```
<board>.json                 master: outline, components{footprint,location,rotate},
                             distances{from-pour-to, soldermask}, refs to routing
<board>_routing.json         routes.top / routes.bottom — each net is an SVG path + stroke-width
shapes/silkscreen.json       silk shapes: type=path (layers top/bottom) + type=text
components/<fp>.json          footprints: pins{location,pad,rotate}, pads{shapes,drills},
                             layout.silkscreen.shapes (incl. pin labels!), location offset
components/hole-Nmm.json      mounting holes (buffer-to-pour clearance lives here)
```

In part this project is a library of Python scripts that port those geometries into those
Eagle layers as faithfully as possible.

The resulting structure is an Eagle board with a few sidecar files that record the
geometric transform that maps the json files to the .brd, and the state the scripts need
to make their changes safely repeatable. The structure becomes:

**Boldport Eagle-ported board anatomy**
```
<board> rN.M.brd                      the Eagle board
<board> rN.M.sch                      the linked Eagle schematic
<board> rN.M.brd.transform.json       the OX/OY frame offsets chosen at placement --
                                      every later tool reads the transform from here
<board> rN.M.brd.inject.json          manifest of script-injected geometry, so re-runs
                                      replace their own work instead of stacking it
<board> rN.M.brd.seeds-<side>.json    human overrides for net binding (routes geometry
                                      could not decide)
<board> rN.M.brd.decor.json           decorative-copper designations for the pour
                                      (e.g. a logo and its cutout style)
```

When dealing with these boards it is easiest to make a next-version copy of ALL the files,
so that any change that corrupts the relationship between them can be reverted by going
back to the most recent stable revision. `increment_version.py` does exactly that:

```
python3 increment_version.py "<board> rN.M.brd"            # copies the whole family to rN.M+1
python3 increment_version.py "<board> rN.M.brd" --to r2.0  # or an explicit target revision
```

It refuses to overwrite an existing revision, and skips Eagle's own backup files and stale
airwire exports (regenerate those per revision). Branching only *some* of the family is a
bad practice: a branch missing its `.inject.json`, for example, causes the next silk or outline
re-run to silently stack new geometry on top of the old instead of replacing it.


## Overview of Fabrication

To fabricate a PCBmodE board, one uses a Python script that converts the json files to
Gerber files. (This library includes its own such writer, `make_gerber.py`, which renders
the published repo with true arcs — used as the fidelity reference when verifying a port,
not as the production path.)

To fabricate an Eagle board, one uses a CAM job that converts the .brd into Gerber files
(the fab's stock 2-layer job — JLCPCB's or OSHPark's — works; see Step 9).

In order to exert the kind of creative control of the .brd offered by PCBmodE, one needs
to pay attention to the geometry on many layers: the ground pour (Step 6), the solder mask
stop (Step 8), and the copper pad shapes in the footprints of the parts (Step 1), to name
a few.

When it comes to fabricating the .brd, it is important to use a specific DRC which
minimizes the use of Eagle to calculate things like the thermals, the tenting of vias, and
the solder mask stop width, all of which are carefully ported into the board itself using
the scripts in this library. The toolkit's `PCBmodEagle.dru` carries these settings
(Stop frame pinned to 0.1 mm min = max, thermal isolation 0); load it before running DRC
or CAM, or verify the same settings in whatever DRC you use.


## Dependencies

Python deps (installed `pip install --user`): `shapely`, `numpy`, `scipy`,
`matplotlib`, `svgpathtools`.

**Core libraries**

| File | Role |
|---|---|
| `arcfit.py` | Fit SVG cubic Béziers → **circular arcs** (Eagle `<wire curve=…>` / `<vertex curve=…>`). `fit_path(value, tol)` → list of `(verts, closed)`; each vert is `(x, y, curve_deg)`. This is what makes the curves faithful instead of faceted. |
| `svgfont.py` | `SvgFont` — lays out glyphs from an SVG font (`fonts/GoodDog-Regular-webfont.svg`) for the wordmark text. |
| `flatten.py` | `adaptive_flatten(value, tol)` — Bézier → line segments, for shapely geometry (pour, decor); arcfit is for the faithful output geometry. |
| `brdlib.py` | Shared .brd `<element>` placement editing (read/write x/y/rot, smashed-label transforms) used by `rotate_part.py` / `swap_parts.py`. |
| `gerber.py` / `excellon.py` | Direct RS-274X writer with true G02/G03 arcs + X2 attributes, and the drill writer — used by `make_gerber.py` (Eagle CAM linearizes arcs; this doesn't). |
| `increment_version.py` | Branch a board to its next revision — copies the whole file family (.brd, .sch, all sidecars), skips Eagle backups and stale airwire exports. Run it before every step. |

---

## 1. Start a new Eagle schematic and add all the parts to it

The difference between a PCBmodE board and an Eagle board, from an Eagle perspective, is
the presence of a schematic that defines the electrical connections. This will allow Eagle
to do error checks that can't be done in PCBmodE, and allow Eagle to do a GND pour that
respects the non-GND pads on the board, among other advantages. This is the way we "make
an Eagle board an Eagle board."

For each part in the Boldport repo "components" directory, start by adding a similar part
to the schematic. For example, for a resistor, add R-US from the Eagle rcl library or some
similar parts library you like. Alternatively, use the step 1A `roll_call.py` script below
to add placeholder parts to the schematic for each part in the components directory.

For each of these parts edit the footprint to match that of the components/<fp>.json that
corresponds. Typical modifications would be to lay a copper polygon over the pad to give
the rounded-square shape, or one-rounded-corner shape, or whatever shape of the pad was
creatively used by Boldport.

Next, make sure any shapes that help visualize the layout of the part, such as the shape
of a header, or the outline of the body of a diode, go on a layer other than the silkscreen layer,
since Boldport will provide a custom silkscreen. The layers tDocu and bDocu can be used
for the shapes of the parts, and tPlace can be used for the standard Eagle part labels.
These two layers can then be excluded from the CAM silkscreen output.

It is helpful to put copper polygons on even the pads that have standard Eagle shapes.
This helps with a consistent look in the board editor.

### 1A. The roll call — `roll_call.py` (toolkit)

This is a way to get started with the schematic more quickly, by getting all the parts
pulled out of the Boldport repository and into a custom library for the board, drafting
footprints for those parts based on the footprints in the repository, and adding these
draft parts to the schematic with instructions on how to continue editing the drafts to
make them more usable for the project (more on that in 1B below).


```
py roll_call.py <repo-dir> "<schematic path>"     # then IN EAGLE: File ▸ Execute Script… the .scr
```

For example, for a port of The Cuttle, one could create a schematic (with .brd) called `the-cuttle-test r0.1.sch` and run:

```
py roll_call.py ..\..\Boldport-masters\thecuttle "..\the-cuttle-test\the-cuttle-test r0.1.sch"

or, on a Mac (from the toolkit directory):

python3 roll_call.py ../../Boldport-masters/thecuttle "../the-cuttle-test/the-cuttle-test r0.1.sch"
```

Running the script creates two files:
- **`<libname>.lbr`** (→ `~/Dropbox/EAGLE/libraries/`) — one deviceset per (refdes-prefix,
  footprint) group. **Footprints are the real Boldport footprints** (full pad treatment from the repo
  JSON: copper polygons both sides, rounded-rect composites, stopmask, tDocu-only body).
  The symbols are stand-ins — 2-pin R/C parts borrow R-US/C-EU from stock rcl (safe guess, map flagged
  UNVERIFIED); everything else gets an honest placeholder box symbol with pins named from
  the pad keys (U1's placeholder reads `1-PC6`, `2-PD0`, … — self-documenting for step 1B).
  The refdes prefix is the repo's ONLY part-type semantic: footprints shared across
  prefixes (The Cuttle uses the same footprint for D1 and R1) are created as separate
  parts per prefix so they can diverge later (R1 and D1 need different symbols in Eagle
  schematics, even if the footprint is the same).
- **`roll-call-<libname>.scr`** (→ written to the same project folder as the .sch given on
  the command line) — places one helpful TEXT comment on the schematic per part *type* (one
  comment for C1–C7, not seven) and does one ADD per refdes (so it places seven caps, C1–C7,
  as defined in the Boldport repository), in a grid, ending in WINDOW FIT. Run it on a
  **blank** .sch (typically the one given on the roll-call script's command line) because
  re-runs collide with existing part names.


### 1B. Replace guessed/placeholder symbols with curated ones (per part)
After the roll call, replace as necessary the placeholder symbols with ones that will make sense to the reader, and hand-verify each pin→pad map.

### 1C. Wire the schematic by hand
The repo has no netlist, and you'll want to understand the circuit anyway. (Note: `raster_netlist.py` recovers a netlist from the routing copper as a crib sheet, and
`sync.py` checks your wiring against it, but this is not the same as having a well-laid-out schematic drawing.)

**Eagle script-language lessons (cost a day; don't relearn):**
- **`USE 'file.lbr'` does NOT make a library ADD-able.** `ADD dev@libname` resolves
  against Eagle's configured library *directories* (Options ▸ Directories ▸ Libraries).
  Put the .lbr in one of those; then the bare library name works.
- **Always quote the part name in ADD.** Unquoted `R1` parses as an *orientation* token —
  "rotate 1°" — giving "Non orthogonal orientations can only be used in a board or
  footprint!" Any `R<digits>` refdes collides; `'R1'` is safe.
- A script halts at its first error: parts before the bad line ARE placed, and if
  `WINDOW FIT` never ran, placed TEXT may sit far outside the visible frame — zoom out
  before concluding "nothing happened."

## 2. Place the parts at the repo's positions — `place.py`

When you create the board from the schematic, Eagle drops every part in a default pile in
the −x/−y quadrant. `place.py` writes each part's final position and rotation **directly
into the `.brd`**:

```
python3 place.py <repo-dir> "<board.brd>"     # then open (or reload) the board in Eagle
```

(`<repo-dir>` is the Boldport repo; the master `<board>.json` is found by content — the
root json holding both `components` and `outline`, same rule as `roll_call.py`. A direct
path to the master `.json` works too.) **Branch the board first** (e.g. r0.1 → r0.2) — as
before every step — and have Eagle *closed on that file*, since this writes it.

**Where the numbers come from:** the **master** `<board>.json` → `components[]`, each entry
carrying `footprint`, `location`, `rotate`. (Not `components/<fp>.json` — those are
footprint-local pin/pad geometry; the board-level placement lives only in the master.)
Each part's `<element>` gets its `x`/`y`/`rot` set through the coordinate transform:

```
x_brd  =  x_repo + OX
y_brd  = -y_repo + OY
rot    = -rot_repo          # e.g. repo 90 → Eagle R270
```

No mirror for top-layer parts — the footprints already bake in the y-flip (Step 1), so a
plain rotate lands the pins. Bottom-layer parts are mirrored (MR) and flagged to eyeball.
Smashed `>NAME`/`>VALUE` labels are transformed along with their part (rotated about the
element origin), so they follow it.

**Why edit the `.brd` directly** (rather than generate an Eagle MOVE/ROTATE script): a
part's position and rotation are **board-only data** — they don't touch the schematic,
netlist, or forward/back annotation (Eagle writes exactly this `x/y/rot` every time you
drag a part). So writing it ourselves is identical and safe, and it sidesteps every
Eagle-script quirk: no move-sweep no-ops, no free-edition "can't ROTATE in the −x/−y
quadrant," no warnings. The part is simply written where it belongs, in any frame. (This
is the same direct-`.brd` approach as Steps 3, 4, 6, 7. The earlier Eagle-script tool
`make_place_scr.py` is retired.) Output is minidom-validated before writing; the file's
header/DOCTYPE and every untouched element are preserved byte-for-byte.

**Choosing OX/OY — per board, not a constant.** A PCBmodE master is centred on the origin,
so the board must shift into the positive quadrant — and *how far* depends on the board's
size. By default `place.py` computes it from the outline bbox plus every part location:

```
OX ≥ margin − xmin_source     # the lowest source x sets the rightward shift
OY ≥ margin + ymax_source     # the HIGHEST source y sets the upward shift —
                              # the y-flip turns the source's largest y into Eagle's lowest
```

both rounded **up** to a multiple of 2.54 mm so any repo-side grid alignment survives.
Default margin 5 mm (`--margin`), or set offsets explicitly with `--ox/--oy`. Auto mode
keeps the board inside the free edition's legal area; it's the right default. The chosen
transform is printed and saved to a sidecar `<board>.brd.transform.json`, because **every
later step (outline, copper, silk, pour) must use the same offsets** or nothing will
register.

Because the parts arrive pre-smashed (Eagle smashes them when the board is built from the
schematic), `place.py` already carries the `>NAME`/`>VALUE` labels into position — the
board is readable straight away, no separate label step needed. *(`smash_labels.py` in the
toolkit emits a `SMASH '<ref>';` per part for the case where a board's parts are **not**
smashed and you want their package-defined labels exposed.)*

**Branching rule — sidecars travel with the board.** Every later tool keys its state to
the board's filename: `.transform.json` (frame), `.inject.json` (what's removable on
re-run), `.seeds-<side>.json` (binding overrides), `.decor.json` (pour art). When you
branch rN → rN+1, **copy all of them** under the new name — a missing `.inject.json`
makes a re-run *stack* new geometry on top of the old instead of replacing it (silently:
the output looks unchanged because the old geometry is still underneath).

**Important:** `place.py` places parts at the repo's **published** positions. Some of those
are wrong (the repo predates Boldport's final cleanup) — but you don't know that yet at
this stage. The corrections (C1/C2 bump, C6↔C7, D1↔R1, J1↔J2 reflections/swaps) are
deliberately a *later* step (5E), discovered when the copper goes in and the airwires
won't resolve. No need to worry about this yet, the geometry and error checks with tell you.

## 3. Board outline → Dimension layer (20) — `inject_brd.py dimension`

```
python3 inject_brd.py <repo-dir> "<board.brd>" dimension     # branch the board first; Eagle closed
```

For example, on macOS:
```
cd ~/Dropbox/EAGLE/projects/Boldport/the-cuttle2
# branch: the board, the schematic, and ALL sidecars (see the branching rule, Step 2)
for ext in brd sch brd.transform.json brd.inject.json brd.decor.json brd.seeds-bottom.json; do
  cp "the-cuttle2 r0.2.$ext" "the-cuttle2 r0.3.$ext" 2>/dev/null
done
python3 ../boldport-eagle-toolkit/inject_brd.py ../../Boldport-masters/thecuttle "the-cuttle2 r0.3.brd" dimension
```

Reads `master['outline']['shape']['value']` (one SVG path), `arcfit.fit_path(d, tol=0.02)`,
applies the coordinate transform **with the curve-sign negation**, and emits a `<wire layer="20"
width="0.0" curve="…">` chain into `<plain>`. It also **removes Eagle's default board-outline
rectangle** (the (0,0)-anchored 4-wire box Eagle draws when a board is created), so the
Boldport outline is the only one left. On The Cuttle: 67 wires (55 arcs + 12 lines).

**How `inject_brd.py` manages change (applies to Steps 3 *and* 4):**
- **Reads the frame from the sidecar.** It loads OX/OY from `<board>.brd.transform.json`
  (written by `place.py`), so injected geometry uses the **same frame as the placed parts** —
  no hardcoded offsets. (`--ox/--oy` override if there's no sidecar.)
- **Idempotent via a manifest** (`<board>.brd.inject.json`, beside the board): re-running a
  feature removes only the elements *it* previously injected (exact string match), then adds
  the fresh set. Hand-drawn additions are never touched — additive, never replace-all.
- **Edits the `.brd` directly** (like `place.py` and Steps 6, 7), minidom-validated, header
  and all other elements preserved byte-for-byte. Branch the board first and have Eagle
  closed; afterward, open (or File ▸ Reload) it.


## 4. Copper traces → layers 1 (top) / 16 (bottom) — `inject_brd.py top_copper / bottom_copper`

```
python3 inject_brd.py <repo-dir> "<board.brd>" top_copper
python3 inject_brd.py <repo-dir> "<board.brd>" bottom_copper
```

From `<board>_routing.json`: `routes['top']` (44 routes) → layer 1, `routes['bottom']`
(19 routes) → layer 16. Each route's `value` is the trace **centerline**; arc-fit it
(tol 0.02), apply the coordinate transform, and emit a chain of stroked `<wire>`s whose width is
the route's own `stroke-width` — **SVG stroke-width maps 1:1 to Eagle wire width in mm**
(default 0.25 when absent).

- This injects **FREE copper** — geometry only, no `<signal>` binding. Making it electrical
  is Step 5A (`bind_copper.py`); don't conflate the two.
- **Bottom uses the SAME transform as top — no mirror.** THT pads share x,y on both sides
  and Eagle x-rays L16 from the top. Confirmed empirically ("bottom copper aligns cleanly
  with the pads"); the board also has no vias.

## 5. Make it electrical

### 5A. Bind the free copper to nets — `bind_copper.py`

Based on the position of the free copper traces, each PCBmodE route can be assigned to a
net from the wired schematic, turning free geometry into routed `<signal>`s that Eagle
will net-check and ratsnest.

```
python3 bind_copper.py <repo-dir> "<board.brd>" report top      # dry run: what binds where
python3 bind_copper.py <repo-dir> "<board.brd>" apply  top      # move wires into <signal>s
python3 bind_copper.py <repo-dir> "<board.brd>" report bottom   # then the other side
python3 bind_copper.py <repo-dir> "<board.brd>" apply  bottom
```

Branch the board first; Eagle closed; afterward open it and run `RATSNEST`.

**The model — a net can be a TREE**, branching like suburban streets rather than running
point-to-point. A trace end therefore legitimately lands on *another trace* (a T-junction)
or floats into the coming pour (a thermal), not always on a pad. Which nets (if any) have
this shape varies by board; the algorithm handles both. Therefore:

- work **per route** (the PCBmodE paths), never merge routes into groups;
- bind a route to the net of whichever **end hits a pad** (tightest wins; pad geometry
  comes from the board's own embedded libraries, nets from the signals' contactrefs);
- a route with no pad end whose two ends' *nearest* pads agree on a net takes that net
  (crystal clusters, power stubs);
- remaining routes inherit by **T-junction majority vote** (bidirectional, propagated
  until stable);
- **trust Eagle's DRC/ratsnest to flag wrong binds** — don't try to be perfect up front.

**Seeds** are the human's authoritative overrides for the cases geometry can't decide
("lands on" vs "passes over"). Sticky in `<board>.brd.seeds-<side>.json`:

```
... report <side> ROUTEKEY=NET       # bind that route to NET, locked
... report <side> MILX,MILY=NET      # same, route picked from an Eagle mil readout
... show --suspect                   # copy non-pad-bound traces onto layers 200+ so you
                                     # can toggle them in Eagle, then answer: L<layer>=NET
```

`apply` gathers every wire on the side's layer — free *and* already-bound — so re-runs
self-correct; `show` layers are cleaned up automatically. The offsets come from the
board's `.transform.json` sidecar.

On The Cuttle: all 44 top routes bind by pad ends alone; 18 of 19 bottom routes bind
(13 are the hand-drawn GND thermal spokes). The one holdout is the decorative B-logo
sprawl (`01ebecddc`), which touches no pad — a human call. It belongs to GND: binding it
there means the GND pour treats it as own-net copper and **merges into it** instead of
clearing a buffer moat around it (the pour-net's own traces are unbuffered — PCBmodE's
`buffer-to-pour: 0` convention). Expect the **ratsnest after binding to be the detector**
for the repo's placement errors — that's the next step.

### 5B. Airwire resolution — `export_airwires.ulp` + `airwire_stitch.py`

After binding, `RATSNEST` shows airwires: places where Eagle does not consider the
same-net copper connected, even though it visually is. Eagle has very specific fusing
rules (each cost a debugging round to learn):

1. **Fuse = shared endpoint OR true body crossing.** A thin wire that merely *ends on*
   another wire's body (a dangling T) does **not** fuse; neither does stitching "past" an
   arc. Splitting a wire adds no connection by itself.
2. **THT pads connect at the CENTER node, not the ring.** Overlapping the annular ring is
   not enough; a wire must reach the pad's center node. (The drill hole is irrelevant —
   a wire ending at the center connects even though the center is physically a hole.)
3. **Minimum fuse length (~1–2 mil).** A sub-fuse-length wire collapses to nothing and
   makes no connection — close tiny gaps by **moving** an endpoint onto the node, never
   with a short stitch.

The iterative workflow (each pass changes connectivity, so later passes reach more):

```
in Eagle:  RATSNEST                          # make the airwires current
           RUN export_airwires.ulp           # writes <board>.airwires.txt (mm, net last)
                                             # (the ULP dialog's count can render garbled -- trust the file)
python3 airwire_stitch.py "<board.brd>" [--preview]
in Eagle:  File ▸ Reload, RATSNEST, re-RUN the ULP — repeat until only genuine
           pour/routing gaps remain
```

Branch before the first pass. **Always re-export after a reload** — a stale airwires
file silently mis-resolves (the script warns when the export is older than the board).

For each airwire (whose two ends are Eagle nodes), the script applies in order: **snap**
(sub-fuse-length gap → move the loose endpoint onto the node), **stitch** (gap hidden
under same-net copper → 6-mil node-to-node wire), **pad-rescue** (endpoints inside a pad
moved/stitched to its center node, both pad ends), **arc-ladder** (split a host wire at
the touch point — sub-arcs stay on the same circle — then snap onto the new node, a true
3-way junction), **local node-to-node** (a hidden short hop to a nearby node), and
finally **LOG** — left to the human, with the target node named. Geometry edits move
endpoints by at most a few mil, within the Bézier→arc conversion error bars.

What stays human, by design: GND thermals reaching for a pour that doesn't exist yet
(Step 6 resolves those), true routing gaps (a same-net pad pair with no trace), and
hidden node-to-node paths that must weave under fat copper. `strip_stitches.py
"<board.brd>"` removes all 6-mil copper stitches for a clean restart (it reports per
layer first and touches only layers 1/16 — tDocu body outlines are also 6-mil and are
left alone).

*Worked example (The Cuttle, original port): 83 airwires → 6 in a single pass
(41 stitches + 63 pad-rescues + 1 arc-ladder); the remaining 6 were genuine pour/routing
decisions.*

### 5C. Rotate a reversed part under its traces — `rotate_part.py`

If you discover a part is reversed on the board at this point, you can't just rotate it in
Eagle without either deleting the traces (and losing the faithful arcs) or dragging the
traces along with the rotation (making a mess). The traces are the faithful artifact — the
**part** must rotate under them, programmatically, in the `.brd` XML, where a rotation is
just the element's `rot` attribute and signal wires are separate elements untouched by
construction.

```
python3 rotate_part.py "<board.brd>" R1 180          # rotate R1 BY 180° (CCW)
python3 rotate_part.py "<board.brd>" R1 90 --set     # set R1's rotation TO 90°
```
For example, on macOS:
```
python3 ../boldport-eagle-toolkit/rotate_part.py "the-cuttle2 r0.6.brd" R1 180
R1: R0 -> R180 at (28.075, 23.443)
Traces untouched (signal wires are separate XML). NEXT: File > Reload in Eagle, then RATSNEST.
```

Branch first; Eagle closed; afterward File ▸ Reload and `RATSNEST`. The rotation is about
the element's own origin — for a 2-pin part with origin-symmetric pads, ±180° exactly
exchanges the pads (the "sitting backwards" fix). Smashed `>NAME`/`>VALUE` labels are
transformed with the part.

### 5D. Swap two parts' placements — `swap_parts.py`

When the repo placed two parts in each other's positions, exchange their placements the
same way — parts move under the stationary traces:

```
python3 swap_parts.py "<board.brd>" D1 R1
```

The swap is exact: each part takes the other's position **and** rotation (and mirror
state). If the swapped pair also needs reorienting — e.g. mirror-image footprints whose
pads only line up after a half-turn — **compose with 5C** afterward:

```
python3 swap_parts.py  "<board.brd>" D1 R1
python3 rotate_part.py "<board.brd>" D1 180
python3 rotate_part.py "<board.brd>" R1 180
```

Both tools leave the `<signals>` section byte-identical (verified), and both are exact
involutions (swap twice / rotate 360° returns the identical file) — so a mis-fix backs
out cleanly. Which fix to use for which situation — and when to fix the *schematic* or
the *footprint* instead of the board — is the placement-correction philosophy below.


### 5E. Placement corrections vs the repo

A published repo can predate the vendor's final cleanup, so some placements are wrong —
discovered exactly here, when binding and airwires won't resolve. Fix by hand, one part
at a time (5C/5D are the on-board tools); the philosophy decides which artifact to fix.

#### The philosophy (how to decide each fix)

**Detect wrongness against a chosen ground truth.** Three independent references catch a bad
placement: the board's own **silkscreen** (drawn in the same repo — a part that doesn't sit
under its silk label is misplaced), the **as-built physical board** if you have one, and
**Eagle's airwires/DRC** (a placement error usually shows up as airwires that won't resolve
in 5B). Reverse-transform each part (the coordinate transform's inverse) and diff against the master
`location`/`rotate` to get the candidate list, then judge each against one of these truths.

**The repair direction follows from which artifact you trust for that part.** A part that is
180° off can be made right two ways, and the choice is deliberate:
- If the **physical board** is truth (it matches what was actually built and routed), rotate
  the part **on the board** to match it.
- If the **repo JSON** is truth (the board geometry should stay faithful to the source),
  leave the board and rotate the part **in the schematic** instead.

Same symptom, opposite fix — picked by what you've decided is authoritative for that part.

**Directional parts get a pin-swap, not a rotation.** A diode (D1) is directional, and you
may not want to introduce a rotation that the source JSON doesn't contain. Instead of
rotating, **swap the footprint's two pins and reverse the tDocu arrow** — a rotation-
*equivalent* that leaves the part's source placement/orientation untouched while landing the
copper correctly.

**A mirror is substantive; a rotation is cosmetic.** Reflecting a part to the other layer
changes its **pin order / handedness**, which is physically real — for U1 (the DIP) a naive
mirror-to-bottom would imply soldering the chip to the *bottom*. The right fix is to bake the
mirror into the **footprint** (pins in mirror order) so the part sits on the top layer with a
correct pinout. Treat a 180° rotation as no big deal; treat a mirror as a real change.

---

## 6. The pour — `pour.py`  (run AFTER all electrical work)

Replicates PCBmodE's pour `g` as Eagle's **native cutout representation** so CAM exports
Boldport-style copper (Eagle's own ratsnest pour would drop the decorative floating
islands Boldport keeps). Goal: `ge(transform(board)) = transform(g(board))`. The pour
must land in a real copper layer so CAM picks it up.

```
python3 pour.py <repo-dir> "<board.brd>"     # branch first; Eagle closed; then open + RATSNEST
```

Defaults are read from the repo and the board: **clearances** from the master's
`distances.from-pour-to` (pad/route/drill/outline), the **pour side** from
`shapes/pours.json`, OX/OY from the board's `.transform.json` sidecar. Override with
`--net` (default GND), `--side`, `--width`, `--ox/--oy`. Re-runs replace the prior pour.

**Representation that won** — all inside the pour net's `<signal>`:
- **one** `<polygon pour="solid">` = board interior (`outline.buffer(-outline_clearance)`)
- **one** `<polygon pour="cutout">` **per** pad / hole / foreign-trace clearance

**PCBmodE `g` semantics honoured:**
- **Net-agnostic flood** — rings of clearance around **all** pads/traces/drills uniformly.
- **No orphan removal** — keep decorative/floating islands (Eagle's native pour deletes them).
- Connection is via the hand-drawn **spokes/thermals** authored with `buffer-to-pour: 0`
  (convention: the pour-net's *own* traces are unbuffered, so they fuse into the fill).
  The pour net's traces are **excluded** from the cutout set so they merge.

**Pour width 0.2 default.** ⚠️ A width below Eagle's DRC minimum copper width makes Eagle
silently refuse to fill ("very little pour") — set the DRC minimum ≤ the pour width.
The solid is **pre-deflated by width/2** before injection: Eagle strokes a polygon's
boundary at its width, pushing copper width/2 outside the vertex path, so without the
deflation the pour edge lands ~0.1 mm past PCBmodE's.

**Decorative copper** (logo art on the copper layer — a Boldport signature, but common
elsewhere too, e.g. an OSHW logo) needs protecting or the pour swallows it visually. Two
cases:

- **A board part** (e.g. a logo part from a vendor library): protected automatically —
  the pour traces every footprint's copper into clearance cutouts, logo parts included.
- **Route-art on the pour net** (e.g. a logo drawn as route strokes): designate it —
  `--decor ROUTEKEY[:STYLE]`, sticky in `<board>.brd.decor.json` so **re-pours keep the
  protection**. STYLE is an artistic choice:
  - `silhouette` (default) — one cutout on the motif's outer shape (sub-paths merged,
    counters filled): the whole motif sits on bare substrate (The Cuttle's production look);
  - `traced` — clearance capsules along each stroke: the fill threads through the motif's
    negative space. (Capsules are per segment because a traced closed ring would need a
    cutout-with-a-hole, which an Eagle polygon can't express.)

  **How you find these routes:** they're the ones `bind_copper` couldn't bind — decorative
  copper touches no pad — so you seeded them. The pour reads the seeds file and *suggests*
  pour-net seeded routes as `--decor` candidates; the human confirms (the script can't know
  art from copper).

**Cutout shapes are TRACED, not guessed:** pad clearances come from the footprint copper
polygons on L16, shapely-buffered by `CL_PAD`. Guessing circles over square pads produced
"little peaks at edge midpoints" — trace + buffer instead (smooth, correct).

**Dead ends (kept as reference scripts):**
- `gnd_flood.py` — shapely free-copper flood + keyhole slits. Gold-standard PNG, but in
  the .brd the slits showed as visible **bridges** across the fill. Cutouts fixed this.
- `bottom_restrict.py` — bRestrict keep-outs + Eagle grid-fill. Eagle's grid-based fill
  was capricious in narrow (≈0.34 mm) channels (random fill). Superseded.
- A generic `<polygon … layer="16">` regex matched the **first** L16 polygon (a footprint
  pad), not the pour → partial fill. Target the lone pour polygon by an `isolate` attribute.

**Result:** "Ratsnests amazingly." Confirmed 2026-06. Cost: the ~165 cutouts make CAM fill
+ Gerber-viewer rasterize noticeably slower (acceptable; see Other Notes).

---

## 7. Silkscreen art — `silk.py`

Places the Boldport silk onto the silk layers (top → 21, bottom → 22) **in the .brd
frame** so it registers with the outline/components. Idempotent via the board's
`.inject.json` manifest (re-runs replace only what silk.py added).

```
python3 silk.py <repo-dir> "<board.brd>"     # branch first; Eagle closed; then reload + eyeball
```

What renders is driven by the shapes' own attributes in `shapes/silkscreen.json` —
`type` (path/text) × `style` (stroke/fill) — plus the **footprint silkscreen art** of
placed components (path/text shapes only; `rect` body outlines are deliberately skipped —
those are tDocu material in the library packages, not production silk). Run it AFTER the
outline step: fill patterns and wordmarks are centred on the outline's bbox centre.

**The methods (each empirically settled on the first port):**

- **Stroke paths** — direct transform `(x+OX, -y+OY, -cv)` → stroked `<wire>`s, at the
  shape's own stroke-width. For outline-art and legends (things that are genuinely
  strokes). Their `location` attribute is NOT applied (matches PCBmodE's rendering —
  verified against the pads).

- **Pattern fills** (`style=fill` paths, e.g. the Cuttle's seigaiha waves) — **the lean,
  arc-preserving way to fill a shape.** Emitted with a **1-mil hairline polygon width**:
  Eagle fills a polygon AND strokes its boundary at the polygon width, dilating the shape
  by width/2 — at the earlier 0.1524 width the wave crescents rendered visibly fatter
  than Boldport's pure-region fill. `arcfit.fit_path` → flip → re-centre on the board
  centre (pattern fills are authored offset in the source) → emit one `<polygon>` per
  subpath with `curve` on the vertices. The Cuttle's bottom silk comes out as **46 wave
  polygons** (+13 wordmark polygons = the "59" recorded on the first port). ⚠️ **This is
  the correct method.** An earlier board (`full_board.brd`) rendered the same waves as
  **8,761 flattened `<wire>`s** — heavier and not arc-true. If a port's silk shows
  thousands of wires, it reverted to the old way.

- **Filled wordmarks** (`type=text`) — **FILLED** (Boldport's look), not stroke
  outlines. Glyph contours are combined with shapely `symmetric_difference` so counters
  (the hole in "o", "e", "d") become **even-odd holes**; holes are then bridged to the
  outside with `_keyhole()`. On silk this is the right call because silk rasterizes to the
  fab's DPI — outline-vs-fill is invisible there, but fill matches Boldport, and the slit
  is sub-resolution so it never shows.

- `_keyhole(poly, w=0.01)` — Eagle polygons **cannot have interior holes**, so each hole
  is connected to the exterior by a ~0.01 mm slit (picks the nearest hole→exterior pair
  each pass; keeps the largest resulting polygon). Invisible at silk resolution.
  *(Note: keyholing is fine for tiny glyph counters; do NOT use it on the large GND flood
  — there the slits read as visible "bridges." Use cutouts instead — see Step 6.)*

---


## 8. Explicit soldermask openings — `stopmask.py`

```
python3 stopmask.py "<board.brd>" --expand 0.1     # branch first; Eagle closed; then reload
```

Writes an explicit NSMD opening to tStop/bStop (29/30) for **every pad**, traced from the
board's own pad copper (same harvest as the pour) and buffered by `--expand` — so openings
follow the true pad shapes, rounded corners included, instead of Eagle's circular DRC
annulus. Eagle unions all stop sources, so smaller DRC- or footprint-derived openings
disappear harmlessly underneath; the explicit (largest) opening wins, no library surgery
needed. Manifest-idempotent: re-running with a different `--expand` replaces the set, so
the expansion is a tunable knob to iterate against fab renders. Default 0.1 mm/side — the
ring measured on Boldport's shipped boards (see the NSMD note in Other Notes for why the
measurement, not the repo config, sets this number).

## 9. CAM → Gerbers → verification → fab

**Producing the Gerbers:** run the fab's stock CAM job from Eagle's CAM processor
(JLCPCB's 2-layer job, OSHPark's 2-layer job — keep the same job AND the same DRC for
any sets you intend to compare). Eagle exports X2 Gerbers whose `TF.FileFunction`
attributes make the set self-describing — fab importers map sides from those, not from
filenames. **Load the toolkit's `PCBmodEagle.dru` as the DRC before CAM** — it carries
the settled settings (Masks ▸ Stop pinned Min = Max = 0.1 mm, thermal isolate 0). If
using another fab's DRC instead, verify those same settings plus: minimum copper width
≤ the pour width (0.2).
Eagle CAM **linearizes all arcs** (see Other Notes); the reference writer
`make_gerber.py <repo-dir> <out-dir>` renders the published repo with true G02/G03 arcs
and the same X2 attributes — it is the fidelity baseline, not a workflow step.

**Verification stack** — three layers, each catching what the others can't:
1. **`gerber_compare.py <setA> <setB> <out>`** — parses and rasterizes both Gerber sets
   (mm/inch, 3.4/3.6, polarity, regions, arcs), aligns by outline-bbox centre, emits
   per-layer tri-colour diffs + IoU and a drill reconciliation. Exact, fast, ours.
2. **`osh_render_compare.py <dirA> <dirB> <out>`** — diffs OSHPark's OWN renders
   (top.png/bottom.png) of uploaded sets: a third-party engine, immune to our parser's
   bugs. (Caveat in the tool: OSHPark draws silk in the same colour as masked copper.)
3. **Human eyes on the fab's render.** Twice on this project the renders exposed what
   every automated layer had blessed: a tokenizer bug that silently dropped polarity
   (clearance rings poured over identically in both sets — symmetric wrongs agree), and
   a mask model half the measured size.

**Lessons learned the hard way:**

- **Check the comparison tool itself against an independent renderer before trusting
  it.** A bug in the comparator affects *both* sides of the comparison the same way, so
  the diff still shows agreement — the tool cannot catch a bug it shares with itself.
  This actually happened: a parsing bug silently ignored every polarity (LPC) command,
  so soldermask-style clearances rendered as solid copper in *both* Gerber sets, the
  diffs looked clean, and the boards appeared to match. The error only surfaced when
  OSHPark's own render of the same files showed clearance rings around every pad that
  the home-grown renders lacked. Cure: render one set with the tool and with the fab's
  viewer, and confirm they show the same board, before believing any diff.
- **When the config file and the physical board disagree, match the board.** PCBmodE's
  config specifies a 0.05 mm soldermask buffer, but measuring the openings on Boldport's
  actually-shipped boards shows rings of about 0.1 mm. Whatever the explanation (perhaps
  the renderer strokes the opening outline, doubling the effective gap), the boards are
  the product — so the toolkit uses the measured 0.1 mm.
- **What the comparison scores mean in practice** (numbers from The Cuttle, useful as
  expectations for the next board): when the *same* design was built twice by different
  pipelines and both were exported with an identical CAM job and DRC, every layer's
  pixels matched 99.6–100% and all drills coincided within 0.05 mm — that is what
  "these are the same board" looks like. Comparing a finished board against the
  *published repo* scored about 91% on copper — and that is not a failure: the missing
  9% was itemized and every piece was a known, deliberate change (placement corrections,
  pad restring, logo-pour style, silkscreen label text). A clean comparison is not one
  with no differences; it is one with **no unexplained differences**.

---


## The coordinate transform (the heart of everything in this project)

PCBmodE/SVG is **y-down**, origin at board centre; Eagle is **y-up**. Every shape —
outline, traces, pads, silk — uses the same map:

```
x_brd = x_svg + OX
y_brd = -y_svg + OY         # the y-flip
curve_brd = -curve_svg      # arc sweep sign flips with the y-flip
```

Inverse (for reverse-engineering placements / diffing against the repo):

```
x_svg = x_brd - OX
y_svg = OY - y_brd
```

- **OX/OY are chosen per board** — they depend on the board's size (see the placement
  step for the rule). The Cuttle uses **OX=50, OY=40**, which lands its centre at
  ≈ **(49.97, 40.00)** in Eagle's positive quadrant; every Cuttle-era script assumes
  those values.
- Mils → mm = **× 0.0254** (Boldport JSON is mm; Eagle UI often reads mil).
- Text glyphs are authored y-**up**, so they **skip** the y-flip (they only get `+OX/+OY`
  and, for the bottom layer, an x-mirror). Everything path-based gets the full flip.

---



## Other Notes (findings, gotchas, the "why")

### Edge clearance & the "pad too close to edge" DRC — it's advisory
- The Cuttle's (for example) two header rows sit symmetric about the board centre (±8.89 mm), but the
  **repo's fish outline is ~0.045 mm (1.77 mil) off-centre** relative to the parts. Result:
  one row clears the edge by **0.391 mm (15.4 mil)**, the other by only **0.301 mm (11.9 mil)**.
  This is **in the repo**, faithfully reproduced — *not* a transform bug (the board's bbox
  centre is a clean 40.000; the asymmetry is local fish-shape, confirmed against the master).
- Both OSHPark and JLCPCB Eagle-DRC rulesets flag the 11.9 mil row. **But it's an advisory
  warning, fine in production:** the pure-geometry `full_board.brd` has the *identical*
  clearance and passes both DRC rulesets (free copper isn't edge-checked) **and** JLC's
  ordering-site check; `r1.4` outputs the same geometry and also passed JLC's site. Eagle's
  "Distance: Copper/Dimension" default is conservative; the fab's real capability is finer.
- **Decision: do not "fix" it.** A bottom-arc nudge (lower the edge 0.09 mm to symmetrize)
  was scoped (`gnd_cutout`-style edit on L20 wires) but **declined** — it deviates from a
  faithful outline to silence a warning production already accepts. Don't re-centre either
  (that averages the good row down and can fail both).

### Soldermask (NSMD) gap — PCBmodE's model and how to match it in Eagle
- PCBmodE (`distances.soldermask`): **rect/circle pads get an ABSOLUTE buffer per side**
  (typ. 0.05 mm ≈ 2 mil) — uniform, size-independent. `path`-type pads use
  `path-scale: 1.05`, a **centroid scale** — deliberately non-uniform, and not
  expressible in Eagle's DRC at all (no centroid-scale mode); match those with explicit
  polygons on tStop/bStop (29/30), the same move as the rounded-rect pads' stopmask.
- **The effective number is 2× the config buffer.** Side-by-side measurement against
  Boldport's shipped boards shows their NSMD ring is ~0.1 mm (≈4 mil) per side — double
  the config's 0.05. (Plausible mechanism: PCBmodE strokes the mask shape on the pad
  edge, so a centred stroke pushes the opening out by the full stroke width per side;
  in any case, the measurement rules.) All our generators therefore use **0.1 mm/side**.
- **Eagle side:** pin the rule so it's uniform and size-independent: DRC ▸ Masks ▸ Stop
  **Min = Max = 0.1 mm (4 mil)**; with min = max the percentage drops out and every
  opening is a uniform ring. (The generated libraries' rounded-rect pads carry explicit
  +0.1 stop polygons and are unaffected by the DRC.)
- **`stopmask.py` is the full-control mechanism** — the PCBmodE-equivalent capability:
  explicit openings on tStop/bStop (29/30), traced from the board's own pad copper,
  expansion as a parameter (`python3 stopmask.py "<board.brd>" --expand 0.1`).
  Manifest-idempotent. Smaller DRC/footprint-derived openings union harmlessly
  underneath, so the explicit (largest) opening wins — no library surgery needed.
- `make_gerber.py` applies 2× the repo's absolute buffer (the earlier multiplicative
  `pad_scale_factor` model made big pads' gaps too wide and small pads' too tight).
- 0.1 mm is the ~4 mil expansion most fabs (including JLCPCB) quote as standard — no
  fab-tightness concern at this value.

### Pad diameter & restring — the repo is fab-independent; Eagle is not
- The header pad is **1.7 mm** in three places that all agree: repo spec, our Eagle
  `<pad diameter>`, and the copper-art polygon. Faithful.
- **The repo does NOT vary by fab.** PCBmodE writes the final 1.7 mm pad into the Gerber;
  the fab images it as-is. There is no DRC-growth step in PCBmodE's pipeline.
- **What varies is Eagle's restring DRC.** Eagle treats `<pad>` as parametric and recomputes
  the annular ring from the loaded design rules: JLCPCB's rules **grow** the pad past 1.7 (a
  ring pokes out beyond the 1.7 art); OSHPark's keep it ≤1.7 (pad hidden under the art). Same
  pad, two DRC opinions.
- To emit a faithful fixed 1.7 mm: either pin Eagle's restring (annular ≈ 0.30 mm, i.e.
  min/max ≈ 11.8 mil) **or** export through `make_gerber.py` (flashes the repo diameter
  directly, no DRC in the loop). Bonus: pinning the pad also keeps JLC's grown pad from
  eating into the edge clearance above.

### Eagle CAM linearizes ALL arcs
- Empirically, Eagle CAM converts **both** `<wire>` and `<polygon>` arcs to `G01` line
  segments on export. Only **flashed pads** survive as true apertures. This is *the* reason
  the project owns `gerber.py` (true `G02/G03`) for any output that must keep real arcs.

### Footprint copper that actually joins a net
- Only `<smd>`/`<pad>` (and `<polygon>` pours) connect to the net. A bare `<circle>` is
  graphics only; it shows as invalid-overlap stripes and never fuses. (Discovered during
  work with the FlexyPin footprint.)

### Two mask mechanisms: DRC for round pads/vias, explicit polygons for everything else

Eagle controls the distance from the solder mask to the circular pads and vias using the
first three settings in the Masks tab of the DRC. But polygon pads (rounded rects, any
custom shape) need their own tStop/bStop openings custom built — either on the board's
stop layers (Step 8, `stopmask.py`) or in the footprint itself. The toolkit's
`PCBmodEagle.dru` pins the DRC side; `stopmask.py` covers the rest.

Setting the DRC stop to 2 mil (min and max) yields about half the opening width seen in
the Boldport outputs, so 4 mil is a better starting point. Building custom tStop and bStop
layers with a script (Step 8) is the way around Eagle's limitations here.
