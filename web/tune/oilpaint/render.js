// 2D anisotropic Gaussian rasterizer -- the browser-side port of `oilpaint/render.py`.
//
// Python is the source of truth (.claude/skills/python-js-parity/SKILL.md rule 1). This
// file mirrors `render()` statement for statement; `tests/test_raster_parity.py` runs THIS
// module under node against the Python and compares every stage, so a divergence is a test
// failure rather than a painting that is merely "a bit different".
//
// Why plain JS on the CPU rather than a fragment shader: the Python front half runs under
// Pyodide at ~1 s, so a 5 ms shader and a 250 ms JS loop are the same page. What the shader
// costs instead is testability -- WebGL cannot be driven from node, so the parity test
// above would need a headless GL context in CI, or a second CPU transcription of the shader
// that is exactly the thing that drifts. Should this ever become the bottleneck, a WebGL
// backend can sit behind `render()` unchanged, with this module as its parity oracle.
//
// Float note: Python computes this in float32 (float32 arrays, and numpy's value-based
// casting keeps a Python float scalar from upcasting them), while JS computes in float64
// and rounds only on the stores into Float32Array. The port is therefore slightly MORE
// accurate than its reference, not different from it -- the measured size of that gap is
// FLOAT32_SLACK in the parity test. Per-stroke scalars are `Math.fround`ed to match the
// Python exactly, because there it costs nothing.

// The only import here, and only for the cavity map in `light`: the same separable blur the
// base layer uses, so the occlusion pass has no kernel of its own to drift.
import { gaussianBlur } from './image.js';

export const CUTOFF_D2 = 4.0; // matches the shader's `if (d2 > 4.0) discard`

// Step 2 constants -- see the Python for why each is the value it is. Restated rather than
// fetched because this module has no I/O, and pinned by test_raster_parity.py's probes.
export const BRISTLE_F1 = 1.40;
export const BRISTLE_F2 = 2.20;
export const BRISTLE_REF = 6.0;
export const BRISTLE_DRIFT1 = 0.05;
export const BRISTLE_DRIFT2 = 0.09;
export const BRISTLE_ENV_F = 0.085;
export const BRISTLE_ENV = 0.35;
export const BRISTLE_ELONG = 2.0;
export const FRINGE_FEATURE_PX = 4.0;
export const FRINGE_K = Math.PI / (2.0 * FRINGE_FEATURE_PX);
export const FRINGE_MAX_AMP = 0.25;
export const FRINGE_MAX_M = 48;
export const WEAVE_PERIOD = 4.0;
export const AMBIENT = 0.5;
export const AO_FINE = 1.5;
export const AO_SIGMA = 3.5;
export const AO_MAX = 0.5;
// Where the ground sits within the stroke height range. See the Python: at 0 (no ground)
// every stroke is a plateau on an abyss and both the bevel and the cavity map trace its
// silhouette, which is a drop shadow and reads as stickers. At 0.5 the ground is the mean
// paint level, so strokes sit in it rather than on it.
export const GROUND_AT = 0.5;

/** Height of the base layer, given the stroke range 1.0 .. 1.0 + impastoLayer. */
export function groundHeight(impastoLayer) {
  return 1.0 + GROUND_AT * impastoLayer;
}

export function smoothstep(e0, e1, x) {
  let t = (x - e0) / Math.max(e1 - e0, 1e-12);
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return t * t * (3.0 - 2.0 * t);
}

// Analytic stand-in for GLSL `fwidth(d)` = |dFdx(d)| + |dFdy(d)|; see the Python docstring
// for the chain rule. Kept identical here so a later WebGL backend has one expression to
// match rather than two.
export function fwidthD(u, v, d, cosT, sinT, sMajor, sMinor) {
  const dsafe = Math.max(d, 1e-6);
  const ddx = (u * (cosT / sMajor) - v * (sinT / sMinor)) / dsafe;
  const ddy = (u * (sinT / sMajor) + v * (cosT / sMinor)) / dsafe;
  return Math.abs(ddx) + Math.abs(ddy);
}

