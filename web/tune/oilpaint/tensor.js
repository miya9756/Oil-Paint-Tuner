// Port of oilpaint/tensor.py -- structure tensor -> stroke orientation and coherence.
//
// Orientation is the tensor's MINOR eigenvector: the direction ALONG the edge, not across
// it. Coherence drives elongation. See the Python for the derivation and the sanity check.

import { sobel } from './detail.js';
import { decimate, gaussianBlur, luma } from './image.js';

const EPS = 1e-12;

// The sigma the FLAT field is actually computed at, after decimation, and the smallest
// short side it may be decimated to. See the Python for both numbers.
export const FLAT_STEP_SIGMA = 2.0;
export const FLAT_MIN_SIDE = 32;

/** How far the flat field is decimated. 1 means "not at all". */
export function flatStep(sigma, h, w) {
  const step = Math.trunc(Math.max(1.0, sigma / FLAT_STEP_SIGMA));
  return Math.max(1, Math.min(step, Math.max(1, Math.trunc(Math.min(h, w) / FLAT_MIN_SIDE))));
}

/**
 * The coarse orientation field for the flat-region brush.
 * Returns [theta, coherence, step, hs, ws] -- the field is on a DECIMATED grid.
 *
 * Two things happen here that `structureTensor` does not do, and they are the same idea
 * twice: attack the noise rather than average more of it. The pre-blur removes the
 * isotropic noise power that otherwise lands on both eigenvalues and pins coherence near
 * zero; the decimation drops a field that is band-limited to `sigma` onto a grid that can
 * actually represent it. `step` travels back with the field because the caller has to
 * divide its sample coordinates by it. See the Python for the derivation.
 */
export function flatTensor(rgb, h, w, sigma) {
  const step = flatStep(sigma, h, w);
  let lum = luma(rgb, h, w), fh = h, fw = w;
  if (step > 1) [lum, fh, fw] = decimate(lum, h, w, step);
  const s = sigma / step;
  const [theta, coh] = tensorFromLuma(lum, fh, fw, s, s);
  return [theta, coh, step, fh, fw];
}

/** Returns [theta, coherence], both float32 h*w. */
export function structureTensor(rgb, h, w, sigma = 2.0) {
  return tensorFromLuma(luma(rgb, h, w), h, w, sigma, 0.0);
}

/** The tensor itself, on a luma plane. Shared by both fields above. */
export function tensorFromLuma(lum0, h, w, sigma, prefilter = 0.0) {
  let lum = lum0;
  if (prefilter > 0) lum = gaussianBlur(lum, h, w, prefilter);
  const [gx, gy] = sobel(lum, h, w);

  const n = h * w;
  const xx = new Float32Array(n), yy = new Float32Array(n), xy = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    xx[i] = gx[i] * gx[i];
    yy[i] = gy[i] * gy[i];
    xy[i] = gx[i] * gy[i];
  }
  const jxx = gaussianBlur(xx, h, w, sigma);
  const jyy = gaussianBlur(yy, h, w, sigma);
  const jxy = gaussianBlur(xy, h, w, sigma);

  const theta = new Float32Array(n);
  const coh = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    // 0.5*atan2(2b, a-c) is the MAJOR axis (the gradient, across the edge); +pi/2 turns
    // it along the edge, which is where the stroke goes.
    const major = 0.5 * Math.atan2(2.0 * jxy[i], jxx[i] - jyy[i]);
    theta[i] = major + Math.PI / 2.0;

    const tr = jxx[i] + jyy[i];
    const d = (jxx[i] - jyy[i]) ** 2 + 4.0 * jxy[i] ** 2;
    const disc = Math.sqrt(Math.max(d, 0.0));
    let c = (disc / (tr + EPS)) ** 2;
    c = c < 0 ? 0 : c > 1 ? 1 : c;
    coh[i] = c;
  }
  return [theta, coh];
}

/**
 * Nearest-neighbour sample at float pixel coordinates, clamped to the image.
 *
 * Returns float32, because indexing a float32 numpy array yields float32 -- and that dtype
 * propagates: it is what makes `ratio` in strokes.js float32 before the jitter promotes it.
 */
export function sample(field, h, w, y, x) {
  const out = new Float32Array(y.length);
  for (let i = 0; i < y.length; i++) {
    let iy = npRound(y[i]);
    let ix = npRound(x[i]);
    iy = iy < 0 ? 0 : iy > h - 1 ? h - 1 : iy;
    ix = ix < 0 ? 0 : ix > w - 1 ? w - 1 : ix;
    out[i] = field[iy * w + ix];
  }
  return out;
}

/**
 * `np.round` is round-half-to-EVEN; `Math.round` is round-half-up and disagrees on exact
 * .5 (and, differently again, on negatives). Cell centres land on .5 constantly -- a cell
 * at y0=4 of size 9 has its centre at 8.5 -- so this is not a rare path.
 */
export function npRound(v) {
  const f = Math.floor(v);
  const diff = v - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;   // exactly .5 -> to even
}

/**
 * Sample an ORIENTATION field (pi-periodic). Going through the doubled angle makes the
 * +-pi/2 wrap invisible; sampling the angle directly would be wrong there.
 */
export function sampleAngle(theta, h, w, y, x) {
  const t = sample(theta, h, w, y, x);
  // float32 all the way, because `t` is float32 and every operand here is a Python scalar.
  // numpy calls sinf/cosf/atan2f; JS has only the f64 versions, so these agree to within an
  // ulp of f32 rather than exactly. That is the one irreducible gap in this file, and the
  // parity test measures it instead of assuming it away.
  const out = new Float32Array(t.length);
  for (let i = 0; i < t.length; i++) {
    const tt = Math.fround(2.0 * t[i]);
    const s = Math.fround(Math.sin(tt));
    const c = Math.fround(Math.cos(tt));
    out[i] = Math.fround(0.5 * Math.fround(Math.atan2(s, c)));
  }
  return out;
}
