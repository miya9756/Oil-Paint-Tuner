// Region-wise look -- the transliteration of oilpaint/regions.py.
//
// The Python carries the reasoning: colour is decided per STROKE and a stroke knows where
// it is, so dividing the picture into passages costs one array lookup per stroke rather
// than a pass over the canvas. Read that file first; this one is the same arithmetic in a
// language with one number type.
//
// A region says two kinds of thing and they attach at two different seams: the COLOUR
// fields to the grade at the end of `plan`, and the FLOW fields to step 3 of
// `fromCells`, where they move that passage's own strokes and nothing else. `paramsFor`
// and `flowParamsFor` are the two halves; each returns null when no region asks for it.
//
// Two things here are parity decisions rather than style:
//
//   * `Math.floor` on the stroke centre, mirroring the Python's `np.floor`. `Math.round`
//     rounds half UP and numpy's rounds half to EVEN, and on a boundary that is not a
//     one-ulp disagreement -- it is a stroke graded as part of the wrong passage.
//   * every loop walks the region ids in ASCENDING NUMERIC order, because `Object.keys`
//     on a numeric-keyed object is insertion-ordered for anything above an array index and
//     `sort()` is lexicographic ('10' < '2'). The blocks are independent so the order
//     cannot change a colour, but the reference fits inside `blocksFor` read the buffer
//     and a suite that compares them one at a time would fail on the ordering alone.
//
// The gather/scatter is what makes this a placement of `palette.grade` rather than a copy:
// every region is graded by the same routine with the same block shape, so there is no
// second implementation of the grade to hold in parity -- only the labelling.

import { centresFor, paramsFor as flowParamsForCfg, vortexMap } from './flow.js';
import { blockFor, gradeImage, gradeStrokes, paramsFor as paletteParamsFor } from './palette.js';
import { fit as referenceFit, paramsFor as referenceParamsFor } from './reference.js';

export const MAX_REGIONS = 8;

