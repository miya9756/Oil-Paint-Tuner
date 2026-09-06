// Port of oilpaint/reference.py -- matching a named painting's colour distribution by
// optimal transport (the linear Monge-Kantorovich map between two Gaussians).
//
// The Python carries the reasoning: why OT rather than Reinhard, why the Gaussian version
// rather than sliced/iterative or a neural transfer, where the reference numbers come from
// and what their provenance is NOT. Read it there; this file is the arithmetic.
//
// Two things about the arithmetic are load-bearing for the port and are the reason the
// Python does not simply call numpy:
//
//   * every 3x3 product and the whole Jacobi eigensolver are written out as scalar
//     operations on both sides, in the same order. `np.linalg.eigh` is LAPACK, which has
//     no browser equivalent, and an eigensolver that stops on a convergence test can take
//     a different number of iterations in the two languages on the same matrix. A fixed
//     sweep count cannot.
//   * the ONE place the two deliberately differ is `stats`, where numpy reduces over
//     thousands of stroke colours pairwise and this file loops. See the Python.

import { linearToSrgb, srgbToLinear } from './image.js';

export const GRAIN = 0.045;
export const EIG_FLOOR = 1e-6;
export const JACOBI_SWEEPS = 6;
// A floor on the SOURCE distribution's sd before it is inverted; the Python carries the
// measurement (an eigenvalue of 15.0, and saturated speckle, without it).
export const SRC_SD_FLOOR = 0.025;

// (r, g, b, area weight). The Python names the pigments behind each row.
export const REFERENCES = {
  'none': [],
  'starry-night': [[0.055, 0.106, 0.31, 0.34], [0.129, 0.22, 0.451, 0.24],
                   [0.043, 0.078, 0.157, 0.14], [0.929, 0.827, 0.404, 0.1],
                   [0.086, 0.129, 0.11, 0.1], [0.396, 0.443, 0.353, 0.08]],
  'great-wave': [[0.075, 0.192, 0.325, 0.3], [0.31, 0.463, 0.573, 0.22],
                 [0.878, 0.855, 0.784, 0.28], [0.647, 0.639, 0.573, 0.12],
                 [0.259, 0.263, 0.243, 0.08]],
  'water-lilies': [[0.216, 0.353, 0.373, 0.26], [0.157, 0.239, 0.373, 0.2],
                   [0.353, 0.451, 0.404, 0.18], [0.478, 0.451, 0.596, 0.16],
                   [0.796, 0.741, 0.741, 0.12], [0.643, 0.686, 0.612, 0.08]],
  'sunflowers': [[0.929, 0.769, 0.192, 0.3], [0.816, 0.596, 0.153, 0.24],
                 [0.702, 0.639, 0.353, 0.18], [0.463, 0.376, 0.176, 0.14],
                 [0.353, 0.427, 0.278, 0.08], [0.898, 0.878, 0.741, 0.06]],
  'the-scream': [[0.855, 0.451, 0.157, 0.24], [0.729, 0.259, 0.153, 0.16],
                 [0.153, 0.243, 0.353, 0.22], [0.216, 0.318, 0.298, 0.16],
                 [0.694, 0.612, 0.431, 0.12], [0.129, 0.129, 0.153, 0.1]],
  'vermeer': [[0.075, 0.071, 0.071, 0.34], [0.157, 0.271, 0.443, 0.16],
              [0.749, 0.643, 0.243, 0.12], [0.808, 0.671, 0.573, 0.16],
              [0.475, 0.353, 0.298, 0.14], [0.918, 0.898, 0.855, 0.08]],
};

export const NAMES = Object.keys(REFERENCES);

/** sRGB triple -> OKLab triple. The same expressions as palette.js, kept here rather than
 *  imported because the Python's dependency runs the other way (palette imports nothing
 *  from reference, so that reference can import _to_oklab from palette without a cycle). */
function toOklab(r, g, b) {
  const lr = srgbToLinear(r), lg = srgbToLinear(g), lb = srgbToLinear(b);
  const cl = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const cm = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const cs = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [0.2104542553 * cl + 0.7936177850 * cm - 0.0040720468 * cs,
          1.9779984951 * cl - 2.4285922050 * cm + 0.4505937099 * cs,
          0.0259040371 * cl + 0.7827717662 * cm - 0.8086757660 * cs];
}

/** The effective reference block, or null when there is nothing to do. */
export function paramsFor(cfg) {
  if (!Object.prototype.hasOwnProperty.call(REFERENCES, cfg.reference)) {
    throw new Error(`unknown reference ${JSON.stringify(cfg.reference)}`);
  }
  const s = cfg.reference_strength;
  // Finite, for the reason palette.js's band guard exists: a config that predates these
  // fields carries `undefined`, and `undefined > 0` is false -- which lands on the safe
  // side here, but the check is written out so it stays that way.
  if (cfg.reference === 'none' || !Number.isFinite(s) || s <= 0.0) return null;
  const [mu, cov] = target(cfg.reference);
  return { name: cfg.reference, strength: Math.min(1.0, s), mu, cov };
}

