"""Artistic structure: a procedural flow field laid over the picture's own orientation.

`palette.py` makes the COLOUR read as paint rather than as a photograph. This is the same
argument for the STRUCTURE. Every painting this pipeline makes is organised by one thing --
the structure tensor of the source -- so a van Gogh sky and a Monet pond come out with the
same underlying placement and differ only in hue. A painter's hand is the other way round:
the marks carry an organisation the subject does not have, and that organisation is most of
what makes a Starry Night look like a Starry Night.

WHERE IT RUNS IS THE DESIGN, exactly as in palette.py, but at a different seam. Stroke
placement is three things -- position (quadtree + jitter), angle (tensor), elongation
(coherence) -- and this attaches to the last two, inside `strokes.from_cells` step 3. That
step is the one part of `from_cells` that consumes NO random numbers, which is what makes an
otherwise expensive attachment point cheap:

  * the quadtree leaves, tau, the stroke count, the radii, the per-cell colours and the paint
    order are all untouched, and so is the `rng` draw stream (CLAUDE.md: "the RNG draw ORDER
    is the algorithm"). Nothing in this module draws a random number -- the vortex layout is
    a golden-angle spiral, not a random one.
  * it is evaluated ANALYTICALLY PER STROKE rather than as an image-sized field. Six vortices
    across 5 000 strokes is microseconds; the same field at megapixel resolution is not, and
    it would drag decimation, interpolation and the float32-field dtype traps into the port
    for nothing (see tensor.flat_tensor for what that costs).

Inert at the default, and inert by SKIPPING: `params_for` returns None at `flow='none'`, so
the default painting never enters this file and stays bit-identical to one made before it
existed.

COORDINATES ARE NORMALISED -- (x - w/2) / min(h, w) -- so the same photograph at 700 px and
at 1400 px gets the same swirls in the same places. A vortex measured in pixels would be a
different painting at every --max-side, which is not what a style is.

Everything runs in float64 and rounds to float32 once, on the way out, so that
`web/tune/oilpaint/flow.js` is a transliteration rather than a reconstruction.
"""

import numpy as np

# The golden angle, pi * (3 - sqrt(5)). Successive multiples of it never fall into a
# repeating pattern, which is why sunflowers use it and why the vortex centres do: a
# lattice of swirls reads as wallpaper, and the alternative -- placing them at random --
# would need an rng this module refuses to own.
GOLDEN_ANGLE = float(np.pi * (3.0 - np.sqrt(5.0)))

# Successive vortices step DOWN in size by this factor, cycling every three. Van Gogh's
# sky is not one scale of swirl but several nested ones, and a field of identical vortices
# reads as a texture rather than as weather. 0.7 rather than 0.5: at a half-octave step the
# third vortex is a quarter the size of the first and simply disappears under the strokes.
SIG_STEP = 0.7
_SIG_POW = (1.0, SIG_STEP, SIG_STEP * SIG_STEP)

# The two turbulence waves' directions, in radians. Chosen non-parallel and at no simple
# ratio to each other so their sum does not close into a regular lattice anywhere on the
# canvas -- which is the one thing that would give the "turbulence" away as arithmetic.
TURB_DIR_A = 0.9
TURB_DIR_B = 2.3
# The second wave is higher frequency and half the amplitude: one octave of detail on top
# of the first, which is as much as is visible at stroke scale and costs two more sin calls.
TURB_RATIO_B = 1.7
TURB_AMP_B = 0.5

TWO_PI = 2.0 * np.pi
HALF_PI = 0.5 * np.pi
EPS = 1e-9

# Ceiling on hand-placed vortices. Each one is a full pass over every stroke, so the cost
# is linear and small -- 32 vortices across the 40 000-stroke ceiling is 1.3 M evaluations,
# a few milliseconds. The cap is not there for speed. It is there because a field with
# dozens of overlapping vortices has no legible swirls left in it: the tangential terms
# cancel wherever three or more overlap, and what survives reads as noise. 32 is far past
# any composition anyone has wanted and still bounds the pathological case.
MAX_VORTICES = 32

