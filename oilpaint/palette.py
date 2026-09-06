"""Artistic colour: the pigment grade, applied to the paint rather than to the photo.

A photograph's colours are not a painting's, and no amount of stroke geometry fixes that.
Paint has a warm-cool structure a camera does not record (lights swing toward the light's
own hue, shadows toward its complement), it cannot reach pure black or pure white, and its
chroma peaks in the midtones and dies at both ends. This module is those three facts as
arithmetic.

WHERE IT RUNS IS THE DESIGN. It is applied by `pipeline.plan` to `sb.rgb` -- the per-stroke
pigment -- and to the base canvas, AFTER the quadtree, the tau search, the tensor and the
stroke geometry have all been decided. Two consequences, both deliberate:

  * the leaves, the stroke order, the angles and the radii are untouched, so nothing the
    parity suites already pin can move, and there is no new `rng` call to shift the draw
    stream (CLAUDE.md: "the RNG draw ORDER is the algorithm");
  * a stroke is ONE dab of ONE mixture, which is what paint actually is. That is what makes
    the pigment projection below meaningful: per pixel it would be a posterising filter with
    brush marks drawn over it, and it would band every smooth passage in the picture.

Two things happen here, in that order. The GRADE is statistical -- warm lights and cool
shadows, a value range with no pure black or white in it, chroma shaped by value, and a
warm-cool alternation between neighbouring strokes. The PROJECTION is a gamut: every preset
names real tubes, and each stroke is pulled onto the nearest colour those tubes can actually
mix. The grade is a wish; the projection is what the palette can grant.

Inert at the defaults, and inert by SKIPPING: `params_for` returns None when nothing is
asked for, so the default painting never round-trips through OKLab and stays bit-identical.

The maths is in OKLab (Björn Ottosson, 2020) -- perceptually even, so "cut chroma",
"compress value" and "rotate hue" each do what their name says, and closed form with no
tables. Everything below runs in float64 and rounds to float32 once, on the way out: that
is what lets `web/tune/oilpaint/palette.js` mirror it in a language with no float32
arithmetic, leaving only libm's last ulp between the two.
"""

import numpy as np

from .image import linear_to_srgb, srgb_to_linear

# The warm-cool swing, in OKLab units per unit of (L - pivot) per unit of `wc`. At the
# Impressionist preset (wc 0.8) a light 0.4 above the pivot moves 0.038 -- a quarter of a
# saturated colour's chroma, which reads as a decision rather than as a cast.
WC_SCALE = 0.12

# Broken colour: the per-stroke warm-cool alternation, in the same units. Smaller than
# WC_SCALE because it is a difference between NEIGHBOURS, where the eye is far more
# sensitive than it is to a drift across the whole picture.
BROKEN_SCALE = 0.06

# --- hue-band steering ----------------------------------------------------------------
#
# A GLOBAL hue rotation is the wrong tool and it is worth saying why, because it is the
# obvious first thing to reach for: it moves every colour by the same angle, so it cannot
# cool a sky without also cooling the skin in front of it. What a painter does is per
# FAMILY -- the specific giveaway is foliage, because digital green is a hue paint can
# barely make, and pulling just the greens toward yellow-green fixes a picture that a
# global turn only trades for a different wrongness.
#
# So a band is (centre, width, rotation, chroma gain) and only colours NEAR the centre
# move. The weight is a von MISES bump, exp(k*(cos(dh) - 1)), not a Gaussian on a wrapped
# angle difference. Three reasons, in order of how much they matter:
#
#   * hue is circular, and von Mises is the circular Gaussian -- it needs no wrapping at
#     all, so there is no `mod` and no `round` for the port to disagree about (numpy rounds
#     half to even, JS rounds half up, and that is exactly the class of one-ulp fork this
#     project cannot see by looking);
#   * cos(h - centre) is `(a*cx + b*cy)/r`, so there is no `arctan2` either -- the band
#     reads the colour's own (a, b) directly;
#   * for small dh it IS the Gaussian: cos(dh) - 1 -> -dh^2/2, so `width` still means what
#     a standard deviation means, in degrees.
#
# At the widest setting the bump is almost flat and the band degenerates into the global
# rotation -- which is the honest way to offer that: as the limiting case of the control
# that can do better, rather than as a separate knob.
HUE_MIN_WIDTH_DEG = 5.0   # kappa = 1/sigma^2 blows up as the band narrows; this caps it
HUE_EPS = 1e-9            # a neutral has no hue, so it belongs to no band
# Degrees -> radians as ONE multiply by a precomputed constant, on both sides. `np.radians`
# and the JS `x * Math.PI / 180` are not the same arithmetic -- one scales by pi/180, the
# other multiplies by pi and then divides -- and the two disagree in the last bit. Every
# other angle in this file is inside the grade's existing tolerance; a band's is multiplied
# by a rotation of up to 180 degrees, so it is not.
_DEG = np.pi / 180.0

