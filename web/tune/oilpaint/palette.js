// Port of oilpaint/palette.py -- the pigment grade, applied to the paint, not to the photo.
//
// The Python carries the reasoning: what the parameters mean, why each preset is set the
// way it is, and above all WHY THIS RUNS WHERE IT DOES -- after the geometry, on `sb.rgb`
// and on the base canvas, so that nothing here can move a quadtree leaf, a stroke or the
// RNG stream. Read it there; this file is the arithmetic and the arithmetic only.
//
// The Python deliberately computes in float64 and rounds to float32 once, on the way out.
// That is what makes this port a transliteration rather than a reconstruction: a JS number
// IS a float64, so mirroring it means writing the same expressions in the same order and
// calling Math.fround exactly where numpy's `.astype(np.float32)` is. There is no
// `Math.fround` sprinkled through the middle of these functions, and that is correct.
//
// What is left between the two sides is libm: pow (the sRGB transfer, twice) and cbrt.
// The cube is written out as `x*x*x` on both sides for the same reason -- see the Python.

import { linearToSrgb, srgbToLinear } from './image.js';

export const WC_SCALE = 0.12;
export const BROKEN_SCALE = 0.06;
// Hue-band steering; the Python carries the reasoning (a von Mises bump, so there is no
// wrap and no arctan2 for the two sides to disagree about). DEG is one multiply by a
// precomputed constant on both sides -- `x * Math.PI / 180` is a different rounding.
export const HUE_MIN_WIDTH_DEG = 5.0;
export const HUE_EPS = 1e-9;
const DEG = Math.PI / 180.0;

const NONE = {
  wc: 0.0, pivot: 0.5, warm_deg: 60.0, cool_deg: 250.0, lo: 0.0, hi: 1.0,
  tint_lo_a: 0.0, tint_lo_b: 0.0, tint_hi_a: 0.0, tint_hi_b: 0.0,
  chroma: 1.0, chroma_mid: 0.0, broken: 0.0, pigment: 0.0, bands: [],
};

export const PRESETS = {
  none: NONE,
  zorn: Object.assign({}, NONE, {
    wc: 0.55, pivot: 0.50, warm_deg: 60.0, cool_deg: 250.0,
    lo: 0.06, hi: 0.93, tint_lo_a: 0.010, tint_lo_b: 0.006,
    tint_hi_a: 0.004, tint_hi_b: 0.014, chroma: 0.78, chroma_mid: 0.35,
    pigment: 0.55 }),
  impressionist: Object.assign({}, NONE, {
    wc: 0.80, pivot: 0.48, warm_deg: 70.0, cool_deg: 288.0,
    lo: 0.12, hi: 0.97, tint_lo_a: 0.020, tint_lo_b: -0.045,
    tint_hi_a: 0.0, tint_hi_b: 0.020, chroma: 1.25, chroma_mid: 0.30,
    broken: 0.20, pigment: 0.35 }),
  'old-master': Object.assign({}, NONE, {
    wc: 0.45, pivot: 0.55, warm_deg: 55.0, cool_deg: 265.0,
    lo: 0.10, hi: 0.88, tint_lo_a: 0.018, tint_lo_b: 0.020,
    tint_hi_a: 0.004, tint_hi_b: 0.030, chroma: 0.72, chroma_mid: 0.45,
    pigment: 0.50 }),
  nocturne: Object.assign({}, NONE, {
    wc: 0.50, pivot: 0.40, warm_deg: 50.0, cool_deg: 275.0,
    lo: 0.04, hi: 0.72, tint_lo_a: 0.0, tint_lo_b: -0.030,
    tint_hi_a: 0.010, tint_hi_b: -0.020, chroma: 0.70, chroma_mid: 0.25,
    pigment: 0.45 }),
  fauve: Object.assign({}, NONE, {
    wc: 1.00, pivot: 0.50, warm_deg: 45.0, cool_deg: 300.0,
    lo: 0.05, hi: 1.00, chroma: 1.60, chroma_mid: 0.50, broken: 0.35,
    pigment: 0.60 }),
};