# Each preset is a complete parameter block; the five exposed sliders trim it.
#
#   kind          'swirl' (vortices) or 'wave' (a direction that undulates)
#   strength      how far the style takes over from the picture's own structure. At 0 the
#                 content is untouched; at 1 even a clean edge is overruled -- which is not
#                 a bug for van Gogh, whose cypress IS a swirl.
#   coh           the coherence the style ASSERTS, which is how it decides mark SHAPE:
#                 elongation is driven by coherence (strokes.py step 4), so near 1 the
#                 strokes stretch to aniso_max and become ribbons and near 0.2 they stay
#                 stubby dabs. Independent of `strength` on purpose -- see flow_blend.
#   rot           the field's base direction / the spiral's rotation, in degrees
#   scale         feature size in normalised units: vortex sigma, or wave wavelength
#   n / spread    swirl only: how many vortices, and how far they spread from the centre
#   amp           wave only: how far the direction swings, in radians
#   turb / turb_scale   high-frequency wobble on the resulting angle, and its wavelength
#   drift         how far a stroke slides ALONG the flow, in stroke radii. This is the one
#                 term that moves a mark rather than turning it -- see strokes.from_cells.
_NONE = dict(kind="none", strength=0.0, coh=0.0, rot=0.0, scale=0.35, n=6, spread=0.45,
             bg=0.15, amp=0.0, turb=0.0, turb_scale=0.5, drift=0.0)

PRESETS = {
    # The identity, present as a name rather than as a null so the dropdown has an "off"
    # that reads like the others. `params_for` returns None on it and nothing here runs.
    "none": _NONE,
    # The Starry Night (1889). Counter-rotating vortices at three scales over a steady
    # background drift, strong enough to overrule the subject, and coherent enough that
    # every mark stretches to the full aniso_max -- van Gogh's sky is ribbons, not dabs.
    # `drift` is what turns those ribbons into CHAINS following the curve, which is the
    # part orientation alone cannot give.
    #
    # `turb` was 0.35 at turb_scale 0.22 and that was WRONG, in a way only the pictures
    # showed: 0.35 * 1.5 is a 30 degree scatter, on top of the 20 degrees jitter_theta_deg
    # already adds, at a wavelength of only a few strokes -- so neighbouring marks pointed
    # different ways and the result read as fur rather than as combed paint. What makes a
    # van Gogh is that the marks next to each other AGREE and the disagreement is between
    # one passage and the next, which is the swirl field's job, not the turbulence's. At
    # 0.12 / 0.45 the roughening survives (10 degrees, and half of that at the octave) and
    # the combing comes back.
    "starry": dict(_NONE, kind="swirl", strength=0.85, coh=0.95, rot=0.0, scale=0.30,
                   n=7, spread=0.55, bg=0.18, turb=0.12, turb_scale=0.45, drift=0.55),
    # Monet's Nympheas. Two claims at once, and they are independent numbers for the reason
    # `flow_blend` explains: the water plane is HORIZONTAL and asserted firmly (strength
    # 0.70), and the marks on it are SHORT (coh 0.18). What separates Monet from van Gogh
    # at a glance is dabs against ribbons, not one angle against another. Measured on a
    # test scene: mean cos(2*theta) +0.24 unstyled -> +0.70, and mean elongation 5.3 -> 2.5.
    # A long wavelength so the surface undulates rather than ripples.
    "waterlily": dict(_NONE, kind="wave", strength=0.70, coh=0.18, rot=0.0, scale=0.90,
                      amp=0.45, turb=0.18, turb_scale=0.30, drift=0.15),
    # Cezanne's constructive stroke: parallel diagonal hatching laid over everything
    # regardless of what it describes. A wave with almost no amplitude is a constant
    # direction, so this is the same code with `amp` turned off -- and no drift, because
    # the marks are discrete blocks and are not meant to run into each other.
    "hatch": dict(_NONE, kind="wave", strength=0.70, coh=0.55, rot=35.0, scale=1.50,
                  amp=0.06, turb=0.04, turb_scale=0.60, drift=0.0),
}

FLOWS = list(PRESETS)


