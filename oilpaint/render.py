"""2D anisotropic Gaussian rasterizer.

The falloff is taken VERBATIM from the fragment shader of
`4d-relight/web/demo/index.html`, which is where the target look was observed:

    d2 = dot(vPos, vPos)                                  // vPos in units of sigma
    soft: A = exp(-d2)
    hard: A = 1.0 - smoothstep(hard_r - aa, hard_r + aa, sqrt(d2))
    discard when d2 > 4.0                                 // the quad spans +-2 sigma

Keeping it verbatim is what makes the eventual WebGL port a port rather than a
reimplementation -- see .claude/skills/python-js-parity/SKILL.md. The one deliberate
divergence is `aa`: the shader gets it from `fwidth(d)`, a screen-space derivative, and
here it is the same quantity computed analytically (see `_fwidth_d`). Spec §5.

STEP 2 -- paint texture and impasto. [Hertzmann02] ("Fast Paint Texture", NPAR 2002) is
the method, followed structurally: every stroke carries an OPACITY map and a HEIGHT map in
its own local frame; the colour image is the ordinary back-to-front composite of the
opacity maps, a second buffer accumulates height, and the painting is finally bump-mapped
from that height field under Phong. Three of that paper's choices are load-bearing and are
reproduced exactly:

  * the height field is NOT cumulative. Heights are composited with the same alpha blend
    as the colour, not summed -- the paper tried summing and abandoned it because strokes
    buried under later paint pushed through the surface anyway.
  * each stroke's height carries a constant offset PROPORTIONAL TO THE NUMBER OF STROKES
    ALREADY DRAWN, so early strokes sit low and late strokes sit high. That offset is what
    turns a stroke boundary into a height discontinuity, and it is most of the effect.
  * the textures are in stroke-local coordinates and scale with the stroke (the paper gets
    this from mip-mapping; here `u, v` are already in units of sigma, so it is free).

What differs: the paper's maps are images, painted by hand or scanned. Ours are closed
form -- `bristle`, `taper` and `weave` below -- which the paper itself names as the better
alternative it did not take. That is not only a licensing dodge (no surveyed stroke-texture
set turned out to be both commercially usable and of known provenance); it is what keeps
this file portable under the parity rule, keeps the deployed page free of binary assets,
and keeps the texture resolution-independent.
"""

import numpy as np

CUTOFF_D2 = 4.0  # matches the shader's `if (d2 > 4.0) discard`

# Bristle ranks, in radians per PIXEL across the stroke, at the reference stroke width
# below. How the pitch scales with the stroke took three attempts and both extremes are
# visibly wrong, so the reasoning is worth keeping:
#
#   * pitch PROPORTIONAL to the stroke (what [Hertzmann02] gets from mip-mapping, because
#     its textures are bitmaps and it has no other option) gave the 128 px underpainting
#     strokes 25 px grooves. The background read as corduroy.
#   * pitch CONSTANT in pixels put ~27 fine grooves across those same strokes. The flat
#     regions read as an engraving -- mechanically parallel hatching.
#   * pitch as sqrt(width) is the compromise here, and it is not just a fudge: a larger
#     brush really does have coarser hair, just not proportionally coarser. A big stroke
#     gets ~10 broad streaks and a small one ~3 fine ones, which is what a brush does.
BRISTLE_F1 = 1.40  # ~4.5 px pitch at BRISTLE_REF
BRISTLE_F2 = 2.20  # ~2.9 px pitch; 2.20/1.40 is not a small rational
BRISTLE_REF = 6.0  # the minor sigma, in px, at which those pitches hold exactly
# Radians per pixel ALONG the stroke. Both ranks drift, at different rates, so the grooves
# curve and cross instead of shearing as one block.
BRISTLE_DRIFT1 = 0.05
BRISTLE_DRIFT2 = 0.09
# A slow envelope along the stroke, so the streaking comes and goes over roughly a 75 px
# run. Without it a long stroke carries the same groove from end to end and the flat
# regions read as an ENGRAVING -- evenly hatched, mechanically parallel. A real trail is
# loaded here and skipping there, and this one term is most of the difference.
BRISTLE_ENV_F = 0.085
BRISTLE_ENV = 0.35  # depth of that envelope; mean strength is therefore 1 - BRISTLE_ENV

