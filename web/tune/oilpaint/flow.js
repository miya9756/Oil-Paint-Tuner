// Port of oilpaint/flow.py -- the artistic flow field over the stroke orientation.
//
// The Python carries the design: why this attaches inside step 3 of `fromCells` (the one
// step that draws no random numbers), why it is evaluated analytically per stroke rather
// than as an image-sized field, and what each preset stands for. Read that first.
//
// This file is the easiest of the ported modules to keep in parity, and that is by design
// rather than by luck: `flowBlend` promotes both its inputs to float64 on entry, so unlike
// `flatBlend` next door there is no float32 trig anywhere in here and not one `Math.fround`
// until the coherence is stored on the way out. A JS number IS a float64, so the port is
// the same expressions in the same order.
//
// The one place that still needs care is accumulation ORDER in the swirl sum: the Python
// adds one vortex at a time across the whole array, k = 0..n-1, and so does this.

// pi * (3 - sqrt(5)). Successive multiples never fall into a repeating pattern, which is
// why the vortex centres use it -- a lattice of swirls reads as wallpaper, and placing them
// at random would need an rng this module refuses to own.
export const GOLDEN_ANGLE = Math.PI * (3.0 - Math.sqrt(5.0));

// Successive vortices step down in size by this, cycling every three: van Gogh's sky is
// several nested scales of swirl, not one. Written as a table rather than as a `**` so both
// languages perform the same two multiplies -- pow is correctly rounded and multiply is not.
export const SIG_STEP = 0.7;
const SIG_POW = [1.0, SIG_STEP, SIG_STEP * SIG_STEP];

// The two turbulence waves: non-parallel, at no simple frequency ratio, so their sum never
// closes into a regular lattice on the canvas.
const TURB_DIR_A = 0.9;
const TURB_DIR_B = 2.3;
const TURB_RATIO_B = 1.7;
const TURB_AMP_B = 0.5;

const TWO_PI = 2.0 * Math.PI;
const HALF_PI = 0.5 * Math.PI;
const EPS = 1e-9;

// Ceiling on hand-placed vortices. Not for speed -- each one is a cheap pass over the
// strokes -- but because a field with dozens of overlapping vortices has no legible swirls
// left in it. The Python carries the reasoning.
export const MAX_VORTICES = 32;

const NONE = {
  kind: 'none', strength: 0.0, coh: 0.0, rot: 0.0, scale: 0.35, n: 6, spread: 0.45,
  bg: 0.15, amp: 0.0, turb: 0.0, turb_scale: 0.5, drift: 0.0,
};

// Each preset is a complete parameter block; the five exposed sliders trim it. The Python
// carries what each one stands for and why its numbers are what they are.
export const PRESETS = {
  none: NONE,
  starry: Object.assign({}, NONE, {
    kind: 'swirl', strength: 0.85, coh: 0.95, rot: 0.0, scale: 0.30,
    n: 7, spread: 0.55, bg: 0.18, turb: 0.12, turb_scale: 0.45, drift: 0.55,
  }),
  waterlily: Object.assign({}, NONE, {
    kind: 'wave', strength: 0.70, coh: 0.18, rot: 0.0, scale: 0.90,
    amp: 0.45, turb: 0.18, turb_scale: 0.30, drift: 0.15,
  }),
  hatch: Object.assign({}, NONE, {
    kind: 'wave', strength: 0.70, coh: 0.55, rot: 35.0, scale: 1.50,
    amp: 0.06, turb: 0.04, turb_scale: 0.60, drift: 0.0,
  }),
};

export const FLOWS = Object.keys(PRESETS);

// Which field kind each preset is, for the page. Only a 'swirl' has vortices to place, so
// this is what tells the click tool whether it has anything to do -- sent on the worker's
// ready message rather than restated in the page, exactly as the pigment sets are. A page
// that hard-coded "starry is the swirly one" would be wrong the day a second swirl preset
// is added, and wrong silently.
export const FLOW_KINDS = Object.fromEntries(
  Object.entries(PRESETS).map(([k, v]) => [k, v.kind]));

/**
 * The effective flow block for a config, or null when there is nothing to do.
 *
 * null rather than a zero-strength block: at strength 0 the blend is an identity in exact
 * arithmetic and is NOT one in floating point, because it still rebuilds theta through
 * atan2. Returning null is what keeps an unstyled painting bit-identical.
 */