// The corners of the RGB cube plus black, in the Python's order. Region 0 is black: an
// unpainted mask asks for nothing, exactly as an unpainted foveal map does.
export const LEGEND = [
  [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
  [1.0, 1.0, 0.0], [1.0, 0.0, 1.0], [0.0, 1.0, 1.0], [1.0, 1.0, 1.0],
];

// Mirrors `regions.REGION_COLOR_PARAMS` / `REGION_FLOW_PARAMS` / `REGION_PARAMS`.
// tests/test_core.py checks the Python's sets against the pipeline; the parity suite checks
// these against the Python's, so a field added on one side and forgotten on the other is a
// failure rather than a browser that quietly ignores a control the CLI honours.
export const REGION_COLOR_PARAMS = new Set([
  'palette', 'palette_strength', 'warm_cool', 'chroma', 'value_compress',
  'broken_color', 'pigment',
  'hue_target', 'hue_range', 'hue_rotate', 'hue_boost',
  'reference', 'reference_strength',
]);

export const REGION_FLOW_PARAMS = new Set([
  'flow', 'flow_strength', 'flow_scale', 'flow_rot', 'flow_coh', 'flow_drift',
]);

export const REGION_PARAMS = new Set([...REGION_COLOR_PARAMS, ...REGION_FLOW_PARAMS]);

/** Region ids in ascending NUMERIC order -- see the header for why not `Object.keys`. */
function ids(obj) {
  return Object.keys(obj).map(Number).sort((a, b) => a - b);
}

/**
 * An (h*w*3) mask in [0,1] -> a Uint8Array of region ids, by nearest legend colour.
 *
 * A parser, not part of the transform: the parity boundary is the label array, so this
 * exists to read a mask someone painted in an image editor rather than to agree with the
 * Python bit for bit. It does anyway -- the legend is cube corners and nothing is close.
 */
export function labelsFromImage(rgb, h, w) {
  const out = new Uint8Array(h * w);
  for (let i = 0; i < h * w; i++) {
    const r = rgb[3 * i], g = rgb[3 * i + 1], b = rgb[3 * i + 2];
    let best = 0, bd = Infinity;
    for (let k = 0; k < LEGEND.length; k++) {
      const dr = r - LEGEND[k][0], dg = g - LEGEND[k][1], db = b - LEGEND[k][2];
      const d = dr * dr + dg * dg + db * db;
      if (d < bd) { bd = d; best = k; }
    }
    out[i] = best;
  }
  return out;
}

/**
 * Validate the whole override set, then keep only the fields in `keep`. Null when empty.
 *
 * The validation is over the FULL set on both calls, never over `keep`: a field this
 * control cannot honour has to be REFUSED wherever it is named, or it falls between the
 * two filters and is silently dropped by both halves. See the Python.
 */
function live(regions, keep) {
  if (!regions || !regions.labels) return null;
  const overrides = regions.overrides || {};
  const out = {};
  let any = false;
  for (const rid of ids(overrides)) {
    if (!(rid >= 0 && rid < MAX_REGIONS)) {
      throw new Error(`region id ${rid} outside 0..${MAX_REGIONS - 1}`);
    }
    const ov = overrides[rid] || {};
    const bad = Object.keys(ov).filter(k => !REGION_PARAMS.has(k)).sort();
    if (bad.length) {
      throw new Error(`region ${rid} cannot set ${JSON.stringify(bad)}; `
                      + 'a region may only set colour and flow');
    }
    // An override that says nothing about THIS half is not a region for this half -- what
    // makes clearing a region on the page restore the base painting exactly rather than a
    // copy of it, and what keeps a flow-only layer out of the grade entirely.
    const sub = {};
    let n = 0;
    for (const k of Object.keys(ov)) if (keep.has(k)) { sub[k] = ov[k]; n++; }
    if (n) { out[rid] = sub; any = true; }
  }
  return any ? out : null;
}

/**
 * The region COLOUR plan for a config, or null when no region asks for a colour.
 *
 * `regions` is `{labels, h, w, overrides, key}`. `key` is the worker's own cache handle for
 * the mask -- the canvas memo needs to re-key when the mask changes, and hashing a
 * megapixel of labels on every slider drag to discover that it did not is exactly the cost
 * the memo exists to avoid.
 */
export function paramsFor(cfg, regions) {
  const ov = live(regions, REGION_COLOR_PARAMS);
  if (ov === null) return null;
  return { labels: regions.labels, h: regions.h, w: regions.w, overrides: ov,
           key: regions.key || '' };
}

/**
 * The region FLOW plan, or null when no region asks for a flow of its own.
 *
 * `{labels, h, w, blocks, key}` -- one block straight out of `flow.paramsFor`, so a
 * region's dropdown and its five trims read exactly as the base's do. A block of null is a
 * region that asked for no flow over a base that has one (the figure left alone under a
 * swirling sky), served by SKIPPING those strokes rather than by blending an identity into
 * them. Needed BEFORE the strokes exist, unlike the grade: what it changes is where they
 * point. `vortices` takes either shape `flow.vortexMap` accepts, and each region gets the
 * set `flow.centresFor` says it uses -- its own, or the base's inherited.
 */
export function flowParamsFor(cfg, regions, vortices = null) {
  const ov = live(regions, REGION_FLOW_PARAMS);
  if (ov === null) return null;
  const vmap = vortexMap(vortices);
  const blocks = {};
  for (const rid of ids(ov)) {
    blocks[rid] = flowParamsForCfg(Object.assign({}, cfg, ov[rid]), centresFor(vmap, rid));
  }
  return { labels: regions.labels, h: regions.h, w: regions.w, blocks,
           key: regions.key || '' };
}

/**
 * The same flow plan with every region's `drift` off -- what the underpainting gets.
 *
 * Mirrors what `plan` already does to the base block: that layer's job is to COVER, and
 * drift bunches strokes along the flow lines and opens gaps between them.
 */
export function flowUndrifted(spec) {
  if (spec === null || spec === undefined) return null;
  const blocks = {};
  for (const rid of ids(spec.blocks)) {
    const p = spec.blocks[rid];
    blocks[rid] = p === null ? null : Object.assign({}, p, { drift: 0.0 });
  }
  return Object.assign({}, spec, { blocks });
}

/**
 * The regions live in ANY of the given plans -- what the info dict counts and reports.
 *
 * Two plans, one number: a layer that only re-aims the flow is as live as one that only
 * regrades, and a panel saying "0 regions" for it would be reporting the still-image
 * failure's own symptom at exactly the wrong moment.
 */
export function liveIds(...specs) {
  const seen = new Set();
  for (const s of specs) {
    if (!s) continue;
    for (const rid of ids(s.overrides || s.blocks)) seen.add(rid);
  }
  return [...seen].sort((a, b) => a - b);
}

/** The region id under each stroke centre. Truncation, not rounding -- see the header. */
export function strokeLabels(spec, x, y) {
  const { labels, h, w } = spec;
  const n = x.length;
  const out = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    let xi = Math.floor(x[i]); if (xi < 0) xi = 0; else if (xi > w - 1) xi = w - 1;
    let yi = Math.floor(y[i]); if (yi < 0) yi = 0; else if (yi > h - 1) yi = h - 1;
    out[i] = labels[yi * w + xi];
  }
  return out;
}

/**
 * `{region id: grade block or null}` for the base and every live region.
 *
 * Built once and used for both the strokes and the ground under them, because a block can
 * carry a fitted transport and two fits against two different sets of colours would grade
 * the canvas toward a different painting than the strokes on it. The transport is fitted
 * over the colours it will be applied to; a region that does not name its own reference
 * inherits the base's already-fitted one, which is also what keeps the equivalence
 * invariant (every region set to the base's values repaints the base's painting) exact.
 */
