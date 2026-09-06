// Port of oilpaint/pipeline.py -- orchestration. One image in, one painting out.
//
// The Python's `plan` / `render` / `finish` split is kept, so the two files still read
// alike and a change to the order of operations on one side is visible as a diff against
// the other.

import { DetailField, FovealField } from './detail.js';
import { gaussianKernel1d, linearToSrgb, srgbToLinearArray } from './image.js';
import { defaultRng } from './nprandom.js';
import { centresFor, paramsFor as flowParamsFor, vortexMap } from './flow.js';
import { blockFor, gradeImage, gradeStrokes, paramsFor } from './palette.js';
import { fit as referenceFit,
         paramsFor as referenceParamsFor } from './reference.js';
import * as regionsMod from './regions.js';
import * as quadtree from './quadtree.js';
import { groundHeight, light, render } from './render.js';
import { flatTensor, structureTensor } from './tensor.js';
import * as strokes from './strokes.js';

/** Mirrors the PaintConfig dataclass. Kept in this order for readability, not by need. */
export const DEFAULTS = {
  metric: 'var', target_n: 5000, tau: null, max_cell: 64, min_cell: 8, base_block: 16,
  tau_floor: 1e-4,
  foveal_strength: 0.0,
  kappa: 1.35, jitter_centre: 0.35, jitter_radius: 0.15, jitter_theta_deg: 20.0,
  jitter_aniso: 0.2, aniso_max: 6.0, orient: 'structure', tensor_sigma: 3.5,
  flat_dir: 0.8, flat_sigma: 8.0, flat_theta_deg: 45.0,
  flow: 'none', flow_strength: 1.0, flow_scale: 0.0, flow_rot: 0.0, flow_coh: 0.0,
  flow_drift: 0.0,
  palette: 'none', palette_strength: 1.0, warm_cool: 0.0, chroma: 0.0,
  value_compress: 0.0, broken_color: 0.0, pigment: 0.0,
  hue_target: 120.0, hue_range: 40.0, hue_rotate: 0.0, hue_boost: 0.0,
  reference: 'none', reference_strength: 1.0,
  color: 'median', alpha: 1.0, hard: true, hard_r: 1.5, linear: false,
  size_sigma: 0.30, drop_p: 0.0, color_jitter: 0.0, wobble_amp: 0.18,
  bristle_amp: 0.24, taper_amp: 0.35, fringe_px: 2.0,
  impasto: true, impasto_relief: 0.45, impasto_layer: 0.6, canvas_weave: 0.15,
  impasto_depth: 0.25, light_deg: 135.0, light_elev_deg: 35.0, gloss: 0.30,
  occlusion: 0.6, view_deg: 90.0, view_elev_deg: 90.0,
  base: 'blur', base_cell_scale: 2.0, seed: 0,
};

/**
 * Fold a 1-D kernel onto a block grid: returns {idx, wgt, span}, each row of (idx, wgt)
 * naming the BLOCK samples the blurred value at that pixel draws on, and with what weight.
 *
 * The clamp is inside the block division, not outside it, because that is where
 * `convolve1d`'s edge padding puts it. See the Python.
 */
function blockBlurWeights(n, nb, block, k) {
  const taps = k.length, r = (taps - 1) >> 1;
  const lo = new Int32Array(n), hi = new Int32Array(n);
  for (let y = 0; y < n; y++) {
    const a = Math.min(Math.max(y - r, 0), n - 1);
    const b = Math.min(Math.max(y + r, 0), n - 1);
    lo[y] = Math.trunc(a / block);
    hi[y] = Math.trunc(b / block);
  }
  let span = 1;
  for (let y = 0; y < n; y++) span = Math.max(span, hi[y] - lo[y] + 1);
  const idx = new Int32Array(n * span);
  const wgt = new Float32Array(n * span);
  for (let y = 0; y < n; y++) {
    for (let c = 0; c < span; c++) idx[y * span + c] = Math.min(lo[y] + c, nb - 1);
    for (let t = 0; t < taps; t++) {
      const sy = Math.min(Math.max(y + t - r, 0), n - 1);
      const c = Math.trunc(sy / block) - lo[y];
      // Float32Array store supplies the rounding numpy's float32 `.sum` does per term.
      wgt[y * span + c] = wgt[y * span + c] + k[t];
    }
  }
  return { idx, wgt, span };
}