# Each preset is a complete parameter block; the four exposed sliders trim it.
#
#   wc / pivot            warm-cool swing, and the value it pivots about
#   warm_deg / cool_deg   the two directions it swings BETWEEN, as angles in the OKLab
#                         (a, b) plane. Not one axis and its negative: a painter's warm is
#                         yellow-orange (~60 deg) while the cool that answers it is a
#                         choice -- steel blue (250) reads as daylight shadow, violet (285)
#                         as Impressionist, and the difference between those two is most of
#                         what separates the presets from each other.
#   lo / hi               the output value range. Paint has no pure black and no pure
#                         white; this is that, and it is most of the "oil" feel.
#   tint_lo / tint_hi     the (a, b) offset at the dark and the light end. What black and
#                         white are MADE of -- a warm brown-black, a cream white.
#   chroma / chroma_mid   overall chroma gain, plus a midtone bell on top of it. The bell
#                         is the shape a limited palette has: mixtures are most saturated
#                         where they are least tinted or shaded.
#   broken                per-stroke warm-cool alternation, for optical mixing.
#   bands                 hue-band steering: a tuple of (centre_deg, width_deg, rot_deg,
#                         chroma_gain). Empty in every preset shipped so far -- the four
#                         hue sliders are the way in, and a preset that grew a band would
#                         silently change every painting already made with that name.
_NONE = dict(wc=0.0, pivot=0.5, warm_deg=60.0, cool_deg=250.0, lo=0.0, hi=1.0,
             tint_lo_a=0.0, tint_lo_b=0.0, tint_hi_a=0.0, tint_hi_b=0.0,
             chroma=1.0, chroma_mid=0.0, broken=0.0, pigment=0.0, bands=())

PRESETS = {
    # The identity. Present as a preset rather than as a null so that the four sliders
    # still work on their own -- picking no character is not the same as picking no grade.
    "none": _NONE,
    # Zorn: yellow ochre, vermilion, ivory black, white. Four pigments cannot make a
    # saturated blue OR a saturated green, so the chroma comes down hard and what is left
    # leans warm; the cool is what black-plus-white does, which is a neutral steel.
    "zorn": dict(_NONE, wc=0.55, pivot=0.50, warm_deg=60.0, cool_deg=250.0,
                 lo=0.06, hi=0.93, tint_lo_a=0.010, tint_lo_b=0.006,
                 tint_hi_a=0.004, tint_hi_b=0.014, chroma=0.78, chroma_mid=0.35,
                 pigment=0.55),
    # Impressionist: no black on the palette at all. Shadows are ultramarine and violet,
    # never a darkened version of the local colour, and the whole thing is keyed high.
    "impressionist": dict(_NONE, wc=0.80, pivot=0.48, warm_deg=70.0, cool_deg=288.0,
                          lo=0.12, hi=0.97, tint_lo_a=0.020, tint_lo_b=-0.045,
                          tint_hi_a=0.0, tint_hi_b=0.020, chroma=1.25, chroma_mid=0.30,
                          broken=0.20, pigment=0.35),
    # Earth palette under varnish: ochre, sienna, umber, lead white. Narrow value range,
    # warm brown-black instead of a neutral one, and the chroma living almost entirely in
    # the midtones -- which is what a glaze over an underpainting does.
    "old-master": dict(_NONE, wc=0.45, pivot=0.55, warm_deg=55.0, cool_deg=265.0,
                       lo=0.10, hi=0.88, tint_lo_a=0.018, tint_lo_b=0.020,
                       tint_hi_a=0.004, tint_hi_b=0.030, chroma=0.72, chroma_mid=0.45,
                       pigment=0.50),
    # Night. The ceiling comes down rather than the floor coming up: what makes a nocturne
    # is that nothing in it is bright, not that everything in it is dark.
    "nocturne": dict(_NONE, wc=0.50, pivot=0.40, warm_deg=50.0, cool_deg=275.0,
                     lo=0.04, hi=0.72, tint_lo_a=0.0, tint_lo_b=-0.030,
                     tint_hi_a=0.010, tint_hi_b=-0.020, chroma=0.70, chroma_mid=0.25,
                     pigment=0.45),
    # Colour off the leash: chroma past what the camera saw, the full warm-cool swing, and
    # enough broken colour that neighbouring strokes disagree with each other.
    "fauve": dict(_NONE, wc=1.00, pivot=0.50, warm_deg=45.0, cool_deg=300.0,
                  lo=0.05, hi=1.00, chroma=1.60, chroma_mid=0.50, broken=0.35,
                  pigment=0.60),
}