export function paramsFor(cfg, vortices = null) {
  if (!(cfg.flow in PRESETS)) throw new Error(`unknown flow ${cfg.flow}`);
  if (cfg.flow === 'none' || cfg.flow_strength <= 0.0) return null;
  const p = Object.assign({}, PRESETS[cfg.flow]);
  // The sliders TRIM the preset rather than replacing it. `flow_coh` and `flow_drift` go
  // negative so a preset's own choice can be cancelled.
  p.strength = Math.min(1.0, Math.max(0.0, p.strength * cfg.flow_strength));
  // Feature SIZE, so the vortices and the turbulence scale together; `spread` does not,
  // because that is composition and scaling it by 3 would push the swirls off the canvas.
  const s = Math.max(0.05, 1.0 + cfg.flow_scale);
  p.scale = p.scale * s;
  p.turb_scale = p.turb_scale * s;
  p.rot = p.rot + cfg.flow_rot;
  p.coh = Math.min(1.0, Math.max(0.0, p.coh + cfg.flow_coh));
  p.drift = p.drift + cfg.flow_drift;
  // Hand-placed centres, as (fx, fy) FRACTIONS of the image -- what a click gives you, and
  // resolution-independent like the rest of the field. Converted only where h and w are
  // known; see fieldCentres. Empty means "nothing placed", which is the spiral, NOT "no
  // vortices" -- clearing the markers returns the preset as shipped.
  p.vortices = null;
  if (vortices && vortices.length) {
    p.vortices = vortices.slice(0, MAX_VORTICES).map(q => [q[0], q[1]]);
  }
  p.name = cfg.flow;
  return p;
}

/**
 * `vortices` in either accepted shape -> a Map of `region id -> array of [fx, fy]`.
 *
 * A plain array is the BASE's set, `{0: [...]}`; an object is already keyed by region. The
 * shapes exist because the swirl centres came before the regions did and every caller that
 * predates them means "the picture's swirls". null is an empty map: every field on its own
 * preset's spiral. See the Python for why normalising here keeps `paramsFor` taking a plain
 * list.
 */
export function vortexMap(vortices) {
  const out = new Map();
  if (vortices === null || vortices === undefined) return out;
  if (Array.isArray(vortices)) {
    out.set(0, vortices.map(q => [q[0], q[1]]));
    return out;
  }
  for (const k of Object.keys(vortices)) {
    out.set(Number(k), (vortices[k] || []).map(q => [q[0], q[1]]));
  }
  return out;
}

/**
 * The placed centres region `rid` uses: its OWN set, or the base's when it has none.
 *
 * Inheritance is FORCED, not a preference: the equivalence invariant says that giving every
 * region the base config's own values reproduces the undivided painting bit for bit, and a
 * region falling back to the preset's spiral while the base used placed centres would be a
 * different painting. Absence inherits; an explicitly empty set is the spiral. See the
 * Python, which carries the whole argument.
 */
export function centresFor(vmap, rid = 0) {
  if (vmap.has(rid)) return vmap.get(rid);
  return vmap.has(0) ? vmap.get(0) : null;
}

/**
 * The hand-placed vortex centres in the field's own frame, or null for the spiral.
 *
 * Through `normCoords`, the SAME transform the stroke positions go through, so a vortex
 * placed at a pixel sits exactly where a stroke at that pixel sits. Null for any field kind
 * that has no vortices, so placed points handed to a 'wave' preset leave it untouched.
 */
export function fieldCentres(p, h, w) {
  if (!p.vortices || !p.vortices.length || p.kind !== 'swirl') return null;
  const n = p.vortices.length;
  const fy = new Float64Array(n), fx = new Float64Array(n);
  for (let i = 0; i < n; i++) { fx[i] = p.vortices[i][0] * w; fy[i] = p.vortices[i][1] * h; }
  return normCoords(fy, fx, h, w);
}

/**
 * Pixel coordinates -> the normalised frame the field lives in: centred on the canvas and
 * divided by the SHORT side, so the field is the same shape at any resolution.
 */
export function normCoords(cy, cx, h, w) {
  const s = Math.min(h, w);
  const n = cy.length;
  const u = new Float64Array(n), v = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    u[i] = (cx[i] - 0.5 * w) / s;
    v[i] = (cy[i] - 0.5 * h) / s;
  }
  return [u, v];
}

/**
 * The style's own direction at each point, in radians. float64 throughout.
 *
 * The swirl branch sums TRUE 2*pi vectors, not doubled-angle ones: a flow field is
 * directed, and two counter-rotating vortices that meet must cancel into a straight run
 * between them. Only the sum is folded down to an orientation, at the atan2 -- which is
 * also why the background term matters: without it the sum goes to zero far from every
 * vortex and the angle there is the arctangent of nothing.
 */
export function spiralCentres(p) {
  const rot = p.rot * (Math.PI / 180.0);
  const n = Math.trunc(p.n);
  const px = new Float64Array(n), py = new Float64Array(n);
  for (let k = 0; k < n; k++) {
    // Golden-angle spiral: the angle marches by GOLDEN_ANGLE and the radius by sqrt(k/n),
    // the placement that spreads n points evenly over a disc.
    const ang = k * GOLDEN_ANGLE + rot;
    const rad = p.spread * Math.sqrt((k + 0.5) / n);
    px[k] = rad * Math.cos(ang);
    py[k] = rad * Math.sin(ang);
  }
  return [px, py];
}

