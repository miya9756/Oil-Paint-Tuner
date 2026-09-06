// Port of oilpaint/strokes.py -- quadtree cells -> brush strokes.
//
// This is the file where parity is easiest to lose, for two reasons.
//
// THE DRAW ORDER IS THE ALGORITHM. Every `rng.*` call below consumes a fixed number of
// values from one stream. Adding, removing or reordering a single call shifts everything
// after it and produces a completely different painting from the same seed -- not a
// slightly different one. The calls are therefore in exactly the Python's order, including
// the ones guarded by `if size_sigma > 0` and friends, whose guards are also part of the
// order. `nprandom.js` supplies the stream itself, bit-exact (tests/test_rng_parity.py).
//
// NUMPY'S DTYPE RULES ARE ALSO THE ALGORITHM. numpy keeps a float32 array float32 when it
// meets a Python float, but promotes it to float64 when it meets a float64 ARRAY. So `coh`
// (sampled from a float32 field) makes `ratio` float32, and the very next line promotes it
// to float64 by multiplying with a float64 jitter array. Each such step is reproduced with
// `Math.fround` where numpy would round, and commented where it is not obvious.

import * as flowMod from './flow.js';
import { strokeLabels } from './regions.js';
import { sample, sampleAngle } from './tensor.js';

// Exactly covers a square cell: the half-diagonal is sqrt(2) * half-edge. Below this a
// circle inscribed in the cell covers only pi/4 of it and the missing corners are the
// "black holes". Not a tuned constant.
export const KAPPA_FULL_COVER = Math.sqrt(2.0);

// Ceiling on `coveringKappa`: a ratio of two worst cases runs away at the far end of two
// sliders, asking for kappa 14.14 at the tuner's limits -- a 905px stroke radius on a
// 1000px canvas. Above the corrected value at every default, so it changes no painting anyone is
// making. The Python carries the measurement and the reason it is 4.0.
export const KAPPA_COVER_MAX = 4.0;

/**
 * kappa that still covers the cell AFTER the centre and the radius have been jittered.
 * KAPPA_FULL_COVER is derived for a circle sitting exactly on the cell centre at exactly its
 * nominal radius, which is a stroke `fromCells` never actually places -- see the Python for
 * the full derivation and for why size_sigma, taper and elongation are each left out.
 */
export function coveringKappa(jitterCentre, jitterRadius) {
  const k = KAPPA_FULL_COVER * (1.0 + jitterCentre) / Math.max(1e-6, 1.0 - jitterRadius);
  return Math.min(k, KAPPA_COVER_MAX);
}

/** Join stroke layers. Order matters: earlier buffers are painted FIRST (underneath). */
export function concat(...buffers) {
  const bs = buffers.filter(b => b && b.x.length);
  if (!bs.length) throw new Error('nothing to concatenate');
  const n = bs.reduce((k, b) => k + b.x.length, 0);
  const out = {
    x: new Float32Array(n), y: new Float32Array(n),
    r_major: new Float32Array(n), r_minor: new Float32Array(n),
    theta: new Float32Array(n), rgb: new Float32Array(3 * n),
    alpha: new Float32Array(n), phase: new Float32Array(n),
  };
  let p = 0;
  for (const b of bs) {
    for (const f of ['x', 'y', 'r_major', 'r_minor', 'theta', 'alpha', 'phase']) {
      out[f].set(b[f], p);
    }
    out.rgb.set(b.rgb, 3 * p);
    p += b.x.length;
  }
  return out;
}

/** A full-coverage grid of cells at one scale -- the underpainting layer [Hertzmann98]. */
export function uniformGrid(h, w, cell) {
  cell = Math.max(1, Math.trunc(cell));
  const ny = Math.ceil(h / cell), nx = Math.ceil(w / cell);
  const n = ny * nx;
  const y0 = new Int32Array(n), x0 = new Int32Array(n);
  const size = new Int32Array(n), depth = new Int32Array(n);
  for (let i = 0, k = 0; i < ny; i++) {
    for (let j = 0; j < nx; j++, k++) {
      y0[k] = i * cell; x0[k] = j * cell; size[k] = cell; depth[k] = 0;
    }
  }
  return { y0, x0, size, depth };
}

