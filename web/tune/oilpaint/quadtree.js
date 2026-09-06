// Port of oilpaint/quadtree.py -- adaptive subdivision [Samet84].
//
// A cell splits into four if its detail score exceeds tau, subject to a minimum and maximum
// depth. The tree lives on a padded square of side 2^k so cells stay power-of-two aligned.
// Level by level, whole frontier at a time, exactly as the Python does it -- the ORDER
// cells come out in is part of the contract, because it feeds the seeded shuffle in
// strokes.js and any reordering changes the painting.

export function treeSize(h, w) {
  return 1 << Math.ceil(Math.log2(Math.max(h, w)));
}

/**
 * Subdivide level by level; returns {y0, x0, size, depth} as Int32Arrays.
 *
 * @param {import('./detail.js').DetailField} field
 */
export function build(field, h, w, tau, dmin = 2, minCell = 4, dmax = null, tauFloor = 0.0) {
  const size = treeSize(h, w);
  if (dmax === null) {
    dmax = Math.trunc(Math.log2(Math.max(1, Math.floor(size / Math.max(1, minCell)))));
  }
  dmax = Math.max(dmax, dmin);

  let y0 = Int32Array.from([0]);
  let x0 = Int32Array.from([0]);
  let cur = size;
  let depth = 0;
  let parentMean = null;
  const leaves = [];

  for (;;) {
    if (depth >= dmax || cur <= 1) {
      leaves.push([y0, x0, cur, depth]);
      break;
    }

    let split;
    if (depth < dmin) {
      split = new Uint8Array(y0.length).fill(1);
    } else {
      const score = field.score(y0, x0, cur, parentMean);
      split = new Uint8Array(y0.length);
      // tau_floor is an ABSOLUTE floor the budget cannot buy past -- without it a budget
      // larger than the image's real detail drives tau to ~0 and the tree subdivides JPEG
      // noise, which came back as speckle across a flat sky.
      for (let i = 0; i < score.length; i++) {
        split[i] = (score[i] > tau && score[i] > tauFloor) ? 1 : 0;
      }
    }

    let nSplit = 0;
    for (let i = 0; i < split.length; i++) nSplit += split[i];
    if (nSplit === 0) {
      leaves.push([y0, x0, cur, depth]);
      break;
    }

    // the cells that did NOT split become leaves at this level
    const keepY = new Int32Array(split.length - nSplit);
    const keepX = new Int32Array(split.length - nSplit);
    for (let i = 0, k = 0; i < split.length; i++) {
      if (!split[i]) { keepY[k] = y0[i]; keepX[k] = x0[i]; k++; }
    }
    leaves.push([keepY, keepX, cur, depth]);

    const sy = new Int32Array(nSplit), sx = new Int32Array(nSplit);
    for (let i = 0, k = 0; i < split.length; i++) {
      if (split[i]) { sy[k] = y0[i]; sx[k] = x0[i]; k++; }
    }
    // Parent flat colour travels down for the `residual` metric; harmless otherwise.
    const pm = field.metric === 'residual' ? field.meanLuma(sy, sx, cur) : null;

    const half = cur >> 1;
    // np.concatenate([sy, sy, sy+half, sy+half]) / [sx, sx+half, sx, sx+half]
    const ny = new Int32Array(nSplit * 4), nx = new Int32Array(nSplit * 4);
    for (let i = 0; i < nSplit; i++) {
      ny[i] = sy[i];               nx[i] = sx[i];
      ny[nSplit + i] = sy[i];      nx[nSplit + i] = sx[i] + half;
      ny[2 * nSplit + i] = sy[i] + half; nx[2 * nSplit + i] = sx[i];
      ny[3 * nSplit + i] = sy[i] + half; nx[3 * nSplit + i] = sx[i] + half;
    }
    let npm = null;
    if (pm !== null) {
      npm = new Float64Array(nSplit * 4);        // np.tile(pm, 4)
      for (let r = 0; r < 4; r++) npm.set(pm, r * nSplit);
    }
    cur = half;
    depth += 1;

    // OVERLAP, not centre-inside. A coarse cell can overlap the image while its own centre
    // lies out on the padding; testing the centre here deleted a whole band of the image
    // and left it showing bare base layer. The centre test is only valid for a LEAF, and
    // that is where it still happens, below.
    let nk = 0;
    for (let i = 0; i < ny.length; i++) if (ny[i] < h && nx[i] < w) nk++;
    if (nk !== ny.length) {
      const fy = new Int32Array(nk), fx = new Int32Array(nk);
      const fpm = npm === null ? null : new Float64Array(nk);
      for (let i = 0, k = 0; i < ny.length; i++) {
        if (ny[i] < h && nx[i] < w) {
          fy[k] = ny[i]; fx[k] = nx[i];
          if (fpm) fpm[k] = npm[i];
          k++;
        }
      }
      y0 = fy; x0 = fx; parentMean = fpm;
    } else {
      y0 = ny; x0 = nx; parentMean = npm;
    }
    if (y0.length === 0) break;
  }

  // Leaves whose CENTRE falls outside the image would place a stroke on padding.
  let total = 0;
  const kept = [];
  for (const [cy, cx, cs, cd] of leaves) {
    if (cy.length === 0) continue;
    const half = cs >> 1;
    let k = 0;
    for (let i = 0; i < cy.length; i++) if (cy[i] + half < h && cx[i] + half < w) k++;
    if (k === 0) continue;
    kept.push([cy, cx, cs, cd, k, half]);
    total += k;
  }

  const oy = new Int32Array(total), ox = new Int32Array(total);
  const os = new Int32Array(total), od = new Int32Array(total);
  let p = 0;
  for (const [cy, cx, cs, cd, , half] of kept) {
    for (let i = 0; i < cy.length; i++) {
      if (cy[i] + half < h && cx[i] + half < w) {
        oy[p] = cy[i]; ox[p] = cx[i]; os[p] = cs; od[p] = cd; p++;
      }
    }
  }
  return { y0: oy, x0: ox, size: os, depth: od };
}

/**
 * Binary-search tau for a target leaf count. Leaf count is monotone decreasing in tau.
 *
 * Exists so A/B arms compare at EQUAL stroke count. Returns [tau, reachable, ceiling].
 */
export function solveTau(field, h, w, targetN, dmin = 2, minCell = 4, dmax = null,
                         iters = 24, tol = 0.02, tauFloor = 0.0) {
  const count = t => build(field, h, w, t, dmin, minCell, dmax, tauFloor).y0.length;

  // The tree has a hard leaf ceiling at (h/min_cell)*(w/min_cell). Report it rather than
  // silently undershooting -- an early sweep asked for 2500, got 1533, and looked like a
  // tuning failure when it was the floor.
  const ceiling = count(0.0);
  const reachable = targetN <= ceiling;

  let lo = 0.0, hi = 1.0;
  for (let i = 0; i < 40; i++) {
    if (count(hi) <= targetN) break;
    hi *= 4.0;
  }

  let best = hi, bestErr = Math.abs(count(hi) - targetN);
  for (let i = 0; i < iters; i++) {
    const mid = 0.5 * (lo + hi);
    const n = count(mid);
    const err = Math.abs(n - targetN);
    if (err < bestErr) { best = mid; bestErr = err; }  // the CLOSEST tau, not the last
    if (err <= tol * targetN) break;
    if (n > targetN) lo = mid; else hi = mid;
  }
  return [best, reachable, ceiling];
}