# --- the pigments themselves ---------------------------------------------------------
#
# A preset's NAME is a claim about which tubes were on the palette, and until now it was
# only a claim: the block above shapes colour statistically, it does not stop a mixture
# existing that no pigment set can make. This is the other half. Each set is real paint, as
# sRGB, and `_mix_lut` enumerates what a painter can actually reach from it.
#
# WHITE IS ALWAYS LAST. The tint pass below reaches for it by index, because tinting with
# white is not one mixture among many -- it is the axis every other mixture moves along.
#
# The values are eyeballed masstone approximations, not spectrophotometry: what has to be
# right for the look is the SHAPE of the reachable set (a Zorn palette cannot reach a
# saturated blue at any mixture, and that is the whole point), not the exact hue of one tube.
PIGMENTS = {
    # No projection. The statistical grade above still applies.
    "none": (),
    # Yellow ochre, vermilion, ivory black, titanium white. Four tubes, no blue and no
    # green anywhere in the reachable set -- which is why a Zorn portrait's cool passages
    # are black-and-white greys reading as blue against the warm ones.
    "zorn": ((0.796, 0.596, 0.216), (0.855, 0.216, 0.129), (0.110, 0.100, 0.100),
             (0.980, 0.970, 0.940)),
    # Cadmium lemon, cadmium red, alizarin, ultramarine, cerulean, viridian, lead white.
    # No black: the darkest thing on this palette is ultramarine over alizarin.
    "impressionist": ((0.980, 0.850, 0.200), (0.870, 0.200, 0.160), (0.680, 0.130, 0.280),
                      (0.200, 0.240, 0.620), (0.220, 0.500, 0.720), (0.100, 0.450, 0.360),
                      (0.980, 0.970, 0.930)),
    # Yellow ochre, burnt sienna, burnt umber, terre verte, ivory black, lead white -- the
    # earth palette, whose reachable set is a narrow warm wedge and almost nothing else.
    "old-master": ((0.780, 0.600, 0.250), (0.600, 0.300, 0.150), (0.320, 0.200, 0.140),
                   (0.360, 0.400, 0.280), (0.100, 0.090, 0.090), (0.960, 0.930, 0.860)),
    # Prussian, ultramarine, burnt umber, yellow ochre, ivory black, lead white.
    "nocturne": ((0.060, 0.200, 0.300), (0.200, 0.240, 0.620), (0.320, 0.200, 0.140),
                 (0.780, 0.600, 0.250), (0.090, 0.090, 0.100), (0.950, 0.940, 0.900)),
    # Cadmiums and the two strongest blues, nothing earth, nothing black.
    "fauve": ((1.000, 0.830, 0.100), (0.980, 0.500, 0.060), (0.900, 0.130, 0.130),
              (0.800, 0.100, 0.450), (0.150, 0.200, 0.700), (0.050, 0.600, 0.400),
              (1.000, 1.000, 0.980)),
}

# Mixing is SUBTRACTIVE, and the difference is not academic: averaged in linear light,
# ultramarine and cadmium yellow give grey, which is the single most obviously wrong thing
# a naive pigment model does. A weighted GEOMETRIC mean of the reflectances -- multiplying
# what each pigment lets through, rather than adding what each emits -- gives green, and it
# gives black its real behaviour too (a strong tinter that pulls a mixture down fast).
# It is not Kubelka-Munk; it is the cheapest model that is right about the two things a
# viewer would notice.
#
# The floor keeps a pigment's darkest channel off zero, where the geometric mean would
# collapse any mixture containing it to black.
REFL_FLOOR = 0.004

