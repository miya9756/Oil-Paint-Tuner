"""Quadtree cells -> brush strokes.

The five steps of the original proposal, with the spec's §1 changes folded in:

  1. centre     = cell centre + jitter          (jitter is REQUIRED -- kills the lattice)
  2. radius     = kappa * edge/2, jittered       (kappa = sqrt(2) exactly covers the cell)
  3. orientation= structure tensor + jitter      (change A; [Litwinowicz97])
  4. elongation = f(coherence), jittered         (change B)
  5. colour     = per-channel median of the cell (median, not mean -- see below)

Spec §4.3-4.8.
"""

from dataclasses import dataclass

import numpy as np

from . import flow as flow_mod
from . import regions as regions_mod
from .tensor import sample, sample_angle

# Exactly covers a square cell: the half-diagonal is sqrt(2) * half-edge. Below this a
# circle inscribed in the cell covers only pi/4 ~ 78.5% of it and the missing corners are
# the "black holes". Not a tuned constant (spec §4.4).
KAPPA_FULL_COVER = float(np.sqrt(2.0))


# Ceiling on `covering_kappa`. The correction below is a ratio of two worst cases, so it
# runs away at the far end of two sliders that are individually reasonable: at the tuner's
# limits (jitter_centre 1.0, jitter_radius 0.8) it asks for kappa 14.14. On a 1000px canvas
# at the default cell that is a 905px nominal radius, 2391px at the top of the size_sigma
# tail: 64 strokes each covering the whole canvas, at 13.8s a render against 9.0s capped and
# 3.5s for base='blur'.
#
# 4.0 rather than something tighter because it is a CEILING, not a target: measured at those
# same worst-case jitters over three seeds, kappa 3.0 already covers 1.0000 and 2.5 does not
# (0.9965 on one of them), so 4.0 keeps a third again of margin over the smallest value that
# demonstrably covers. It is above `covering_kappa` at every default (2.25), so it changes no
# painting anyone is making -- it only stops the pathological corner.
#
# What is genuinely lost past this point cannot be bought with a bigger brush anyway: a
# stroke that spans several cells paints them all with ONE cell's median, so the ground's
# colour resolution collapses to whichever blob was drawn last. The residual holes at
# extreme jitter want less jitter, not more paint.
KAPPA_COVER_MAX = 4.0


def covering_kappa(jitter_centre, jitter_radius):
    """kappa that still covers the cell AFTER the centre and the radius have been jittered.

    KAPPA_FULL_COVER is derived for a circle sitting exactly on the cell centre at exactly
    its nominal radius, and `from_cells` then moves it and shrinks it. So on its own it is a
    covering guarantee for a stroke this code never actually places -- which is how the
    underpainting, whose entire job is to cover, came to leave 30% of the canvas bare while
    the call site said "this layer must actually cover".

    Two corrections, both worst-case rather than average, because a covering condition that
    holds on average does not hold:

      * the centre moves by up to `jitter_centre` * half along each axis, so the far corner
        recedes by that factor of the half-diagonal -> (1 + jitter_centre).
      * the radius is scaled by as little as (1 - jitter_radius) -> divide by it.

    Deliberately NOT corrected for `size_sigma`: the log-normal shrinks some strokes and
    grows others, and coverage is a UNION, so the upper tail more than pays for the lower
    one -- measured, removing size_sigma made the underpainting worse (0.70 -> 0.63), not
    better. Nor for `taper_amp`, which only thins one end of a stroke that is now overlapping
    its neighbours 2.25x; adding that term costs a much larger brush and buys nothing
    measurable (coverage is already 1.000 without it).

    The ELONGATION correction is missing on purpose too, and that one is not an omission but
    a design decision recorded at the call site: it would be sqrt(aniso_max), which at the
    tuner's upper end asks for kappa ~5.2 and a brush wider than the canvas. The
    underpainting goes round instead. See pipeline.plan.

    Capped at KAPPA_COVER_MAX, which the two corrections can otherwise run far past -- see
    there for what that costs and why 4.0 is where it stops.
    """
    k = KAPPA_FULL_COVER * (1.0 + jitter_centre) / max(1e-6, 1.0 - jitter_radius)
    return min(k, KAPPA_COVER_MAX)


