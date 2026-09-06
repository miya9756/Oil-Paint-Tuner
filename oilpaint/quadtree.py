"""Adaptive quadtree subdivision [Samet84].

A cell splits into four if its detail score exceeds tau, subject to a minimum and maximum
depth. The tree lives on a padded square of side 2^k so cells stay power-of-two aligned --
which is what lets `range` use a mip pyramid and keeps every index shift exact.

Cells whose centre falls outside the image are discarded: they would place a stroke on
padding. Spec §4.2.
"""

import numpy as np


def tree_size(h, w):
    return 1 << int(np.ceil(np.log2(max(h, w))))


def build(field, h, w, tau, dmin=2, min_cell=4, dmax=None, tau_floor=0.0):
    """Subdivide level by level; return leaf cells as (y0, x0, size, depth) arrays.

    Vectorized across each level rather than recursing per cell: at every level the whole
    frontier is one array, so the cost is a handful of numpy calls per level.
    """
    size = tree_size(h, w)
    if dmax is None:
        dmax = int(np.log2(max(1, size // max(1, min_cell))))
    dmax = max(dmax, dmin)

    y0 = np.zeros(1, dtype=np.int64)
    x0 = np.zeros(1, dtype=np.int64)
    cur = size
    depth = 0
    parent_mean = None
    leaves = []

    while True:
        if depth >= dmax or cur <= 1:
            leaves.append((y0, x0, cur, depth))
            break

        if depth < dmin:
            split = np.ones(y0.shape, dtype=bool)
        else:
            score = field.score(y0, x0, cur, parent_mean=parent_mean)
            # tau_floor is an ABSOLUTE detail floor, independent of the budget. Without
            # it a stroke budget larger than the image's real detail drives the binary
            # search for tau down to ~0, and the tree then happily subdivides JPEG noise
            # -- a flat sky came back covered in speckle, because there is always SOME
            # variance to chase. Below the noise floor there is no signal to resolve, so
            # the budget must not be able to buy its way past this.
            split = (score > tau) & (score > tau_floor)

        if not split.any():
            leaves.append((y0, x0, cur, depth))
            break

        leaves.append((y0[~split], x0[~split], cur, depth))

        sy, sx = y0[split], x0[split]
        # Parent flat colour travels down for the `residual` metric; harmless otherwise.
        pm = field.mean_luma(sy, sx, cur) if field.metric == "residual" else None

        half = cur // 2
        y0 = np.concatenate([sy, sy, sy + half, sy + half])
        x0 = np.concatenate([sx, sx + half, sx, sx + half])
        parent_mean = np.tile(pm, 4) if pm is not None else None
        cur = half
        depth += 1

        # OVERLAP, not centre-inside. A coarse cell can overlap the image while its own
        # centre lies out on the padding -- e.g. on a 700x466 image the 1024 tree's
        # (512,0) child has its centre at y=768, outside, yet it covers the bottom 188
        # rows. Testing the centre here deleted that whole band and left it showing the
        # bare base layer. The centre test is only valid once a cell is a LEAF, which is
        # where it still happens (below).
        keep = (y0 < h) & (x0 < w)
        if not keep.all():
            y0, x0 = y0[keep], x0[keep]
            if parent_mean is not None:
                parent_mean = parent_mean[keep]
        if y0.size == 0:
            break

    ys, xs, ss, ds = [], [], [], []
    for cy, cx, cs, cd in leaves:
        if cy.size == 0:
            continue
        keep = (cy + cs // 2 < h) & (cx + cs // 2 < w)
        if not keep.any():
            continue
        ys.append(cy[keep])
        xs.append(cx[keep])
        ss.append(np.full(keep.sum(), cs, dtype=np.int64))
        ds.append(np.full(keep.sum(), cd, dtype=np.int64))

    if not ys:
        return (np.zeros(0, np.int64),) * 4
    return (
        np.concatenate(ys),
        np.concatenate(xs),
        np.concatenate(ss),
        np.concatenate(ds),
    )


def solve_tau(field, h, w, target_n, dmin=2, min_cell=4, dmax=None, iters=24, tol=0.02,
              tau_floor=0.0):
    """Binary-search tau for a target leaf count. Leaf count is monotone decreasing in tau.

    Exists so A/B arms compare at EQUAL stroke count -- otherwise a comparison between two
    allocation strategies is confounded by how many strokes each happened to place, and
    means nothing (spec §4.6).
    """
    def count(t):
        return build(field, h, w, t, dmin, min_cell, dmax, tau_floor)[0].size

    # The tree has a hard leaf ceiling at (h/min_cell)*(w/min_cell): past that there is
    # nothing left to subdivide. Report it rather than silently undershooting -- the
    # first sweep asked for 2500 strokes, got 1533, and looked like a tuning failure when
    # it was actually the floor.
    ceiling = count(0.0)
    reachable = target_n <= ceiling

    lo, hi = 0.0, 1.0
    for _ in range(40):  # grow hi until it under-shoots the target
        if count(hi) <= target_n:
            break
        hi *= 4.0

    best, best_err = hi, abs(count(hi) - target_n)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        n = count(mid)
        err = abs(n - target_n)
        if err < best_err:  # keep the CLOSEST tau, not merely the last one probed
            best, best_err = mid, err
        if err <= tol * target_n:
            break
        if n > target_n:
            lo = mid
        else:
            hi = mid
    return best, reachable, ceiling
