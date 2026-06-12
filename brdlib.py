#!/usr/bin/env python3
"""
brdlib.py -- shared helpers for editing Eagle .brd <element> placement in XML.

Used by rotate_part.py / swap_parts.py (and the same logic lives in place.py).
The key facts that make direct XML editing safe:
  * an element's x / y / rot are BOARD-ONLY data -- changing them never touches
    the schematic, netlist, or forward/back annotation;
  * signal wires are separate elements -- a placement edit cannot disturb traces;
  * smashed >NAME / >VALUE labels are <attribute> children of the element and
    must be transformed rigidly along with it (rotate about the element origin).
"""
import math, re


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
    if key == 'rot' and ' align=' in tag:
        # Eagle serializes rot BEFORE align -- insert there so a remove/re-add
        # round-trip stays byte-stable
        return tag.replace(' align=', f' {key}="{val}" align=', 1)
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


def read_placement(block):
    """(x, y, deg, mirrored) of an element block."""
    gt = block.index('>')
    tag = block[:gt + 1]
    deg, mir = parse_rot(get_attr(tag, 'rot', ''))
    return float(get_attr(tag, 'x')), float(get_attr(tag, 'y')), deg, mir


def write_placement(block, nx, ny, ndeg, nmir):
    """Set an element block's x/y/rot, transforming smashed <attribute> label
    children rigidly with it (translate + rotate about the element origin).
    A change of MIRROR is not applied to label positions (rare; eyeball those)."""
    gt = block.index('>')
    self_close = block[gt - 1] == '/'
    open_tag, rest = block[:gt + 1], block[gt + 1:]

    ox, oy, odeg, _ = read_placement(block)
    ndeg = ndeg % 360

    open_tag = set_attr(open_tag, 'x', num(nx))
    open_tag = set_attr(open_tag, 'y', num(ny))
    if ndeg or nmir:
        # set_attr updates in place when rot exists (keeps attribute order stable
        # for clean diffs), appends otherwise
        open_tag = set_attr(open_tag, 'rot', ('M' if nmir else '') + f'R{num(ndeg)}')
    else:
        open_tag = del_attr(open_tag, 'rot')

    d = math.radians(ndeg - odeg)
    cosd, sind = math.cos(d), math.sin(d)

    def xform(am):
        atag = am.group(0)
        rx = float(get_attr(atag, 'x')) - ox
        ry = float(get_attr(atag, 'y')) - oy
        atag = set_attr(atag, 'x', num(nx + rx * cosd - ry * sind))
        atag = set_attr(atag, 'y', num(ny + rx * sind + ry * cosd))
        adeg, amir = parse_rot(get_attr(atag, 'rot', ''))
        adeg = (adeg + (ndeg - odeg)) % 360
        if adeg or amir:
            atag = set_attr(atag, 'rot', ('M' if amir else '') + f'R{num(adeg)}')
        else:
            atag = del_attr(atag, 'rot')
        return atag

    if not self_close:
        rest = re.sub(r'<attribute\b[^>]*/>', xform, rest)
    return open_tag + rest