@dataclass
class StrokeBuffer:
    """Struct-of-arrays, sorted so that ARRAY ORDER IS PAINT ORDER.

    This is the cross-language contract of spec §9: the browser uploads these as typed
    arrays and draws straight through, with no sort of its own.

    `phase` seeds the per-stroke outline wobble in render.py. It is carried explicitly
    rather than hashed from the array index so that the JS side cannot drift from the
    Python by picking a different hash.
    """

    x: np.ndarray
    y: np.ndarray
    r_major: np.ndarray
    r_minor: np.ndarray
    theta: np.ndarray
    rgb: np.ndarray
    alpha: np.ndarray
    phase: np.ndarray = None

    def __post_init__(self):
        if self.phase is None:
            self.phase = np.zeros(self.x.size, dtype=np.float32)

    def __len__(self):
        return self.x.size


def concat(*buffers):
    """Join stroke layers. Order matters: earlier buffers are painted FIRST (underneath)."""
    bs = [b for b in buffers if b is not None and len(b)]
    if not bs:
        raise ValueError("nothing to concatenate")
    return StrokeBuffer(
        **{
            f: np.concatenate([getattr(b, f) for b in bs], axis=0)
            for f in ("x", "y", "r_major", "r_minor", "theta", "rgb", "alpha", "phase")
        }
    )


