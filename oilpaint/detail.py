"""Detail metrics -- where the quadtree should spend strokes.

Every metric answers one question: *can a single flat colour stand in for this cell?*
All five are O(1) per cell (summed-area tables, or a mip pyramid for `range`), so the tree
build costs O(pixels) no matter how deep it goes.

Spec §4.1. `var` is the default and the classic quadtree criterion [Samet84]; `residual`
is the Hertzmann-flavoured one [Hertzmann98]; `dct` is the JPEG proxy.
"""

import numpy as np

from .image import gaussian_kernel1d, luma, pad_to_square, sat_box, summed_area

METRICS = ("var", "range", "grad", "dct", "residual")

# ITU-T T.81 / ISO-IEC 10918-1 Annex K, the JPEG example luminance quantization table.
JPEG_LUMA_Q = np.array(
    [
        [16, 11, 10, 16, 24, 40, 51, 61],
        [12, 12, 14, 19, 26, 58, 60, 55],
        [14, 13, 16, 24, 40, 57, 69, 56],
        [14, 17, 22, 29, 51, 87, 80, 62],
        [18, 22, 37, 56, 68, 109, 103, 77],
        [24, 35, 55, 64, 81, 104, 113, 92],
        [49, 64, 78, 87, 103, 121, 120, 101],
        [72, 92, 95, 98, 112, 100, 103, 99],
    ],
    dtype=np.float64,
)


def _dct8_matrix():
    n = 8
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m *= np.sqrt(2.0 / n)
    m[0] *= np.sqrt(0.5)
    return m


def _sobel(lum):
    """Explicit 3x3 Sobel, separable. No cv2 -- this has to port (package docstring)."""
    from .image import convolve1d

    smooth = np.array([1.0, 2.0, 1.0], dtype=np.float32) / 4.0
    diff = np.array([-1.0, 0.0, 1.0], dtype=np.float32) / 2.0
    gx = convolve1d(convolve1d(lum, smooth, 0), diff, 1)
    gy = convolve1d(convolve1d(lum, diff, 0), smooth, 1)
    return gx, gy


def _jpeg_energy(lum):
    """Per-pixel map of how much JPEG would have to spend on this pixel's 8x8 block.

    Energy is sum over the AC coefficients of (coef / q)^2 -- i.e. the number of
    quantization levels the block actually uses. Broadcast back over the block so it can
    go through a SAT like every other metric.

    Caveat carried from spec §4.1: this grid is fixed at 8x8 while the tree is
    multi-scale, so the block structure can print through. That is what experiment 1 is
    for; a negative result here is a finding, not a bug.
    """
    h, w = lum.shape
    ph, pw = (-h) % 8, (-w) % 8
    p = np.pad(lum, ((0, ph), (0, pw)), mode="edge") * 255.0
    bh, bw = p.shape[0] // 8, p.shape[1] // 8
    blocks = p.reshape(bh, 8, bw, 8).transpose(0, 2, 1, 3)  # (bh, bw, 8, 8)
    d = _dct8_matrix()
    coef = d @ blocks @ d.T
    scaled = (coef / JPEG_LUMA_Q) ** 2
    scaled[:, :, 0, 0] = 0.0  # DC is the flat colour the stroke already carries
    energy = scaled.sum(axis=(2, 3))
    full = np.repeat(np.repeat(energy, 8, axis=0), 8, axis=1)
    return full[:h, :w]


class DetailField:
    """Precomputes whatever the chosen metric needs; `score()` is then O(1) per cell."""

    def __init__(self, rgb, metric="var", tree_size=None):
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; expected one of {METRICS}")
        self.metric = metric
        self.shape = rgb.shape[:2]
        lum = luma(rgb).astype(np.float32)
        self.lum = lum

        if metric in ("var", "residual"):
            self.sat = summed_area(lum)
            self.sat2 = summed_area(lum.astype(np.float64) ** 2)
        elif metric == "grad":
            gx, gy = _sobel(lum)
            self.sat = summed_area(np.hypot(gx, gy))
        elif metric == "dct":
            self.sat = summed_area(_jpeg_energy(lum))
        elif metric == "range":
            # min/max cannot go through a SAT. Quadtree cells are power-of-two aligned,
            # so a mip pyramid gives the exact cell extremum in one lookup.
            size = tree_size or 1 << int(np.ceil(np.log2(max(self.shape))))
            p = pad_to_square(lum, size)
            self.mip_min, self.mip_max = [p], [p]
            while self.mip_min[-1].shape[0] > 1:
                a, b = self.mip_min[-1], self.mip_max[-1]
                n = a.shape[0] // 2
                self.mip_min.append(a.reshape(n, 2, n, 2).min(axis=(1, 3)))
                self.mip_max.append(b.reshape(n, 2, n, 2).max(axis=(1, 3)))

    def mean_luma(self, y0, x0, size):
        total, area = sat_box(self.sat, y0, x0, y0 + size, x0 + size)
        return np.where(area > 0, total / np.maximum(area, 1.0), 0.0)

    def score(self, y0, x0, size, parent_mean=None):
        """Detail score per cell. `size` is a scalar (one tree level at a time)."""
        y1, x1 = y0 + size, x0 + size

        if self.metric == "var":
            s1, area = sat_box(self.sat, y0, x0, y1, x1)
            s2, _ = sat_box(self.sat2, y0, x0, y1, x1)
            a = np.maximum(area, 1.0)
            var = s2 / a - (s1 / a) ** 2
            return np.where(area > 0, np.maximum(var, 0.0), 0.0)

        if self.metric == "residual":
            # MSE of this cell's pixels against the PARENT's flat colour: "how wrong is
            # the colour already on the canvas here?" [Hertzmann98]. Expanded from
            # E[(L-m)^2] = E[L^2] - 2 m E[L] + m^2 so it stays O(1) through the SATs.
            s1, area = sat_box(self.sat, y0, x0, y1, x1)
            s2, _ = sat_box(self.sat2, y0, x0, y1, x1)
            a = np.maximum(area, 1.0)
            m = self.mean_luma(y0, x0, size) if parent_mean is None else parent_mean
            mse = s2 / a - 2.0 * m * (s1 / a) + m * m
            return np.where(area > 0, np.maximum(mse, 0.0), 0.0)

        if self.metric in ("grad", "dct"):
            total, area = sat_box(self.sat, y0, x0, y1, x1)
            return np.where(area > 0, total / np.maximum(area, 1.0), 0.0)

        if self.metric == "range":
            lvl = int(np.log2(size))
            lvl = min(lvl, len(self.mip_min) - 1)
            iy, ix = y0 >> lvl, x0 >> lvl
            n = self.mip_min[lvl].shape[0]
            iy = np.clip(iy, 0, n - 1)
            ix = np.clip(ix, 0, n - 1)
            return (self.mip_max[lvl][iy, ix] - self.mip_min[lvl][iy, ix]).astype(np.float64)

        raise AssertionError("unreachable")