# Where a binary mixture is sampled. The endpoints are the pigments themselves and are
# added separately, so these are the interior only.
MIX_STEPS = (0.2, 0.4, 0.6, 0.8)

# ...and then every binary mixture is tinted with white, because that is the move a painter
# makes more than any other: the reachable set is not a web of pairs, it is that web dragged
# toward white. For a 7-pigment palette this is 7 + 84 + 252 = 343 mixtures.
TINT_STEPS = (0.25, 0.5, 0.75)


def _mix_lut(name):
    """The mixtures a palette can reach, as (L, a, b) arrays. None when there is no palette.

    Built per grade call rather than cached: it is a few hundred entries and a few thousand
    flops, against a stroke buffer that is four orders of magnitude larger. A cache here
    would be a correctness surface (which palette? which floor?) bought for nothing.
    """
    pig = PIGMENTS[name]
    if not pig:
        return None
    lin = np.maximum(srgb_to_linear(np.asarray(pig, dtype=np.float64)), REFL_FLOOR)
    white = lin[-1]

    def mix(a, b, t):
        return a ** (1.0 - t) * b ** t

    rows = [lin[i] for i in range(len(lin))]
    binaries = []
    for i in range(len(lin)):
        for j in range(i + 1, len(lin)):
            for t in MIX_STEPS:
                binaries.append(mix(lin[i], lin[j], t))
    rows.extend(binaries)
    for c in binaries:
        for t in TINT_STEPS:
            rows.append(mix(c, white, t))
    return np.stack(_lin_to_oklab(np.asarray(rows, dtype=np.float64)), axis=-1)


# The nearest-mixture search is a full scan of the LUT per colour, so a chunk of colours
# against a few hundred mixtures is an (n, m, 3) intermediate. 1024 keeps that inside a few
# MB whatever the stroke count; it is a memory bound, not a tuning knob, and the result does
# not depend on it.
PROJ_CHUNK = 1024


def _project(L, a, b, lut, amount):
    """Pull each colour toward the nearest mixture the palette can actually make.

    A full scan, not a tree: the LUT is a few hundred rows, and the constant factor of any
    acceleration structure costs more than it saves at that size -- while an approximate
    nearest neighbour would be a second thing to keep in parity for no gain.

    Ties go to the FIRST minimum on both sides (`argmin`, and a strict `<` in the JS), so
    the two agree even where a palette contains duplicate mixtures -- which it does: mixing
    a pigment with itself is every ratio of the same colour.
    """
    Ll, al, bl = lut[:, 0], lut[:, 1], lut[:, 2]
    idx = np.empty(L.shape, dtype=np.int64)
    for i in range(0, L.shape[0], PROJ_CHUNK):
        sl = slice(i, i + PROJ_CHUNK)
        dL = L[sl, None] - Ll[None, :]
        da = a[sl, None] - al[None, :]
        db = b[sl, None] - bl[None, :]
        idx[sl] = np.argmin(dL * dL + da * da + db * db, axis=1)
    return (L + (Ll[idx] - L) * amount,
            a + (al[idx] - a) * amount,
            b + (bl[idx] - b) * amount)


# The order the panel offers them in; 'none' first because it is the default.
PALETTES = ["none", "zorn", "impressionist", "old-master", "nocturne", "fauve"]


def params_for(cfg):
    """The effective grade for a config, or None when there is nothing to do.

    None rather than an identity block on purpose: an identity grade would still convert
    every colour to OKLab and back, and float32 -> float64 -> float32 through a cube root
    is not the identity. Returning None is what keeps an ungraded painting bit-identical
    to one made before this file existed.
    """
    if cfg.palette not in PRESETS:
        raise ValueError(f"unknown palette {cfg.palette!r}")
    # A band with neither a rotation nor a gain does nothing, so `hue_target` and
    # `hue_range` are deliberately NOT in this test: parking the band over a hue you are
    # about to work on must not start grading the picture behind you.
    #
    # EVERY VALUE MUST BE FINITE. A dataclass cannot be missing a field, so this side is
    # safe by construction where the port is not -- `undefined != 0.0` is true in JS and
    # that shipped a painting full of NaN (see palette.js). The guard is mirrored here
    # anyway: the two files have to read the same, and `--hue-rotate nan` is reachable
    # from the CLI.
    hb = (cfg.hue_target, cfg.hue_range, cfg.hue_rotate, cfg.hue_boost)
    band = None
    if all(np.isfinite(v) for v in hb) and (cfg.hue_rotate != 0.0 or cfg.hue_boost != 0.0):
        band = tuple(float(v) for v in hb)
    trims = (cfg.warm_cool, cfg.chroma, cfg.value_compress, cfg.broken_color, band)
    if cfg.palette_strength <= 0.0 or (cfg.palette == "none" and not any(trims)):
        return None
    return block_for(cfg, band)