/**
 * Opaque coarse approximation painted under every stroke.
 *
 * Box-downsample, nearest-upsample, THEN blur. The blur is not cosmetic: without it the
 * underpainting's 2^k rectangles print straight through the gaps, which is a worse artefact
 * than the holes it was added to fix.
 *
 * The blur is NOT evaluated at full resolution. What is being blurred is piecewise constant
 * on the block grid, and a separable Gaussian is linear, so every output pixel is a fixed
 * weighted sum of a handful of block samples -- a 49-tap convolution at full resolution
 * becomes a 5-tap one on a grid 16x coarser in each axis. Same weights, same value,
 * different summation order. See the Python for the measurement.
 */
export function baseLayer(rgb, h, w, block) {
  block = Math.max(1, Math.trunc(block));
  const bh = Math.ceil(h / block), bw = Math.ceil(w / block);
  const small = new Float64Array(bh * bw * 3);
  const inv = 1.0 / (block * block);
  for (let by = 0; by < bh; by++) {
    for (let bx = 0; bx < bw; bx++) {
      const acc = [0, 0, 0];
      for (let dy = 0; dy < block; dy++) {
        // 'edge' padding to the block grid, then the mean over the full block.
        const y = Math.min(by * block + dy, h - 1);
        for (let dx = 0; dx < block; dx++) {
          const x = Math.min(bx * block + dx, w - 1);
          const p = (y * w + x) * 3;
          acc[0] += rgb[p]; acc[1] += rgb[p + 1]; acc[2] += rgb[p + 2];
        }
      }
      for (let c = 0; c < 3; c++) small[(by * bw + bx) * 3 + c] = acc[c] * inv;
    }
  }
  // The blur, folded onto the block grid. Vertical first, then horizontal -- the order the
  // separable blur used, kept so the two roundings land in the same sequence.
  const k = gaussianKernel1d(0.5 * block);
  const Y = blockBlurWeights(h, bh, block, k);
  const X = blockBlurWeights(w, bw, block, k);

  const tmp = new Float32Array(h * bw * 3);
  for (let y = 0; y < h; y++) {
    for (let c = 0; c < Y.span; c++) {
      const wv = Y.wgt[y * Y.span + c];
      if (wv === 0) continue;
      const srow = Y.idx[y * Y.span + c] * bw * 3;
      const drow = y * bw * 3;
      for (let j = 0; j < bw * 3; j++) {
        tmp[drow + j] = tmp[drow + j] + Math.fround(wv * small[srow + j]);
      }
    }
  }

  const out = new Float32Array(h * w * 3);
  for (let y = 0; y < h; y++) {
    const drow = y * w * 3, srow = y * bw * 3;
    for (let x = 0; x < w; x++) {
      const d = drow + x * 3;
      for (let c = 0; c < X.span; c++) {
        const wv = X.wgt[x * X.span + c];
        if (wv === 0) continue;
        const sp = srow + X.idx[x * X.span + c] * 3;
        out[d] = out[d] + Math.fround(wv * tmp[sp]);
        out[d + 1] = out[d + 1] + Math.fround(wv * tmp[sp + 1]);
        out[d + 2] = out[d + 2] + Math.fround(wv * tmp[sp + 2]);
      }
    }
  }
  return out;
}

/**
 * A memo for the parts of a plan that do not depend on most of the panel.
 *
 * The tuner's whole interaction model is dragging one slider at a time, and three of the
 * most expensive steps -- the detail field, the structure tensor and the base canvas --
 * depend on almost none of the parameters being dragged. Recomputing them per render was
 * over half the time. This is memoisation keyed on the inputs that actually matter, NOT an
 * approximation: pass no cache (as every parity test does) and the results are identical.
 *
 * The caller owns invalidation: build a fresh one whenever the source image or its size
 * changes, because none of these keys mention the pixels.
 */