# Elongation at which a stroke gets its full bristle trail; a circular dab gets none.
# This is the fix for the last thing that read as fake, and it is physics rather than
# taste: a trail is left by DRAGGING, so a mark that was not dragged has no business
# carrying one. Flat regions have low structure-tensor coherence, hence near-circular
# strokes, and they were coming out as a patchwork of hatched discs each at its own angle
# -- the single most synthetic-looking part of the first result. Weighting the bristle by
# elongation removes it there and leaves it untouched wherever the paint actually follows
# the form, which is exactly where it was earning its keep.
BRISTLE_ELONG = 2.0

# Outline fringe: three octaves of fBm in the polar angle, at an order DERIVED FROM THE
# STROKE'S SIZE IN PIXELS. `wobble` above cannot do this job -- its harmonics are 3 and 5
# of an angle measured in sigma, so its features are a fixed FRACTION of the stroke and a
# 130 px blob gets a 130 px undulation, which is why large blobs read as smooth vector
# shapes no matter how high `wobble_amp` goes. Here the finest octave is pinned to
# FRINGE_FEATURE_PX instead, so a big blob gets a hundred small crumbles and a small stroke
# gets three, and both look like torn paint rather than like geometry.
#
# The orders must be INTEGERS: phi wraps at +-pi, and a non-integer harmonic leaves a seam
# there -- a radial crack down every stroke. Built as m, 2m, 4m from one integer m so that
# is structural rather than something to remember.
FRINGE_FEATURE_PX = 4.0
FRINGE_K = np.pi / (2.0 * FRINGE_FEATURE_PX)  # base order per unit of effective radius
# The AMPLITUDE is pinned to pixels as well, and it has to be: the edge threshold lives in
# sigma, so a fixed multiplier there displaces the outline by a fixed FRACTION of the
# stroke. At the first attempt that put ~10 px spikes on the big blobs and turned them into
# sea urchins, while the small strokes barely moved -- the exact inverse of what was
# wanted. So `fringe_px` is a displacement in pixels and the per-stroke multiplier is
# derived from it, which is also why it is the one knob here quoted in a real unit.
# The cap is for the small end, where a couple of pixels is a large share of the radius.
FRINGE_MAX_AMP = 0.25
# Caps the finest octave at 4 * 48 = 192 lobes. Two reasons, and the second is the binding
# one: past this the crumble is finer than the antialiasing band and just shimmers, AND the
# parity gap scales with the harmonic order, because a high order multiplies the float32
# difference in `phi`. See FRINGE_SLACK in tests/test_raster_parity.py.
FRINGE_MAX_M = 48

# Canvas tooth, in pixels. Fixed rather than tunable because the thread pitch of linen is a
# property of the substrate, not a style choice -- and at 4 px it is the finest corrugation
# whose gradient is still sampled 4x by the central difference in `light`.
WEAVE_PERIOD = 4.0

# Fraction of the surface colour that survives in a groove turned fully away from the
# light. Not 0: paint in shadow is still lit by the room.
AMBIENT = 0.5

# The cavity map is a BAND-PASS, not a low-pass, and the fine end is the important half.
# A plain `blur(H) - H` also finds every bristle groove, and those are already shaded by
# the directional term -- the two stack, and the first attempt came out as heavy parallel
# striping with black clumps where the sum clipped. Occlusion should answer to strokes
# sitting ON each other, which is the scale the fringe lives at, so AO_FINE removes the
# micro-relief first (at 1.5 px it attenuates the ~4.5 px bristle pitch by ~9x) and
# AO_SIGMA then sets the contact-shadow width. Neither is exposed: they are the geometry of
# paint, not a look.
AO_FINE = 1.5
AO_SIGMA = 3.5

# Where the ground -- the base layer -- sits within the stroke height range, as a fraction
# of it. This one number decides whether the relief reads as paint or as plastic, and it
# took two goes:
#
#   * ground at 0 (no ground at all, the first version) makes every stroke a 1.0-tall
#     plateau on an abyss. Both the bevel and the cavity map then trace its silhouette, and
#     the result is a DROP SHADOW -- a graphic-design effect, so the strokes read as
#     stickers on a page. Worst with base='blur', where 45% of the canvas is ground.
#   * ground just BELOW every stroke (0.85) is better but keeps the same bias: every stroke
#     is still above the ground, so every stroke still gets a shadow, systematically.
#
# At 0.5 the ground is the MEAN paint level: roughly half the strokes sit above it and half
# below, so the ground stops being a floor the paint is stacked on and becomes the paint's
# own average. The halo goes because it was never occlusion per se -- it was every stroke
# being on the same side of the ground.
GROUND_AT = 0.5