/**
 * numpy's pairwise summation for float32, transcribed from loops_utils.h.src.
 *
 * `flat.mean(axis=1)` is not a left-to-right sum: numpy sums in blocks with eight
 * accumulators and recurses above 128 elements. A sequential sum gives a different float32,
 * which for the `mean` colour rule is a different pigment on every stroke.
 */
function pairwiseSumF32(a, off, n) {
  if (n < 8) {
    let res = 0;
    for (let i = 0; i < n; i++) res = Math.fround(res + a[off + i]);
    return res;
  }
  if (n <= 128) {
    const r = new Float32Array(8);
    for (let j = 0; j < 8; j++) r[j] = a[off + j];
    let i = 8;
    for (; i < n - (n % 8); i += 8) {
      for (let j = 0; j < 8; j++) r[j] = Math.fround(r[j] + a[off + i + j]);
    }
    let res = Math.fround(
      Math.fround(Math.fround(r[0] + r[1]) + Math.fround(r[2] + r[3])) +
      Math.fround(Math.fround(r[4] + r[5]) + Math.fround(r[6] + r[7])));
    for (; i < n; i++) res = Math.fround(res + a[off + i]);
    return res;
  }
  let n2 = n >> 1;
  n2 -= n2 % 8;
  return Math.fround(pairwiseSumF32(a, off, n2) + pairwiseSumF32(a, off + n2, n - n2));
}

/**
 * Quickselect: the k-th smallest, partitioning `a[0..n)` in place. O(n).
 *
 * This is what `np.partition` does, and the point is that an order STATISTIC does not
 * depend on the algorithm that finds it -- so this is exact, not approximate. A full sort
 * would give the same answer; on a 32px cell it was also ~10x slower, three times per
 * stroke, and stroke building was the single biggest cost in the whole pipeline.
 */
