// Port of oilpaint/detail.py -- where the quadtree should spend strokes.
//
// Every metric is O(1) per cell via a summed-area table (or a mip pyramid for `range`), so
// the tree build stays O(pixels) however deep it goes. See the Python for what each metric
// means; this file is a transcription, not a redesign.

import { convolve1d, luma, padToSquare, satBox, summedArea } from './image.js';

export const METRICS = ['var', 'range', 'grad', 'dct', 'residual'];

// ITU-T T.81 Annex K, the JPEG example luminance quantization table.
const JPEG_LUMA_Q = [
  16, 11, 10, 16, 24, 40, 51, 61,
  12, 12, 14, 19, 26, 58, 60, 55,
  14, 13, 16, 24, 40, 57, 69, 56,
  14, 17, 22, 29, 51, 87, 80, 62,
  18, 22, 37, 56, 68, 109, 103, 77,
  24, 35, 55, 64, 81, 104, 113, 92,
  49, 64, 78, 87, 103, 121, 120, 101,
  72, 92, 95, 98, 112, 100, 103, 99,
];

function dct8Matrix() {
  const n = 8;
  const m = new Float64Array(n * n);
  for (let k = 0; k < n; k++) {
    for (let j = 0; j < n; j++) {
      let v = Math.cos((Math.PI * (2 * j + 1) * k) / (2 * n)) * Math.sqrt(2.0 / n);
      if (k === 0) v *= Math.sqrt(0.5);
      m[k * n + j] = v;
    }
  }
  return m;
}

/** Explicit 3x3 Sobel, separable. The taps are exact in binary, so this is exact. */
export function sobel(lum, h, w) {
  const smooth = Float32Array.from([0.25, 0.5, 0.25]);       // [1,2,1]/4
  const diff = Float32Array.from([-0.5, 0.0, 0.5]);          // [-1,0,1]/2
  const gx = convolve1d(convolve1d(lum, h, w, smooth, 0), h, w, diff, 1);
  const gy = convolve1d(convolve1d(lum, h, w, diff, 0), h, w, smooth, 1);
  return [gx, gy];
}

/** Per-pixel map of how much JPEG would spend on this pixel's 8x8 block. */
function jpegEnergy(lum, h, w) {
  const ph = (-h % 8 + 8) % 8, pw = (-w % 8 + 8) % 8;
  const H = h + ph, W = w + pw;
  // FLOAT32: `np.pad(lum, ...) * 255.0` keeps lum's float32 (a Python scalar does not
  // promote it), and only the DCT matmul below pulls it up to float64. Holding it in f64
  // here skips a rounding numpy performs, and the dct scores then differ by ~8e-6.
  const p = new Float32Array(H * W);
  for (let y = 0; y < H; y++) {
    const sy = y < h ? y : h - 1;
    for (let x = 0; x < W; x++) p[y * W + x] = lum[sy * w + (x < w ? x : w - 1)] * 255.0;
  }
  const d = dct8Matrix();
  const bh = H / 8, bw = W / 8;
  const out = new Float64Array(h * w);
  const tmp = new Float64Array(64), coef = new Float64Array(64);
  for (let by = 0; by < bh; by++) {
    for (let bx = 0; bx < bw; bx++) {
      // coef = D @ block @ D.T
      for (let i = 0; i < 8; i++) {
        for (let j = 0; j < 8; j++) {
          let s = 0;
          for (let k = 0; k < 8; k++) s += d[i * 8 + k] * p[(by * 8 + k) * W + bx * 8 + j];
          tmp[i * 8 + j] = s;
        }
      }
      let energy = 0;
      for (let i = 0; i < 8; i++) {
        for (let j = 0; j < 8; j++) {
          let s = 0;
          for (let k = 0; k < 8; k++) s += tmp[i * 8 + k] * d[j * 8 + k];
          coef[i * 8 + j] = s;
          if (i === 0 && j === 0) continue;       // DC is the flat colour the stroke carries
          const q = s / JPEG_LUMA_Q[i * 8 + j];
          energy += q * q;
        }
      }
      // broadcast back over the block, clipped to the real image
      for (let y = by * 8; y < by * 8 + 8 && y < h; y++) {
        for (let x = bx * 8; x < bx * 8 + 8 && x < w; x++) out[y * w + x] = energy;
      }
    }
  }
  return out;
}

