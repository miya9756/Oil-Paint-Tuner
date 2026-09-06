"""Reference colour: match a named painting's colour distribution, by optimal transport.

The hue band next door is a scalpel and it asks you to already know what you want. This is
the other kind of control: pick a painting, and the photograph's colours are moved onto its
colour statistics. Nothing here is a filter with a look baked into it -- the transform is
derived from the two distributions, so the same reference does something different to a
sunset than it does to a portrait, which is what "adapt this image to that palette" has to
mean.

WHY OPTIMAL TRANSPORT, AND WHICH ONE. The classic answer is Reinhard 2001: match the mean
and standard deviation per channel in a decorrelated space. It is one line and it ignores
the CORRELATION between channels, which is most of what a painting's colour is -- "the
darks go blue and the lights go yellow" is a covariance, not two variances. The fix is the
linear Monge-Kantorovich map (Pitie & Kokaram 2007), which is the optimal transport between
two Gaussians and has a closed form:

    T = Ss^-1/2 (Ss^1/2 St Ss^1/2)^1/2 Ss^-1/2,      x' = mu_t + T (x - mu_s)

It is unique, it is the gradient of a convex function (so it cannot fold the colour space
over on itself), it needs no iteration and no random projection, and it is 3x3 -- which is
why it survives into a repo that has to run the same arithmetic in two languages and prove
it. `web/tune/oilpaint/reference.js` is a transliteration of this file.

WHAT WE ARE NOT DOING, and why, since the newer options are the obvious question:

  * FULL-DISTRIBUTION transport -- Pitie's IDT, or its modern name, sliced Wasserstein:
    project onto many 1-D directions, sort, repeat. It matches the whole histogram rather
    than two moments, and on a PAINTING as the reference that is the wrong target. A
    painting's histogram is spiky, because a painter used eleven tubes; forcing a
    photograph's smooth histogram onto those spikes quantises it, and the artefact is
    posterisation in exactly the smooth passages -- skies, skin -- that a viewer looks at.
    It is also iterative and stochastic, which this project would have to pin.
  * NEURAL transfer -- AdaIN, WCT, PhotoWCT, and the diffusion recolourers. Worth knowing
    that the two classical ones are this same maths in disguise: AdaIN is Reinhard on deep
    features, and WCT's whitening-colouring transform IS a Gaussian transport. They are out
    of scope here for an architectural reason rather than a quality one -- the deployed page
    downloads nothing at runtime (no CDN, no WASM, no model), and a network's output cannot
    be held to the float-epsilon parity every other stage in this repo is held to.
  * And the spiky half is already covered, better: `palette.py`'s pigment projection pulls
    each stroke onto a colour a named set of real tubes can mix. So the division is
    deliberate -- THIS matches the statistics, THAT enforces the gamut, and they compose:
    `--reference starry-night --palette impressionist` moves the colour cloud onto Van
    Gogh's and then only lets the strokes land on mixtures his palette can reach.

WHERE THE NUMBERS COME FROM, and this is the part to read before trusting them. Each entry
below is a set of KEY COLOURS WITH AREA WEIGHTS, authored from the pigments the painting is
documented to use and from what its reproductions plainly show -- the same epistemic status
as `palette.PIGMENTS`, which says of itself that it is eyeballed masstone approximations
rather than spectrophotometry. They are NOT measured from a calibrated scan, and this file
must not pretend otherwise. What they capture is the thing that survives reproduction and
matters here: which colours a painting is mostly made of, in what proportion, and how they
covary. `scripts/extract_reference.py` measures a real image and prints a block in this
format, so any of these can be replaced with a measurement of a scan you have the rights to.

The statistics are computed FROM the key colours at import time rather than being written
out as nine covariance numbers, so the table stays readable and a change to a colour cannot
silently disagree with a hand-typed matrix.
"""

import math

import numpy as np

from .image import linear_to_srgb, srgb_to_linear
from .palette import _to_oklab