def ground_height(impasto_layer):
    """Height of the base layer, given the stroke range 1.0 .. 1.0 + impasto_layer."""
    return 1.0 + GROUND_AT * impasto_layer
# Ceiling on the darkening. Measured on the test photo, the raw cavity signal runs
# p50 -0.000 / p90 +0.084 / p99 +0.239 / p99.9 +0.465 / max +0.677 -- so the effect the
# fringe actually wants lives around 0.1-0.25, and the long tail above 0.4 is somewhere
# else entirely: at `occ > 0.4` the mean coverage is 0.04, i.e. it is the ~2% of pixels
# that are BARE CANVAS in the gaps between strokes. A hole between two thick strokes really
# is deeply shadowed, so the signal is not wrong, but taken at face value it renders those
# gaps as soot and they read as damage rather than as paint. This clips that tail and
# leaves the useful range untouched.
AO_MAX = 0.5


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / np.maximum(e1 - e0, 1e-12), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _fwidth_d(u, v, d, cos_t, sin_t, s_major, s_minor):
    """Analytic stand-in for GLSL `fwidth(d)` = |dFdx(d)| + |dFdy(d)|.

    d = sqrt(u^2 + v^2) with u,v the sigma-space coordinates, so by the chain rule
        dd/dx = (u * cos/s_maj - v * sin/s_min) / d
        dd/dy = (u * sin/s_maj + v * cos/s_min) / d
    On an axis-aligned pixel grid with unit spacing this is exact, where the GPU's
    version is a 2x2-quad finite difference -- hence the two sides agree to a tolerance
    rather than bit-exactly. The parity test documents that tolerance instead of hiding it.
    """
    dsafe = np.maximum(d, 1e-6)
    ddx = (u * (cos_t / s_major) - v * (sin_t / s_minor)) / dsafe
    ddy = (u * (sin_t / s_major) + v * (cos_t / s_minor)) / dsafe
    return np.abs(ddx) + np.abs(ddy)


