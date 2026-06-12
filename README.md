# boldport-eagle-toolkit

Tools and a step-by-step guide for porting Boldport (PCBmodE) boards into
Autodesk Eagle — faithful curves and art, a linked schematic, and Gerber
output that survives comparison with the original, pixel for pixel.

Boldport boards are defined as PCBmodE JSON (outlines, routes, footprints,
silkscreen art as SVG paths). This toolkit ports that geometry into a native
Eagle `.brd`/`.sch` pair: real parts on a real schematic, net-checked copper,
the ground pour with its decorative islands intact, and the silkscreen art
registered to the board. The full method — including the design decisions and
the dead ends — is in
**[Boldport_to_Eagle_Step-by-Step.md](Boldport_to_Eagle_Step-by-Step.md)**.

## The pipeline

| Step | Tool |
|---|---|
| 1A | `roll_call.py` — repo parts → starter library + schematic roll call |
| 2 | `place.py` — position every part by editing the `.brd` directly |
| 3–4 | `inject_brd.py` — board outline and copper traces, arc-faithful |
| 5A | `bind_copper.py` — bind free copper into nets |
| 5B | `airwire_stitch.py` + `export_airwires.ulp` — resolve airwires the way Eagle actually fuses copper |
| 5C–D | `rotate_part.py` / `swap_parts.py` — fix reversed/swapped parts under their traces |
| 6 | `pour.py` — the pour as Eagle-native solid + cutouts, PCBmodE semantics |
| 7 | `silk.py` — silkscreen art, pattern fills, filled wordmarks |
| 8 | `stopmask.py` — explicit soldermask openings traced from the pad copper |
| 9 | `make_gerber.py`, `gerber_compare.py`, `osh_render_compare.py` — reference Gerbers and pixel-level verification |

Plus `increment_version.py` (branch a board with all its sidecar files),
`smash_labels.py`, `strip_stitches.py`, and the `PCBmodEagle.dru` design rules.

Validated by porting Boldport's [The Cuttle](https://github.com/boldport/thecuttle)
twice — once exploratory, once from this guide — and verifying the two results
agree with each other (99.6–100% per layer) and with the published source.

## Requirements

Eagle 9.6.2 (the free tier works), Python 3 with `shapely`, `svgpathtools`,
`numpy`, and `Pillow` (`scipy`/`matplotlib` for some diagnostics).

## Credits

The `fonts/` directory includes the GoodDog typeface (© Fonthead Design,
distributed free of charge), in the SVG-webfont form used by Boldport's
PCBmodE for board wordmarks. The font is included for faithful reproduction
of Boldport silkscreen text and is not covered by this repository's MIT
license.
