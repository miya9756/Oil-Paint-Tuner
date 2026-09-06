// Port of oilpaint/metrics.py -- instrumentation. None of these are objectives.
//
// The Python module's docstring exempts itself from the no-scipy rule on the grounds that
// it "never ports to the browser". That stopped being true when the page stopped shipping
// Python, so it ports -- but the warning attached to `psnr` travels with it.

import { structureTensor, sample, sampleAngle } from './tensor.js';

/**
 * Fidelity against the source.
 *
 * A DIAGNOSTIC OF THE ALLOCATOR, NOT A GOAL. At a fixed stroke budget, higher means detail
 * went where detail was. But a method that maximises this is converging on a photograph,
 * which is the failure mode of the whole project -- always read it next to the image.
 */
export function psnr(a, b) {
  let mse = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i] < 0 ? 0 : a[i] > 1 ? 1 : a[i];
    const y = b[i] < 0 ? 0 : b[i] > 1 ? 1 : b[i];
    mse += (x - y) * (x - y);
  }
  mse /= a.length;
  return mse === 0 ? Infinity : 10.0 * Math.log10(1.0 / mse);
}

/** Fraction of pixels fully painted by strokes alone -- the black-hole measure. */
export function coverage(cover, thresh = 0.99) {
  let k = 0;
  for (let i = 0; i < cover.length; i++) if (cover[i] > thresh) k++;
  return k / cover.length;
}

/** 2/pi -- the null hypothesis for edgeAlignment, i.e. uniformly random angles. */
export const RANDOM_ALIGNMENT = 2.0 / Math.PI;

/**
 * Mean |cos(theta_stroke - theta_isophote)|, weighted by structure coherence.
 *
 * This is what makes the orientation experiment objective instead of a matter of taste.
 * Expected ~1.0 for orient='structure' and ~0.637 for 'random'. A structure-aligned run
 * that scores near 0.637 has a bug, not a style.
 */
export function edgeAlignment(sb, rgb, h, w, sigma = 2.0, tensor = null) {
  // `tensor` lets the caller hand in an already-computed pair. The Python recomputes it
  // every call, which is correct and slow. Note this sigma is FIXED at 2.0 rather than
  // following cfg.tensor_sigma, so the number keeps meaning the same thing across a sweep
  // of that knob -- which since the default moved to 3.5 means it is no longer the field
  // `plan` just built, and the worker's cache holds both. One extra tensor per image and
  // size, not per render, because cache.tensors is keyed by sigma and never evicted.
  const [thetaF, coh] = tensor || structureTensor(rgb, h, w, sigma);
  const ref = sampleAngle(thetaF, h, w, sb.y, sb.x);
  const wgt = sample(coh, h, w, sb.y, sb.x);
  let num = 0, tot = 0, cSum = 0;
  for (let i = 0; i < sb.theta.length; i++) {
    // Orientations are pi-periodic, so compare through the doubled angle.
    const c = Math.abs(Math.cos(sb.theta[i] - ref[i]));
    num += c * wgt[i];
    tot += wgt[i];
    cSum += c;
  }
  return tot > 0 ? num / tot : cSum / sb.theta.length;
}

export function strokeStats(sb) {
  const n = sb.x.length;
  let rSum = 0, rMin = Infinity, rMax = -Infinity, aSum = 0;
  for (let i = 0; i < n; i++) {
    const r = 0.5 * (sb.r_major[i] + sb.r_minor[i]);
    rSum += r;
    if (r < rMin) rMin = r;
    if (r > rMax) rMax = r;
    aSum += sb.r_major[i] / Math.max(sb.r_minor[i], 1e-6);
  }
  return { n, r_mean: rSum / n, r_min: rMin, r_max: rMax, aniso_mean: aSum / n };
}