export function makeCache() {
  return { detail: null, foveal: null, tau: null, tensors: new Map(), canvas: null,
           graded: null };
}

// Always returns a SLOT, never the bare value -- returning one on a hit and the other on a
// miss is a bug that only shows up on the second render, which is the one no test made.
function memo(slot, key, make) {
  if (slot && slot.k === key) return slot;
  return { k: key, v: make() };
}

/** The structure tensor at one sigma, cached. Both `plan` and `edgeAlignment` want one. */
export function tensorFor(rgb, h, w, sigma, cache) {
  if (!cache) return structureTensor(rgb, h, w, sigma);
  const key = `fine|${sigma}`;
  if (!cache.tensors.has(key)) cache.tensors.set(key, structureTensor(rgb, h, w, sigma));
  return cache.tensors.get(key);
}

/**
 * The flat brush's coarse field, cached in the same map.
 *
 * Its own key prefix, not just its sigma: it is a DECIMATED field of a different shape from
 * the fine one, and a cache that confused the two would hand `fromCells` a field whose
 * dimensions do not match the coordinates it is about to divide.
 */
export function flatTensorFor(rgb, h, w, sigma, cache) {
  if (!cache) return flatTensor(rgb, h, w, sigma);
  const key = `flat|${sigma}`;
  if (!cache.tensors.has(key)) cache.tensors.set(key, flatTensor(rgb, h, w, sigma));
  return cache.tensors.get(key);
}

/**
 * Everything up to the rasteriser: detail field, tree, strokes, base canvas.
 *
 * Returns {sb, canvas, info}. Mirrors `pipeline.plan`. `cache` is optional; see makeCache.
 *
 * `foveal`, when given, is `{mask, key}`: a Float32Array of h*w in [0,1] with 1 = spend
 * strokes here, plus a string that changes whenever those pixels do. The Python takes the
 * map alone -- the key exists only because this side memoises, and a mask that changed
 * without changing its key would be served a stale summed-area table. The CALLER owns that
 * contract; engine.worker.js derives the key from the page's mask serial and the size.
 *
 * `onStage`, like `cache`, is a browser-only addition with no counterpart in the Python:
 * it is called with the name of each phase as that phase BEGINS, using the same names the
 * `info.timing` keys carry, so the page can say which of the seconds it is spending. Cold,
 * the tensor and the base canvas are 2.6 s of the first render before a single pixel
 * exists -- the stretch the page had nothing to show for. It reads nothing and returns
 * nothing; leave it null (as every parity test does) and the path is identical.
 *
 * `vortices` is where the flow field's swirls go, as [[fx, fy], ...] fractions of the image
 * -- what a click on the page produces. Last in the list because it is the newest and every
 * existing call site passes fewer arguments; the Python takes it as a keyword for the same
 * reason. Null or empty is the preset's own spiral. See flow.paramsFor.
 *
 * `regions` is `{labels, h, w, overrides, key}` dividing the picture into passages that
 * are painted separately -- the third input of this shape, after the two above, and for
 * the same reason: a mask is an image and a per-region parameter set is not a number. Its
 * COLOUR fields attach to the pigment grade at the end, which moves no stroke, and its
 * FLOW fields to step 3 of `fromCells`, which moves that passage's strokes and nothing
 * else. Null, or a mask nothing overrides, is one region and the painting the panel
 * alone describes.
 */