def base_layer(rgb, block):
    """Opaque coarse approximation painted under every stroke.

    Spec §1(C) / §4.7 and Hertzmann's first pass [Hertzmann98]. This is what decouples
    "no black holes" from "how big are the strokes": any gap shows an approximately
    correct colour, so the radius budget is free to be an aesthetic choice instead of a
    coverage constraint.

    Box-downsample, nearest-upsample, THEN blur. The blur is not cosmetic: without it the
    first run showed the underpainting's 2^k rectangles straight through the gaps, which
    is a worse artefact than the holes it was added to fix.

    The blur is not, however, evaluated at full resolution, and this is the one place in the
    pipeline where that would be pure waste rather than a trade. The signal being blurred is
    piecewise constant on the block grid -- it came from a block mean and a nearest
    upsample -- and both passes of a separable Gaussian are linear, so every output pixel is
    a fixed weighted sum of a handful of BLOCK samples. Folding the kernel onto that grid
    (`_block_blur_weights`) collapses a 49-tap convolution at full resolution to a 5-tap one
    on a grid 16x coarser in each axis. Measured on a 12.2 Mpx canvas: 25.0 s -> 0.6 s.

    Same weights, same value, different summation order -- so it is exact in arithmetic and
    agrees with the old form to a few float32 ulps rather than bit for bit. That is the same
    standing this layer already had against the JS port, and for the same reason: it is the
    ground, and the 8-bit gate at the end of the parity suite is what proves the difference
    costs nothing visible.
    """
    from .image import gaussian_kernel1d

    h, w = rgb.shape[:2]
    block = max(1, int(block))
    bh, bw = -(-h // block), -(-w // block)
    pad = np.pad(rgb, ((0, bh * block - h), (0, bw * block - w), (0, 0)), mode="edge")
    small = pad.reshape(bh, block, bw, block, 3).mean(axis=(1, 3)).astype(np.float32)

    k = gaussian_kernel1d(0.5 * block)
    iy, wy = _block_blur_weights(h, bh, block, k)
    ix, wx = _block_blur_weights(w, bw, block, k)

    # Vertical first, then horizontal -- the order `gaussian_blur` used, kept so the two
    # roundings land in the same sequence they did.
    tmp = np.zeros((h, bw, 3), dtype=np.float32)
    for c in range(wy.shape[1]):
        tmp += wy[:, c, None, None] * small[iy[:, c]]

    # Chunked over rows: the horizontal gather is a full-resolution temporary, and at 12 Mpx
    # five of those back to back is 700 MB of traffic for no reason. 256 rows keeps it in
    # cache-sized pieces without making the loop itself the cost.
    out = np.empty((h, w, 3), dtype=np.float32)
    for y0 in range(0, h, 256):
        y1 = min(h, y0 + 256)
        acc = np.zeros((y1 - y0, w, 3), dtype=np.float32)
        for c in range(wx.shape[1]):
            acc += wx[None, :, c, None] * tmp[y0:y1, ix[:, c]]
        out[y0:y1] = acc
    return out


def _block_blur_weights(n, nb, block, k):
    """Fold a 1-D kernel onto a block grid: return (idx, wgt), both (n, span).

    Row y of the pair says which BLOCK samples the blurred value at pixel y draws on and
    with what weight -- i.e. it is the banded matrix `M[y, m] = sum of k over the taps that
    land in block m`, stored as its nonzeros. `span` is the widest band any row needs; rows
    that need fewer are padded with a repeated index and a zero weight, so the caller's loop
    is rectangular and branch-free.

    The clamp is inside the block division, not outside it, because that is where
    `convolve1d`'s edge padding puts it -- clamping the block index instead would give the
    top and bottom rows a different answer.
    """
    r = (k.size - 1) // 2
    t = np.arange(k.size)
    src = np.clip(np.arange(n)[:, None] + t[None, :] - r, 0, n - 1) // block
    lo = src[:, 0]
    span = int((src[:, -1] - lo).max()) + 1
    j = src - lo[:, None]
    wgt = np.zeros((n, span), dtype=np.float32)
    for c in range(span):
        wgt[:, c] = np.where(j == c, k[None, :], np.float32(0.0)).sum(axis=1, dtype=np.float32)
    idx = np.minimum(lo[:, None] + np.arange(span)[None, :], nb - 1)
    return idx, wgt


def wobble(phi, phase, amp):
    """Per-stroke angular modulation of the hard edge -- a ragged outline, not an ellipse.

    A perfect ellipse is the single strongest "computer generated" cue in the output: real
    strokes have irregular edges from bristles and from paint running out. Two low
    harmonics of the local angle, offset by a per-stroke phase, break the outline without
    changing the stroke's scale or orientation. Deliberately cheap and closed-form so the
    fragment shader can evaluate the identical expression -- no texture, no noise lookup.

    This is the LOW-frequency half of the outline: two lobes and three, whatever the size
    of the stroke. `fringe` below is the high-frequency half, and the two are separate
    knobs because they fix different things -- this one stops a stroke being an ellipse,
    that one stops its edge being smooth.

    Takes the polar angle rather than `u, v` so that the `atan2` -- comfortably the most
    expensive operation in the inner loop -- is computed once and shared with `fringe`.

    Returns a multiplier on the edge threshold, mean 1.
    """
    return 1.0 + amp * (
        0.60 * np.sin(3.0 * phi + phase) + 0.40 * np.sin(5.0 * phi - 1.7 * phase)
    )


def fringe_params(r_major, r_minor, fringe_px):
    """`(order, amplitude)` for `fringe` on a stroke of this size.

    Both are driven by the AREA-EQUIVALENT radius, so an elongated stroke and a round one
    of the same footprint get the same crumble. The order sets the crumble's SIZE and the
    amplitude its DEPTH, and pinning both to pixels is what makes a 130 px blob and an 8 px
    dab look like they were made by the same paint.

    Computed in float64 from the float32 radii on both sides of the port. That matters for
    the order especially: an off-by-one there is not a rounding difference, it is a
    different outline, so it must not be allowed to depend on which language rounded it.
    `floor(x + 0.5)` rather than `round`, because Python rounds halves to even and JS
    rounds them up.
    """
    r_eff = np.sqrt(float(r_major) * float(r_minor))
    m = min(max(int(np.floor(FRINGE_K * r_eff + 0.5)), 1), FRINGE_MAX_M)
    amp = min(fringe_px / max(r_eff, 1e-6), FRINGE_MAX_AMP)
    return m, amp


def fringe(phi, phase, amp, m):
    """Fractal roughness on the outline: three octaves of fBm in the polar angle.

    Amplitude falls 0.5 / 0.3 / 0.2 as the order doubles, which is the usual fBm shape --
    the coarsest octave carries the silhouette and the finest only granulates it. `m` comes
    from `fringe_order`, so all three orders are integers and scale with the stroke.

    Returns a multiplier on the edge threshold, mean 1, in [1 - amp, 1 + amp].
    """
    return 1.0 + amp * (
        0.50 * np.sin(m * phi + phase)
        + 0.30 * np.sin(2 * m * phi + 2.3 * phase)
        + 0.20 * np.sin(4 * m * phi - 1.7 * phase)
    )


def bristle(up, vp, phase):
    """The trail a loaded brush leaves: grooves running ALONG the stroke. Mean 0, in [-1,1].

    `up, vp` are the stroke-local coordinates IN PIXELS -- i.e. `u * s_major, v * s_minor`,
    not the sigma-space `u, v` everything else here uses. See the BRISTLE_F* note above for
    why the pitch is anchored to the pixel grid and not to the stroke.

    This is the closed-form stand-in for [Hertzmann02]'s stroke texture, and it is one
    function serving two consumers at different strengths -- the opacity map (where a
    groove lets the paint underneath show through) and the height map (where the same
    groove is relief). Hence it returns the bare mean-zero field and lets the caller apply
    its own amplitude, unlike `wobble`, which folds its amplitude in because it has one.

    Bristles run along the stroke, so the modulation is in `vp`, ACROSS it. The `up` terms
    are what stop the result being a straight comb: the two ranks drift down the length of
    the stroke at different rates, and the envelope makes the whole trail fade in and out
    along it. Together those read as a dragged trail rather than as a texture stamped on
    an ellipse.
    """
    env = (1.0 - BRISTLE_ENV) + BRISTLE_ENV * np.sin(BRISTLE_ENV_F * up + 2.1 * phase)
    return env * (
        0.60 * np.sin(BRISTLE_F1 * vp + BRISTLE_DRIFT1 * up + phase)
        + 0.40 * np.sin(BRISTLE_F2 * vp + BRISTLE_DRIFT2 * up - 1.3 * phase)
    )


def taper(u, cos_phase, amount):
    """Blunt where the brush landed, thin where the paint ran out. Multiplier on the edge.

    Takes `cos(phase)` rather than `phase` so that BOTH ports hoist that cosine out of
    their inner loop with one expression between them. It costs nothing here, where the
    call is once per stroke over an array, and it matters in render.js, where the same
    call is once per pixel.

    This is the one cue an ellipse cannot carry, and it is worth more than the bristles:
    a symmetric blob is a blob whatever texture is inside it, whereas a stroke that is
    fatter at one end reads as having been PUT there in a direction. It modulates the edge
    threshold rather than alpha -- so the stroke gets narrower towards its tail rather than
    merely fainter, which is what a drying brush actually does, and it keeps the falloff as
    crisp at the tail as at the head.

    `cos(phase)` sets both the direction of the taper and its strength, which costs no new
    rng draw. That matters more than it looks: `phase` is already in the stroke buffer, so
    the whole feature leaves the draw ORDER in strokes.py untouched, and every painting
    made before this existed still comes back identical with `amount=0`.

    Returns a multiplier in [1 - amount, 1].
    """
    return 1.0 - amount * 0.5 * (1.0 + 0.5 * u * cos_phase)


def weave(h, w, period=WEAVE_PERIOD):
    """Canvas tooth: a plain weave as two orthogonal corrugations. Mean 0, in [-1, 1].

    Only ever added to the height field WHERE THE PAINT IS THIN (see `light`), which is
    both physically right and the reason it is worth having -- it gives the raking light
    something to catch in the gaps between strokes, so a thin passage reads as bare canvas
    instead of as flat colour.
    """
    k = 2.0 * np.pi / period
    ys = np.sin(k * np.arange(h, dtype=np.float32))[:, None]
    xs = np.sin(k * np.arange(w, dtype=np.float32))[None, :]
    return (0.5 * (ys + xs)).astype(np.float32)


def _direction(az_deg, el_deg):
    """Screen compass -> unit vector. 0 degrees is from the RIGHT, 90 from the TOP.

    The y term is negated because image y runs downward, and that sign is the whole reason
    this is a function rather than three inline lines: the light and the view now both need
    it, and getting it right in one place and wrong in the other would be a lighting bug
    that only shows up when the viewpoint moves off centre.
    """
    az, el = np.deg2rad(az_deg), np.deg2rad(el_deg)
    ce = np.cos(el)
    return float(ce * np.cos(az)), float(-ce * np.sin(az)), float(np.sin(el))


def light(rgb, height, cover, *, depth=0.35, light_deg=135.0, elev_deg=35.0, gloss=0.35,
          canvas_weave=0.0, occlusion=0.0, view_deg=90.0, view_elev_deg=90.0):
    """Bump-map the painting from its height field. [Hertzmann02] step 3, plus two things
    that paper does not do: ambient occlusion, and a movable viewpoint.

    Three terms, and they answer to different things, which is the point:

      * DIFFUSE depends on the surface normal and the light. Moves with the light.
      * OCCLUSION depends on the surface alone -- a crease sees less of the room than a
        ridge does, whatever the light is doing and wherever the eye is. So it multiplies
        the AMBIENT term only, which is what ambient occlusion physically is: it occludes
        indirect light. It is the reason a stroke's edge reads as sitting ON something
        rather than being painted onto it, and it is NOT view-dependent.
      * SPECULAR depends on the normal, the light AND the eye. This is the view-dependent
        term, and it is the one an interactive page should drive: a real oil painting's
        raking sheen is what changes as you move in front of it.

    The DIFFUSE term is normalised by the flat `ndl`, so a flat height field leaves the
    painting unchanged at any light angle. Turning impasto on must not be a hidden exposure
    change, or every A/B against a non-impasto arm measures brightness instead of relief.

    The SPECULAR is deliberately NOT normalised that way, and the reason is worth recording
    because the first attempt did normalise it and was wrong. Subtracting the flat Phong
    lobe looks right -- it stops a flat varnish glaring the whole canvas white when the eye
    nears the light's mirror direction. But at that mirror direction the flat lobe is at
    its MAXIMUM, and every real surface normal scores below it, so the subtraction does not
    merely remove the glare: it removes the entire signal at exactly the angle where the
    relief has most to say, and clamping the remainder at zero blanks it. The measured
    effect was a view slider that did nothing. So the specular here is plain Phong: at the
    default straight-on view the flat lobe is ~1e-6 and it is a no-op anyway, and swinging
    the eye toward the mirror genuinely does flood the surface with sheen -- which is what
    happens when you tilt an oil painting into a light, and is the whole reason the relief
    becomes visible there. `gloss` controls how much.

    Applied to display values rather than in linear light, matching the paper, which
    bump-maps the raw colour image on the GPU. Doing it in linear would be more defensible
    physically and is a one-line move if it ever looks wrong.
    """
    fld = height
    if canvas_weave > 0:
        # Thin paint only: `1 - cover` is the fraction of the pixel the strokes left, so
        # the tooth fades out exactly as the impasto builds over it.
        fld = fld + canvas_weave * weave(*height.shape) * (1.0 - cover)

    # 'edge' padding IS the clamp-to-border the JS port does with clamped indices; keeping
    # them the same shape is what lets the parity test compare the borders too, where an
    # off-by-one in a gradient normally hides.
    pad = np.pad(fld, 1, mode="edge")
    gx = 0.5 * (pad[1:-1, 2:] - pad[1:-1, :-2])
    gy = 0.5 * (pad[2:, 1:-1] - pad[:-2, 1:-1])
    nx = -gx * depth
    ny = -gy * depth
    inv_len = 1.0 / np.sqrt(nx * nx + ny * ny + 1.0)
    nx = nx * inv_len
    ny = ny * inv_len
    nz = inv_len

    # Cavity map: a pixel sitting below its own neighbourhood is in a crease. The sign is
    # the whole trick -- `far - near` is positive in a dip and negative on a lip, and
    # clamping at 0 keeps only the dips. It follows the fringe for free, because the fringe
    # is already in the height field. Two cascaded blurs (sigmas add in quadrature) rather
    # than one, for the band-pass reason given at AO_FINE.
    occl = 0.0
    if occlusion > 0:
        from .image import gaussian_blur

        near = gaussian_blur(fld, AO_FINE)
        far = gaussian_blur(near, AO_SIGMA)
        occl = np.clip(occlusion * (far - near), 0.0, AO_MAX)

    lx, ly, lz = _direction(light_deg, elev_deg)
    vx, vy, vz = _direction(view_deg, view_elev_deg)
    ndl = np.maximum(nx * lx + ny * ly + nz * lz, 0.0)

    # Phong: the mirror direction R = 2 (N.L) N - L, against the eye. This used to collapse
    # to R's z component, which was the same thing only because the eye was nailed to +z.
    rx = 2.0 * ndl * nx - lx
    ry = 2.0 * ndl * ny - ly
    rz = 2.0 * ndl * nz - lz
    rv = np.maximum(rx * vx + ry * vy + rz * vz, 0.0)
    shin = 8.0 + 56.0 * gloss
    spec = (0.5 * gloss) * rv ** shin

    shade = AMBIENT * (1.0 - occl) + (1.0 - AMBIENT) * (ndl / max(lz, 1e-6))
    out = rgb * shade[:, :, None] + spec[:, :, None]
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def render(strokes, h, w, *, hard=True, hard_r=1.5, wobble_amp=0.0, canvas=None,
           split_at=None, bristle_amp=0.0, taper_amp=0.0, fringe_px=0.0,
           want_height=False, impasto_relief=0.0, impasto_layer=0.0, ground=0.0):
    """Composite strokes back-to-front with `over`. Array order IS paint order.

    The demo renderer uses front-to-back `under` because it is a depth-sorted 3D
    rasterizer; in 2D with an explicit paint order, back-to-front `over` gives the same
    result and is simpler on both sides of the port (spec §5).

    Returns a tuple whose length depends on what was asked for, in this fixed order:
    `(out, cover[, cover_tail][, height])` -- `cover_tail` present iff `split_at` is not
    None, `height` present iff `want_height`. The height pass costs one more buffer and
    one more blend per tile, so it is off unless the caller wants impasto.

    `bristle_amp` and `taper_amp` are both zero by default, and at zero every expression
    below is the one that was here before them -- so this function is still bit-exact
    against paintings made before step 2 existed.
    """
    out = (
        np.zeros((h, w, 3), dtype=np.float32)
        if canvas is None
        else np.array(canvas, dtype=np.float32, copy=True)
    )
    cover = np.zeros((h, w), dtype=np.float32)
    # `ground` is the height of the base layer under everything; see GROUND_HEIGHT. At 0
    # (the default, and what `base='none'` should pass) bare canvas really is an abyss.
    height = np.full((h, w), ground, dtype=np.float32) if want_height else None
    # [Hertzmann02]: the per-stroke height offset is proportional to the number of strokes
    # ALREADY DRAWN, so the last stroke sits exactly `impasto_layer` above the first.
    inv_n = 1.0 / max(1, len(strokes) - 1)
    want_bristle = bristle_amp > 0 or (height is not None and impasto_relief > 0)
    # Coverage of the strokes from `split_at` onward, accumulated in the SAME pass. It
    # measures the detail layer's holes with the underpainting discounted, and used to be
    # obtained by rendering the detail layer a second time -- which doubled the cost of
    # every render to produce one diagnostic number.
    cover_tail = np.zeros((h, w), dtype=np.float32) if split_at is not None else None

    cutoff = np.sqrt(CUTOFF_D2)
    cos_a = np.cos(strokes.theta)
    sin_a = np.sin(strokes.theta)
    s_major = strokes.r_major / hard_r
    s_minor = strokes.r_minor / hard_r
    # Pixel-centre coordinates built ONCE and sliced per stroke. Rebuilding two aranges
    # inside a loop that runs thousands of times is pure allocator traffic.
    YS = np.arange(h, dtype=np.float32)
    XS = np.arange(w, dtype=np.float32)

    for i in range(len(strokes)):
        sx, sy = s_major[i], s_minor[i]
        ct, st = float(cos_a[i]), float(sin_a[i])
        m_fringe, fringe_amp = fringe_params(strokes.r_major[i], strokes.r_minor[i],
                                             fringe_px)
        bscale = np.float32(np.sqrt(BRISTLE_REF / max(float(sy), 1e-3)))
        belong = np.float32(min(max((float(sx) / max(float(sy), 1e-6) - 1.0)
                                    / (BRISTLE_ELONG - 1.0), 0.0), 1.0))

        # Bounding box of the +-2 sigma quad after rotation.
        ext_x = cutoff * (sx * abs(ct) + sy * abs(st))
        ext_y = cutoff * (sx * abs(st) + sy * abs(ct))
        x0 = max(0, int(np.floor(strokes.x[i] - ext_x)))
        x1 = min(w, int(np.ceil(strokes.x[i] + ext_x)) + 1)
        y0 = max(0, int(np.floor(strokes.y[i] - ext_y)))
        y1 = min(h, int(np.ceil(strokes.y[i] + ext_y)) + 1)
        if x1 <= x0 or y1 <= y0:
            continue

        yy = (YS[y0:y1] - strokes.y[i])[:, None]
        xx = (XS[x0:x1] - strokes.x[i])[None, :]
        u = (xx * ct + yy * st) / sx
        v = (-xx * st + yy * ct) / sy
        d2 = u * u + v * v

        inside = d2 <= CUTOFF_D2
        if not inside.any():
            continue

        phase = float(strokes.phase[i])
        cos_phase = float(np.cos(phase))
        if hard:
            d = np.sqrt(d2)
            aa = np.maximum(_fwidth_d(u, v, d, ct, st, sx, sy), 1e-4)
            edge = hard_r
            if wobble_amp > 0 or fringe_px > 0:
                # The one atan2 both outline terms share.
                phi = np.arctan2(v, u)
                if wobble_amp > 0:
                    edge = edge * wobble(phi, phase, wobble_amp)
                if fringe_px > 0:
                    edge = edge * fringe(phi, phase, fringe_amp, m_fringe)
                # Clamped just inside the +-2 sigma quad: a crest that reached the cutoff
                # would be sliced off flat by the discard and read as a chord across the
                # stroke, which is a worse artefact than the ellipse it fixes. After BOTH
                # terms, since either can push the threshold outward -- and, with
                # fringe_px at 0, still exactly `min(hard_r * wobble, cap)` as before.
                edge = np.minimum(edge, np.sqrt(CUTOFF_D2) - 0.02)
            if taper_amp > 0:
                # AFTER the clamp, never before: `taper` only ever shrinks the threshold,
                # so the crest stays inside the quad and the wobble-only path above is
                # left bit-for-bit as it was.
                edge = edge * taper(u, cos_phase, taper_amp)
            a = 1.0 - smoothstep(edge - aa, edge + aa, d)
        else:
            a = np.exp(-d2)

        a = a.astype(np.float32, copy=False)
        a[~inside] = 0.0
        # Back to pixels for the bristle -- `u, v` are in sigma and the groove pitch is not
        # -- then the sqrt(width) pitch scaling of BRISTLE_REF, as one factor on both axes
        # so the drift and the envelope stretch with the grooves rather than shearing them.
        g = (belong * bristle(u * (sx * bscale), v * (sy * bscale), phase)
             if want_bristle else None)
        if bristle_amp > 0:
            # Clipped because `1 + bristle_amp*g` exceeds 1 on a crest, and an alpha above
            # 1 makes `inv` negative and the `over` below subtract paint. The clip also
            # means that at alpha 1 the grooves can only ever CUT, which is correct: paint
            # cannot be more than opaque, though it can be thicker -- and it is, because
            # the height field below uses the same `g` unclipped.
            a = np.clip(a * (1.0 + bristle_amp * g), 0.0, 1.0).astype(np.float32)
        if strokes.alpha[i] != 1.0:
            a *= strokes.alpha[i]
        if not a.any():
            continue

        # `over`, in place on the destination views. The readable form allocates four
        # full-tile temporaries per stroke; across thousands of strokes that allocator
        # traffic was a large part of the render, and the arithmetic is identical.
        inv = 1.0 - a
        a3, inv3 = a[:, :, None], inv[:, :, None]
        tile = out[y0:y1, x0:x1]
        tile *= inv3
        tile += a3 * strokes.rgb[i][None, None, :]

        c = cover[y0:y1, x0:x1]
        c *= inv
        c += a

        if height is not None:
            # [Hertzmann02] §2, "Height map computation": the stroke's grey value is its
            # height texture PLUS a constant proportional to the strokes already drawn,
            # composited with ordinary alpha blending. Not summed -- the paper tried that
            # and found buried strokes surfacing through the paint above them.
            hv = 1.0 + impasto_layer * (i * inv_n)
            if impasto_relief > 0:
                hv = hv + impasto_relief * g
            ht = height[y0:y1, x0:x1]
            ht *= inv
            ht += a * hv

        if cover_tail is not None and i >= split_at:
            ctail = cover_tail[y0:y1, x0:x1]
            ctail *= inv
            ctail += a

    res = (out, cover)
    if cover_tail is not None:
        res = res + (cover_tail,)
    if height is not None:
        res = res + (height,)
    return res