def block_for(cfg, band=None):
    """The grade block a config describes, with no activity test in front of it.

    Split out of `params_for` for ONE caller: a reference transport (reference.py) is a
    colour move that has to ride the grade -- it is the same OKLab pass, and giving it a
    second one would mean a second conversion of every stroke and a second memo slot for
    the canvas. So `plan` asks for the block even when nothing else in the panel is on,
    and attaches the fitted transport to it. Everything the activity test protects is
    still protected: when no reference is on either, `params_for` returns None and this is
    never called.
    """
    if band is None:
        hb = (cfg.hue_target, cfg.hue_range, cfg.hue_rotate, cfg.hue_boost)
        if all(np.isfinite(v) for v in hb) and (cfg.hue_rotate != 0.0
                                                or cfg.hue_boost != 0.0):
            band = tuple(float(v) for v in hb)
    p = dict(PRESETS[cfg.palette])
    # A NEW tuple, never an append: the preset's own block is shared, and a band pushed
    # into it would leak into every later render of that palette.
    if band is not None:
        p["bands"] = tuple(p["bands"]) + (band,)
    # The sliders TRIM the preset rather than replacing it: at 0 you get the preset as
    # written, which is what makes the dropdown a starting point instead of a lock.
    p["wc"] += float(cfg.warm_cool)
    p["chroma"] += float(cfg.chroma)
    p["lo"] += 0.15 * float(cfg.value_compress)
    p["hi"] -= 0.10 * float(cfg.value_compress)
    p["broken"] += float(cfg.broken_color)
    p["pigment"] += float(cfg.pigment)
    # The name rides along because the pigment set is looked up by it, and `grade` is the
    # only place that can afford to build the mixture LUT once.
    p["name"] = cfg.palette
    p["strength"] = float(cfg.palette_strength)
    return p


def _steer_hue(a, b, bands):
    """Turn and saturate one family of hues at a time. (a, b) in, (a, b) out, float64.

    See HUE_MIN_WIDTH_DEG above for what a band is and why the weight is a von Mises bump
    rather than a wrapped Gaussian. The two accumulators are what make several bands
    composable: the ROTATIONS add as angles and the GAINS add as offsets from 1, so two
    overlapping bands agree on a colour between them instead of one of them winning. One
    cos/sin pair for the total, not one per band.
    """
    r = np.sqrt(a * a + b * b)
    inv = 1.0 / np.maximum(r, HUE_EPS)
    rot = np.zeros_like(r)
    gain = np.ones_like(r)
    for centre_deg, width_deg, rot_deg, boost in bands:
        c = float(centre_deg) * _DEG
        cx, cy = np.cos(c), np.sin(c)
        sig = max(float(width_deg), HUE_MIN_WIDTH_DEG) * _DEG
        sig2 = sig * sig
        rot_r = float(rot_deg) * _DEG
        # cos(h - centre), straight off the colour's own (a, b) -- no arctan2, no wrap.
        # A neutral has r at the floor, so the dot product is ~0 and the weight lands at
        # exp(-1/sig2), which for any usable width is zero. The clip is what keeps the
        # argument exactly in [-1, 1] when it is not: float error on a saturated colour can
        # put the ratio a hair past 1, and exp of a positive argument is a gain, not a bump.
        cosd = np.clip((a * cx + b * cy) * inv, -1.0, 1.0)
        w = np.exp((cosd - 1.0) / sig2)
        rot = rot + w * rot_r
        gain = gain + w * float(boost)
    # A gain below zero would flip the hue through the neutral axis, which is not what
    # "less of this colour" means to anyone.
    gain = np.maximum(gain, 0.0)
    cr, sr = np.cos(rot), np.sin(rot)
    return gain * (a * cr - b * sr), gain * (a * sr + b * cr)