class FovealField:
    """A `DetailField` reweighted by a hand-painted importance map.

    Content-based allocation answers "where is this image hard to approximate with one flat
    colour", which is *not* the question a viewer's eye asks -- a gravel path outscores a
    face, every time. This puts the second question back in the loop as an INPUT rather than
    a model: a coarse map in [0,1], 1 = look here, painted by hand.

    Painted by hand and NOT prefilled from a saliency guess, which the tuner page briefly
    had. The reason is structural rather than a matter of the guess being poor: a cheap
    saliency field is smooth and non-zero nearly everywhere, so its MEAN over a cell barely
    varies from cell to cell -- and a weight that is near-constant is a weight `solve_tau`
    absorbs by construction (see the tau note below). Anything auto-generated has to be
    SPARSE to move this allocator at all.

    ONE knob, and one weight per cell, from the mask's MEAN over that cell -- an O(1)
    summed-area lookup like every metric above it:

        w      = (1 - strength) + strength * mask_mean
        score' = score * w

    `strength` is the whole control surface: it slides continuously from the content metric
    alone (0) to the map alone (1), so there is no separate blend/replace mode to keep in
    step with it. An earlier draft had one, and a `coarsen` knob that let an unmarked region
    keep strokes above `max_cell` by reaching into `quadtree.build`; both are gone. The
    coarsening in particular moved the stroke-size distribution far too abruptly to be
    tunable -- one octave of it emptied every intermediate cell size out of the tree.

    A wrapper rather than a subclass because the mask belongs to the REQUEST, not to the
    image: the browser caches one DetailField per metric and rebuilds this on every brush
    stroke, and the two lifetimes must not be tied together.

    Two properties worth knowing before tuning it:

    - `solve_tau` searches against the weighted score, so the stroke BUDGET is preserved
      and strokes MIGRATE into the fovea rather than being added to it -- an A/B against
      the unweighted tree is still at matched stroke count (spec §4.6). With one exception,
      and it is not a bug: at strength = 1 the weight reaches 0, an unmarked cell scores 0
      and can never beat tau, so the map becomes a hard GATE and the budget is capped by
      what the marked region alone can hold. `solve_tau` reports that as usual through
      `budget_reachable`, and the tuner's stats line says so.
    - An unpainted mask is a CONSTANT weight, and the tau search absorbs any constant, so
      below strength 1 it spends the same budget on the same tree. At strength 0 it is not
      merely equivalent but bit-identical, because `plan` does not wrap the field at all.
      The exception is strength exactly 1, where the constant is ZERO and the gate above
      closes on everything: a blank map then yields the bare max_cell grid. The tuner never
      sends a map with no ink on it, and tests/test_core.py pins all three cases.
    """

    def __init__(self, field, mask, strength=1.0):
        mask = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
        if mask.shape != field.shape:
            raise ValueError(f"foveal map {mask.shape} != image {field.shape}")
        self.inner = field
        self.metric = field.metric  # quadtree reads this to decide on the parent mean
        self.shape = field.shape
        self.strength = float(strength)
        self.mask_sat = summed_area(mask)

    def mask_mean(self, y0, x0, size):
        total, area = sat_box(self.mask_sat, y0, x0, y0 + size, x0 + size)
        return np.where(area > 0, total / np.maximum(area, 1.0), 0.0)

    def weight(self, y0, x0, size):
        return (1.0 - self.strength) + self.strength * self.mask_mean(y0, x0, size)

    def score(self, y0, x0, size, parent_mean=None):
        return self.inner.score(y0, x0, size, parent_mean=parent_mean) * \
            self.weight(y0, x0, size)

    def mean_luma(self, y0, x0, size):
        """Unweighted, always -- this is a COLOUR travelling down to the residual metric,
        not a score. Weighting it would tint the tree's own reference value."""
        return self.inner.mean_luma(y0, x0, size)
