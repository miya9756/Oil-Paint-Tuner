#!/usr/bin/env python
"""CLI: measure a painting's key colours, and print a `reference.REFERENCES` block.

    conda run -n 4dre python scripts/extract_reference.py PAINTING.jpg --name starry-night

The table in `oilpaint/reference.py` is AUTHORED -- key colours written from documented
pigment lists and from what reproductions plainly show -- and that file says so rather than
implying a measurement it did not make. This is the other half of that honesty: point it at
a scan you have the rights to and it prints a block measured from the real thing, ready to
paste over the authored one.

WHAT IT MEASURES, and why it is not just a k-means. A reference here is a set of key colours
WITH AREA WEIGHTS, because area is what makes a transport land: The Starry Night is mostly
dark blue, and a set of six equally-weighted colours from it describes a painting that does
not exist. So the clustering keeps the cluster POPULATIONS and prints them as the weights.

The clustering is k-means in OKLab, seeded deterministically:

  * OKLab because that is the space the transport works in, so a cluster is a group of
    colours that look alike rather than one that happens to share a byte range;
  * k-means++ WITHOUT randomness -- the first centre is the darkest pixel and each next one
    is the pixel furthest from every centre so far. That is the standard greedy furthest-
    point seeding with the random tie-break removed, so two runs on the same image give the
    same table. A `--seed` would be a worse answer: the numbers end up pasted into a source
    file, and a table you cannot reproduce from its own input is a magic number.

The image is downsampled first (`--side`, default 256). This is a statistic over areas, and
a megapixel of it converges to the same answer as a thumbnail for a hundredth of the work.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from oilpaint.palette import _to_oklab  # noqa: E402
from oilpaint.reference import GRAIN  # noqa: E402


def load(path, side):
    img = Image.open(path).convert("RGB")
    if max(img.size) > side:
        s = side / max(img.size)
        img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                         Image.LANCZOS)
    return np.asarray(img, dtype=np.float64) / 255.0


def kmeans(lab, k, iters):
    """k-means with deterministic furthest-point seeding. Returns (centres, populations)."""
    n = len(lab)
    # Seed 1: the darkest pixel. An arbitrary but FIXED choice -- what matters is that it
    # is a property of the image rather than of a random number generator.
    idx = [int(np.argmin(lab[:, 0]))]
    d2 = ((lab - lab[idx[0]]) ** 2).sum(axis=1)
    for _ in range(k - 1):
        # ...and each next centre is the pixel furthest from every centre so far.
        idx.append(int(np.argmax(d2)))
        d2 = np.minimum(d2, ((lab - lab[idx[-1]]) ** 2).sum(axis=1))
    c = lab[idx].copy()

    for _ in range(iters):
        # (n, k) distances, chunked so a megapixel thumbnail does not allocate n*k floats
        # in one go on a machine that would rather not.
        lab_ = lab[:, None, :]
        assign = np.argmin(((lab_ - c[None, :, :]) ** 2).sum(axis=2), axis=1)
        for j in range(k):
            m = assign == j
            if m.any():
                c[j] = lab[m].mean(axis=0)
    counts = np.bincount(assign, minlength=k).astype(np.float64)
    return c, counts / counts.sum()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--name", default=None, help="the key to print (default: the filename)")
    ap.add_argument("-k", type=int, default=6, help="how many key colours (default 6)")
    ap.add_argument("--side", type=int, default=256, help="downsample the long side to this")
    ap.add_argument("--iters", type=int, default=24)
    a = ap.parse_args()

    img = load(a.src, a.side)
    flat = img.reshape(-1, 3)
    L, ka, kb = _to_oklab(flat)
    lab = np.stack([L, ka, kb], axis=-1)
    c, w = kmeans(lab, a.k, a.iters)

    # Back to sRGB for the table, because that is what a reader of reference.py can judge:
    # nobody can look at an OKLab triple and say "that is the ultramarine".
    from oilpaint.palette import _from_oklab
    srgb = _from_oklab(c[:, 0], c[:, 1], c[:, 2])
    order = np.argsort(-w)                       # biggest area first, so the table reads

    name = a.name or os.path.splitext(os.path.basename(a.src))[0]
    print(f'    # Measured from {os.path.basename(a.src)} by scripts/extract_reference.py')
    print(f'    "{name}": (', end="")
    pad = " " * (9 + len(name))
    for n, i in enumerate(order):
        r, g, b = (float(v) for v in srgb[i])
        end = "),\n" if n == len(order) - 1 else ",\n" + pad
        print(f"({r:.3f}, {g:.3f}, {b:.3f}, {w[i]:.2f})", end=end)

    # The numbers the transport will actually see, so a table can be sanity-checked without
    # running a render: this is exactly what `reference._target` will compute from the rows
    # above, GRAIN included.
    mu = (w[:, None] * c).sum(axis=0)
    d = c - mu
    cov = (w[:, None, None] * d[:, :, None] * d[:, None, :]).sum(axis=0)
    cov = cov + GRAIN * GRAIN * np.eye(3)
    print(f"\n    # OKLab mean {np.round(mu, 4).tolist()}")
    print(f"    # OKLab sd   {np.round(np.sqrt(np.diag(cov)), 4).tolist()}")


if __name__ == "__main__":
    main()