# Within-passage variation the key colours cannot carry. A painting is not six flat areas:
# every passage has its own modelling, and without this the covariance is the scatter of a
# handful of points and comes out far too tight, which makes the transport over-confident
# and posterises the result. Isotropic in OKLab and small -- 0.045 is roughly the spread of
# one passage of paint -- so it widens the target without steering it.
GRAIN = 0.045

# Eigenvalues below this are lifted to it before any inverse square root. A photograph with
# almost no colour variation (a fog, a monochrome) has a near-singular covariance, and
# Ss^-1/2 is then unbounded: the transport would take three pixels' worth of noise and
# stretch it across the reference's whole gamut. 1e-6 is a standard deviation of 1e-3 in
# OKLab, which is below one 8-bit level, so this only ever bites the degenerate case.
EIG_FLOOR = 1e-6

# A floor on the SOURCE distribution's standard deviation, per principal direction, before
# it is inverted. This is the single most important number in the file and it was learned
# the expensive way: without it, matching a nearly-monochrome photograph produced garish
# saturated speckle, and the reason is visible in one number.
#
# A photograph's colour cloud is often a thin SLIVER -- a field of sunflowers varies almost
# entirely along one direction in OKLab. The transport has to stretch that sliver onto a
# target that is fat in three directions, and the gain it needs along the thin one is
# sd_target / sd_source. Measured on the sample: the source's third direction had sd 0.006
# against the reference's 0.087, and the map came out with an eigenvalue of 15.0 -- so three
# pixels' worth of sensor noise became three quarters of the OKLab gamut, went out of range,
# and CLIPPED, which is what scrambles a hue rather than merely shifting it.
#
# That is not a bug in the transport; it is the correct optimal map for two Gaussians, and
# it is the standard reason a colour transfer needs regularising. Flooring the source's
# variance is the regularisation: it says "treat no direction of this photograph as thinner
# than this", which caps the gain at sd_target / SRC_SD_FLOOR ~ 3.5 for the widest reference
# here. It is the exact counterpart of GRAIN above, which does the same job for the target.
#
# 0.025 rather than smaller: at 0.01 the speckle is still there (gain ~9), and rather than
# larger because past ~0.04 the floor starts dominating a normal photograph's real variation
# and the transport stops committing to the reference at all.
SRC_SD_FLOOR = 0.025

# Cyclic Jacobi sweeps for the 3x3 symmetric eigenproblem. Three sweeps is convergence to
# machine precision for a matrix this size; six is the same answer with margin, and a FIXED
# count is what makes the two languages agree -- a convergence test would let them take a
# different number of iterations on the same input.
JACOBI_SWEEPS = 6

