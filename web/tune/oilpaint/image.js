// Port of oilpaint/image.py -- colour, separable convolution, summed-area tables.
//
// Python is the source of truth. The one thing to keep in mind through this whole JS
// package: numpy's DTYPES ARE PART OF THE ALGORITHM. `image.py` computes luma, the blur and
// the Sobel in float32 but accumulates summed-area tables in float64, on purpose (see
// `summedArea` below). JS has only f64 arithmetic, so float32 steps are reproduced by
// storing into a Float32Array -- which rounds on store -- and by `Math.fround` on any
// intermediate numpy would have rounded. Dropping one of those is not a rounding detail: it
// moves a quadtree split across its threshold and changes the stroke count.
//
// 2D arrays are flat typed arrays plus explicit (h, w); index (y, x) is `y * w + x`.

// Rec. 709 luma, as the float32 values numpy actually multiplies by.
export const LUMA_709 = Float32Array.from([0.2126, 0.7152, 0.0722]);

/** `rgb @ LUMA_709` -- float32 in, float32 out. rgb is h*w*3, interleaved. */
export function luma(rgb, h, w) {
  const out = new Float32Array(h * w);
  const l0 = LUMA_709[0], l1 = LUMA_709[1], l2 = LUMA_709[2];
  for (let i = 0, p = 0; i < h * w; i++, p += 3) {
    // numpy's float32 dot over three terms: each product and each partial sum rounds.
    const a = Math.fround(Math.fround(rgb[p] * l0) + Math.fround(rgb[p + 1] * l1));
    out[i] = a + Math.fround(rgb[p + 2] * l2);
  }
  return out;
}

export function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

export function linearToSrgb(c) {
  const x = c < 0 ? 0 : c > 1 ? 1 : c;
  return x <= 0.0031308 ? x * 12.92 : 1.055 * Math.pow(x, 1.0 / 2.4) - 0.055;
}

/** Elementwise sRGB -> linear over an interleaved rgb buffer, float32 out. */
export function srgbToLinearArray(rgb) {
  const out = new Float32Array(rgb.length);
  for (let i = 0; i < rgb.length; i++) out[i] = srgbToLinear(rgb[i]);
  return out;
}

/**
 * Summed-area table with a zero first row/column, so a box sum is 4 lookups.
 *
 * FLOAT64, and not negotiable: a 4k image of squared luma sums to ~1e7, and float32 loses
 * the low bits of a difference of two large partial sums -- which is exactly what a
 * variance query computes. Returns an (h+1)*(w+1) Float64Array.
 *
 * numpy does `cumsum(axis=0)` then `cumsum(axis=1)`, both in f64; the accumulation order is
 * mirrored here because a different order is a different number.
 */
export function summedArea(a, h, w) {
  const sw = w + 1;
  const s = new Float64Array((h + 1) * sw);
  // down the columns, then across the rows -- into the (1,1) offset block
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      s[(y + 1) * sw + (x + 1)] = a[y * w + x] + (y > 0 ? s[y * sw + (x + 1)] : 0);
    }
  }
  for (let y = 1; y <= h; y++) {
    let acc = 0;
    for (let x = 1; x <= sw - 1; x++) {
      acc += s[y * sw + x];
      s[y * sw + x] = acc;
    }
  }
  return s;
}

/**
 * Sum over [y0,y1) x [x0,x1), clipped to the image. Returns [total, area].
 *
 * Cells may hang off the edge of the padded square tree, so the clip is not optional; area
 * can be 0 and every caller must guard the division. The subtraction order matches numpy's
 * left-to-right `a - b - c + d`.
 */
export function satBox(sat, h, w, y0, x0, y1, x1) {
  const sw = w + 1;
  y0 = y0 < 0 ? 0 : y0 > h ? h : y0;
  y1 = y1 < 0 ? 0 : y1 > h ? h : y1;
  x0 = x0 < 0 ? 0 : x0 > w ? w : x0;
  x1 = x1 < 0 ? 0 : x1 > w ? w : x1;
  const total = sat[y1 * sw + x1] - sat[y0 * sw + x1] - sat[y1 * sw + x0] + sat[y0 * sw + x0];
  return [total, (y1 - y0) * (x1 - x0)];
}

/**
 * `np.exp` on a float32 array. numpy dispatches to `expf`, whose last bit need not match
 * `Math.fround(Math.exp(x))` -- the divergence is bounded by one ulp of f32 and is measured
 * by the parity test rather than assumed away.
 */
function expf(x) {
  return Math.fround(Math.exp(x));
}