function selectKth(a, n, k) {
  let lo = 0, hi = n - 1;
  while (lo < hi) {
    const pivot = a[(lo + hi) >> 1];
    let i = lo, j = hi;
    while (i <= j) {
      while (a[i] < pivot) i++;
      while (a[j] > pivot) j--;
      if (i <= j) { const t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
    }
    if (k <= j) hi = j;
    else if (k >= i) lo = i;
    else break;
  }
  return a[k];
}

/** numpy's float32 median: partition, and for an even count the mean of the two middles. */
function medianF32(vals, n) {
  if (n % 2) return selectKth(vals, n, (n - 1) >> 1);
  const k = n >> 1;
  const hi = selectKth(vals, n, k);
  // After the select, everything left of k is <= a[k], so the largest of them IS the
  // (k-1)-th smallest -- no second pass over the whole array needed.
  let lo = -Infinity;
  for (let i = 0; i < k; i++) if (vals[i] > lo) lo = vals[i];
  return Math.fround(Math.fround(lo + hi) / 2);
}

/**
 * Per-cell colour. `median` is the default and it matters: a cell straddling an edge has a
 * MEAN that is a muddy intermediate colour, which shows up as blur exactly where the
 * subdivision worked hardest to find detail.
 */
function cellColors(rgb, h, w, y0, x0, idx, size, mode, out) {
  const n = idx.length;
  if (mode === 'centre') {
    // Haeberli's original: sample the source at the stroke's own position.
    for (let i = 0; i < n; i++) {
      const j = idx[i];
      const cy = Math.min(Math.max(y0[j] + (size >> 1), 0), h - 1);
      const cx = Math.min(Math.max(x0[j] + (size >> 1), 0), w - 1);
      const p = (cy * w + cx) * 3;
      out[3 * j] = rgb[p]; out[3 * j + 1] = rgb[p + 1]; out[3 * j + 2] = rgb[p + 2];
    }
    return;
  }
  const m = size * size;
  const buf = new Float32Array(m);
  for (let i = 0; i < n; i++) {
    const j = idx[i];
    for (let c = 0; c < 3; c++) {
      // Cells hang off the edge of the image, so the sample grid is CLAMPED rather than
      // the cell dropped -- an edge cell keeps its inside colour.
      let k = 0;
      for (let dy = 0; dy < size; dy++) {
        const gy = Math.min(Math.max(y0[j] + dy, 0), h - 1);
        for (let dx = 0; dx < size; dx++, k++) {
          const gx = Math.min(Math.max(x0[j] + dx, 0), w - 1);
          buf[k] = rgb[(gy * w + gx) * 3 + c];
        }
      }
      out[3 * j + c] = mode === 'median'
        ? medianF32(buf, m)
        : Math.fround(pairwiseSumF32(buf, 0, m) / m);
    }
  }
}

// Weight of the GLOBAL fallback direction, relative to `flat_dir`, where neither scale has
// any confidence at all -- a genuinely uniform ground. Not a knob; the Python carries why.
export const FLAT_GLOBAL = 0.35;

/**
 * Fold a coarse-scale orientation field into the fine one, in doubled-angle space.
 *
 * Three candidate directions summed as VECTORS at the doubled angle, with weights that hand
 * over as confidence runs out; the effective coherence is the LENGTH of that sum. A
 * coherent edge (c = 1) zeroes the other two weights and is untouched; agreeing scales add
 * and stretch the stroke; disagreeing ones cancel and it goes rounder. See the Python.
 *
 * Returns [theta (f64), cohEff (f32)]. The f32 is load-bearing: it is what keeps `ratio`
 * float32 in step 4 until the jitter array promotes it.
 */
export function flatBlend(tFine, cohFine, tFlat, cohFlat, flatDir, flatThetaDeg) {
  const n = tFine.length;
  const theta = new Float64Array(n);
  const coh = new Float32Array(n);
  const tg = flatThetaDeg * (Math.PI / 180.0);   // np.deg2rad, on a Python float -> f64
  const cg = Math.cos(2.0 * tg), sg = Math.sin(2.0 * tg);
  for (let i = 0; i < n; i++) {
    const c = cohFine[i], cf = cohFlat[i];
    const wFine = c;
    const wCoarse = flatDir * cf * (1.0 - c);
    const wGlobal = flatDir * FLAT_GLOBAL * (1.0 - c) * (1.0 - cf);
    // numpy evaluates cos(2*t) in FLOAT32 here -- `t` is a float32 array and 2.0 a Python
    // scalar, so it calls cosf -- and only promotes when it meets the float64 weights.
    const a = Math.fround(2.0 * tFine[i]);
    const b = Math.fround(2.0 * tFlat[i]);
    const vx = wFine * Math.fround(Math.cos(a)) + wCoarse * Math.fround(Math.cos(b))
      + wGlobal * cg;
    const vy = wFine * Math.fround(Math.sin(a)) + wCoarse * Math.fround(Math.sin(b))
      + wGlobal * sg;
    theta[i] = 0.5 * Math.atan2(vy, vx);
    coh[i] = Math.fround(Math.min(Math.sqrt(vx * vx + vy * vy), 1.0));
  }
  return [theta, coh];
}

/**
 * Cells -> strokes. `rng` is an nprandom Generator, already seeded.
 *
 * Returns a StrokeBuffer: struct-of-arrays, sorted so ARRAY ORDER IS PAINT ORDER. That
 * ordering is the cross-language contract -- the renderer draws straight through with no
 * sort of its own.
 */
export function fromCells(rgb, h, w, cells, thetaField, coherence, opts) {
  const {
    kappa = 1.15, jitter_centre = 0.35, jitter_radius = 0.15, jitter_theta_deg = 20.0,
    jitter_aniso = 0.2, aniso_max = 3.0, orient = 'structure', color = 'median',
    alpha = 1.0, drop_p = 0.0, size_sigma = 0.0, color_jitter = 0.0, rng,
    theta_flat = null, coh_flat = null, flat_dir = 0.0, flat_theta_deg = 45.0,
    flat_step = 1, flat_h = 0, flat_w = 0, flow = null, flow_regions = null,
  } = opts;

  const { y0, x0, size, depth } = cells;
  const n = y0.length;

  // --- 1. centre + jitter -------------------------------------------------------
  // Stratified (jittered-grid) sampling: the lattice goes, the density the tree worked
  // out stays. Without it the result reads as a mosaic filter rather than a painting.
  const uCy = rng.uniform(-1.0, 1.0, n);
  const uCx = rng.uniform(-1.0, 1.0, n);
  const cy = new Float64Array(n), cx = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const half = size[i] / 2.0;
    cy[i] = y0[i] + half + uCy[i] * jitter_centre * half;
    cx[i] = x0[i] + half + uCx[i] * jitter_centre * half;
  }

  // --- 2. radius ----------------------------------------------------------------
  const uR = rng.uniform(-1.0, 1.0, n);
  const r = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const half = size[i] / 2.0;
    r[i] = kappa * half * (1.0 + uR[i] * jitter_radius);
  }
  // A log-normal tail on top of the bounded uniform jitter, which cannot escape its
  // octave -- within a flat region every stroke otherwise comes out the same size.
  if (size_sigma > 0) {
    const z = rng.standardNormal(n);
    for (let i = 0; i < n; i++) r[i] = r[i] * Math.exp(size_sigma * z[i]);
  }

  // Random drop with area-compensating expansion: area goes as r^2 and (1-p) survive.
  const keep = new Uint8Array(n).fill(1);
  if (drop_p > 0) {
    const u = rng.random(n);
    for (let i = 0; i < n; i++) keep[i] = u[i] >= drop_p ? 1 : 0;
    const s = Math.sqrt(Math.max(1e-6, 1.0 - drop_p));
    for (let i = 0; i < n; i++) r[i] = r[i] / s;
  }

  // --- 3. orientation -----------------------------------------------------------
  // `cohEff` is settled here rather than in step 4 when the flat-region brush is on: the
  // blend produces the angle and the effective coherence from ONE vector sum and they have
  // to agree. It consumes no random numbers, so the draw order -- which is the algorithm,
  // see the header -- is the same either way.
  let base;
  let cohEff = null;
  let baseIsF32 = false;
  if (orient === 'structure') {
    base = sampleAngle(thetaField, h, w, cy, cx);   // float32 field -> float32 result
    baseIsF32 = true;
    if (flat_dir > 0.0 && theta_flat) {
      // The flat field lives on a grid decimated by `flat_step` (tensor.flatTensor), so its
      // sample coordinates are the stroke's divided by that, against ITS dimensions and not
      // the canvas's. Exactly cy/cx at step 1, the undecimated case.
      const fy = new Float64Array(n), fx = new Float64Array(n);
      for (let i = 0; i < n; i++) { fy[i] = cy[i] / flat_step; fx[i] = cx[i] / flat_step; }
      [base, cohEff] = flatBlend(
        base, sample(coherence, h, w, cy, cx),
        sampleAngle(theta_flat, flat_h, flat_w, fy, fx),
        sample(coh_flat, flat_h, flat_w, fy, fx),
        flat_dir, flat_theta_deg);
      baseIsF32 = false;
    }
    // The artistic flow field, LAST of the three orientation terms, so it decides what the
    // picture's own structure has already said rather than being averaged into it. Structure
    // mode only: 'random' and 'fixed' are A/B diagnostics, not looks. Draws no random
    // numbers, exactly like the two above -- see flow.js.
    if (flow || flow_regions) {
      const [fu, fv] = flowMod.normCoords(cy, cx, h, w);
      if (cohEff === null) cohEff = sample(coherence, h, w, cy, cx);
      // ONE BLOCK OR SEVERAL, down one code path. `groups` is a list of [block, indices],
      // and the undivided picture is one group over every index -- literally the arithmetic
      // this ran before a region could name a flow, not an equivalent of it. The label
      // comes off cy/cx BEFORE any drift, because drift is this field's own output and so
      // cannot be what chooses it. See oilpaint/regions.py.
      let groups;
      if (!flow_regions) {
        const all = new Int32Array(n);
        for (let i = 0; i < n; i++) all[i] = i;
        groups = [[flow, all]];
      } else {
        const rl = strokeLabels(flow_regions, cx, cy);
        const rids = Object.keys(flow_regions.blocks).map(Number).sort((a, b) => a - b);
        const mine = new Set(rids);
        const pick = keep => {
          const out = [];
          for (let i = 0; i < n; i++) if (keep(rl[i])) out.push(i);
          return Int32Array.from(out);
        };
        groups = [[flow, pick(v => !mine.has(v))]];
        for (const rid of rids) groups.push([flow_regions.blocks[rid], pick(v => v === rid)]);
      }
      // float64 up front, so a subset's blended angle can be written back without the
      // float32 `sampleAngle` result silently rounding it. Exact for the undivided case:
      // `flowBlend` promotes its input the same way and step 4's float64 jitter would have
      // promoted the result regardless.
      const bAll = Float64Array.from(base);
      const cAll = Float32Array.from(cohEff);
      for (const [p, idx] of groups) {
        // A region that asked for no flow at all keeps the picture's own orientation,
        // untouched -- inert by skipping, one level down.
        if (!p || idx.length === 0) continue;
        const m = idx.length;
        const gb = new Float64Array(m), gc = new Float32Array(m);
        const gu = new Float64Array(m), gv = new Float64Array(m);
        for (let j = 0; j < m; j++) {
          gb[j] = bAll[idx[j]]; gc[j] = cAll[idx[j]];
          gu[j] = fu[idx[j]]; gv[j] = fv[idx[j]];
        }
        // The hand-placed swirl centres, through the SAME transform as the strokes above,
        // so one placed at a pixel lands where a stroke at that pixel lands. null --
        // nothing placed, or a field kind with no vortices -- is the preset's own spiral.
        const cen = flowMod.fieldCentres(p, h, w);
        const [tb, tc] = flowMod.flowBlend(gb, gc, gu, gv, p, cen);
        for (let j = 0; j < m; j++) { bAll[idx[j]] = tb[j]; cAll[idx[j]] = tc[j]; }
        // DRIFT: slide the stroke along its own (now styled) axis. The only term in the
        // feature that MOVES a mark instead of turning it, and what makes marks on a flow
        // line bunch and read as a CHAIN following the curve. No draw; and `colors` below
        // is sampled from the CELL (y0/x0), so a drifted stroke keeps its own cell's
        // colour.
        if (p.drift !== 0.0) {
          for (let j = 0; j < m; j++) {
            const i = idx[j];
            cy[i] = cy[i] + p.drift * r[i] * Math.sin(tb[j]);
            cx[i] = cx[i] + p.drift * r[i] * Math.cos(tb[j]);
          }
        }
      }
      base = bAll;
      cohEff = cAll;
      baseIsF32 = false;
    }
  } else if (orient === 'random') {
    base = rng.uniform(-Math.PI / 2, Math.PI / 2, n);
  } else if (orient === 'fixed') {
    base = new Float64Array(n);
  } else {
    throw new Error(`unknown orient mode ${orient}`);
  }
  void baseIsF32;
  const uT = rng.uniform(-1.0, 1.0, n);
  const dtheta = jitter_theta_deg * (Math.PI / 180.0);   // np.deg2rad
  const theta = new Float64Array(n);
  for (let i = 0; i < n; i++) theta[i] = base[i] + dtheta * uT[i];

  // --- 4. elongation ------------------------------------------------------------
  const uA = rng.uniform(-1.0, 1.0, n);
  const rMajor = new Float64Array(n), rMinor = new Float64Array(n);
  const cohArr = cohEff !== null ? cohEff
    : orient === 'structure' ? sample(coherence, h, w, cy, cx) : null;
  for (let i = 0; i < n; i++) {
    // `coh` is float32 when it comes from the coherence field, so `ratio` is float32 too
    // -- and then the jitter, a float64 array, promotes it. Both steps are numpy's.
    let ratio;
    if (cohArr) {
      ratio = Math.fround(1.0 + Math.fround((aniso_max - 1.0) * cohArr[i]));
    } else {
      ratio = 1.0 + (aniso_max - 1.0) * 0.5;
    }
    ratio = Math.max(ratio * (1.0 + uA[i] * jitter_aniso), 1e-3);
    // Area-preserving: r_major * r_minor == r^2, so elongation changes stroke SHAPE
    // without moving the coverage statistics between A/B arms.
    const root = Math.sqrt(ratio);
    rMajor[i] = r[i] * root;
    rMinor[i] = r[i] / root;
  }

  // --- 5. colour ----------------------------------------------------------------
  const colors = new Float32Array(3 * n);
  // `np.unique(size)` -- ascending, and cells of one size are gathered together.
  const bySize = new Map();
  for (let i = 0; i < n; i++) {
    if (!bySize.has(size[i])) bySize.set(size[i], []);
    bySize.get(size[i]).push(i);
  }
  for (const s of [...bySize.keys()].sort((a, b) => a - b)) {
    cellColors(rgb, h, w, y0, x0, bySize.get(s), s, color, colors);
  }

  // Per-stroke pigment variation [Litwinowicz97]: one luminance shift plus a smaller
  // per-channel one. A pure per-channel jitter desaturates towards grey in aggregate,
  // while a shared luminance term reads as pigment loaded unevenly.
  if (color_jitter > 0) {
    const lum = rng.standardNormal(n);
    const chan = rng.standardNormal(3 * n);   // (n, 3), C order
    for (let i = 0; i < n; i++) {
      const ls = color_jitter * lum[i];
      for (let c = 0; c < 3; c++) {
        const v = colors[3 * i + c] + ls + 0.4 * color_jitter * chan[3 * i + c];
        colors[3 * i + c] = v < 0 ? 0 : v > 1 ? 1 : v;
      }
    }
  }

  const phase = rng.uniform(0.0, 2.0 * Math.PI, n);

  // --- paint order: coarse to fine [Hertzmann98] --------------------------------
  // Big strokes first, fine detail on top; a seeded shuffle within each level so equal
  // sized strokes do not overlap in scan order (which reads as a raster sweep).
  const keyRnd = rng.random(n);
  const key = new Float64Array(n);
  for (let i = 0; i < n; i++) key[i] = keyRnd[i] + depth[i];
  // `np.argsort(kind='stable')`: ties keep their original index order.
  const order = Array.from({ length: n }, (_, i) => i)
    .sort((a, b) => (key[a] - key[b]) || (a - b))
    .filter(i => keep[i]);

  const m = order.length;
  const out = {
    x: new Float32Array(m), y: new Float32Array(m),
    r_major: new Float32Array(m), r_minor: new Float32Array(m),
    theta: new Float32Array(m), rgb: new Float32Array(3 * m),
    alpha: new Float32Array(m).fill(alpha), phase: new Float32Array(m),
  };
  for (let k = 0; k < m; k++) {
    const i = order[k];
    out.x[k] = cx[i]; out.y[k] = cy[i];
    out.r_major[k] = rMajor[i]; out.r_minor[k] = rMinor[i];
    out.theta[k] = theta[i]; out.phase[k] = phase[i];
    out.rgb[3 * k] = colors[3 * i];
    out.rgb[3 * k + 1] = colors[3 * i + 1];
    out.rgb[3 * k + 2] = colors[3 * i + 2];
  }
  return out;
}