export function flowDir(u, v, p, centres = null) {
  const n = u.length;
  const rot = p.rot * (Math.PI / 180.0);   // np.deg2rad, on a Python float -> f64
  const a = new Float64Array(n);

  if (p.kind === 'swirl') {
    // Placed or procedural, ONE path from here: the sizes still cycle through SIG_POW and
    // the handedness still alternates with the index, so with placed points the CLICK ORDER
    // decides which way each swirl turns. `rot` swings the background drift and does not
    // turn placed centres -- they are where they were put.
    const [px, py] = centres === null ? spiralCentres(p) : centres;
    const nv = px.length;
    const bgx = p.bg * Math.cos(rot), bgy = p.bg * Math.sin(rot);
    // Per-vortex sizes hoisted, but the ACCUMULATION runs k-innermost so the additions land
    // in the same order as numpy's array-at-a-time loop.
    const sg = new Float64Array(nv);
    for (let k = 0; k < nv; k++) sg[k] = p.scale * SIG_POW[k % 3];
    for (let i = 0; i < n; i++) {
      let vx = bgx, vy = bgy;
      for (let k = 0; k < nv; k++) {
        const dx = u[i] - px[k], dy = v[i] - py[k];
        const sig = sg[k];
        const d2 = dx * dx + dy * dy;
        // A Gaussian vortex: tangential, falling off smoothly so neighbouring swirls blend
        // instead of meeting at a seam. Alternating sign, so they counter-rotate.
        let wgt = Math.exp(-0.5 * d2 / (sig * sig));
        if (k % 2) wgt = -wgt;
        const r = Math.sqrt(d2) + EPS;
        vx = vx + wgt * (-dy / r);
        vy = vy + wgt * (dx / r);
      }
      a[i] = Math.atan2(vy, vx);
    }
  } else if (p.kind === 'wave') {
    // The direction undulates as you move ACROSS it -- hence rot + pi/2 -- so the marks
    // form bands that slide past each other, which is what a water surface does. At amp
    // ~ 0 this is a constant direction and the branch is hatching.
    const c = Math.cos(rot + HALF_PI), s = Math.sin(rot + HALF_PI);
    const lam = Math.max(EPS, p.scale);
    for (let i = 0; i < n; i++) {
      a[i] = rot + p.amp * Math.sin(TWO_PI * (u[i] * c + v[i] * s) / lam);
    }
  } else {
    throw new Error(`unknown flow kind ${p.kind}`);
  }

  if (p.turb !== 0.0) {
    // Two crossed sine waves, one an octave up. Cheap multi-scale wobble on the angle.
    const k = TWO_PI / Math.max(EPS, p.turb_scale);
    const ca = Math.cos(TURB_DIR_A), sa = Math.sin(TURB_DIR_A);
    const cb = Math.cos(TURB_DIR_B), sb = Math.sin(TURB_DIR_B);
    for (let i = 0; i < n; i++) {
      const t1 = Math.sin(k * (u[i] * ca + v[i] * sa));
      const t2 = Math.sin(TURB_RATIO_B * k * (u[i] * cb + v[i] * sb) + 1.3);
      a[i] = a[i] + p.turb * (t1 + TURB_AMP_B * t2);
    }
  }
  return a;
}

/**
 * Fold the style's direction into the picture's own, in doubled-angle space.
 *
 * The same vector sum as `flatBlend`, and for the same reason: an orientation is
 * pi-periodic, so the only way to average two is through (cos 2t, sin 2t), and the length
 * of the sum falls where the two disagree.
 *
 * The coherence, unlike `flatBlend`'s, is NOT simply that length -- it is the weighted
 * average of what each side asks for, cut by how much they agree. The Python carries the
 * measurement that forced it: coherence drives elongation, so folding it into the style's
 * weight made a style that wants SHORT marks automatically a weak style, and Monet is
 * short marks asserted firmly. Two separate numbers, `strength` and `coh`.
 *
 * DTYPE, deliberately unlike `flatBlend`: both inputs are read as float64 here, because the
 * Python promotes them with an explicit `.astype(np.float64)` before any trig. That removes
 * every float32 trig call -- and every `Math.fround` around one -- from this routine. The
 * VALUES arriving are still the float32-rounded ones on both sides.
 *
 * `coh` comes back Float32Array because that dtype is load-bearing downstream: it is what
 * keeps `ratio` float32 in step 4 until the jitter array promotes it.
 */
export function flowBlend(base, cohIn, u, v, p, centres = null) {
  const n = base.length;
  const t = flowDir(u, v, p, centres);
  const theta = new Float64Array(n);
  const coh = new Float32Array(n);
  const wStyle = p.strength;
  const oneMinus = 1.0 - p.strength;
  for (let i = 0; i < n; i++) {
    const c = cohIn[i];
    const wContent = c * oneMinus;
    const vx = wContent * Math.cos(2.0 * base[i]) + wStyle * Math.cos(2.0 * t[i]);
    const vy = wContent * Math.sin(2.0 * base[i]) + wStyle * Math.sin(2.0 * t[i]);
    theta[i] = 0.5 * Math.atan2(vy, vx);
    // `tot` cannot be zero: paramsFor returned null at strength 0, so wStyle > 0 here.
    const tot = wContent + wStyle;
    const mag = Math.sqrt(vx * vx + vy * vy);
    coh[i] = Math.fround(
      Math.min(mag * (wContent * c + wStyle * p.coh) / (tot * tot), 1.0));
  }
  return [theta, coh];
}
