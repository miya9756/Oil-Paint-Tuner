"""Structure tensor -> stroke orientation and coherence.

This is change (A)/(B) of the spec's assessment: strokes follow the form rather than
pointing at random. Orientation is the tensor's MINOR eigenvector -- the direction along
the edge, not across it -- which is Litwinowicz's "normal to the gradient" [Litwinowicz97],
and the Gaussian smoothing of the tensor is the cheap form of Hays & Essa's smoothed
orientation field [HaysEssa04].

Coherence then drives elongation: strongly oriented neighbourhoods (an edge, a fold in
cloth) stretch along the flow; isotropic ones (open sky) stay round. Spec §4.5.
"""

import numpy as np

from .detail import _sobel
from .image import decimate, gaussian_blur, luma

EPS = 1e-12

# The sigma the FLAT field is actually computed at, after decimation. The flat brush reads
# the picture at `flat_sigma` pixels, and a field whose finest feature is that wide carries
# no information at full resolution -- so it is computed on a grid decimated until its own
# sigma lands here, and sampled back through `flat_step`.
#
# 2.0 rather than smaller: below ~1.5 a Gaussian is only a few taps wide and the block-mean
# decimation's residual aliasing stops being filtered out; above ~3 the saving falls off
# quadratically for no gain in the field. At the default flat_sigma of 8 this decimates by
# 4, which is 16x fewer pixels through a 13-tap kernel instead of a 49-tap one -- measured
# 34.6 s -> 0.6 s on a 12.2 Mpx canvas, with the sampled orientation moving a median 0.6 deg.
FLAT_STEP_SIGMA = 2.0

# Never decimate a small image into a field with no room left in it. 32 keeps at least a
# 32-sample short side, below which the tensor's own smoothing runs off both edges.
FLAT_MIN_SIDE = 32


def flat_step(sigma, h, w):
    """How far the flat field is decimated. 1 means "not at all", and then `flat_tensor`
    is the full-resolution computation it replaces."""
    step = int(max(1.0, sigma / FLAT_STEP_SIGMA))
    return max(1, min(step, max(1, min(h, w) // FLAT_MIN_SIDE)))


def flat_tensor(rgb, sigma):
    """The coarse orientation field for the flat-region brush. Returns (theta, coh, step).

    Two things happen here that `structure_tensor` does not do, and they are the same idea
    twice -- attack the noise, not the average of it.

    PRE-FILTER. Write the gradient as signal + noise; the outer products average to
    jxx ~ gs^2 + sn^2 and jyy ~ sn^2, so the isotropic noise power lands on BOTH eigenvalues
    and coherence saturates at (gs^2 / (gs^2 + 2 sn^2))^2. Raising the tensor's own sigma
    averages more of that bias but does not remove it -- a smooth gradient buried in sensor
    or JPEG noise stays incoherent at every tensor scale. Blurring the LUMA first attacks
    sn^2 itself (it falls as ~1/sigma^2 for white noise) while leaving a low-frequency
    gradient almost untouched, and is the only one of the two that recovers a direction.
    Measured on assets/zurisee at 1000 px over its hazy band: median coherence 0.376 without
    it, 0.945 with it, and the region's doubled-angle parallelism barely moves (0.75 ->
    0.82) -- the direction was always there and the noise was hiding it.

    DECIMATE. The field that comes out is band-limited to `sigma` by construction, so
    computing it at full resolution is spending 16x the arithmetic to represent nothing. The
    block mean is itself the anti-alias filter, and the remaining Gaussian at FLAT_STEP_SIGMA
    does the rest. `step` travels with the field because the caller has to divide its sample
    coordinates by it -- returning the field alone would silently sample the wrong place.

    Both theta and coherence are scale-INVARIANT (an angle, and a ratio of eigenvalues), so
    decimation does not rescale what comes back the way it would a gradient magnitude.
    """
    step = flat_step(sigma, *rgb.shape[:2])
    lum = luma(rgb).astype(np.float32)
    if step > 1:
        lum = decimate(lum, step)
    s = sigma / step
    theta, coh = _tensor_from_luma(lum, s, prefilter=s)
    return theta, coh, step


def structure_tensor(rgb, sigma=2.0):
    """Return (theta, coherence).

    theta is the angle of the minor eigenvector, i.e. the direction to lay the stroke
    along. coherence is ((l1-l2)/(l1+l2))^2 in [0,1]: 1 = a clean edge, 0 = flat or
    isotropic texture.
    """
    return _tensor_from_luma(luma(rgb).astype(np.float32), sigma)


def _tensor_from_luma(lum, sigma, prefilter=0.0):
    """The tensor itself, on a luma plane. Shared by both fields above; see `flat_tensor`
    for what `prefilter` is for and why it is not the same knob as `sigma`."""
    if prefilter > 0:
        lum = gaussian_blur(lum, prefilter)
    gx, gy = _sobel(lum)

    jxx = gaussian_blur(gx * gx, sigma)
    jyy = gaussian_blur(gy * gy, sigma)
    jxy = gaussian_blur(gx * gy, sigma)

    # Closed form for a symmetric 2x2. 0.5*atan2(2b, a-c) is the MAJOR axis, which for
    # this tensor is the gradient direction (across the edge); +pi/2 turns it along.
    # Sanity: a vertical edge has gy=0, so jxy=jyy=0 and the major angle is 0 (the x
    # axis, across the edge) -- so the stroke angle is pi/2, running down the edge.
    major = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
    theta = major + np.pi / 2.0

    tr = jxx + jyy
    disc = np.sqrt(np.maximum((jxx - jyy) ** 2 + 4.0 * jxy**2, 0.0))
    coherence = (disc / (tr + EPS)) ** 2
    coherence = np.clip(coherence, 0.0, 1.0)

    return theta.astype(np.float32), coherence.astype(np.float32)


def sample(field, y, x):
    """Nearest-neighbour sample at float pixel coordinates, clamped to the image."""
    h, w = field.shape
    iy = np.clip(np.round(y).astype(np.int64), 0, h - 1)
    ix = np.clip(np.round(x).astype(np.int64), 0, w - 1)
    return field[iy, ix]


def sample_angle(theta, y, x):
    """Sample an ORIENTATION field (pi-periodic, not 2pi).

    Sampling the angle directly would be wrong at the +-pi/2 wrap; going through the
    doubled-angle vector (cos 2t, sin 2t) and back makes the wrap invisible.
    """
    t = sample(theta, y, x)
    return 0.5 * np.arctan2(np.sin(2.0 * t), np.cos(2.0 * t))