// --- the pigments themselves ---------------------------------------------------------
// The Python carries the reasoning and the tube names. Two things to keep in mind editing
// this: WHITE IS ALWAYS LAST in each set (the tint pass reaches for it by index), and the
// ENUMERATION ORDER in `mixLut` is part of the algorithm -- the nearest-mixture search
// breaks ties on the first minimum, so a reordered LUT is a different painting.
export const PIGMENTS = {
  none: [],
  zorn: [[0.796, 0.596, 0.216], [0.855, 0.216, 0.129], [0.110, 0.100, 0.100],
         [0.980, 0.970, 0.940]],
  impressionist: [[0.980, 0.850, 0.200], [0.870, 0.200, 0.160], [0.680, 0.130, 0.280],
                  [0.200, 0.240, 0.620], [0.220, 0.500, 0.720], [0.100, 0.450, 0.360],
                  [0.980, 0.970, 0.930]],
  'old-master': [[0.780, 0.600, 0.250], [0.600, 0.300, 0.150], [0.320, 0.200, 0.140],
                 [0.360, 0.400, 0.280], [0.100, 0.090, 0.090], [0.960, 0.930, 0.860]],
  nocturne: [[0.060, 0.200, 0.300], [0.200, 0.240, 0.620], [0.320, 0.200, 0.140],
             [0.780, 0.600, 0.250], [0.090, 0.090, 0.100], [0.950, 0.940, 0.900]],
  fauve: [[1.000, 0.830, 0.100], [0.980, 0.500, 0.060], [0.900, 0.130, 0.130],
          [0.800, 0.100, 0.450], [0.150, 0.200, 0.700], [0.050, 0.600, 0.400],
          [1.000, 1.000, 0.980]],
};

// Subtractive mixing: a weighted GEOMETRIC mean of reflectances, so ultramarine and cadmium
// yellow give green rather than the grey a linear average gives. See the Python.
const REFL_FLOOR = 0.004;
const MIX_STEPS = [0.2, 0.4, 0.6, 0.8];
const TINT_STEPS = [0.25, 0.5, 0.75];

/** linear rgb -> OKLab, as a flat [L,a,b,L,a,b,...]. The LUT never touches sRGB. */
function linToOklab(rows) {
  const out = new Float64Array(rows.length * 3);
  for (let i = 0; i < rows.length; i++) {
    const [r, g, b] = rows[i];
    const cl = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const cm = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const cs = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    out[3 * i] = 0.2104542553 * cl + 0.7936177850 * cm - 0.0040720468 * cs;
    out[3 * i + 1] = 1.9779984951 * cl - 2.4285922050 * cm + 0.4505937099 * cs;
    out[3 * i + 2] = 0.0259040371 * cl + 0.7827717662 * cm - 0.8086757660 * cs;
  }
  return out;
}

/** The mixtures a palette can reach, as a flat OKLab triple list. null when it has none. */
export function mixLut(name) {
  const pig = PIGMENTS[name];
  if (!pig || !pig.length) return null;
  const lin = pig.map(c => c.map(v => Math.max(srgbToLinear(v), REFL_FLOOR)));
  const white = lin[lin.length - 1];
  const mix = (a, b, t) => [0, 1, 2].map(
    k => Math.pow(a[k], 1.0 - t) * Math.pow(b[k], t));

  const rows = lin.slice();
  const binaries = [];
  for (let i = 0; i < lin.length; i++) {
    for (let j = i + 1; j < lin.length; j++) {
      for (const t of MIX_STEPS) binaries.push(mix(lin[i], lin[j], t));
    }
  }
  for (const c of binaries) rows.push(c);
  for (const c of binaries) {
    for (const t of TINT_STEPS) rows.push(mix(c, white, t));
  }
  return linToOklab(rows);
}

