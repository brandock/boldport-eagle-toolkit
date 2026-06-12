#!/usr/bin/env python3
"""
gerber.py -- a minimal RS-274X writer that emits TRUE arcs (G02/G03), the thing
Eagle's own CAM refuses to do. This is the PCBmodE move: generate manufacturing
data straight from the vector art instead of routing it through an EDA CAM that
linearizes every curve.

Takes the same arc primitives arcfit.py produces -- vertices (x, y, curve_deg)
where curve_deg is the signed sweep (CCW +) of the edge leaving that vertex --
and writes:
  * strokes  -> aperture + G01/G02/G03 draws
  * fills    -> G36 region with G01/G02/G03 boundary (G37)

Coordinates stay in the board-centred frame (negative allowed) so a layer we
write registers with the layers Eagle exported from the same .brd.
Format: %FSLAX34Y34% (3.4, mm), G75 multi-quadrant.
"""
import math

def _fmt(v):                       # mm -> 3.4 fixed integer string
    return str(int(round(v * 10000)))

def arc_center(x1, y1, x2, y2, sweep_deg):
    """Reconstruct arc centre from endpoints + signed sweep (CCW +)."""
    s = complex(x1, y1); e = complex(x2, y2)
    sweep = math.radians(sweep_deg); half = sweep / 2
    chord = e - s; c = abs(chord)
    R = c / (2 * math.sin(abs(half)))
    mid = (s + e) / 2
    hdir = 1j * chord / c * (1 if sweep > 0 else -1)
    center = mid + hdir * R * math.cos(half)
    return center.real, center.imag

class Gerber:
    def __init__(self, comment="PCBmodEagle direct Gerber", function=None):
        # function: X2 .FileFunction attribute (e.g. "Copper,L1,Top", "Legend,Bot",
        # "Soldermask,Top", "Profile,NP"). Emitting it makes the set SELF-DESCRIBING:
        # fab importers (OSHPark etc.) map sides from this instead of guessing from
        # filenames -- a bare-named set can otherwise come back with its sides
        # duplicated or swapped.
        self.comment = comment
        self.function = function
        self.apertures = {}        # width(mm) -> Dcode
        self._next = 10
        self.body = []
        self.n_arcs = 0
        self.n_lines = 0
        self.n_flashes = 0

    def _ap(self, width):
        w = round(width, 4)
        if w not in self.apertures:
            self.apertures[w] = self._next; self._next += 1
        return self.apertures[w]

    def _edge(self, x1, y1, cv, x2, y2, region=False):
        if abs(cv) < 1e-6:
            self.body.append(f'G01X{_fmt(x2)}Y{_fmt(y2)}D01*'); self.n_lines += 1
        else:
            cx, cy = arc_center(x1, y1, x2, y2, cv)
            g = 'G03' if cv > 0 else 'G02'        # G03 = CCW
            self.body.append(f'{g}X{_fmt(x2)}Y{_fmt(y2)}'
                             f'I{_fmt(cx - x1)}J{_fmt(cy - y1)}D01*'); self.n_arcs += 1

    def stroke(self, verts, closed, width):
        d = self._ap(width)
        self.body.append(f'D{d}*')
        x0, y0, _ = verts[0]
        self.body.append(f'X{_fmt(x0)}Y{_fmt(y0)}D02*')
        n = len(verts)
        for i in (range(n) if closed else range(n - 1)):
            x1, y1, cv = verts[i]; x2, y2, _ = verts[(i + 1) % n]
            self._edge(x1, y1, cv, x2, y2)

    def flash(self, x, y, diameter):
        """Stamp a round pad (circle aperture, D03)."""
        d = self._ap(diameter)
        self.body.append(f'D{d}*')
        self.body.append(f'X{_fmt(x)}Y{_fmt(y)}D03*')
        self.n_flashes += 1

    def clear(self):
        """Switch to clear polarity -- subsequent draws remove copper."""
        self.body.append('%LPC*%')

    def dark(self):
        """Switch back to dark polarity -- subsequent draws add copper."""
        self.body.append('%LPD*%')

    def region(self, verts):
        d = self._ap(0.01)                        # region mode needs a current ap
        self.body.append(f'D{d}*')
        self.body.append('G36*')
        x0, y0, _ = verts[0]
        self.body.append(f'X{_fmt(x0)}Y{_fmt(y0)}D02*')
        n = len(verts)
        for i in range(n):
            x1, y1, cv = verts[i]; x2, y2, _ = verts[(i + 1) % n]
            self._edge(x1, y1, cv, x2, y2, region=True)
        self.body.append('G37*')

    def render(self):
        out = [f'G04 {self.comment}*']
        if self.function:
            out.append(f'%TF.FileFunction,{self.function}*%')
            out.append('%TF.FilePolarity,Positive*%')
        out += ['%FSLAX34Y34*%', '%MOMM*%', 'G75*', '%LPD*%']
        for w, d in sorted(self.apertures.items(), key=lambda kv: kv[1]):
            out.append(f'%ADD{d}C,{w:.4f}*%')
        out.append('G01*')
        out += self.body
        out.append('M02*')
        return '\n'.join(out) + '\n'