# Each reference is (r, g, b, area weight) in sRGB, plus the pigments the painting is
# documented to have been made with, named in the comment because a colour with no
# provenance is a magic number.
REFERENCES = {
    # The identity, present as a name so the dropdown has an "off" that reads like the
    # others. `params_for` returns None on it and nothing here runs.
    "none": (),
    # Van Gogh, The Starry Night (1889). Ultramarine and cobalt over most of the canvas,
    # chrome yellow and zinc white in the stars and moon, viridian and Prussian blue in the
    # cypress, with the village a dark warm nothing. The weights are the point: this
    # painting is SEVENTY PERCENT dark blue, and a transfer that treats its yellow as an
    # equal partner produces a painting that looks nothing like it.
    "starry-night": ((0.055, 0.106, 0.310, 0.34),   # deep ultramarine night
                     (0.129, 0.220, 0.451, 0.24),   # cobalt, the swirling mid-sky
                     (0.043, 0.078, 0.157, 0.14),   # the darkest sky and the hills
                     (0.929, 0.827, 0.404, 0.10),   # chrome yellow, stars and moon
                     (0.086, 0.129, 0.110, 0.10),   # the cypress, near-black viridian
                     (0.396, 0.443, 0.353, 0.08)),  # the village, warm grey-green
    # Hokusai, Under the Wave off Kanagawa (c. 1831). A woodblock print, so the palette is
    # literally the blocks: Prussian blue in two strengths, the paper itself, and one warm
    # tan. Nothing else. The tightest reference here, and the one that most obviously
    # rearranges a photograph.
    "great-wave": ((0.075, 0.192, 0.325, 0.30),    # Prussian blue, full strength
                   (0.310, 0.463, 0.573, 0.22),    # the diluted blue of the mid-water
                   (0.878, 0.855, 0.784, 0.28),    # the paper: foam, sky, spray
                   (0.647, 0.639, 0.573, 0.12),    # the grey-tan of the boats and cloud
                   (0.259, 0.263, 0.243, 0.08)),   # the keyblock outline
    # Monet, Water Lilies. No black anywhere; the darks are ultramarine and violet, the
    # water is a green-blue that shifts warm where the sky is in it, and the blossoms are
    # the only high-chroma warm notes. Cobalt violet, ultramarine, viridian, cadmium.
    "water-lilies": ((0.216, 0.353, 0.373, 0.26),   # the water's own green-blue
                     (0.157, 0.239, 0.373, 0.20),   # ultramarine, the deep reflections
                     (0.353, 0.451, 0.404, 0.18),   # the lily pads
                     (0.478, 0.451, 0.596, 0.16),   # cobalt violet, the shadow note
                     (0.796, 0.741, 0.741, 0.12),   # the blossoms, barely pink
                     (0.643, 0.686, 0.612, 0.08)),  # sky caught on the surface
    # Van Gogh, Sunflowers (1888). Almost the whole picture is three yellows -- chrome
    # yellow, chrome orange, yellow ochre -- against a ground that is another one. The
    # narrowest HUE range of any reference here, which is what makes it drastic: it has
    # nowhere to put a blue.
    "sunflowers": ((0.929, 0.769, 0.192, 0.30),    # chrome yellow, the petals
                   (0.816, 0.596, 0.153, 0.24),    # chrome orange, the deeper petals
                   (0.702, 0.639, 0.353, 0.18),    # the ground behind them
                   (0.463, 0.376, 0.176, 0.14),    # ochre and umber, the seed heads
                   (0.353, 0.427, 0.278, 0.08),    # the one green, the stems
                   (0.898, 0.878, 0.741, 0.06)),   # the lit table edge
    # Munch, The Scream (1893). The sky is the subject: cadmium orange and vermilion in
    # bands over a fjord of Prussian blue and viridian, with the figure a sallow ochre.
    # Two nearly complementary clusters, which is a covariance no photograph has.
    "the-scream": ((0.855, 0.451, 0.157, 0.24),    # cadmium orange, the sky
                   (0.729, 0.259, 0.153, 0.16),    # vermilion, the deeper bands
                   (0.153, 0.243, 0.353, 0.22),    # Prussian blue, the fjord
                   (0.216, 0.318, 0.298, 0.16),    # viridian, the shore
                   (0.694, 0.612, 0.431, 0.12),    # the figure and the boardwalk
                   (0.129, 0.129, 0.153, 0.10)),   # the near-black of the railing
    # Vermeer, Girl with a Pearl Earring (c. 1665). A dark ground almost everywhere,
    # natural ultramarine in the turban, lead-tin yellow in its tail, and flesh built from
    # lead white and vermilion. The value range is the tightest here -- nothing in it is
    # bright -- which is what a reference is for when a photograph is too contrasty.
    "vermeer": ((0.075, 0.071, 0.071, 0.34),       # the ground, a warm near-black
                (0.157, 0.271, 0.443, 0.16),       # natural ultramarine, the turban
                (0.749, 0.643, 0.243, 0.12),       # lead-tin yellow, the turban's tail
                (0.808, 0.671, 0.573, 0.16),       # the lit flesh
                (0.475, 0.353, 0.298, 0.14),       # flesh in shadow, and the jacket
                (0.918, 0.898, 0.855, 0.08)),      # the collar and the pearl
}

# The order the panel offers them in; 'none' first because it is the default.
NAMES = list(REFERENCES)