export function plan(rgb, h, w, cfg, cache = null, foveal = null, onStage = null,
                     vortices = null, regions = null) {
  cfg = Object.assign({}, DEFAULTS, cfg);
  const info = { h, w, timing: {} };
  const work = cfg.linear ? srgbToLinearArray(rgb) : rgb;

  if (onStage) onStage('detail');
  let t = performance.now();
  const size = quadtree.treeSize(h, w);
  let fld;
  if (cache) {
    const s = memo(cache.detail, cfg.metric,
                   () => new DetailField(rgb, h, w, cfg.metric, size));
    cache.detail = s;
    fld = s.v;
  } else {
    fld = new DetailField(rgb, h, w, cfg.metric, size);
  }
  // The map belongs to the REQUEST, not to the image, so it wraps the cached DetailField
  // rather than living inside it -- one field per metric, one wrapper per brush stroke.
  let fovKey = '';
  if (foveal && cfg.foveal_strength > 0.0) {
    fovKey = `${foveal.key}|${cfg.foveal_strength}`;
    const mk = () => new FovealField(fld, foveal.mask, cfg.foveal_strength);
    if (cache) {
      cache.foveal = memo(cache.foveal, `${cfg.metric}|${fovKey}`, mk);
      fld = cache.foveal.v;
    } else {
      fld = mk();
    }
  }
  info.foveal = fovKey !== '';
  info.timing.detail = performance.now() - t;

  if (onStage) onStage('quadtree');
  t = performance.now();
  // dmin is derived: whatever depth first brings the cell down to max_cell px. These were
  // depths in the first draft, which silently meant 256px strokes on a 1024 tree.
  const dmin = Math.max(0, Math.ceil(Math.log2(size / Math.max(1, cfg.max_cell))));
  info.dmin = dmin;
  const floor = cfg.metric === 'var' ? cfg.tau_floor : 0.0;
  let tau = cfg.tau;
  if (tau === null || tau === undefined) {
    // The tau search runs `build` two dozen times, so it is worth not repeating when only
    // a stroke-geometry slider moved. Its key is every input the search actually reads.
    const key = `${cfg.metric}|${cfg.target_n}|${dmin}|${cfg.min_cell}|${floor}|${fovKey}`;
    const make = () => quadtree.solveTau(
      fld, h, w, cfg.target_n, dmin, cfg.min_cell, null, 24, 0.02, floor);
    let solved;
    if (cache) {
      cache.tau = memo(cache.tau, key, make);
      solved = cache.tau.v;
    } else {
      solved = make();
    }
    const [t0, reachable, ceiling] = solved;
    tau = t0;
    info.budget_reachable = reachable;
    info.stroke_ceiling = ceiling;
  }
  const cells = quadtree.build(fld, h, w, tau, dmin, cfg.min_cell, null, floor);
  info.tau = tau;
  info.n_strokes = cells.y0.length;
  info.timing.quadtree = performance.now() - t;

  if (onStage) onStage('tensor');
  t = performance.now();
  const [theta, coh] = tensorFor(rgb, h, w, cfg.tensor_sigma, cache);
  // The coarse companion field, on a DECIMATED grid -- it is band-limited to `flat_sigma`
  // by construction, so full resolution would spend 16x the arithmetic to represent
  // nothing. See tensor.flatTensor, and note that `flatStep` comes back with it because
  // fromCells has to divide its sample coordinates by it.
  let thetaFlat = null, cohFlat = null, flatStep = 1, flatH = 0, flatW = 0;
  if (cfg.flat_dir > 0.0) {
    [thetaFlat, cohFlat, flatStep, flatH, flatW] =
      flatTensorFor(rgb, h, w, cfg.flat_sigma, cache);
  }
  info.flat_step = flatStep;
  info.timing.tensor = performance.now() - t;

  if (onStage) onStage('strokes');
  t = performance.now();
  // `null` at the defaults, and then step 3 of fromCells never enters flow.js at all.
  // The placed swirl centres, normalised ONCE: region id -> points, a plain array being the
  // base's set. Each region's field reads its own or, having none, the base's -- see
  // flow.centresFor for why that inheritance is forced rather than chosen.
  const vmap = vortexMap(vortices);
  const fp = flowParamsFor(cfg, centresFor(vmap, 0));
  info.flow = fp !== null;
  // The regions at their OTHER attachment point, and the reason this one sits up here
  // rather than with the grade at the end: what it changes is where the strokes point, so
  // it has to be decided before they are made. Null unless a region actually names a flow
  // field, so a picture with only colour regions -- or none -- never enters it.
  const frp = regionsMod.flowParamsFor(cfg, regions, vortices);
  info.flow_regions = frp === null ? 0 : Object.keys(frp.blocks).length;
  // How many placed centres this painting actually read, counted once per OWNING set: a
  // region that inherited the base's is not a second placement, and counting it as one
  // would make the panel's "(n placed)" grow when a layer changed nothing.
  {
    const owners = new Set();
    if (fp !== null && fp.vortices) owners.add(0);
    for (const rid of Object.keys(frp === null ? {} : frp.blocks)) {
      const b = frp.blocks[rid];
      if (b !== null && b.vortices) owners.add(vmap.has(Number(rid)) ? Number(rid) : 0);
    }
    let n = 0;
    for (const o of owners) n += (vmap.get(o) || []).length;
    info.flow_vortices = n;
  }
  const common = {
    jitter_centre: cfg.jitter_centre, jitter_radius: cfg.jitter_radius,
    jitter_theta_deg: cfg.jitter_theta_deg, jitter_aniso: cfg.jitter_aniso,
    aniso_max: cfg.aniso_max, orient: cfg.orient, color: cfg.color, alpha: cfg.alpha,
    size_sigma: cfg.size_sigma, color_jitter: cfg.color_jitter,
    theta_flat: thetaFlat, coh_flat: cohFlat, flat_dir: cfg.flat_dir,
    flat_theta_deg: cfg.flat_theta_deg,
    flat_step: flatStep, flat_h: flatH, flat_w: flatW,
    flow: fp,
    flow_regions: frp,
  };

  // The underpainting is a COMPLETE canvas of coarse strokes, painted before anything else
  // [Hertzmann98]. Its own seed offset, and it never drops -- this layer must cover.
  //
  // Round, and at a jitter-corrected kappa. Saying KAPPA_FULL_COVER was not enough: the
  // aspect ratio is applied area-preservingly, so elongation shrinks the MINOR axis -- the
  // one the covering condition binds on -- and the layer measured 0.704. See pipeline.plan
  // on the Python side for the derivation and the trade. Neither the draw order nor the
  // draw count changes, so the detail layer is untouched.
  let under = null;
  if (cfg.base === 'strokes') {
    under = strokes.fromCells(
      work, h, w,
      strokes.uniformGrid(h, w, Math.trunc(cfg.max_cell * cfg.base_cell_scale)),
      theta, coh,
      Object.assign({}, common, {
        kappa: strokes.coveringKappa(cfg.jitter_centre, cfg.jitter_radius),
        drop_p: 0.0,
        aniso_max: 1.0,
        jitter_aniso: 0.0,
        // Drift off here for the same reason elongation is: this layer's job is to COVER,
        // and drift bunches strokes along the flow lines and opens gaps between them. The
        // field still turns the ground's strokes; only the term that moves them is dropped.
        flow: fp === null ? null : Object.assign({}, fp, { drift: 0.0 }),
        flow_regions: regionsMod.flowUndrifted(frp),
        rng: defaultRng(cfg.seed + 9973),
      }));
  }

  const detail = strokes.fromCells(work, h, w, cells, theta, coh,
    Object.assign({}, common, {
      drop_p: cfg.drop_p, kappa: cfg.kappa, rng: defaultRng(cfg.seed),
    }));

  // Underpainting first, so array order stays paint order across both layers.
  const sb = under ? strokes.concat(under, detail) : detail;
  info.n_under = under ? under.x.length : 0;
  info.n_detail = detail.x.length;
  info.timing.strokes = performance.now() - t;

  if (onStage) onStage('canvas');
  t = performance.now();
  // The blurred base stays UNDERNEATH the stroke underpainting rather than being replaced
  // by it. Without it the canvas is black wherever the coarse layer leaves a gap, and at
  // ~1.8% uncovered that is a scatter of hard black specks across the painting.
  //
  // It is also the single most expensive step -- a 49-tap separable blur at the default
  // base_block -- and it depends on nothing but the block size and the colour space, so it
  // is the biggest thing the cache saves on a slider drag.
  let canvas = null;
  if (cfg.base === 'blur' || cfg.base === 'strokes') {
    const key = `${cfg.base_block}|${cfg.linear}`;
    const make = () => baseLayer(work, h, w, cfg.base_block);
    if (cache) {
      cache.canvas = memo(cache.canvas, key, make);
      canvas = cache.canvas.v;
    } else {
      canvas = make();
    }
  }
  info.timing.canvas = performance.now() - t;

  // The pigment grade, LAST: everything above has already decided where the strokes go and
  // how big they are, so this can only change what colour they are. See oilpaint/palette.py
  // for why that placement is the whole design. `paramsFor` returns null at the defaults
  // and none of this runs.
  //
  // The canvas gets a memo slot of its own rather than being graded in place: `canvas` is
  // the cached one, so writing into it would poison every later render, and re-grading a
  // full-resolution ground on every drag of an unrelated slider is ~0.4 s that the key
  // below spends once. The key carries the canvas's own key as well as the grade, because
  // a graded canvas depends on both.
  t = performance.now();
  let gp = paramsFor(cfg);
  // The reference match rides the SAME grade pass -- see the Python's `plan`. It is fitted
  // here because it is a statistic over ALL the stroke colours, which the chunked grade
  // cannot see; landing it in `gp` also re-keys the canvas memo below for free.
  const rp = referenceParamsFor(cfg);
  info.reference = rp !== null;
  if (rp !== null) {
    gp = gp !== null ? gp : blockFor(cfg);
    gp.xfer = referenceFit(sb.rgb, rp, cfg.linear);
  }
  // Mirrors the Python's `info["xfer"]`; null when no reference ran.
  info.xfer = gp === null ? null : (gp.xfer || null);
  info.palette = gp !== null;
  // The regions: the same grade, aimed one passage at a time. `paramsFor` returns null
  // unless a region actually asks for something different, and then the branch below is
  // exactly the painting this made before regions existed -- not an equivalent one.
  const rspec = regionsMod.paramsFor(cfg, regions);
  let lab = null;
  if (rspec === null) {
    if (gp !== null) {
      sb.rgb = gradeStrokes(sb.rgb, sb.phase, gp, cfg.linear);
      if (canvas) {
        const gkey = `${cfg.base_block}|${cfg.linear}|${JSON.stringify(gp)}`;
        const mk = () => gradeImage(canvas, gp, cfg.linear);
        if (cache) {
          cache.graded = memo(cache.graded, gkey, mk);
          canvas = cache.graded.v;
        } else {
          canvas = mk();
        }
      }
    }
  } else {
    lab = regionsMod.strokeLabels(rspec, sb.x, sb.y);
    const blocks = regionsMod.blocksFor(cfg, rspec, gp, sb.rgb, lab, cfg.linear);
    sb.rgb = regionsMod.applyStrokes(sb.rgb, sb.phase, lab, blocks, cfg.linear);
    if (canvas) {
      // The mask's own key rather than its pixels: the memo exists to avoid re-grading a
      // full-resolution ground on every drag, and hashing a megapixel of labels to
      // discover it did not change would cost more than the grade it saves.
      const gkey = `${cfg.base_block}|${cfg.linear}|r:${rspec.key}|${JSON.stringify(blocks)}`;
      const mk = () => regionsMod.applyImage(canvas, rspec.labels, blocks, cfg.linear);
      if (cache) {
        cache.graded = memo(cache.graded, gkey, mk);
        canvas = cache.graded.v;
      } else {
        canvas = mk();
      }
    }
  }
  // ONE count over BOTH plans: a layer that only re-aims the flow is as live as one that
  // only regrades, and a panel reporting "0 regions" for it would be showing the
  // still-image failure's own symptom. Counted, not assumed: a mask painted at one size
  // against a picture rendered at another catches nothing, and silently produces the
  // undivided painting.
  const rids = regionsMod.liveIds(rspec, frp);
  info.regions = rids.length;
  if (rids.length) {
    if (lab === null) lab = regionsMod.strokeLabels(frp, sb.x, sb.y);
    info.region_strokes = regionsMod.summary(rids, lab);
  }
  info.timing.palette = performance.now() - t;

  return { sb, canvas, info };
}

