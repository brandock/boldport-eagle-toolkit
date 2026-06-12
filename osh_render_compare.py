#!/usr/bin/env python3
"""
osh_render_compare.py -- pixel-diff OSHPark's OWN renders of two Gerber sets.

OSHPark's online viewer renders every uploaded set with the same engine, so
diffing its top.png/bottom.png removes our Gerber interpreter from the loop
entirely -- the ground-truth comparison. (Lesson learned: a home-grown
comparator can agree with itself symmetrically; a third party's renderer
can't.)

Each side's image is classified into COPPER-PRESENT (soldermask-over-copper
lavender + exposed-copper gold) vs not, aligned by the board's bounding box
(the non-background region), and diffed tri-colour with IoU.

CAVEAT: OSHPark renders SILKSCREEN in the same lavender as masked copper, so
silk is chromatically inseparable -- the IoU is a copper+silk metric. Sides
dominated by a pour are barely affected; sparse sides show silk differences
(e.g. label text) as copper differences. For a pure copper comparison use
gerber_compare.py on the Gerbers themselves.

Usage:
    python3 osh_render_compare.py <dirA> <dirB> <out-dir> [--label-a A --label-b B]

where each dir holds OSHPark's downloaded top.png and bottom.png.
"""
import argparse, os, sys

try:
    import numpy as np
    from PIL import Image
except ImportError:
    sys.exit("needs numpy + Pillow")

# OSHPark render classes (RGB)
COPPER = [(197, 183, 208), (193, 177, 203), (177, 136, 131)]   # mask-over-copper, AA, exposed gold
OTHER = [(0, 0, 0), (64, 18, 100), (51, 0, 85), (88, 88, 87), (128, 36, 200),
         (255, 255, 255), (230, 225, 235)]   # white silk is NOT copper


def copper_mask(path):
    im = np.asarray(Image.open(path).convert('RGB'), dtype=np.int32)
    h, w, _ = im.shape
    classes = np.array(COPPER + OTHER, dtype=np.int32)
    d = ((im[:, :, None, :] - classes[None, None, :, :]) ** 2).sum(axis=3)
    nearest = d.argmin(axis=2)
    copper = nearest < len(COPPER)
    board = ~np.all(im < 40, axis=2)            # not near-black background
    return copper, board


def bbox(mask):
    ys, xs = np.where(mask)
    return xs.min(), ys.min(), xs.max(), ys.max()


def main():
    ap = argparse.ArgumentParser(description="diff OSHPark renders of two Gerber sets")
    ap.add_argument('dira'); ap.add_argument('dirb'); ap.add_argument('out')
    ap.add_argument('--label-a', default='A'); ap.add_argument('--label-b', default='B')
    ap.add_argument('--swap-b', action='store_true',
                    help="set B's sides are swapped (its top.png is the board's bottom) -- "
                         "compare A top vs B bottom and vice versa. Happens when a "
                         "renderer guesses layer roles from bare filenames")
    ap.add_argument('--mirror-b', action='store_true',
                    help="x-mirror set B's images before comparing (view-side mismatch)")
    a = ap.parse_args()
    out = os.path.expanduser(a.out)
    os.makedirs(out, exist_ok=True)
    BG, BOTH, AONLY, BONLY = (12, 14, 18), (45, 70, 120), (210, 80, 60), (80, 200, 110)

    print(f"{'side':7s} {'IoU':>6s} {'both':>9s} {'A-only':>8s} {'B-only':>8s}   "
          f"(A={a.label_a}, B={a.label_b})")
    for side in ('top', 'bottom'):
        bside = ({'top': 'bottom', 'bottom': 'top'}[side]) if a.swap_b else side
        pa = os.path.join(os.path.expanduser(a.dira), f"{side}.png")
        pb = os.path.join(os.path.expanduser(a.dirb), f"{bside}.png")
        if not (os.path.exists(pa) and os.path.exists(pb)):
            print(f"{side}: missing png, skipped"); continue
        ca, ba = copper_mask(pa)
        cb, bb_ = copper_mask(pb)
        if a.mirror_b:
            cb, bb_ = cb[:, ::-1], bb_[:, ::-1]
        xa0, ya0, xa1, ya1 = bbox(ba)
        xb0, yb0, xb1, yb1 = bbox(bb_)
        wa, ha = xa1 - xa0, ya1 - ya0
        wb, hb = xb1 - xb0, yb1 - yb0
        if abs(wa - wb) > 6 or abs(ha - hb) > 6:
            print(f"{side}: board bbox sizes differ a lot ({wa}x{ha} vs {wb}x{hb}) -- "
                  f"renders may be at different scales; comparing anyway by top-left align")
        w, h = min(wa, wb) + 1, min(ha, hb) + 1
        A = ca[ya0:ya0 + h, xa0:xa0 + w]
        B = cb[yb0:yb0 + h, xb0:xb0 + w]
        both = A & B
        aonly = A & ~B
        bonly = B & ~A
        union = both.sum() + aonly.sum() + bonly.sum()
        iou = both.sum() / union if union else 1.0
        img = np.zeros((h, w, 3), dtype=np.uint8); img[:] = BG
        img[both] = BOTH; img[aonly] = AONLY; img[bonly] = BONLY
        Image.fromarray(img).save(os.path.join(out, f"{side}_diff.png"))
        print(f"{side:7s} {iou:6.3f} {both.sum():9d} {aonly.sum():8d} {bonly.sum():8d}")
    print(f"\ndiffs in {out}  (BLUE=both, RED={a.label_a}-only, GREEN={a.label_b}-only)")


if __name__ == '__main__':
    main()