export const PALETTES = ['none', 'zorn', 'impressionist', 'old-master', 'nocturne',
                         'fauve'];

/**
 * The effective grade for a config, or null when there is nothing to do.
 *
 * null rather than an identity block: an identity grade still round-trips every colour
 * through a cube root, which is not the identity in float32. This is what keeps an
 * ungraded painting bit-identical to one made before the feature existed.
 */
export function paramsFor(cfg) {
  if (!Object.prototype.hasOwnProperty.call(PRESETS, cfg.palette)) {
    throw new Error(`unknown palette ${JSON.stringify(cfg.palette)}`);
  }
  // A band with neither a rotation nor a gain does nothing, so hue_target and hue_range
  // are deliberately not part of the activity test.
  //
  // EVERY VALUE MUST BE A FINITE NUMBER, and that guard is not defensive programming --
  // it is a bug this shipped. `undefined !== 0.0` is TRUE, so a config that simply does
  // not carry these fields (an engine serving a schema.json older than the page: a server
  // started before they existed, or a cached worker) was read as "a band is live", and
  // every colour downstream came out NaN. A missing knob has to mean the feature is OFF,
  // never a painting full of NaN, so a band that is not four real numbers is no band.
  const hb = [cfg.hue_target, cfg.hue_range, cfg.hue_rotate, cfg.hue_boost];
  const band = (hb.every(Number.isFinite) && (hb[2] !== 0.0 || hb[3] !== 0.0)) ? hb : null;
  const trims = cfg.warm_cool || cfg.chroma || cfg.value_compress || cfg.broken_color
    || band !== null;
  if (cfg.palette_strength <= 0.0 || (cfg.palette === 'none' && !trims)) return null;
  return blockFor(cfg, band);
}

/**
 * The grade block a config describes, with no activity test in front of it. Split out for
 * ONE caller: a reference transport rides this same OKLab pass rather than paying for a
 * second one. See the Python's `block_for`.
 */
export function blockFor(cfg, band = undefined) {
  if (band === undefined) {
    const hb = [cfg.hue_target, cfg.hue_range, cfg.hue_rotate, cfg.hue_boost];
    band = (hb.every(Number.isFinite) && (hb[2] !== 0.0 || hb[3] !== 0.0)) ? hb : null;
  }
  const p = Object.assign({}, PRESETS[cfg.palette]);
  // A NEW array, never a push: the preset's own block is shared, and a band pushed into
  // it would leak into every later render of that palette.
  if (band !== null) p.bands = (p.bands || []).concat([band]);
  p.wc += cfg.warm_cool;
  p.chroma += cfg.chroma;
  p.lo += 0.15 * cfg.value_compress;
  p.hi -= 0.10 * cfg.value_compress;
  p.broken += cfg.broken_color;
  p.pigment += cfg.pigment;
  // The name rides along because the pigment set is looked up by it.
  p.name = cfg.palette;
  p.strength = cfg.palette_strength;
  return p;
}

/**
 * The grade itself: interleaved rgb in, a NEW Float32Array out.
 *
 * `phase` is the per-stroke wobble seed the StrokeBuffer already carries, uniform on
 * [0, 2pi), and it is what drives the broken colour -- reusing it rather than drawing is
 * why this feature costs nothing in the RNG stream. Pass null for an image.
 *
 * A new array rather than in place, and that is load-bearing on this side: the base canvas
 * comes out of `plan`'s memo, and grading it in place would poison the cache for every
 * later render.
 */