export function blocksFor(cfg, spec, gp, rgb, lab, linear = false) {
  const baseXfer = gp === null ? null : (gp.xfer || null);
  const blocks = { 0: gp };
  for (const rid of ids(spec.overrides)) {
    const ov = spec.overrides[rid];
    const sub = Object.assign({}, cfg, ov);
    let g = paletteParamsFor(sub);
    const rp = referenceParamsFor(sub);
    if (rp !== null) {
      g = g !== null ? g : blockFor(sub);
      if ('reference' in ov || 'reference_strength' in ov) {
        const sel = gather(rgb, lab, rid, 3);
        // A region nobody painted has no strokes to fit against, and a transport fitted on
        // an empty set is a singular matrix.
        g = sel.length === 0 ? null
          : Object.assign({}, g, { xfer: referenceFit(sel, rp, linear) });
      } else {
        g = Object.assign({}, g, { xfer: baseXfer });
      }
    }
    blocks[rid] = g;
  }
  return blocks;
}

/** The rows of `src` whose label is `rid`, in ascending index order. `stride` values each. */
function gather(src, lab, rid, stride) {
  let n = 0;
  for (let i = 0; i < lab.length; i++) if (lab[i] === rid) n++;
  const out = new Float32Array(n * stride);
  let j = 0;
  for (let i = 0; i < lab.length; i++) {
    if (lab[i] !== rid) continue;
    for (let k = 0; k < stride; k++) out[j * stride + k] = src[i * stride + k];
    j++;
  }
  return out;
}

/** Grade an (n*3) stroke buffer region by region. Returns a new Float32Array. */
export function applyStrokes(rgb, phase, lab, blocks, linear = false) {
  const out = Float32Array.from(rgb);
  for (const rid of ids(blocks)) {
    const gp = blocks[rid];
    if (gp === null || gp === undefined) continue;
    const idx = [];
    for (let i = 0; i < lab.length; i++) if (lab[i] === rid) idx.push(i);
    if (!idx.length) continue;
    const sub = new Float32Array(idx.length * 3);
    const ph = new Float32Array(idx.length);
    for (let j = 0; j < idx.length; j++) {
      sub[3 * j] = rgb[3 * idx[j]];
      sub[3 * j + 1] = rgb[3 * idx[j] + 1];
      sub[3 * j + 2] = rgb[3 * idx[j] + 2];
      ph[j] = phase[idx[j]];
    }
    const g = gradeStrokes(sub, ph, gp, linear);
    for (let j = 0; j < idx.length; j++) {
      out[3 * idx[j]] = g[3 * j];
      out[3 * idx[j] + 1] = g[3 * j + 1];
      out[3 * idx[j] + 2] = g[3 * j + 2];
    }
  }
  return out;
}

/**
 * The same for the (h*w*3) base canvas: per pixel, and neither per-dab effect.
 *
 * The ground is graded region by region because it shows through the ~12% the strokes do
 * not cover, and a warm ground under a cool sky is visible exactly where it should not be.
 */
export function applyImage(img, labels, blocks, linear = false) {
  const out = Float32Array.from(img);
  for (const rid of ids(blocks)) {
    const gp = blocks[rid];
    if (gp === null || gp === undefined) continue;
    const idx = [];
    for (let i = 0; i < labels.length; i++) if (labels[i] === rid) idx.push(i);
    if (!idx.length) continue;
    const sub = new Float32Array(idx.length * 3);
    for (let j = 0; j < idx.length; j++) {
      sub[3 * j] = img[3 * idx[j]];
      sub[3 * j + 1] = img[3 * idx[j] + 1];
      sub[3 * j + 2] = img[3 * idx[j] + 2];
    }
    const g = gradeImage(sub, gp, linear);
    for (let j = 0; j < idx.length; j++) {
      out[3 * idx[j]] = g[3 * j];
      out[3 * idx[j] + 1] = g[3 * j + 1];
      out[3 * idx[j] + 2] = g[3 * j + 2];
    }
  }
  return out;
}

/**
 * `{region id: stroke count}` -- what the mask actually caught.
 *
 * A region with zero strokes is the failure this reports: a mask painted at one size
 * against a picture rendered at another catches nothing, and produces the ungraded
 * painting while looking like it worked. Counted rather than assumed.
 *
 * `ids` rather than a plan, because there are two plans and a region is live if it is in
 * either; `lab` is the colour labelling, off the strokes' final centres.
 */
export function summary(rids, lab) {
  const out = {};
  for (const rid of rids) {
    let n = 0;
    for (let i = 0; i < lab.length; i++) if (lab[i] === rid) n++;
    out[rid] = n;
  }
  return out;
}