// Per-stroke angular modulation of the hard edge -- a ragged outline, not an ellipse. Two
// low harmonics, mean 1, closed-form on purpose: no texture, no noise lookup. This is the
// LOW-frequency half of the outline, two lobes and three whatever the size of the stroke;
// `fringe` is the high-frequency half. Takes the polar angle rather than u/v so the atan2
// -- the most expensive operation in the inner loop -- is computed once and shared.
export function wobble(phi, phase, amp) {
  return 1.0 + amp * (0.6 * Math.sin(3.0 * phi + phase) + 0.4 * Math.sin(5.0 * phi - 1.7 * phase));
}

// `[order, amplitude]` for `fringe` on a stroke of this size, both pinned to pixels via the
// area-equivalent radius. Computed in f64 from the f32 radii, exactly as the Python does:
// the ORDER is an integer, and an off-by-one in it is a different outline rather than a
// rounding difference. `floor(x + 0.5)`, not `Math.round`, because Python rounds halves to
// even -- the two disagree on exactly the values `Math.round` is famous for.
export function fringeParams(rMajor, rMinor, fringePx) {
  const rEff = Math.sqrt(rMajor * rMinor);
  const m = Math.min(Math.max(Math.floor(FRINGE_K * rEff + 0.5), 1), FRINGE_MAX_M);
  return [m, Math.min(fringePx / Math.max(rEff, 1e-6), FRINGE_MAX_AMP)];
}

// Fractal roughness on the outline: three octaves of fBm in the polar angle, amplitude
// falling 0.5/0.3/0.2 as the order doubles. `m` comes from `fringeParams`, so all three
// orders are integers -- a non-integer harmonic would leave a seam where phi wraps at +-pi,
// which is a radial crack down every stroke.
export function fringe(phi, phase, amp, m) {
  return 1.0 + amp * (
      0.50 * Math.sin(m * phi + phase)
    + 0.30 * Math.sin(2 * m * phi + 2.3 * phase)
    + 0.20 * Math.sin(4 * m * phi - 1.7 * phase));
}

// Bristle grooves in the stroke's own frame, mean 0 in [-1, 1]. `up, vp` are in PIXELS
// (already pitch-scaled by the caller), unlike everything else here, which is in sigma.
// Returns the bare field: the opacity map and the height map apply their own amplitudes.
export function bristle(up, vp, phase) {
  const env = (1.0 - BRISTLE_ENV) + BRISTLE_ENV * Math.sin(BRISTLE_ENV_F * up + 2.1 * phase);
  return env * (0.60 * Math.sin(BRISTLE_F1 * vp + BRISTLE_DRIFT1 * up + phase)
              + 0.40 * Math.sin(BRISTLE_F2 * vp + BRISTLE_DRIFT2 * up - 1.3 * phase));
}

// Silhouette taper along the stroke -- blunt head, thin tail. Multiplier on the edge
// threshold, in [1 - amount, 1]. Takes cos(phase), not phase, so the cosine hoists out of
// the pixel loop below with the same expression the Python uses.
export function taper(u, cosPhase, amount) {
  return 1.0 - amount * 0.5 * (1.0 + 0.5 * u * cosPhase);
}

// Canvas tooth as two orthogonal corrugations, mean 0 in [-1, 1]. Evaluated per pixel here
// rather than built as an array, because `light` is the only caller and it is already
// walking the grid.
export function weave(y, x, period = WEAVE_PERIOD) {
  // `k` is rounded to f32 BEFORE the multiply, and that is not a detail: numpy's
  // value-based casting demotes the scalar in `k * np.arange(h, dtype=np.float32)`, so the
  // Python multiplies an f32 k by an f32 index. Rounding only the product instead leaves
  // the f64/f32 gap in k (~4.4e-8) scaled by the index, which reached 6e-5 at y=1024 and
  // failed the parity test outright -- six times the tolerance, from one misplaced round.
  const k = Math.fround(2.0 * Math.PI / period);
  return 0.5 * (Math.fround(Math.sin(Math.fround(k * y)))
              + Math.fround(Math.sin(Math.fround(k * x))));
}