def params_for(cfg, vortices=None):
    """The effective flow block for a config, or None when there is nothing to do.

    None rather than a zero-strength block, for the same reason `palette.params_for`
    returns None: at strength 0 the blend below is an identity in exact arithmetic and is
    NOT one in floating point, because it still rebuilds theta through atan2. Returning
    None is what keeps an unstyled painting bit-identical to one made before this file.

    `vortices` is where the swirls go, as (fx, fy) FRACTIONS of the image in [0, 1] -- the
    thing a click gives you, and resolution-independent like the rest of the field. It is
    not a `PaintConfig` field for the same reason the foveal map is not one: it is a list
    of points, not a scalar, so no slider could carry it (see pipeline.PaintConfig). None
    or empty means the procedural golden-angle spiral the presets shipped with -- so
    clearing the placed points returns the preset as written, rather than leaving a field
    with no swirls in it at all.

    A PLAIN LIST, always: one field, one set of centres. Which set a REGION's field gets is
    decided before this by `centres_for`, so that the per-region addressing lives with the
    caller and this function has exactly one thing to be right about.
    """
    if cfg.flow not in PRESETS:
        raise ValueError(f"unknown flow {cfg.flow!r}")
    if cfg.flow == "none" or cfg.flow_strength <= 0.0:
        return None
    p = dict(PRESETS[cfg.flow])
    # The sliders TRIM the preset rather than replacing it: at the defaults you get the
    # preset as written, which is what makes the dropdown a starting point instead of a
    # lock. `flow_coh` and `flow_drift` go negative so a preset's own choice can be
    # cancelled -- without that, a preset's ribbons could not be judged against its dabs.
    p["strength"] = min(1.0, max(0.0, p["strength"] * float(cfg.flow_strength)))
    # Feature SIZE, so the vortices and the turbulence scale together; `spread` does not,
    # because that is composition -- where the swirls sit -- and scaling it by 3 would
    # push them off the canvas.
    s = max(0.05, 1.0 + float(cfg.flow_scale))
    p["scale"] = p["scale"] * s
    p["turb_scale"] = p["turb_scale"] * s
    p["rot"] = p["rot"] + float(cfg.flow_rot)
    p["coh"] = min(1.0, max(0.0, p["coh"] + float(cfg.flow_coh)))
    p["drift"] = p["drift"] + float(cfg.flow_drift)
    # Hand-placed centres, kept as fractions and converted only where h and w are known
    # (see `field_centres`). A tuple, so the block cannot be mutated through a reference a
    # caller kept.
    p["vortices"] = None
    if vortices is not None:
        vs = tuple((float(a), float(b)) for a, b in vortices)[:MAX_VORTICES]
        # Empty means "nothing placed", which is the spiral -- NOT "no vortices", which
        # would leave only the background drift and paint a uniform sweep.
        p["vortices"] = vs or None
    # The name rides along so an info dict and a test can say which preset actually ran.
    p["name"] = cfg.flow
    return p


def vortex_map(vortices):
    """`vortices` in either accepted shape -> `{region id: tuple of (fx, fy)}`.

    TWO SHAPES, because the swirl centres came before the regions did, and a caller that
    predates them -- `--vortices`, or anything reading a setup written back then -- means
    "the picture's swirls":

      * a plain sequence of points, which is the base's set, i.e. `{0: [...]}`;
      * `{region id: [points]}`, which is what the page's swirl tool produces once a layer
        has a set of its own.

    None (or an empty map) is `{}`: every field on its own preset's spiral. Normalising here
    rather than at each reader is what keeps `flow.params_for` taking a plain list -- the
    per-region addressing is the CALLER's problem, not the field's.
    """
    if vortices is None:
        return {}
    if isinstance(vortices, dict):
        return {int(k): tuple((float(a), float(b)) for a, b in (v or ()))
                for k, v in vortices.items()}
    return {0: tuple((float(a), float(b)) for a, b in vortices)}


def centres_for(vmap, rid=0):
    """The placed centres region `rid` uses: its OWN set, or the base's when it has none.

    INHERITANCE IS FORCED, not a preference, and it is the argument reference.py already
    makes for its fitted transport. The equivalence invariant says that giving every region
    the base config's own values must reproduce the undivided painting BIT for bit, and a
    region that fell back to the preset's spiral while the base was using placed centres
    would be a different painting. It is also what a person means by it: the picture's
    swirls, unless this passage says otherwise.

    So "this region has no set of its own" is ABSENCE, not an empty list. The page deletes
    the key when a layer's last marker goes, so a layer with no markers always means one
    thing and the markers on screen are the ones that will be read. An EXPLICITLY empty set
    is still the spiral -- which is what the base has always done with one, and is the third
    state the CLI can name and the page deliberately never produces.
    """
    if rid in vmap:
        return vmap[rid]
    return vmap.get(0)