// A pixel with essentially no paint on it: what shows is the base layer, or bare canvas
// when there is none. See `finish` in the Python for why this is a SEPARATE question from
// `coverage`, and for the measurement.
const BARE = 0.05;

// The parameters `finish` can re-apply to an ALREADY-RASTERISED painting, so changing one
// re-runs the lighting alone rather than the whole render. Mirrors
// `pipeline.RELIGHT_PARAMS` on the Python side, which is where the reasoning lives -- next
// to the `finish` that makes it true, and where tests/test_core.py checks the claim by
// perturbing each name and requiring the pre-lighting buffers to stay bit-identical.
//
// `linear` and `impasto` are deliberately NOT here even though `finish` reads them:
// `linear` also chooses the working colour space back in `plan`, and `impasto` decides
// whether the height field is allocated at all, so neither survives without a re-render.
export const RELIGHT_PARAMS = new Set([
  'impasto_depth', 'light_deg', 'light_elev_deg', 'gloss',
  'canvas_weave', 'occlusion', 'view_deg', 'view_elev_deg',
]);

/**
 * Post-rasterisation: the coverage statistics, the colour-space exit, the lighting.
 *
 * Coverage is measured BEFORE the lighting on purpose -- the bristles cut real holes in
 * the alpha, so the figure the panel prints should fall as `bristle_amp` rises rather than
 * hide behind a change in brightness.
 *
 * Two numbers: `coverage` is a `> 0.99` threshold and therefore moves whenever anything
 * modulates alpha INSIDE a painted pixel, hole or no hole, while `bare` counts the pixels
 * where no layer painted at all. The Python carries the measurement that separates them.
 */