def params_for(cfg):
    """The effective reference block for a config, or None when there is nothing to do.

    None rather than an identity transform, for the same reason `palette.params_for`
    returns None: an identity affine still round-trips every colour through OKLab, and
    float32 -> float64 -> cube root -> float32 is not the identity. A painting made
    without a reference has to stay bit-identical to one made before this file existed.
    """
    if cfg.reference not in REFERENCES:
        raise ValueError(f"unknown reference {cfg.reference!r}")
    s = float(cfg.reference_strength)
    if cfg.reference == "none" or not np.isfinite(s) or s <= 0.0:
        return None
    mu, cov = _target(cfg.reference)
    return {"name": cfg.reference, "strength": min(1.0, s), "mu": mu, "cov": cov}


def _target(name):
    """The reference's own (mean, covariance) in OKLab, from its key colours and weights.

    Weighted, because area is the whole point -- see the note on `starry-night`. The GRAIN
    term is added to the diagonal rather than being folded into the weights: it stands for
    variation the key colours do not describe, which is isotropic by assumption, and mixing
    it into the scatter would let it steer the direction as well as widen it.

    Written as an explicit loop over a handful of rows rather than as numpy reductions, for
    the same reason the matrix helpers below are: this feeds a 3x3 eigenproblem, and the
    port has to walk it in the same order.
    """
    rows = REFERENCES[name]
    c = np.array([[r, g, b] for r, g, b, _ in rows], dtype=np.float64)
    L, a, b = _to_oklab(c)
    tot = 0.0
    for q in rows:
        tot += q[3]
    mu = [0.0, 0.0, 0.0]
    w = []
    for i, q in enumerate(rows):
        wi = q[3] / tot
        w.append(wi)
        mu[0] += wi * float(L[i])
        mu[1] += wi * float(a[i])
        mu[2] += wi * float(b[i])
    cov = [[0.0] * 3 for _ in range(3)]
    for i in range(len(rows)):
        d = (float(L[i]) - mu[0], float(a[i]) - mu[1], float(b[i]) - mu[2])
        for r in range(3):
            for k in range(3):
                cov[r][k] += w[i] * d[r] * d[k]
    for r in range(3):
        cov[r][r] += GRAIN * GRAIN
    return mu, cov


def stats(colors, linear=False):
    """The (mean, covariance) of a set of sRGB colours, in OKLab. float64 throughout.

    numpy does the reduction over the strokes here and the port does a plain loop, so the
    two summation orders differ in the last bits of a sum of thousands of terms. That is
    the one place this module is not written for exactness, and it is deliberate: the
    difference is ~1e-16 relative on a statistic that then steers a 3x3, which lands far
    inside the grade's existing tolerance, and matching numpy's pairwise summation in JS
    would be a reimplementation of numpy rather than of this file.
    """
    c = np.asarray(colors, dtype=np.float64).reshape(-1, 3)
    if linear:
        c = linear_to_srgb(c)
    L, a, b = _to_oklab(c)
    lab = np.stack([L, a, b], axis=-1)
    mu = lab.mean(axis=0)
    d = lab - mu
    # The population covariance (divide by n), not the sample one: this is a description of
    # the pixels in front of us, not an estimate of a population they were drawn from, and
    # the port has to pick the same denominator.
    cov = (d[:, :, None] * d[:, None, :]).sum(axis=0) / len(lab)
    return [float(v) for v in mu], [[float(v) for v in row] for row in cov]


def _mat3(a, b):
    """A 3x3 product, written out. Explicit because `@` is numpy's summation order and a
    JS loop is its own: for a matrix this small there is no reason to let them differ."""
    return [[a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]
             for j in range(3)] for i in range(3)]


def _jacobi(m):
    """Eigenvalues and eigenvectors of a 3x3 SYMMETRIC matrix. Returns (vals, vecs).

    Cyclic Jacobi with a FIXED sweep count, which is the whole reason it is here rather
    than `np.linalg.eigh`: LAPACK is not available in a browser, and an algorithm that
    stops on a convergence test can stop after a different number of iterations in the two
    languages. Six sweeps of a 3x3 is machine precision with margin, and the only branch in
    it tests an exact zero, which both languages agree on.
    """
    a = [list(row) for row in m]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(JACOBI_SWEEPS):
        for p, q in ((0, 1), (0, 2), (1, 2)):
            apq = a[p][q]
            if apq == 0.0:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * apq)
            sign = 1.0 if theta >= 0.0 else -1.0
            t = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                akp, akq = a[k][p], a[k][q]
                a[k][p] = c * akp - s * akq
                a[k][q] = s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p][k], a[q][k]
                a[p][k] = c * apk - s * aqk
                a[q][k] = s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq
    return [a[0][0], a[1][1], a[2][2]], v