def uniform_grid(h, w, cell):
    """A full-coverage grid of cells at one scale -- the underpainting layer.

    Hertzmann's first pass is a complete canvas of the largest brush [Hertzmann98]; only
    later layers are selective. Our adaptive tree skips flat regions entirely, so without
    this the sky is base layer rather than paint.
    """
    cell = max(1, int(cell))
    ny, nx = -(-h // cell), -(-w // cell)
    gy, gx = np.meshgrid(np.arange(ny) * cell, np.arange(nx) * cell, indexing="ij")
    n = gy.size
    return (
        gy.ravel().astype(np.int64),
        gx.ravel().astype(np.int64),
        np.full(n, cell, dtype=np.int64),
        np.zeros(n, dtype=np.int64),
    )


def _cell_colors(rgb, y0, x0, size, mode):
    """Per-cell colour. Cells of one size are processed together via a reshape.

    `median` is the default and it matters: a cell straddling an edge has a MEAN that is
    a muddy intermediate colour, which shows up in the painting as blur exactly where the
    subdivision worked hardest to find detail (spec §4.8).
    """
    h, w = rgb.shape[:2]
    n = y0.size
    out = np.zeros((n, 3), dtype=np.float32)

    if mode == "centre":
        # Haeberli's original: sample the source at the stroke's own position.
        cy = np.clip(y0 + size // 2, 0, h - 1)
        cx = np.clip(x0 + size // 2, 0, w - 1)
        return rgb[cy, cx].astype(np.float32)

    # Gather each cell's pixels. Cells hang off the edge of the image, so clamp the
    # sample grid rather than dropping the cell -- an edge cell keeps its inside colour.
    off = np.arange(size)
    gy = np.clip(y0[:, None] + off[None, :], 0, h - 1)
    gx = np.clip(x0[:, None] + off[None, :], 0, w - 1)
    patch = rgb[gy[:, :, None], gx[:, None, :]]  # (n, size, size, 3)
    flat = patch.reshape(n, size * size, 3)

    if mode == "median":
        out = np.median(flat, axis=1)
    elif mode == "mean":
        out = flat.mean(axis=1)
    else:
        raise ValueError(f"unknown colour mode {mode!r}")
    return out.astype(np.float32)


# Weight of the GLOBAL fallback direction, relative to `flat_dir`, where neither the fine
# nor the coarse tensor has any confidence at all -- a genuinely uniform ground, where
# there is no gradient to follow and something has to decide. 0.35 puts the effective
# coherence there at 0.35 * flat_dir, i.e. a modest elongation (ratio ~2.75 at aniso_max 6)
# rather than the long dragged marks a real gradient earns. Not a knob: what it would tune
# is already reachable through `flat_dir`, and the DIRECTION it points is `flat_theta_deg`.
FLAT_GLOBAL = 0.35


def flat_blend(t_fine, coh_fine, t_flat, coh_flat, flat_dir, flat_theta_deg):
    """Fold a coarse-scale orientation field into the fine one, in doubled-angle space.

    The problem this solves is visible in any sky or gradient ground: the structure tensor
    at the detail scale has no signal there, so `theta` is the arctangent of noise and
    `coherence` is ~0. Step 4 then reads that as "isotropic, stay round", and the region
    comes out as a field of randomly-turned round dabs -- the blobs. A painter does the
    opposite: the flatter the passage, the more it is laid in with one directional sweep.

    Three candidate directions, summed as VECTORS at the doubled angle (the only way to
    average a pi-periodic orientation) with weights that hand over as confidence runs out:

        fine    c                            -- the local structure, wherever it exists
        coarse  flat_dir * cf * (1 - c)      -- the broad gradient, where the fine one gave up
        global  flat_dir * FLAT_GLOBAL * (1 - c) * (1 - cf)   -- neither: sweep at one angle

    and the effective coherence is the LENGTH of that sum, clipped to 1. Three properties
    fall out of that choice rather than being bolted on:

      * at c = 1 the other two weights are 0, so a coherent edge is untouched -- this
        cannot disturb the places that already worked.
      * where the two scales AGREE the length adds, so a flat gradient recovers a high
        coherence and step 4 stretches the stroke along it. That is the whole effect.
      * where they DISAGREE the length cancels, so an ambiguous neighbourhood goes rounder
        instead of committing to one of two directions at random. A max() or a mode switch
        would have committed; this is why the sum is over vectors and not over angles.

    Returns (theta_base, coh_eff). `coh_eff` comes back float32 because the coherence it
    replaces is sampled from a float32 field, and that dtype is load-bearing downstream:
    it is what keeps `ratio` float32 until the jitter promotes it (see the header).
    """
    c = coh_fine.astype(np.float64)
    cf = coh_flat.astype(np.float64)
    w_fine = c
    w_coarse = flat_dir * cf * (1.0 - c)
    w_global = flat_dir * FLAT_GLOBAL * (1.0 - c) * (1.0 - cf)

    tg = np.deg2rad(flat_theta_deg)
    vx = w_fine * np.cos(2.0 * t_fine) + w_coarse * np.cos(2.0 * t_flat) + \
        w_global * np.cos(2.0 * tg)
    vy = w_fine * np.sin(2.0 * t_fine) + w_coarse * np.sin(2.0 * t_flat) + \
        w_global * np.sin(2.0 * tg)

    theta = 0.5 * np.arctan2(vy, vx)
    coh = np.minimum(np.sqrt(vx * vx + vy * vy), 1.0)
    return theta, coh.astype(np.float32)


def from_cells(
    rgb,
    cells,
    theta_field,
    coherence,
    *,
    kappa=1.15,
    jitter_centre=0.35,
    jitter_radius=0.15,
    jitter_theta_deg=20.0,
    jitter_aniso=0.2,
    aniso_max=3.0,
    orient="structure",
    theta_flat=None,
    coh_flat=None,
    flat_step=1,
    flat_dir=0.0,
    flat_theta_deg=45.0,
    flow=None,
    flow_regions=None,
    color="median",
    alpha=1.0,
    drop_p=0.0,
    size_sigma=0.0,
    color_jitter=0.0,
    seed=0,
    return_order=False,
):
    y0, x0, size, depth = cells
    rng = np.random.default_rng(seed)
    n = y0.size
    h, w = rgb.shape[:2]

    # --- 1. centre + jitter (spec §4.3) -------------------------------------------
    # Without this the centres sit on a regular lattice and the result reads as a mosaic
    # filter rather than a painting. This is stratified (jittered-grid) sampling: the
    # lattice goes, the density the tree worked out stays.
    half = size / 2.0
    cy = y0 + half + rng.uniform(-1.0, 1.0, n) * jitter_centre * half
    cx = x0 + half + rng.uniform(-1.0, 1.0, n) * jitter_centre * half

    # --- 2. radius (spec §4.4) ----------------------------------------------------
    # Jittered continuously, which also smears the power-of-two size banding across
    # octave boundaries.
    r = kappa * half * (1.0 + rng.uniform(-1.0, 1.0, n) * jitter_radius)

    # Log-normal size spread on top of the uniform jitter. The uniform term is bounded by
    # +-jitter_radius and cannot escape its octave, so within a flat region every stroke
    # still came out the same size -- which is a large part of what reads as "regular".
    # A log-normal has a tail, so a few strokes are markedly larger or smaller, and it is
    # multiplicative so it does not depend on the cell's absolute scale.
    if size_sigma > 0:
        r = r * np.exp(size_sigma * rng.standard_normal(n))

    # Random drop with area-compensating expansion. Total painted area is preserved by
    # scaling radius by 1/sqrt(1-p): area goes as r^2, and (1-p) of the strokes survive.
    # NOTE this is content-blind -- it can delete a stroke that carried a highlight, and
    # the neighbours that expand into the gap bring their own colour, not the lost one.
    # That is why the drop happens BEFORE colour is sampled and why the underpainting
    # layer matters: a dropped stroke reveals paint, not canvas.
    keep = np.ones(n, dtype=bool)
    if drop_p > 0:
        keep = rng.random(n) >= drop_p
        r = r / np.sqrt(max(1e-6, 1.0 - drop_p))

    # --- 3. orientation (spec §4.5, change A) -------------------------------------
    # `coh` is settled here rather than in step 4 when the flat-region brush is on,
    # because the blend produces the angle and the effective coherence from one vector
    # sum and they have to agree. It consumes NO random numbers, so the draw order --
    # which is the algorithm, see the JS header -- is the same either way.
    coh = None
    if orient == "structure":
        base = sample_angle(theta_field, cy, cx)
        if flat_dir > 0.0 and theta_flat is not None:
            # The flat field lives on a grid decimated by `flat_step` (tensor.flat_tensor),
            # so its sample coordinates are the stroke's divided by that. Exactly cy at
            # step 1, which is the undecimated case and stays bit-identical to it.
            fy, fx = cy / flat_step, cx / flat_step
            base, coh = flat_blend(
                base, sample(coherence, cy, cx),
                sample_angle(theta_flat, fy, fx), sample(coh_flat, fy, fx),
                flat_dir, flat_theta_deg,
            )
        # The artistic flow field, LAST of the three orientation terms, so it decides what
        # the picture's own structure has already said rather than being averaged into it.
        # Structure mode only: 'random' and 'fixed' are A/B diagnostics, not looks, and a
        # style laid over the arctangent of noise would be measuring nothing. Consumes no
        # random numbers, exactly like the two above -- see flow.py.
        if flow is not None or flow_regions is not None:
            u, v = flow_mod.norm_coords(cy, cx, h, w)
            if coh is None:
                coh = sample(coherence, cy, cx)
            # ONE BLOCK OR SEVERAL, down one code path. `groups` is a list of (block,
            # where), and `where` is a full slice for the undivided picture -- which is
            # then literally the arithmetic this did before regions could name a flow, on
            # the same whole arrays, rather than an equivalent of it. The gather/scatter
            # arm is bit-identical to it because every operation from here down is
            # elementwise (see flow.flow_blend), which is the same argument regions.py
            # already makes for the grade, and tests/test_core.py pins it the same way.
            #
            # The label comes off `cy`/`cx` BEFORE any drift, because drift is this
            # field's own output and so cannot be what chooses it. See regions.py.
            if flow_regions is None:
                groups = [(flow, slice(None))]
            else:
                blocks = flow_regions["blocks"]
                rl = regions_mod.stroke_labels(flow_regions, cx, cy)
                mine = np.isin(rl, sorted(blocks))
                groups = [(flow, np.flatnonzero(~mine))]
                groups += [(blocks[rid], np.flatnonzero(rl == rid))
                           for rid in sorted(blocks)]
            # float64 up front, so a subset's blended angle can be written back without the
            # float32 `sample_angle` result silently rounding it. Exact for the undivided
            # case: `flow_blend` promotes its input the same way and step 4's float64
            # jitter would have promoted the result regardless.
            base = np.asarray(base, dtype=np.float64)
            coh = np.asarray(coh, dtype=np.float32)
            for p, where in groups:
                # A region that asked for no flow at all keeps the picture's own
                # orientation, untouched -- inert by skipping, one level down.
                if p is None or (not isinstance(where, slice) and where.size == 0):
                    continue
                # The hand-placed swirl centres, through the SAME transform as the strokes
                # above, so one placed at a pixel lands where a stroke at that pixel lands.
                # None -- nothing placed, or a field kind with no vortices -- is the spiral.
                cen = flow_mod.field_centres(p, h, w)
                b, c = flow_mod.flow_blend(base[where], coh[where],
                                           u[where], v[where], p, cen)
                base[where] = b
                coh[where] = c
                # DRIFT: slide the stroke along its own (now styled) axis. This is the only
                # term in the whole feature that moves a mark instead of turning it, and it
                # is what makes marks lying along a flow line bunch and read as a CHAIN
                # tracing the curve -- van Gogh's sky is chains, and orientation alone
                # cannot give them. It costs no draw, and `colors` below is sampled from the
                # CELL (y0/x0), not from the centre, so a drifted stroke still carries its
                # own cell's colour.
                if p["drift"] != 0.0:
                    cy[where] = cy[where] + p["drift"] * r[where] * np.sin(b)
                    cx[where] = cx[where] + p["drift"] * r[where] * np.cos(b)
    elif orient == "random":
        base = rng.uniform(-np.pi / 2, np.pi / 2, n)
    elif orient == "fixed":
        base = np.zeros(n)
    else:
        raise ValueError(f"unknown orient mode {orient!r}")
    theta = base + np.deg2rad(jitter_theta_deg) * rng.uniform(-1.0, 1.0, n)

    # --- 4. elongation (spec §4.5, change B) --------------------------------------
    if coh is None:
        coh = sample(coherence, cy, cx) if orient == "structure" else np.ones(n) * 0.5
    ratio = 1.0 + (aniso_max - 1.0) * coh
    ratio = np.maximum(ratio * (1.0 + rng.uniform(-1.0, 1.0, n) * jitter_aniso), 1e-3)
    # Area-preserving: r_major * r_minor == r^2, so elongation changes stroke SHAPE
    # without changing painted area -- and therefore without silently moving the
    # coverage statistics between A/B arms.
    root = np.sqrt(ratio)
    r_major = r * root
    r_minor = r / root

    # --- 5. colour (spec §4.8) ----------------------------------------------------
    colors = np.zeros((n, 3), dtype=np.float32)
    for s in np.unique(size):
        m = size == s
        colors[m] = _cell_colors(rgb, y0[m], x0[m], int(s), color)

    # Per-stroke colour jitter [Litwinowicz97], which perturbs stroke colour for the same
    # "hand-touched" reason it perturbs length and angle. Applied as one luminance shift
    # plus a smaller per-channel shift: a pure per-channel jitter desaturates towards
    # grey in aggregate, while a shared luminance term reads as pigment loaded unevenly.
    if color_jitter > 0:
        lum_shift = color_jitter * rng.standard_normal(n)[:, None]
        chan_shift = 0.4 * color_jitter * rng.standard_normal((n, 3))
        colors = np.clip(colors + lum_shift + chan_shift, 0.0, 1.0).astype(np.float32)

    phase = rng.uniform(0.0, 2.0 * np.pi, n)

    # --- paint order: coarse to fine [Hertzmann98] --------------------------------
    # Big strokes first, fine detail on top; a seeded shuffle within each level so equal
    # sized strokes do not overlap in scan order (which reads as a raster sweep).
    key = rng.random(n) + depth.astype(np.float64)
    order = np.argsort(key, kind="stable")
    order = order[keep[order]]

    sb = StrokeBuffer(
        x=cx[order].astype(np.float32),
        y=cy[order].astype(np.float32),
        r_major=r_major[order].astype(np.float32),
        r_minor=r_minor[order].astype(np.float32),
        theta=theta[order].astype(np.float32),
        rgb=colors[order],
        alpha=np.full(order.size, alpha, dtype=np.float32),
        phase=phase[order].astype(np.float32),
    )
    # `order` maps a stroke back to the CELL it came from, which is the one thing a caller
    # cannot reconstruct without redrawing the whole rng stream -- and redrawing it
    # elsewhere is precisely what this package's rules forbid, since the draw order IS the
    # algorithm. It exists for any caller that wants to re-colour a frozen layout without
    # re-running this function. Purely additive: the default return, the draws and their
    # order are untouched, so the JS port and every parity arm are unaffected.
    return (sb, order) if return_order else sb