def field_centres(p, h, w):
    """The hand-placed vortex centres in the field's own frame, or None for the spiral.

    Through `norm_coords`, the SAME transform the stroke positions go through, so a vortex
    placed at a pixel sits exactly where a stroke at that pixel sits. Converting with a
    second copy of the arithmetic is how a swirl ends up half a canvas away from where it
    was clicked on a non-square image.

    None for any field kind that has no vortices in it, so handing placed points to a
    'wave' preset leaves it bit-identical rather than quietly half-applying them.
    """
    vs = p.get("vortices")
    if not vs or p["kind"] != "swirl":
        return None
    fx = np.array([q[0] for q in vs], dtype=np.float64)
    fy = np.array([q[1] for q in vs], dtype=np.float64)
    return norm_coords(fy * h, fx * w, h, w)


def norm_coords(cy, cx, h, w):
    """Pixel coordinates -> the normalised frame the field lives in.

    Centred on the canvas and divided by the SHORT side, so the field is the same shape at
    any resolution and a square canvas spans [-0.5, 0.5] on both axes.
    """
    s = float(min(h, w))
    return (np.asarray(cx, dtype=np.float64) - 0.5 * w) / s, \
           (np.asarray(cy, dtype=np.float64) - 0.5 * h) / s


def _turbulence(u, v, p):
    """Two crossed sine waves, one an octave up. Cheap multi-scale wobble on the angle."""
    k = TWO_PI / max(EPS, p["turb_scale"])
    a = np.sin(k * (u * np.cos(TURB_DIR_A) + v * np.sin(TURB_DIR_A)))
    b = np.sin(TURB_RATIO_B * k * (u * np.cos(TURB_DIR_B) + v * np.sin(TURB_DIR_B)) + 1.3)
    return p["turb"] * (a + TURB_AMP_B * b)


def spiral_centres(p):
    """Where the swirls go when nobody has placed them: a golden-angle spiral.

    The angle marches by GOLDEN_ANGLE and the radius by sqrt(k/n), which is the placement
    that spreads n points evenly over a disc. Split out of `flow_dir` so that the placed
    and the procedural cases go down exactly ONE code path from here on -- and so a test
    can hand these very numbers back in as placed points and get the same field, bit for
    bit, which is what proves they do.
    """
    rot = np.deg2rad(p["rot"])
    n = int(p["n"])
    px = np.empty(n, dtype=np.float64)
    py = np.empty(n, dtype=np.float64)
    for k in range(n):
        ang = k * GOLDEN_ANGLE + rot
        rad = p["spread"] * np.sqrt((k + 0.5) / n)
        px[k] = rad * np.cos(ang)
        py[k] = rad * np.sin(ang)
    return px, py


def flow_dir(u, v, p, centres=None):
    """The style's own direction at each point, in radians. float64 in, float64 out.

    The swirl branch sums TRUE 2*pi vectors, not doubled-angle ones: a flow field is
    directed, and two counter-rotating vortices that meet must cancel into a straight run
    between them rather than reinforce. Only the sum is folded down to an orientation, at
    the arctan2 -- which is also why the background term matters. Without it the sum goes
    to zero far from every vortex and the angle there is the arctangent of nothing.

    `centres` is (cu, cv) in this frame, from `field_centres` -- the swirls somebody placed
    by hand. None falls back to `spiral_centres`. Everything else about a vortex is the
    same either way: the sizes still cycle through _SIG_POW and the handedness still
    alternates with the index, so with placed points the CLICK ORDER decides which way each
    one turns. `rot` still swings the background drift, and does NOT turn the placed
    centres -- they are where they were put.
    """
    rot = np.deg2rad(p["rot"])
    kind = p["kind"]

    if kind == "swirl":
        px, py = spiral_centres(p) if centres is None else centres
        n = px.size
        vx = np.full(u.shape, p["bg"] * np.cos(rot), dtype=np.float64)
        vy = np.full(u.shape, p["bg"] * np.sin(rot), dtype=np.float64)
        for k in range(n):
            dx = u - px[k]
            dy = v - py[k]
            # Written as a table lookup rather than SIG_STEP ** (k % 3): pow is correctly
            # rounded and the multiply is not, so two roundings both languages agree on
            # beat one rounding each computes with a different libm.
            sig = p["scale"] * _SIG_POW[k % 3]
            d2 = dx * dx + dy * dy
            # A Gaussian vortex: tangential, falling off smoothly so neighbouring swirls
            # blend instead of meeting at a seam. Alternating sign, so they counter-rotate
            # -- a field of same-handed vortices reads as a single rotating disc.
            wgt = np.exp(-0.5 * d2 / (sig * sig))
            if k % 2:
                wgt = -wgt
            r = np.sqrt(d2) + EPS
            vx = vx + wgt * (-dy / r)
            vy = vy + wgt * (dx / r)
        a = np.arctan2(vy, vx)
    elif kind == "wave":
        # The direction undulates as you move ACROSS it -- hence rot + pi/2 in the phase --
        # so the marks form bands that slide past each other, which is what a water
        # surface does. At amp ~ 0 this is a constant direction and the branch is hatching.
        c, s = np.cos(rot + HALF_PI), np.sin(rot + HALF_PI)
        a = rot + p["amp"] * np.sin(TWO_PI * (u * c + v * s) / max(EPS, p["scale"]))
    else:
        raise ValueError(f"unknown flow kind {kind!r}")

    if p["turb"] != 0.0:
        a = a + _turbulence(u, v, p)
    return a