// Screen compass -> unit vector. 0 degrees is from the RIGHT, 90 from the TOP; y is
// negated because image y runs downward. One function because the light and the eye both
// need it, and the sign being right in one place and wrong in the other would be a bug
// that only appears once the viewpoint leaves centre.
function direction(azDeg, elDeg) {
  const az = azDeg * Math.PI / 180.0, el = elDeg * Math.PI / 180.0;
  const ce = Math.cos(el);
  return [ce * Math.cos(az), -ce * Math.sin(az), Math.sin(el)];
}

/**
 * Bump-map the painting from its height field. [Hertzmann02] step 3, plus ambient
 * occlusion and a movable viewpoint. See the Python for why the diffuse is normalised
 * against a flat surface and the specular deliberately is not.
 *
 * @param {Float32Array} rgb  h*w*3, modified in place and returned
 * @param {Float32Array} height @param {Float32Array} cover  h*w
 * @returns {Float32Array} rgb
 */
export function light(rgb, height, cover, h, w, opts = {}) {
  const { depth = 0.35, lightDeg = 135.0, elevDeg = 35.0, gloss = 0.35,
          canvasWeave = 0.0, occlusion = 0.0, viewDeg = 90.0, viewElevDeg = 90.0 } = opts;

  // The weave is folded into a copy of the height field first, because the central
  // difference below has to see it -- adding it to the shading afterwards would give tooth
  // with no relief. Skipped entirely at 0, which is also the no-allocation path.
  let fld = height;
  if (canvasWeave > 0) {
    fld = new Float32Array(h * w);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const p = y * w + x;
        fld[p] = height[p] + canvasWeave * weave(y, x) * (1.0 - cover[p]);
      }
    }
  }

  // Cavity map: a pixel below its own neighbourhood is in a crease. Two cascaded blurs,
  // making it a BAND-PASS -- see the Python's AO_FINE for why the fine scale is removed
  // first rather than being fed straight in.
  let occl = null;
  if (occlusion > 0) {
    const near = gaussianBlur(fld, h, w, AO_FINE);
    const far = gaussianBlur(near, h, w, AO_SIGMA);
    occl = new Float32Array(h * w);
    for (let i = 0; i < occl.length; i++) {
      const o = occlusion * (far[i] - near[i]);
      occl[i] = o < 0 ? 0 : o > AO_MAX ? AO_MAX : o;
    }
  }

  const [lx, ly, lz] = direction(lightDeg, elevDeg);
  const [vx, vy, vz] = direction(viewDeg, viewElevDeg);
  const shin = 8.0 + 56.0 * gloss, ks = 0.5 * gloss;
  const invLz = 1.0 / Math.max(lz, 1e-6);
  // Clamped indices ARE the Python's `np.pad(..., mode='edge')`; the parity test compares
  // the border pixels, where a gradient's off-by-one normally hides.
  const at = (y, x) => fld[(y < 0 ? 0 : y > h - 1 ? h - 1 : y) * w
                          + (x < 0 ? 0 : x > w - 1 ? w - 1 : x)];

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const gx = 0.5 * (at(y, x + 1) - at(y, x - 1));
      const gy = 0.5 * (at(y + 1, x) - at(y - 1, x));
      let nx = -gx * depth, ny = -gy * depth;
      const invLen = 1.0 / Math.sqrt(nx * nx + ny * ny + 1.0);
      nx *= invLen; ny *= invLen;
      const nz = invLen;

      let ndl = nx * lx + ny * ly + nz * lz;
      if (ndl < 0) ndl = 0;
      // Phong: the mirror direction R = 2 (N.L) N - L, against the eye. This used to
      // collapse to R's z component, which was the same thing only because the eye was
      // nailed to +z.
      const rx = 2.0 * ndl * nx - lx;
      const ry = 2.0 * ndl * ny - ly;
      const rz = 2.0 * ndl * nz - lz;
      let rv = rx * vx + ry * vy + rz * vz;
      if (rv < 0) rv = 0;
      const spec = ks * Math.pow(rv, shin);
      const shade = AMBIENT * (1.0 - (occl === null ? 0.0 : occl[y * w + x]))
                  + (1.0 - AMBIENT) * (ndl * invLz);

      const p3 = 3 * (y * w + x);
      for (let c = 0; c < 3; c++) {
        const v = rgb[p3 + c] * shade + spec;
        rgb[p3 + c] = v < 0 ? 0 : v > 1 ? 1 : v;
      }
    }
  }
  return rgb;
}