def _powm(m, half, invert, floor=EIG_FLOOR):
    """A symmetric PSD matrix to the power +1/2 or -1/2, via its eigendecomposition.

    Those two are all the transport needs. Eigenvalues are lifted to EIG_FLOOR first: see
    there for what a near-singular source covariance would otherwise do. `half` is unused
    except as documentation of intent -- the power is always one half -- and `invert`
    picks the sign, because `x ** -0.5` and `1.0 / sqrt(x)` are not the same rounding and
    the two languages must not each choose one.
    """
    vals, vecs = _jacobi(m)
    d = []
    for lam in vals:
        r = math.sqrt(lam if lam > floor else floor)
        d.append(1.0 / r if invert else r)
    # vecs * diag(d) * vecs^T, written out.
    return [[vecs[i][0] * d[0] * vecs[j][0] + vecs[i][1] * d[1] * vecs[j][1]
             + vecs[i][2] * d[2] * vecs[j][2] for j in range(3)] for i in range(3)]


def transport(mu_s, cov_s, mu_t, cov_t):
    """The linear Monge-Kantorovich map from one Gaussian to another. Returns (A, b).

    `x' = A x + b`, so a caller applies one 3x3 and one add per colour and never has to
    know this file exists. The map is
        T = Ss^-1/2 (Ss^1/2 St Ss^1/2)^1/2 Ss^-1/2,   x' = mu_t + T (x - mu_s)
    which is the optimal transport between the two Gaussians under squared cost, and the
    reason it is written with the Ss^1/2 sandwich rather than as `(Ss St)^1/2` is that the
    sandwich is symmetric at every step -- the product of two symmetric matrices is not
    symmetric, and feeding it to a symmetric eigensolver would quietly give the wrong map.
    """
    # The SOURCE's own floor, and the same one for both -- they have to remain each other's
    # inverse, so flooring one and not the other would not merely regularise the map, it
    # would make it wrong. See SRC_SD_FLOOR.
    src_floor = SRC_SD_FLOOR * SRC_SD_FLOOR
    s_half = _powm(cov_s, True, False, src_floor)
    s_inv = _powm(cov_s, True, True, src_floor)
    mid = _powm(_mat3(_mat3(s_half, cov_t), s_half), True, False)
    a = _mat3(_mat3(s_inv, mid), s_inv)
    b = [mu_t[i] - (a[i][0] * mu_s[0] + a[i][1] * mu_s[1] + a[i][2] * mu_s[2])
         for i in range(3)]
    return a, b


def fit(colors, rp, linear=False):
    """The block a grade needs to apply this reference to these colours.

    Fitted on the STROKE colours rather than on the source photograph, and that is the same
    argument the pigment projection makes: the strokes are what the painting is made of. A
    quadtree spends its budget where the detail is, so the stroke colours are already the
    picture's colours weighted by how much attention it pays them -- which is closer to
    "area" than a pixel histogram is.
    """
    mu_s, cov_s = stats(colors, linear=linear)
    a, b = transport(mu_s, cov_s, rp["mu"], rp["cov"])
    # The SOURCE statistics ride along. Only the target changes when somebody picks a
    # different painting, so a front end that kept these can re-aim the transport without
    # the stroke buffer, before the next render has landed. Nothing reads them today;
    # the tuner's colour board did.
    return {"a": a, "b": b, "strength": float(rp["strength"]), "name": rp["name"],
            "src_mu": mu_s, "src_cov": cov_s}


# The APPLICATION lives in `palette._grade_chunk`, not here: this module needs palette's
# OKLab conversion, so palette importing it back would be a cycle. The split is a good one
# anyway -- everything interesting is the fit above, and what the grade does with it is
# nine multiplies and a lerp.