export class DetailField {
  constructor(rgb, h, w, metric = 'var', treeSize = null) {
    if (!METRICS.includes(metric)) throw new Error(`unknown metric ${metric}`);
    this.metric = metric;
    this.h = h;
    this.w = w;
    const lum = luma(rgb, h, w);
    this.lum = lum;

    if (metric === 'var' || metric === 'residual') {
      this.sat = summedArea(lum, h, w);
      const sq = new Float64Array(h * w);
      // `lum.astype(np.float64) ** 2` -- squared in f64, from the f32 value.
      for (let i = 0; i < sq.length; i++) sq[i] = lum[i] * lum[i];
      this.sat2 = summedArea(sq, h, w);
    } else if (metric === 'grad') {
      const [gx, gy] = sobel(lum, h, w);
      const g = new Float32Array(h * w);
      for (let i = 0; i < g.length; i++) g[i] = Math.hypot(gx[i], gy[i]);
      this.sat = summedArea(g, h, w);
    } else if (metric === 'dct') {
      this.sat = summedArea(jpegEnergy(lum, h, w), h, w);
    } else if (metric === 'range') {
      // min/max cannot go through a SAT, but cells are power-of-two aligned, so a mip
      // pyramid gives the exact cell extremum in one lookup.
      const size = treeSize || 1 << Math.ceil(Math.log2(Math.max(h, w)));
      let mn = padToSquare(lum, h, w, size);
      let mx = mn;
      this.mipMin = [mn];
      this.mipMax = [mx];
      this.mipN = [size];
      let n = size;
      while (n > 1) {
        const half = n >> 1;
        const a = this.mipMin[this.mipMin.length - 1];
        const b = this.mipMax[this.mipMax.length - 1];
        const na = new Float32Array(half * half), nb = new Float32Array(half * half);
        for (let y = 0; y < half; y++) {
          for (let x = 0; x < half; x++) {
            const i0 = 2 * y * n + 2 * x, i1 = i0 + 1, i2 = i0 + n, i3 = i2 + 1;
            na[y * half + x] = Math.min(Math.min(a[i0], a[i1]), Math.min(a[i2], a[i3]));
            nb[y * half + x] = Math.max(Math.max(b[i0], b[i1]), Math.max(b[i2], b[i3]));
          }
        }
        this.mipMin.push(na);
        this.mipMax.push(nb);
        this.mipN.push(half);
        n = half;
      }
    }
  }

  /** Mean luma over one cell. Vectorised over the level's whole frontier. */
  meanLuma(y0, x0, size) {
    const out = new Float64Array(y0.length);
    for (let i = 0; i < y0.length; i++) {
      const [total, area] = satBox(this.sat, this.h, this.w,
                                   y0[i], x0[i], y0[i] + size, x0[i] + size);
      out[i] = area > 0 ? total / Math.max(area, 1.0) : 0.0;
    }
    return out;
  }

