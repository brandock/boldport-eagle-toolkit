#!/usr/bin/env python3
"""
SVG-font text layout for PCBmodEagle -- replicates PCBmodE's textToPath():
  * each glyph laid at its native coords, pen advancing by horiz-adv-x
  * scaled by font-size / units-per-em
  * centred on its own bbox (PCBmodE transform(center=True), location=[0,0])

NOTE on orientation: SVG-font glyphs are y-UP (baseline = 0, ascenders +).
That's the OPPOSITE of the PCBmodE *art* paths (Inkscape, y-down). So text does
NOT take the y-flip the other shapes get -- it's already in Eagle's y-up frame.
Returns subpaths (lists of (x,y) in mm) with the text centred on the origin.
"""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import svg2eagle as s

_GLYPH = re.compile(r'<glyph\s+([^>]*?)/?>', re.S)
_ATTR  = re.compile(r'(\S+?)="(.*?)"', re.S)

class SvgFont:
    def __init__(self, path):
        txt = open(path, encoding='utf-8').read()
        m = re.search(r'<font\s+[^>]*horiz-adv-x="([\d.]+)"', txt)
        self.default_adv = float(m.group(1)) if m else 359.0
        m = re.search(r'<font-face[^>]*units-per-em="([\d.]+)"', txt)
        self.upm = float(m.group(1)) if m else 1000.0
        self.glyphs = {}   # unicode char -> {'adv':float, 'd':str|None}
        for g in _GLYPH.findall(txt):
            a = dict(_ATTR.findall(g))
            u = a.get('unicode')
            if u is None:
                continue
            # decode numeric/entity unicode if present
            u = (u.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                  .replace('&quot;', '"').replace('&apos;', "'"))
            mh = re.match(r'&#x([0-9a-fA-F]+);', u)
            if mh:
                u = chr(int(mh.group(1), 16))
            self.glyphs[u] = {'adv': float(a.get('horiz-adv-x', self.default_adv)),
                              'd': a.get('d')}

    def layout(self, text, font_size_mm, letter_spacing=0.0):
        """Return list of subpaths (mm), centred on origin, y-up."""
        scale = font_size_mm / self.upm
        pen = 0.0
        subpaths = []
        for ch in text:
            g = self.glyphs.get(ch)
            if g is None:               # missing glyph: advance default
                pen += self.default_adv + letter_spacing / scale
                continue
            if g['d']:
                for sp in s.parse_path(g['d']):
                    subpaths.append([(x + pen, y) for x, y in sp])
            pen += g['adv'] + letter_spacing / scale
        # scale font-units -> mm
        subpaths = [[(x * scale, y * scale) for x, y in sp] for sp in subpaths]
        # centre on bbox (PCBmodE center=True, location [0,0])
        xs = [x for sp in subpaths for x, y in sp]; ys = [y for sp in subpaths for x, y in sp]
        cx = (min(xs) + max(xs)) / 2; cy = (min(ys) + max(ys)) / 2
        return [[(x - cx, y - cy) for x, y in sp] for sp in subpaths]