/**
 * Composite strokes back-to-front with `over`. ARRAY ORDER IS PAINT ORDER -- the stroke
 * buffer arrives already sorted by the Python (StrokeBuffer's cross-language contract),
 * and this function must never reorder it.
 *
 * `bristleAmp` and `taperAmp` default to 0, and at 0 every expression below is the one
 * that was here before step 2 -- so a step-1 painting still comes back bit-identical.
 *
 * @param {{x,y,r_major,r_minor,theta,rgb,alpha,phase}} sb  struct-of-arrays, Float32Array
 * @param {number} h @param {number} w
 * `from` / `to` / `into` make the composite RESUMABLE, and are the other browser-only
 * addition. `over` is a left fold along the stroke array and this function must never
 * reorder it, so splitting that fold at any index and carrying the accumulators forward is
 * the same computation, not an approximation of it: every per-stroke quantity below is a
 * function of the absolute index `i` and the total `n`, both of which stay absolute when a
 * slice is drawn. test_raster_parity.py checks a sliced composite against a whole one and
 * requires them EXACTLY equal, because anything less would mean they are not.
 *
 * The worker draws in slices so it can reach its own message queue between them -- a worker
 * running straight-line code never does, which is why a render used to be uncancellable.
 *
 * `onProgress` is the one option with no counterpart in render.py: a browser-only hook the
 * page uses to show the painting building up instead of a bare spinner. It is called once
 * per stroke, BEFORE that stroke is composited, with the count already down and the live
 * buffers. It touches no arithmetic and every parity test leaves it null, so the numeric
 * path is the one the Python mirrors either way. Throttling is the caller's job, not this
 * loop's -- see engine.worker.js, which converts a frame only every few hundred ms.
 *
 * @param {{hard?:boolean, hardR?:number, wobbleAmp?:number,
 *          canvas?:Float32Array|null, splitAt?:number|null,
 *          bristleAmp?:number, taperAmp?:number, wantHeight?:boolean,
 *          impastoRelief?:number, impastoLayer?:number,
 *          onProgress?:((done:number,total:number,out:Float32Array)=>void)|null}} opts
 * @returns {{out:Float32Array, cover:Float32Array, coverTail:Float32Array|null,
 *            height:Float32Array|null}}
 */