def _lin_to_oklab(lin):
    """Linear-light rgb -> (L, a, b). Split out because the pigment LUT is mixed in linear
    reflectance and never passes through sRGB at all."""
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def _to_oklab(c):
    """sRGB in [0,1] -> (L, a, b). `c` is (n,3) float64; the three come back as (n,)."""
    return _lin_to_oklab(srgb_to_linear(c))


def _from_oklab(L, a, b):
    """The inverse, clipped back into sRGB. Returns (n,3) float64.

    The cube is written as three multiplies rather than as `** 3` on purpose: `pow` is
    correctly rounded and `x*x*x` is not, so the two differ in the last bit -- and the port
    has no `pow` worth calling here. Two roundings, agreed on by both sides, beat one
    rounding each side computes with a different libm. It is also ~7x faster.
    """
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ * l_ * l_, m_ * m_ * m_, s_ * s_ * s_
    lin = np.stack([
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    ], axis=-1)
    # `linear_to_srgb` clips first, which is exactly the out-of-gamut guard wanted here:
    # a chroma boost routinely asks for colours no monitor has.
    return linear_to_srgb(lin)


# Rows per pass. The grade is nine transcendental calls and ~40 temporaries deep, so at
# full resolution every one of those temporaries is a 24 MB round trip to main memory and
# the whole thing runs at memory speed rather than at libm speed. Cut into blocks that fit
# in cache it is 3x faster, elementwise-identical (measured 2224 -> 757 ms per megapixel),
# and the size is not sensitive -- 8k and 128k rows are within 4% of each other.
CHUNK = 1 << 15


def grade(rgb, gp, phase=None):
    """Apply a grade block to sRGB colours. Shape preserved, float32 out.

    `phase` is the per-stroke wobble seed already carried by `StrokeBuffer` -- uniform on
    [0, 2pi) -- and it is what drives the broken colour. Reusing it rather than drawing is
    the whole reason this feature costs nothing in parity: a new `rng` call here would
    shift the stream and change every stroke in the painting.

    It also gates the pigment projection, for the same reason: `phase is not None` is what
    says "these are dabs of paint". Pass None for an image (the base canvas), which gets the
    statistical grade and neither of the two per-dab effects.
    """
    shape = np.shape(rgb)
    src = np.asarray(rgb, dtype=np.float64).reshape(-1, 3)
    ph = None if phase is None else np.asarray(phase, dtype=np.float64).reshape(-1)
    # Once, not per chunk: a few hundred mixtures against a stroke buffer four orders of
    # magnitude larger. Not built at all when nothing can be projected -- palette='none' has
    # no pigments, and an image has no strokes.
    lut = _mix_lut(gp["name"]) if gp["pigment"] > 0.0 and ph is not None else None
    out = np.empty(src.shape, dtype=np.float32)
    for i in range(0, len(src), CHUNK):
        out[i:i + CHUNK] = _grade_chunk(
            src[i:i + CHUNK], gp, None if ph is None else ph[i:i + CHUNK], lut)
    return out.reshape(shape)