export function finish(out, cover, coverTail, info, cfg, height = null) {
  cfg = Object.assign({}, DEFAULTS, cfg);
  let solid = 0;
  for (let i = 0; i < coverTail.length; i++) if (coverTail[i] > 0.99) solid++;
  info.coverage = solid / coverTail.length;
  solid = 0;
  for (let i = 0; i < cover.length; i++) if (cover[i] > 0.99) solid++;
  info.coverage_all = solid / cover.length;
  // From `cover`, not `coverTail`: the question is whether ANY layer painted here.
  let bare = 0;
  for (let i = 0; i < cover.length; i++) if (cover[i] < BARE) bare++;
  info.bare = bare / cover.length;

  const res = new Float32Array(out.length);
  for (let i = 0; i < out.length; i++) {
    const v = cfg.linear ? linearToSrgb(out[i]) : out[i];
    res[i] = v < 0 ? 0 : v > 1 ? 1 : v;
  }
  if (cfg.impasto && height !== null) {
    return light(res, height, cover, info.h, info.w, {
      depth: cfg.impasto_depth, lightDeg: cfg.light_deg, elevDeg: cfg.light_elev_deg,
      gloss: cfg.gloss, canvasWeave: cfg.canvas_weave,
      occlusion: cfg.occlusion, viewDeg: cfg.view_deg,
      viewElevDeg: cfg.view_elev_deg,
    });
  }
  return res;
}

/** Run the whole pipeline. Returns {out, info, sb}. */
export function paint(rgb, h, w, cfg, cache = null, foveal = null, vortices = null,
                      regions = null) {
  cfg = Object.assign({}, DEFAULTS, cfg);
  const { sb, canvas, info } = plan(rgb, h, w, cfg, cache, foveal, null, vortices, regions);
  const t = performance.now();
  const { out, cover, coverTail, height } = render(sb, h, w, {
    hard: cfg.hard, hardR: cfg.hard_r, wobbleAmp: cfg.wobble_amp,
    canvas, splitAt: info.n_under,
    bristleAmp: cfg.bristle_amp, taperAmp: cfg.taper_amp,
    fringePx: cfg.fringe_px, wantHeight: cfg.impasto, impastoRelief: cfg.impasto_relief,
    impastoLayer: cfg.impasto_layer,
    // No base layer means the gaps ARE bare canvas, and should read as holes.
    ground: cfg.base === 'none' ? 0.0 : groundHeight(cfg.impasto_layer),
  });
  info.timing.render = performance.now() - t;
  return { out: finish(out, cover, coverTail, info, cfg, height), info, sb };
}