export function render(sb, h, w, opts = {}) {
  const { hard = true, hardR = 1.5, wobbleAmp = 0.0, canvas = null, splitAt = null,
          bristleAmp = 0.0, taperAmp = 0.0, fringePx = 0.0, wantHeight = false,
          impastoRelief = 0.0, impastoLayer = 0.0, ground = 0.0,
          onProgress = null, into = null, from = 0, to = null } = opts;
  const n = sb.x.length;

  // Shape guards, not style. A marshalling slip that delivers an empty or short typed array
  // does not throw on its own -- it paints a blank canvas and reports a full stroke count,
  // which is the exact "plausible and wrong" failure this project's parity rules exist to
  // prevent. Failing here costs one comparison per render and names the problem.
  for (const [name, arr] of [['y', sb.y], ['r_major', sb.r_major], ['r_minor', sb.r_minor],
                             ['theta', sb.theta], ['alpha', sb.alpha], ['phase', sb.phase]]) {
    if (arr.length !== n) throw new Error(`stroke field ${name} has ${arr.length}, expected ${n}`);
  }
  if (sb.rgb.length !== 3 * n) {
    throw new Error(`stroke field rgb has ${sb.rgb.length}, expected ${3 * n}`);
  }
  if (canvas && canvas.length !== h * w * 3) {
    throw new Error(`canvas has ${canvas.length} floats, expected ${h * w * 3}`);
  }

  // `into` continues a composite already begun -- the buffers a previous call returned,
  // handed straight back. The allocations below are therefore the FIRST slice's only, and
  // `ground` is laid down once, where it belongs, rather than once per slice.
  const out = into ? into.out
    : canvas ? Float32Array.from(canvas) : new Float32Array(h * w * 3);
  const cover = into ? into.cover : new Float32Array(h * w);
  // Coverage of the strokes from `splitAt` onward, accumulated in the SAME pass, so the
  // detail layer's own holes cost nothing extra to measure (see the Python).
  const coverTail = into ? into.coverTail
    : splitAt === null ? null : new Float32Array(h * w);
  // `ground` is the height of the base layer under everything; see GROUND_AT. At 0 (the
  // default, and what base='none' passes) bare canvas really is an abyss.
  let height = into ? into.height : null;
  if (!into && wantHeight) {
    height = new Float32Array(h * w);
    if (ground !== 0) height.fill(ground);
  }
  // [Hertzmann02]: the per-stroke height offset is proportional to the strokes ALREADY
  // drawn, so the last stroke sits exactly `impastoLayer` above the first. `n` is the whole
  // buffer even when only a slice of it is being drawn, or the ramp would restart per slice.
  const invN = 1.0 / Math.max(1, n - 1);
  const wantBristle = bristleAmp > 0 || (height !== null && impastoRelief > 0);

  const cutoff = Math.sqrt(CUTOFF_D2);

  const last = to === null ? n : Math.min(to, n);
  for (let i = from; i < last; i++) {
    // Ahead of the `continue` below, not after it: a run of off-canvas strokes would
    // otherwise report nothing and the page's clock would be the only thing still moving.
    if (onProgress !== null) onProgress(i, n, out);
    // float32 to match `s_major = strokes.r_major / hard_r` and `np.cos(theta)`, both of
    // which stay float32 in numpy.
    const sx = Math.fround(sb.r_major[i] / hardR);
    const sy = Math.fround(sb.r_minor[i] / hardR);
    const ct = Math.fround(Math.cos(sb.theta[i]));
    const st = Math.fround(Math.sin(sb.theta[i]));
    const px = sb.x[i], py = sb.y[i];
    // Bristle pitch scaling, per stroke: sqrt(width) so a big brush gets coarser hair, and
    // an elongation weight so a stroke that was never dragged carries no drag marks. Both
    // are float32 on the Python side, hence the frounds.
    const bscale = Math.fround(Math.sqrt(BRISTLE_REF / Math.max(sy, 1e-3)));
    const belong = Math.fround(Math.min(Math.max(
      (sx / Math.max(sy, 1e-6) - 1.0) / (BRISTLE_ELONG - 1.0), 0.0), 1.0));
    const bu = Math.fround(sx * bscale), bv = Math.fround(sy * bscale);
    const [mFringe, fringeAmp] = fringeParams(sb.r_major[i], sb.r_minor[i], fringePx);

    // Bounding box of the +-2 sigma quad after rotation.
    const extX = cutoff * (sx * Math.abs(ct) + sy * Math.abs(st));
    const extY = cutoff * (sx * Math.abs(st) + sy * Math.abs(ct));
    const x0 = Math.max(0, Math.floor(px - extX));
    const x1 = Math.min(w, Math.ceil(px + extX) + 1);
    const y0 = Math.max(0, Math.floor(py - extY));
    const y1 = Math.min(h, Math.ceil(py + extY) + 1);
    if (x1 <= x0 || y1 <= y0) continue;

    const cr = sb.rgb[3 * i], cg = sb.rgb[3 * i + 1], cb = sb.rgb[3 * i + 2];
    const sa = sb.alpha[i];
    const phase = sb.phase[i];
    const cosPhase = Math.cos(phase);
    const tail = coverTail !== null && i >= splitAt;
    const edgeCap = cutoff - 0.02;
    const hLayer = 1.0 + impastoLayer * (i * invN);

    for (let yy = y0; yy < y1; yy++) {
      const dy = yy - py;
      for (let xx = x0; xx < x1; xx++) {
        const dx = xx - px;
        const u = (dx * ct + dy * st) / sx;
        const v = (-dx * st + dy * ct) / sy;
        const d2 = u * u + v * v;
        if (d2 > CUTOFF_D2) continue; // == the Python's `inside` mask, which zeroes alpha

        let a;
        if (hard) {
          const d = Math.sqrt(d2);
          const aa = Math.max(fwidthD(u, v, d, ct, st, sx, sy), 1e-4);
          let edge = hardR;
          if (wobbleAmp > 0 || fringePx > 0) {
            // One atan2 for both outline terms, rounded to f32 because the Python's is:
            // `phi` there is `np.arctan2` of two f32 arrays. Without the round the gap
            // between the two would be multiplied by the fringe's harmonic order, which
            // reaches 192.
            const phi = Math.fround(Math.atan2(v, u));
            if (wobbleAmp > 0) edge *= wobble(phi, phase, wobbleAmp);
            if (fringePx > 0) edge *= fringe(phi, phase, fringeAmp, mFringe);
            // A crest that reached the cutoff would be sliced flat by the discard and read
            // as a chord across the stroke, so it is clamped just inside the quad -- after
            // BOTH terms, since either can push the threshold outward.
            if (edge > edgeCap) edge = edgeCap;
          }
          // AFTER the clamp: `taper` only shrinks, so the crest stays inside the quad and
          // the wobble-only path above is left exactly as it was.
          if (taperAmp > 0) edge *= taper(u, cosPhase, taperAmp);
          a = 1.0 - smoothstep(edge - aa, edge + aa, d);
        } else {
          a = Math.exp(-d2);
        }
        a = Math.fround(a); // Python does `a.astype(np.float32)` before compositing
        const g = wantBristle ? belong * bristle(u * bu, v * bv, phase) : 0.0;
        if (bristleAmp > 0) {
          // Clipped: above 1, `inv` goes negative and the `over` below subtracts paint. So
          // at alpha 1 a groove can only ever CUT -- while the height field uses the same
          // `g` unclipped, because paint can be thicker without being more opaque.
          const b = a * (1.0 + bristleAmp * g);
          a = Math.fround(b < 0 ? 0 : b > 1 ? 1 : b);
        }
        if (sa !== 1.0) a *= sa;
        if (a === 0.0) continue;

        const inv = 1.0 - a;
        const p = yy * w + xx, p3 = 3 * p;
        out[p3] = out[p3] * inv + a * cr;
        out[p3 + 1] = out[p3 + 1] * inv + a * cg;
        out[p3 + 2] = out[p3 + 2] * inv + a * cb;
        cover[p] = cover[p] * inv + a;
        if (height !== null) {
          // [Hertzmann02] §2: the stroke's height texture plus the paint-order offset,
          // composited with ordinary alpha blending. NOT summed -- the paper tried that
          // and found buried strokes surfacing through the paint above them.
          height[p] = height[p] * inv + a * (hLayer + impastoRelief * g);
        }
        if (tail) coverTail[p] = coverTail[p] * inv + a;
      }
    }
  }

  return { out, cover, coverTail, height };
}

// Fraction of pixels a coverage buffer paints solid -- `(cover > 0.99).mean()` in numpy.
export function coveredFraction(cover, thresh = 0.99) {
  let k = 0;
  for (let i = 0; i < cover.length; i++) if (cover[i] > thresh) k++;
  return k / cover.length;
}
