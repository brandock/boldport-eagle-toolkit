#!/usr/bin/env python3
"""Minimal Excellon drill writer. One tool per unique diameter; decimal-point
coordinates (mm) to sidestep zero-suppression ambiguity (fab-robust)."""

class Excellon:
    def __init__(self):
        self.tools = {}        # diameter(mm) -> tool number
        self._next = 1
        self.hits = {}         # tool number -> [(x, y), ...]

    def hit(self, x, y, diameter):
        dia = round(float(diameter), 4)
        if dia not in self.tools:
            self.tools[dia] = self._next; self._next += 1
        self.hits.setdefault(self.tools[dia], []).append((x, y))

    def render(self):
        out = ['M48', ';PCBmodEagle drills', 'METRIC,TZ']
        for dia, t in sorted(self.tools.items(), key=lambda kv: kv[1]):
            out.append(f'T{t}C{dia:.4f}')
        out.append('%')
        out += ['G90', 'G05']                       # absolute, drill mode
        for dia, t in sorted(self.tools.items(), key=lambda kv: kv[1]):
            out.append(f'T{t}')
            for x, y in self.hits[t]:
                out.append(f'X{x:.4f}Y{y:.4f}')     # explicit decimal points
        out.append('M30')
        return '\n'.join(out) + '\n'

    @property
    def n_hits(self):
        return sum(len(v) for v in self.hits.values())