/** `gaussian_kernel1d(sigma, truncate)` -- float32 throughout, normalised to sum 1. */
export function gaussianKernel1d(sigma, truncate = 3.0) {
  const r = Math.max(1, Math.trunc(truncate * sigma + 0.5));
  const k = new Float32Array(2 * r + 1);
  for (let i = 0; i < k.length; i++) {
    const x = Math.fround(i - r);
    const t = Math.fround(Math.fround(x / sigma) ** 2);
    k[i] = expf(Math.fround(-0.5 * t));
  }
  // numpy's float32 `.sum()` is pairwise, but for a kernel of this length it reduces to a
  // sequential f32 accumulation, which is what this is.
  let s = 0;
  for (let i = 0; i < k.length; i++) s = Math.fround(s + k[i]);
  for (let i = 0; i < k.length; i++) k[i] = k[i] / s;
  return k;
}

/**
 * Block-mean downsample of an h*w plane by an integer factor, edge-replicating the ragged
 * last block. Returns [data, hs, ws].
 *
 * A step x step box IS the anti-alias filter a decimation needs, so this is one operation
 * and not "filter, then subsample". Accumulated in float64 and rounded once, which is what
 * numpy's `mean(dtype=np.float64).astype(np.float32)` does -- at float64 the reduction
 * ORDER stops mattering to the float32 that comes out, so this holds to parity without
 * having to reproduce numpy's pairwise tree.
 */
export function decimate(a, h, w, step) {
  step = Math.max(1, Math.trunc(step));
  if (step === 1) return [Float32Array.from(a), h, w];
  const hs = Math.ceil(h / step), ws = Math.ceil(w / step);
  const out = new Float32Array(hs * ws);
  const inv = 1.0 / (step * step);
  for (let by = 0; by < hs; by++) {
    for (let bx = 0; bx < ws; bx++) {
      let acc = 0.0;
      for (let dy = 0; dy < step; dy++) {
        const sy = Math.min(by * step + dy, h - 1);   // 'edge' padding == clamp
        const row = sy * w;
        for (let dx = 0; dx < step; dx++) {
          acc += a[row + Math.min(bx * step + dx, w - 1)];
        }
      }
      out[by * ws + bx] = Math.fround(acc * inv);
    }
  }
  return [out, hs, ws];
}

/**
 * Separable convolution with edge padding, along axis 0 (y) or 1 (x).
 *
 * Hand-rolled rather than scipy.ndimage because this file has to port -- and the port is
 * this function. numpy accumulates `out += float32(w) * padded`, i.e. the product rounds to
 * f32 and then the sum rounds again; writing into a Float32Array supplies the second
 * rounding, so only the product needs an explicit `fround`.
 */
export function convolve1d(a, h, w, k, axis) {
  const r = (k.length - 1) >> 1;
  const n = k.length;
  const out = new Float32Array(h * w);
  // Pixel outer, tap inner -- one pass over the image instead of one pass PER TAP. At
  // sigma 8 the kernel is 49 taps, so the tap-outer form read and rewrote the whole output
  // 49 times and the base layer alone cost ~0.5 s. The accumulator is rounded to f32 after
  // every tap, which is exactly what numpy's `out += float32(w) * padded` does when `out`
  // is a float32 array -- so this is the same sequence of roundings in the same order, and
  // the parity test holds it to that.
  if (axis === 0) {
    // Down the y axis the taps are a whole row apart, so the tap-inner form above would
    // stride across the image for every pixel. Instead each output ROW is accumulated in
    // place: one row of scratch stays in cache while the taps are read contiguously. The
    // scratch is a Float32Array, so the store after each tap supplies the same rounding.
    const acc = new Float32Array(w);
    for (let y = 0; y < h; y++) {
      acc.fill(0);
      for (let i = 0; i < n; i++) {
        let sy = y + i - r;                       // 'edge' padding == clamp
        sy = sy < 0 ? 0 : sy >= h ? h - 1 : sy;
        const src = sy * w;
        const wgt = k[i];
        for (let x = 0; x < w; x++) acc[x] = acc[x] + Math.fround(wgt * a[src + x]);
      }
      out.set(acc, y * w);
    }
  } else {
    for (let y = 0; y < h; y++) {
      const row = y * w;
      for (let x = 0; x < w; x++) {
        let acc = 0;
        for (let i = 0; i < n; i++) {
          let sx = x + i - r;
          sx = sx < 0 ? 0 : sx >= w ? w - 1 : sx;
          acc = Math.fround(acc + Math.fround(k[i] * a[row + sx]));
        }
        out[row + x] = acc;
      }
    }
  }
  return out;
}

export function gaussianBlur(a, h, w, sigma) {
  if (sigma <= 0) return Float32Array.from(a);
  const k = gaussianKernel1d(sigma);
  return convolve1d(convolve1d(a, h, w, k, 0), h, w, k, 1);
}

/** Edge-replicate to size x size, anchored at the top-left (the tree's origin). */
export function padToSquare(a, h, w, size) {
  const out = new Float32Array(size * size);
  for (let y = 0; y < size; y++) {
    const sy = y < h ? y : h - 1;
    for (let x = 0; x < size; x++) {
      out[y * size + x] = a[sy * w + (x < w ? x : w - 1)];
    }
  }
  return out;
}