export function grade(rgb, gp, phase = null) {
  const n = rgb.length / 3;
  const out = new Float32Array(rgb.length);
  const warm = gp.warm_deg * Math.PI / 180.0;
  const cool = gp.cool_deg * Math.PI / 180.0;
  const wx = Math.cos(warm), wy = Math.sin(warm);
  const cx = Math.cos(cool), cy = Math.sin(cool);
  const broken = gp.broken !== 0.0 && phase !== null;
  const t = gp.strength;
  // Not built at all when nothing can be projected -- palette='none' has no pigments, and
  // an image has no strokes. A full scan per colour, not a tree: a few hundred rows is
  // below where any acceleration structure pays for itself, and an approximate nearest
  // neighbour would be a second thing to hold in parity for no gain.
  const lut = (gp.pigment > 0.0 && phase !== null) ? mixLut(gp.name) : null;
  // Per-band constants, hoisted: the pixel loop below does the same divide the Python's
  // vectorised form does, so `sig2` is precomputed rather than turned into a reciprocal.
  // The fitted reference transport, hoisted: one 3x3, one offset and one blend weight,
  // read on every colour and changing on none of them.
  const xfer = gp.xfer || null;
  const xm = xfer === null ? null : xfer.a;
  const xb = xfer === null ? null : xfer.b;
  const xt = xfer === null ? 1.0 : xfer.strength;
  const bands = (gp.bands || []).map(q => {
    const c = q[0] * DEG;
    const sig = Math.max(q[1], HUE_MIN_WIDTH_DEG) * DEG;
    return {cx: Math.cos(c), cy: Math.sin(c), sig2: sig * sig, rot: q[2] * DEG, boost: q[3]};
  });

  for (let i = 0; i < n; i++) {
    const p = 3 * i;
    // --- sRGB -> OKLab ---
    const lr = srgbToLinear(rgb[p]);
    const lg = srgbToLinear(rgb[p + 1]);
    const lb = srgbToLinear(rgb[p + 2]);
    const cl = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
    const cm = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
    const cs = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
    // `let`, not `const`: the reference transport below rewrites them in place, and every
    // stage after it is defined in terms of the colour as it arrives.
    let L0 = 0.2104542553 * cl + 0.7936177850 * cm - 0.0040720468 * cs;
    let a0 = 1.9779984951 * cl - 2.4285922050 * cm + 0.4505937099 * cs;
    let b0 = 0.0259040371 * cl + 0.7827717662 * cm - 0.8086757660 * cs;

    // 0. the reference transport, before everything: everything after it is a hand
    //    adjustment ON TOP of the match. Fitted once per plan by reference.js against the
    //    whole stroke buffer, so the per-colour cost here is nine multiplies. Applied in
    //    this file rather than in that one because the Python's dependency runs this way
    //    (reference.py needs palette's OKLab, so palette cannot import it back).
    if (xfer !== null) {
      const nl = xm[0][0] * L0 + xm[0][1] * a0 + xm[0][2] * b0 + xb[0];
      const na = xm[1][0] * L0 + xm[1][1] * a0 + xm[1][2] * b0 + xb[1];
      const nb = xm[2][0] * L0 + xm[2][1] * a0 + xm[2][2] * b0 + xb[2];
      if (xt === 1.0) { L0 = nl; a0 = na; b0 = nb; }
      else {
        L0 = L0 + (nl - L0) * xt;
        a0 = a0 + (na - a0) * xt;
        b0 = b0 + (nb - b0) * xt;
      }
    }

    // 1. hue-band steering, so a band reads the hue as it ARRIVES at the grade -- the
    //    photograph's own, or the reference's if one matched it.
    let a1 = a0, b1 = b0;
    if (bands.length) {
      const r = Math.sqrt(a0 * a0 + b0 * b0);
      const inv = 1.0 / Math.max(r, HUE_EPS);
      let rot = 0.0, gain = 1.0;
      for (let k = 0; k < bands.length; k++) {
        const bd = bands[k];
        let cosd = (a0 * bd.cx + b0 * bd.cy) * inv;
        cosd = cosd < -1.0 ? -1.0 : cosd > 1.0 ? 1.0 : cosd;
        const wt = Math.exp((cosd - 1.0) / bd.sig2);
        rot += wt * bd.rot;
        gain += wt * bd.boost;
      }
      if (gain < 0.0) gain = 0.0;
      const cr = Math.cos(rot), sr = Math.sin(rot);
      a1 = gain * (a0 * cr - b0 * sr);
      b1 = gain * (a0 * sr + b0 * cr);
    }

    // 2. chroma, shaped by value.
    const g = gp.chroma + gp.chroma_mid * (4.0 * L0 * (1.0 - L0));
    let a = a1 * g, b = b1 * g;

    // 3. what the two ends are MADE of.
    a = a + gp.tint_lo_a * (1.0 - L0) + gp.tint_hi_a * L0;
    b = b + gp.tint_lo_b * (1.0 - L0) + gp.tint_hi_b * L0;

    // 4. the warm-cool split.
    const s = gp.wc * (L0 - gp.pivot) * WC_SCALE;
    a = a + Math.abs(s) * (s >= 0.0 ? wx : cx);
    b = b + Math.abs(s) * (s >= 0.0 ? wy : cy);

    // 5. broken colour, along the same two directions.
    if (broken) {
      const d = gp.broken * BROKEN_SCALE * Math.sin(phase[i]);
      a = a + Math.abs(d) * (d >= 0.0 ? wx : cx);
      b = b + Math.abs(d) * (d >= 0.0 ? wy : cy);
    }

    // 6. the value range paint actually has.
    let L = gp.lo + (gp.hi - gp.lo) * L0;

    // 7. ...and onto a colour the palette can actually MIX. Ties go to the FIRST minimum
    //    (strict `<`, matching numpy's argmin), which matters because a palette contains
    //    duplicate mixtures -- a pigment mixed with itself is every ratio of one colour.
    if (lut !== null) {
      let best = Infinity, bi = 0;
      for (let k = 0; k < lut.length; k += 3) {
        const dL = L - lut[k], da = a - lut[k + 1], db = b - lut[k + 2];
        const d = dL * dL + da * da + db * db;
        if (d < best) { best = d; bi = k; }
      }
      L = L + (lut[bi] - L) * gp.pigment;
      a = a + (lut[bi + 1] - a) * gp.pigment;
      b = b + (lut[bi + 2] - b) * gp.pigment;
    }

    if (t !== 1.0) {
      L = L0 + (L - L0) * t;
      a = a0 + (a - a0) * t;
      b = b0 + (b - b0) * t;
    }

    // --- OKLab -> sRGB. The cube is three multiplies, matching the Python. ---
    const il = L + 0.3963377774 * a + 0.2158037573 * b;
    const im = L - 0.1055613458 * a - 0.0638541728 * b;
    const is = L - 0.0894841775 * a - 1.2914855480 * b;
    const l3 = il * il * il, m3 = im * im * im, s3 = is * is * is;
    // linearToSrgb clips first, which is the out-of-gamut guard: a chroma boost routinely
    // asks for colours no monitor has.
    out[p] = linearToSrgb(4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3);
    out[p + 1] = linearToSrgb(-1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3);
    out[p + 2] = linearToSrgb(-0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3);
  }
  return out;
}

/** The (3n,) pigment array. `linear` says the colours arrived in linear light. */
export function gradeStrokes(colors, phase, gp, linear = false) {
  // The Python's `linear_to_srgb(colors)` on a float32 array rounds to float32 before the
  // grade sees it, so the fround here is not cosmetic.
  let c = colors;
  if (linear) {
    c = new Float32Array(colors.length);
    for (let i = 0; i < colors.length; i++) c[i] = Math.fround(linearToSrgb(colors[i]));
  }
  const out = grade(c, gp, phase);
  if (linear) for (let i = 0; i < out.length; i++) out[i] = Math.fround(srgbToLinear(out[i]));
  return out;
}

/** The same, for the h*w*3 base canvas. No strokes, so no broken colour. */
export function gradeImage(img, gp, linear = false) {
  return gradeStrokes(img, null, gp, linear);
}