/** The reference's own (mean, covariance) in OKLab, from its key colours and weights. */
export function target(name) {
  const rows = REFERENCES[name];
  const lab = rows.map(q => toOklab(q[0], q[1], q[2]));
  let tot = 0.0;
  for (const q of rows) tot += q[3];
  const mu = [0.0, 0.0, 0.0];
  const w = [];
  for (let i = 0; i < rows.length; i++) {
    const wi = rows[i][3] / tot;
    w.push(wi);
    mu[0] += wi * lab[i][0];
    mu[1] += wi * lab[i][1];
    mu[2] += wi * lab[i][2];
  }
  const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < rows.length; i++) {
    const d = [lab[i][0] - mu[0], lab[i][1] - mu[1], lab[i][2] - mu[2]];
    for (let r = 0; r < 3; r++) {
      for (let k = 0; k < 3; k++) cov[r][k] += w[i] * d[r] * d[k];
    }
  }
  for (let r = 0; r < 3; r++) cov[r][r] += GRAIN * GRAIN;
  return [mu, cov];
}

/** The (mean, covariance) of interleaved sRGB colours, in OKLab. */
export function stats(rgb, linear = false) {
  const n = rgb.length / 3;
  const lab = new Float64Array(rgb.length);
  for (let i = 0; i < n; i++) {
    const p = 3 * i;
    let r = rgb[p], g = rgb[p + 1], b = rgb[p + 2];
    if (linear) { r = linearToSrgb(r); g = linearToSrgb(g); b = linearToSrgb(b); }
    const c = toOklab(r, g, b);
    lab[p] = c[0]; lab[p + 1] = c[1]; lab[p + 2] = c[2];
  }
  const mu = [0.0, 0.0, 0.0];
  for (let i = 0; i < n; i++) {
    mu[0] += lab[3 * i]; mu[1] += lab[3 * i + 1]; mu[2] += lab[3 * i + 2];
  }
  mu[0] /= n; mu[1] /= n; mu[2] /= n;
  const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < n; i++) {
    const d = [lab[3 * i] - mu[0], lab[3 * i + 1] - mu[1], lab[3 * i + 2] - mu[2]];
    for (let r = 0; r < 3; r++) {
      for (let k = 0; k < 3; k++) cov[r][k] += d[r] * d[k];
    }
  }
  // Population covariance (divide by n), matching the Python.
  for (let r = 0; r < 3; r++) {
    for (let k = 0; k < 3; k++) cov[r][k] /= n;
  }
  return [mu, cov];
}

function mat3(a, b) {
  const o = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      o[i][j] = a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j];
    }
  }
  return o;
}

/** Eigenvalues and eigenvectors of a 3x3 SYMMETRIC matrix, by cyclic Jacobi. */
export function jacobi(m) {
  const a = m.map(row => row.slice());
  const v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  const pairs = [[0, 1], [0, 2], [1, 2]];
  for (let sweep = 0; sweep < JACOBI_SWEEPS; sweep++) {
    for (const [p, q] of pairs) {
      const apq = a[p][q];
      if (apq === 0.0) continue;
      const theta = (a[q][q] - a[p][p]) / (2.0 * apq);
      const sign = theta >= 0.0 ? 1.0 : -1.0;
      const t = sign / (Math.abs(theta) + Math.sqrt(theta * theta + 1.0));
      const c = 1.0 / Math.sqrt(t * t + 1.0);
      const s = t * c;
      for (let k = 0; k < 3; k++) {
        const akp = a[k][p], akq = a[k][q];
        a[k][p] = c * akp - s * akq;
        a[k][q] = s * akp + c * akq;
      }
      for (let k = 0; k < 3; k++) {
        const apk = a[p][k], aqk = a[q][k];
        a[p][k] = c * apk - s * aqk;
        a[q][k] = s * apk + c * aqk;
      }
      for (let k = 0; k < 3; k++) {
        const vkp = v[k][p], vkq = v[k][q];
        v[k][p] = c * vkp - s * vkq;
        v[k][q] = s * vkp + c * vkq;
      }
    }
  }
  return [[a[0][0], a[1][1], a[2][2]], v];
}

/** A symmetric PSD matrix to the power +1/2 or -1/2. See the Python on `invert`. */
function powm(m, invert, floor = EIG_FLOOR) {
  const [vals, vecs] = jacobi(m);
  const d = [];
  for (const lam of vals) {
    const r = Math.sqrt(lam > floor ? lam : floor);
    d.push(invert ? 1.0 / r : r);
  }
  const o = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      o[i][j] = vecs[i][0] * d[0] * vecs[j][0] + vecs[i][1] * d[1] * vecs[j][1]
        + vecs[i][2] * d[2] * vecs[j][2];
    }
  }
  return o;
}

/** The linear Monge-Kantorovich map between two Gaussians. Returns [A, b]. */
export function transport(muS, covS, muT, covT) {
  // The SOURCE's own floor, and the same one for both -- they must stay each other's
  // inverse. See SRC_SD_FLOOR in the Python.
  const srcFloor = SRC_SD_FLOOR * SRC_SD_FLOOR;
  const sHalf = powm(covS, false, srcFloor);
  const sInv = powm(covS, true, srcFloor);
  const mid = powm(mat3(mat3(sHalf, covT), sHalf), false);
  const a = mat3(mat3(sInv, mid), sInv);
  const b = [];
  for (let i = 0; i < 3; i++) {
    b.push(muT[i] - (a[i][0] * muS[0] + a[i][1] * muS[1] + a[i][2] * muS[2]));
  }
  return [a, b];
}

/** The block the grade needs to apply this reference to these colours. */
export function fit(rgb, rp, linear = false) {
  const [muS, covS] = stats(rgb, linear);
  const [a, b] = transport(muS, covS, rp.mu, rp.cov);
  // The SOURCE statistics ride along -- see the Python: only the target changes when
  // somebody picks a different painting, so a front end that keeps these can re-aim the
  // transport without the stroke buffer.
  return { a, b, strength: rp.strength, name: rp.name, src_mu: muS, src_cov: covS };
}