def _grade_chunk(c, gp, phase, lut=None):
    """One cache-sized block of (n,3) float64 sRGB. See `grade`; this is the arithmetic."""
    L0, a0, b0 = _to_oklab(c)

    # 0. the reference transport, before everything, because everything after it is a hand
    #    adjustment ON TOP of the match. `xfer` is fitted once per plan against the whole
    #    stroke buffer (reference.fit) and arrives here as one 3x3 and an offset, so the
    #    per-colour cost is nine multiplies. Absent at the defaults, and then not a single
    #    one of them runs.
    #
    #    APPLIED HERE RATHER THAN IN reference.py, and that is not a style choice: that
    #    module needs this one's OKLab conversion, so it importing back would be a cycle.
    #    reference.py FITS the transport (which is where all the interesting maths is);
    #    this is the nine multiplies that use it. The blend is in OKLab, because half of a
    #    transport should be half of the perceptual move -- lerping toward a colour on the
    #    far side of the gamut in sRGB travels through a different hue on the way.
    if gp.get("xfer") is not None:
        m, off, t = gp["xfer"]["a"], gp["xfer"]["b"], gp["xfer"]["strength"]
        nl = m[0][0] * L0 + m[0][1] * a0 + m[0][2] * b0 + off[0]
        na = m[1][0] * L0 + m[1][1] * a0 + m[1][2] * b0 + off[1]
        nb = m[2][0] * L0 + m[2][1] * a0 + m[2][2] * b0 + off[2]
        if t == 1.0:
            L0, a0, b0 = nl, na, nb
        else:
            L0, a0, b0 = L0 + (nl - L0) * t, a0 + (na - a0) * t, b0 + (nb - b0) * t

    # 1. hue-band steering, so a band reads the hue as it ARRIVES at the grade -- the
    #    photograph's own, or the reference's if one matched it -- rather than one the
    #    warm-cool split has already moved. Skipped entirely when there are no bands, so
    #    the default grade does not pay a sqrt for a rotation of zero.
    a1, b1 = (a0, b0)
    if gp.get("bands"):
        a1, b1 = _steer_hue(a0, b0, gp["bands"])

    # 2. chroma, shaped by value. The bell peaks at 1.0 in the midtones and is 0 at both
    #    ends, so `chroma_mid` cannot push an already-clipped highlight further out.
    g = gp["chroma"] + gp["chroma_mid"] * (4.0 * L0 * (1.0 - L0))
    a, b = a1 * g, b1 * g

    # 3. what the two ends are MADE of, weighted by the original value.
    a = a + gp["tint_lo_a"] * (1.0 - L0) + gp["tint_hi_a"] * L0
    b = b + gp["tint_lo_b"] * (1.0 - L0) + gp["tint_hi_b"] * L0

    # 4. the warm-cool split: lights toward `warm_deg`, shadows toward `cool_deg`.
    warm = np.radians(gp["warm_deg"])
    cool = np.radians(gp["cool_deg"])
    wx, wy = np.cos(warm), np.sin(warm)
    cx, cy = np.cos(cool), np.sin(cool)
    s = gp["wc"] * (L0 - gp["pivot"]) * WC_SCALE
    a = a + np.abs(s) * np.where(s >= 0.0, wx, cx)
    b = b + np.abs(s) * np.where(s >= 0.0, wy, cy)

    # 5. broken colour, along the same two directions, alternating stroke by stroke.
    if gp["broken"] != 0.0 and phase is not None:
        d = gp["broken"] * BROKEN_SCALE * np.sin(phase)
        a = a + np.abs(d) * np.where(d >= 0.0, wx, cx)
        b = b + np.abs(d) * np.where(d >= 0.0, wy, cy)

    # 6. the value range paint actually has.
    L = gp["lo"] + (gp["hi"] - gp["lo"]) * L0

    # 7. ...and finally onto colours the palette can actually MIX. Last, because everything
    #    above is a wish and this is the gamut: a value compressed off the end of the range
    #    or a chroma boost past what a cadmium can do is pulled back by the projection, not
    #    carried past it. Strokes only -- `phase is not None` is what says "this is a dab".
    #    A dab is one mixture; the blurred ground is not, and quantising a smooth gradient
    #    onto a few hundred mixtures would band it.
    if lut is not None and phase is not None:
        L, a, b = _project(L, a, b, lut, gp["pigment"])

    # The master blend, in OKLab rather than in sRGB: half of a hue rotation should be a
    # hue halfway round, not a wash toward grey.
    t = gp["strength"]
    if t != 1.0:
        L, a, b = L0 + (L - L0) * t, a0 + (a - a0) * t, b0 + (b - b0) * t
    return _from_oklab(L, a, b)


def grade_strokes(colors, phase, gp, linear=False):
    """The (n,3) pigment array. `linear` says the colours arrived in linear light."""
    c = linear_to_srgb(colors) if linear else colors
    out = grade(c, gp, phase)
    return srgb_to_linear(out).astype(np.float32) if linear else out


def grade_image(img, gp, linear=False):
    """The same, for the HxWx3 base canvas -- no strokes, so neither per-dab effect."""
    c = linear_to_srgb(img) if linear else img
    out = grade(c, gp)
    return srgb_to_linear(out).astype(np.float32) if linear else out