  /** Detail score per cell, for one tree level (`size` is a scalar). */
  score(y0, x0, size, parentMean = null) {
    const n = y0.length;
    const out = new Float64Array(n);

    if (this.metric === 'var' || this.metric === 'residual') {
      const residual = this.metric === 'residual';
      for (let i = 0; i < n; i++) {
        const [s1, area] = satBox(this.sat, this.h, this.w,
                                  y0[i], x0[i], y0[i] + size, x0[i] + size);
        const [s2] = satBox(this.sat2, this.h, this.w,
                            y0[i], x0[i], y0[i] + size, x0[i] + size);
        const a = Math.max(area, 1.0);
        let v;
        if (!residual) {
          v = s2 / a - (s1 / a) ** 2;
        } else {
          // MSE against the PARENT's flat colour, expanded so it stays O(1) via the SATs.
          const m = parentMean === null
            ? (area > 0 ? s1 / a : 0.0)
            : parentMean[i];
          v = s2 / a - 2.0 * m * (s1 / a) + m * m;
        }
        out[i] = area > 0 ? Math.max(v, 0.0) : 0.0;
      }
      return out;
    }

    if (this.metric === 'grad' || this.metric === 'dct') {
      for (let i = 0; i < n; i++) {
        const [total, area] = satBox(this.sat, this.h, this.w,
                                     y0[i], x0[i], y0[i] + size, x0[i] + size);
        out[i] = area > 0 ? total / Math.max(area, 1.0) : 0.0;
      }
      return out;
    }

    if (this.metric === 'range') {
      let lvl = Math.round(Math.log2(size));
      lvl = Math.min(lvl, this.mipMin.length - 1);
      const nn = this.mipN[lvl];
      for (let i = 0; i < n; i++) {
        let iy = y0[i] >> lvl, ix = x0[i] >> lvl;
        iy = iy < 0 ? 0 : iy > nn - 1 ? nn - 1 : iy;
        ix = ix < 0 ? 0 : ix > nn - 1 ? nn - 1 : ix;
        // `(mip_max - mip_min)` is a float32 subtraction in numpy, rounded before the
        // `.astype(float64)`. Doing it in f64 here is more accurate and therefore wrong.
        out[i] = Math.fround(this.mipMax[lvl][iy * nn + ix] - this.mipMin[lvl][iy * nn + ix]);
      }
      return out;
    }

    throw new Error('unreachable');
  }
}

/**
 * Port of detail.FovealField -- a DetailField reweighted by a hand-painted importance map.
 *
 * Content-based allocation answers "where is this image hard to approximate with one flat
 * colour", which is not the question a viewer's eye asks. This puts that second question
 * back in as an INPUT: a coarse map in [0,1], 1 = look here.
 *
 *     w      = (1 - strength) + strength * mask_mean(cell)
 *     score' = score * w
 *
 * `strength` is the whole control surface -- it slides from the content metric alone to the
 * map alone, so there is no separate mode. See the Python for the two properties that
 * matter (the budget is redistributed, not increased; an unpainted map is a no-op) and for
 * the blend/replace and coarsen knobs an earlier draft had and why they went. This file is
 * a transcription, not a redesign.
 */
export class FovealField {
  constructor(field, mask, strength = 1.0) {
    if (mask.length !== field.h * field.w) {
      throw new Error(`foveal map ${mask.length} != image ${field.h * field.w}`);
    }
    this.inner = field;
    this.metric = field.metric;      // quadtree reads this to decide on the parent mean
    this.h = field.h;
    this.w = field.w;
    this.strength = strength;

    // np.clip(np.asarray(mask, float32), 0, 1) -- the clip is not decoration: a map that
    // arrived above 1 would let a cell's weight exceed the content score's own scale.
    const m = new Float32Array(field.h * field.w);
    for (let i = 0; i < m.length; i++) {
      const v = mask[i];
      m[i] = v < 0 ? 0 : v > 1 ? 1 : v;
    }
    this.maskSat = summedArea(m, field.h, field.w);
  }

  maskMean(y0, x0, size) {
    const out = new Float64Array(y0.length);
    for (let i = 0; i < y0.length; i++) {
      const [total, area] = satBox(this.maskSat, this.h, this.w,
                                   y0[i], x0[i], y0[i] + size, x0[i] + size);
      out[i] = area > 0 ? total / Math.max(area, 1.0) : 0.0;
    }
    return out;
  }

  weight(y0, x0, size) {
    const m = this.maskMean(y0, x0, size);
    const out = new Float64Array(m.length);
    for (let i = 0; i < m.length; i++) out[i] = (1.0 - this.strength) + this.strength * m[i];
    return out;
  }

  score(y0, x0, size, parentMean = null) {
    const w = this.weight(y0, x0, size);
    const s = this.inner.score(y0, x0, size, parentMean);
    const out = new Float64Array(w.length);
    for (let i = 0; i < w.length; i++) out[i] = s[i] * w[i];
    return out;
  }

  /** Unweighted, always -- a COLOUR travelling down to the residual metric, not a score. */
  meanLuma(y0, x0, size) {
    return this.inner.meanLuma(y0, x0, size);
  }
}