def flow_blend(base, coh_in, u, v, p, centres=None):
    """Fold the style's direction into the picture's own, in doubled-angle space.

    The same vector sum as `strokes.flat_blend`, and for the same reason: an orientation is
    pi-periodic, so the only way to average two of them is through (cos 2t, sin 2t), and the
    length of the sum falls where the two disagree. Where the style and the subject point
    the same way the marks commit; where they fight, the stroke goes rounder rather than
    picking one at random.

        w_content = coh_in * (1 - strength)     the picture, handed over as strength rises
        w_style   = strength                    the style's authority over the DIRECTION

    `strength` therefore spans: at low values the flow only fills the passages where the
    tensor had no signal anyway (a sky, an out-of-focus ground), and at 1 it overrules real
    edges as well.

    WHY THE COHERENCE IS NOT SIMPLY THE LENGTH OF THAT SUM, which is what `flat_blend` does
    and what this did first. Coherence drives elongation, so if `w_style` were
    `strength * coh` -- the style's authority scaled by the marks it wants -- then a style
    that asks for SHORT marks would automatically be a weak style. Measured: at Monet's
    coh 0.22 the content outweighed the style 3.7 to 1 in a passage where the tensor was
    confident, so the water came out neither horizontal nor stubby. But "short marks" and
    "assert them firmly" is exactly what Monet is, so the two have to be separate numbers.

    The coherence is instead the weighted average of what each side ASKS FOR, cut by how
    much they agree:

        coh = |v| * (w_content * coh_in + w_style * coh_style) / (w_content + w_style)^2

    which has the three properties the length had, and one it did not:

      * at strength 1 it is exactly `coh_style` -- the style gets the marks it asked for;
      * at strength 0 it is algebraically `coh_in`, so the blend really is an identity
        there rather than approximately one (it is still SKIPPED, see params_for);
      * where the two directions disagree |v| shrinks and the stroke goes round;
      * and elongation no longer decides who wins the argument about direction.

    DTYPE, deliberately different from `flat_blend`: both inputs are promoted to float64 on
    entry. `flat_blend` does not, so numpy calls cosf on its float32 angles and the port has
    to carry Math.fround around every trig call to match. Promoting first removes that whole
    class of divergence from the new code at no cost -- the VALUES are the same float32-
    rounded values either way, and both languages then compute in float64.

    `coh` comes back float32 because that dtype is load-bearing downstream: it is what keeps
    `ratio` float32 until the jitter array promotes it (see the strokes.py header).
    """
    t = flow_dir(u, v, p, centres)
    b = np.asarray(base, dtype=np.float64)
    c = np.asarray(coh_in, dtype=np.float64)

    w_content = c * (1.0 - p["strength"])
    w_style = p["strength"]

    vx = w_content * np.cos(2.0 * b) + w_style * np.cos(2.0 * t)
    vy = w_content * np.sin(2.0 * b) + w_style * np.sin(2.0 * t)

    theta = 0.5 * np.arctan2(vy, vx)
    # `tot` cannot be zero: params_for has already returned None at strength 0, so
    # w_style > 0 for every call that reaches here.
    tot = w_content + w_style
    mag = np.sqrt(vx * vx + vy * vy)
    coh = np.minimum(mag * (w_content * c + w_style * p["coh"]) / (tot * tot), 1.0)
    return theta, coh.astype(np.float32)
