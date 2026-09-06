// The compute engine, off the main thread.
//
// The whole pipeline is JS now: `oilpaint/*.js` mirrors `oilpaint/*.py` file for file, and
// tests/test_pipeline_parity.py holds every stage to the Python -- exactly to the leaf, the
// stroke order and the pigment, and to a named float32 bound on the geometry. The seed in
// the control panel means the same painting on both sides, because nprandom.js reproduces
// numpy's PCG64 and ziggurat bit for bit (tests/test_rng_parity.py).
//
// It is a worker rather than inline script for one specific reason: a render is a few
// hundred ms of straight-line compute, and on the main thread that freezes the page --
// including the busy pill's clock, which the page's own notes call the load-bearing part of
// the indicator ("a bare spinner cannot be told from a freeze, whereas a number that keeps
// moving is proof the thing is alive"). Here the sliders stay smooth while paint computes.
//
// There is no network dependency and no runtime download. Everything below is served from
// the same directory as the page.

import { RELIGHT_PARAMS, finish, makeCache, plan, tensorFor } from './oilpaint/pipeline.js';
import { linearToSrgb } from './oilpaint/image.js';
import { groundHeight, render } from './oilpaint/render.js';
import { edgeAlignment, psnr } from './oilpaint/metrics.js';
import { PIGMENTS } from './oilpaint/palette.js';
import { LEGEND as REGION_LEGEND, MAX_REGIONS, REGION_PARAMS } from './oilpaint/regions.js';
import { FLOW_KINDS, MAX_VORTICES } from './oilpaint/flow.js';

let schema = null;

// ---- showing the work ---------------------------------------------------------------
// A render is seconds of straight-line compute and the only thing the page could show for
// it was a spinner and a clock. The clock proves the tool is alive, which is not the same
// as being worth watching -- and the pipeline happens to build the picture in exactly the
// order a painter would: a blurred ground, then the coarse underpainting, then the detail
// strokes, then the light. So the intermediate states go to the page as they happen.
//
// Every frame below is a REAL pipeline product at that moment, never an illustration of
// one. The one thing they do not carry is the lighting pass, which is ~0.7 s at 1100 px --
// far too expensive to run per frame. On flat paint it is close to neutral by construction
// (see `light`: a flat surface has ndl == lz, so shade == 1), so what arrives with the
// finished image is the relief and the specular, which is the thing worth waiting for.
const PREVIEW_AFTER_MS = 600;   // a render that was quick anyway must not flash a frame
const PREVIEW_EVERY_MS = 180;   // ~5 frames a second while the strokes go down

// The longest side an intermediate frame is sent at. The page draws #prog with
// `width:100%;height:100%` into a pane that is at most ~1100 device px, so anything past
// this is converted, copied, transferred and then thrown away by the scaler.
//
// It is "full res" that makes this matter rather than a nicety: frame cost scales with
// AREA while PREVIEW_EVERY_MS is a fixed interval, so on a 12.2 Mpx canvas one frame was
// 48.8 MB and 212 ms of conversion against a 180 ms budget -- the animation was asking for
// more than 100% of the worker and the strokes went down in whatever was left. At 1024 the
// same frame is 0.76 Mpx and ~13 ms.
const PREVIEW_MAX_SIDE = 1024;

// ...and a backstop that does not depend on having guessed the cap right: never start a
// frame until PREVIEW_BUDGET times its own last cost has elapsed, so the previews can take
// at most 1/PREVIEW_BUDGET of the render no matter how large the canvas or how slow the
// machine. The cap above is what keeps this from throttling to a crawl; this is what keeps
// a wrong cap from ever costing the render again.
const PREVIEW_BUDGET = 4;

/**
 * Float RGB in [0,1] -> the RGBA bytes a canvas draws.
 *
 * The same `trunc(x * 255 + 0.5)` that `image.save_image` writes to disk, so a preview and
 * the finished PNG quantise identically and the handover is not a visible shift. `linear`
 * is for the mid-render frames only: those come from the rasteriser's own buffer, which is
 * still in the working colour space, whereas `finish` has already left it.
 */
function toRgba(rgb, n, linear) {
  const px = new Uint8ClampedArray(n * 4);
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < 3; c++) {
      let v = rgb[3 * i + c];
      if (linear) v = linearToSrgb(v);
      px[4 * i + c] = Math.trunc((v < 0 ? 0 : v > 1 ? 1 : v) * 255.0 + 0.5);
    }
    px[4 * i + 3] = 255;
  }
  return px;
}

/**
 * The same conversion for an INTERMEDIATE frame, subsampled to at most `maxSide`.
 *
 * Nearest, and deliberately: the cost is then proportional to the OUTPUT, which is the
 * whole point -- a box filter would have to read every source pixel and so would keep the
 * cost proportional to the canvas. This frame is a progress indicator drawn into a pane a
 * few hundred pixels wide, not the painting; the painting is sent whole, by `done`.
 *
 * Returns {px, w, h} -- the dimensions come back because they are no longer the canvas's.
 */
function toRgbaPreview(rgb, w, h, linear, maxSide) {
  const step = Math.max(1, Math.ceil(Math.max(w, h) / maxSide));
  if (step === 1) return { px: toRgba(rgb, w * h, linear), w, h };
  const pw = Math.ceil(w / step), ph = Math.ceil(h / step);
  const px = new Uint8ClampedArray(pw * ph * 4);
  for (let y = 0, o = 0; y < ph; y++) {
    const srow = Math.min(y * step, h - 1) * w;
    for (let x = 0; x < pw; x++, o += 4) {
      const p = (srow + Math.min(x * step, w - 1)) * 3;
      for (let c = 0; c < 3; c++) {
        let v = rgb[p + c];
        if (linear) v = linearToSrgb(v);
        px[o + c] = Math.trunc((v < 0 ? 0 : v > 1 ? 1 : v) * 255.0 + 0.5);
      }
      px[o + 3] = 255;
    }
  }
  return { px, w: pw, h: ph };
}

/**
 * The channel back to the page for one render.
 *
 * Two different things, gated differently on purpose. `stage` is a string and costs
 * nothing, so it is always sent and the PAGE decides when a name is worth showing. `frame`
 * converts and copies the whole image, so the cost gate lives here: nothing at all until
 * the render has proven slow, and then at most one frame every PREVIEW_EVERY_MS.
 */
function reporter(id, w, h, linear) {
  const t0 = performance.now();
  let last = 0;
  let cost = 0;                 // what the last frame actually took, in ms
  return {
    under: 0,
    stage(step) { self.postMessage({ id, type: 'progress', step }); },
    frame(rgb, done, total, force = false) {
      const now = performance.now();
      if (now - t0 < PREVIEW_AFTER_MS) return;
      // Whichever gate is slower wins: the fixed cadence on a small canvas, the measured
      // one on a large canvas or a slow machine. `force` skips the cadence, not the cost.
      if (!force && now - last < Math.max(PREVIEW_EVERY_MS, PREVIEW_BUDGET * cost)) return;
      const t = performance.now();
      const f = toRgbaPreview(rgb, w, h, linear, PREVIEW_MAX_SIDE);
      const buf = f.px.buffer;
      self.postMessage({ id, type: 'preview', pixels: buf, w: f.w, h: f.h, done, total,
                         under: this.under }, [buf]);
      cost = performance.now() - t;
      last = performance.now();
    },
  };
}

// One cache per (source image, render size). The detail field, the tau search, the
// structure tensor and the base canvas depend on almost none of the sliders, so re-deriving
// them on every drag was over half the render. `key` is the main thread's image serial plus
// the render size -- nothing else identifies the pixels, so anything that changes them must
// change the key.
//
// TWO slots, not one, and that is the whole point of the Map. The page renders a 360 px
// draft, then a full-width refine, then the next draft: with a single slot that rhythm
// evicts on EVERY render, so the cache never once hit and `plan` paid cold price throughout.
// Measured over draft/full/draft/full/draft/full at 1100 px: 7.48 s with one slot, 2.44 s
// with two, the full-width plan falling from ~2.1 s to ~0.1 s once it stops being evicted.
// That is also what makes cancellation worth having -- `plan` is a synchronous prefix no
// yield can interrupt, so a cold one put a 2 s floor under any "stop and use the new
// config". Two is the smallest number that covers the alternation, and it is a CAP rather
// than a growing pool because the full-res tables run to hundreds of MB.
const MAX_CACHES = 2;
const caches = new Map();   // insertion-ordered, so the first key is the least recent

function cacheFor(key) {
  let c = caches.get(key);
  if (c) caches.delete(key);            // re-insert, so `key` becomes the most recent
  else c = makeCache();
  caches.set(key, c);
  while (caches.size > MAX_CACHES) caches.delete(caches.keys().next().value);
  return c;
}

// The foveal map, converted once per (serial, size). The page sends the mask as one byte
// per pixel at the render size and bumps `maskKey` on every brush stroke; the pipeline
// memoises its summed-area table and mip pyramid against the `key` below, so a mask that
// changed WITHOUT its serial changing would be served a stale table. That contract is the
// page's to keep, and it is why the serial and not the buffer is what identifies a map.
let maskCache = null, maskCacheKey = null;

// ---- stopping a render that is no longer wanted --------------------------------------
// A worker running straight-line code NEVER reaches its own message queue: the page can
// post "stop" a hundred times and not one of them is seen until the loop ends. That is why
// changing a slider used to mean waiting out the render you had already abandoned, and it
// is why the rasteriser is now drawn in slices -- the yield between them is the only moment
// this thread has to look at what has arrived.
//
// `generation` is bumped by every request that lands. A paint parked at a yield compares
// its own number against it and unwinds if it has been superseded; the newest request then
// runs. Every request gets exactly one terminal message -- done, error or cancelled -- or
// the page would sit for ever on a promise nobody settles.
const ABORT = Symbol('superseded');
let generation = 0;   // bumped per request; a paint aborts when it is no longer the newest
let running = false;  // a paint is on the stack, possibly parked at a yield
let pending = null;   // the newest request waiting to run; an older waiting one is dropped

// Slices are sized by TIME, not by stroke count: a stroke covers anywhere from a dozen
// pixels to a few thousand, so a fixed count is either a stutter on big brushes or all
// overhead on small ones. Aim for SLICE_MS of work, then re-estimate from what it cost.
const SLICE_MS = 50;

// A macrotask, not a microtask. `await Promise.resolve()` drains the microtask queue and
// comes straight back without the event loop ever picking up a task, so a `message` event
// -- which IS a task -- would never get its turn and nothing would be cancellable. A
// MessageChannel hop is the cheapest way to reach the task queue; `setTimeout(0)` also
// works but is clamped to 4 ms once timers nest, which against a 50 ms slice is an 8% tax
// on every render for nothing.
// A fresh channel per yield, closed on the way out, rather than one held for the worker's
// whole life: a long-lived port is an open handle, harmless in a browser but enough to stop
// `node` ever exiting in the parity harness -- which is how this was found. The allocation
// is nothing against a 50 ms slice, and the yield then owns no state at all.
function yieldToEvents() {
  return new Promise(resolve => {
    const ch = new MessageChannel();
    ch.port1.onmessage = () => {             // assigning onmessage starts the port
      ch.port1.close();
      ch.port2.close();
      resolve();
    };
    ch.port2.postMessage(0);
  });
}

function fovealFor(mask, maskKey, n) {
  if (!mask) return null;
  const key = `${maskKey}|${n}`;
  if (key !== maskCacheKey) {
    const u8 = new Uint8Array(mask);
    const m = new Float32Array(n);
    for (let i = 0; i < n; i++) m[i] = u8[i] / 255.0;
    maskCache = { mask: m, key };
    maskCacheKey = key;
  }
  return maskCache;
}
// The region mask, on the same contract as the foveal map above: the page sends one byte
// per pixel at the render size plus a serial, and the serial is what identifies it. The
// bytes are the LABELS themselves rather than a weight, so there is nothing to convert --
// what this adds is the `key` the graded ground's memo slot re-keys on. Hashing the labels
// instead would cost more per render than the grade the memo exists to save.
//
// `overrides` rides along as plain JSON: a handful of colour fields per region, small
// enough that a key and a transfer would both be overhead.
function regionsFor(labels, key, overrides, h, w) {
  if (!labels || !overrides || !Object.keys(overrides).length) return null;
  return { labels: new Uint8Array(labels), h, w, overrides,
           key: `${key}|${w}x${h}` };
}


// --- the relight fast path -------------------------------------------------------------
// Moving the LIGHT changes nothing the rasteriser produced: `finish` runs on the height
// field it already built. Measured 42x cheaper -- 0.056 s against 2.311 s at 640px -- which
// is the difference between watching a light sweep and batch-rendering one.
//
// So the last render's pre-lighting buffers are kept, keyed on everything that could have
// changed them. `oilpaint/pipeline.js` holds the set of parameters that CANNOT, mirroring
// `pipeline.RELIGHT_PARAMS` where the reasoning lives; tests/test_core.py checks that set
// against the pipeline rather than trusting it, because a stale entry here means frames
// that should differ and silently do not.
//
// `finish` allocates its result rather than writing into `out`, so the cached buffers are
// safe to serve repeatedly. That is a property of pipeline.js, not an accident of this file.
let relit = null;

/** Everything about a job that could change what the RASTERISER produced. */
function relightKey(job) {
  const p = {};
  for (const k of Object.keys(job.params).sort()) {
    if (!RELIGHT_PARAMS.has(k)) p[k] = job.params[k];
  }
  return JSON.stringify([job.imgKey, job.w, job.h, job.maskKey || null,
                         job.vortices || null, job.regionKey || null,
                         job.regionOverrides || null, p]);
}

/** A frame from the cached buffers. Only the lighting is recomputed. */
function relightFrom(job, t0) {
  const c = relit;
  const info = Object.assign({}, c.info);
  const painting = finish(c.out, c.cover, c.coverTail, info, job.params, c.height);
  // psnr is the one statistic the lighting really moves, so it is recomputed; everything
  // else describes the strokes, which did not change, and is served from the render.
  const stats = Object.assign({}, c.stats, {
    psnr: round(psnr(painting, c.rgb), 2),
    seconds: round((performance.now() - t0) / 1000, 3),
    relit: true,
  });
  return { pixels: toRgba(painting, c.w * c.h, false), w: c.w, h: c.h, stats };
}

async function paint(job, mine) {
  const { id, w, h, params, imgKey, mask, maskKey, vortices,
          regionLabels, regionKey, regionOverrides } = job;
  const t0relight = performance.now();
  // A relight that HITS never touches the rasteriser. A relight that misses -- a different
  // image, size, mask or geometry parameter -- falls through and renders normally, which
  // also refills the cache, so the caller never has to know which it got.
  if (job.type === 'relight' && relit && relit.key === relightKey(job)) {
    return relightFrom(job, t0relight);
  }
  const rgba = new Uint8ClampedArray(job.rgba);
  const report = reporter(id, w, h, !!params.linear);
  // Unwind if a newer request has landed. Only ever called just after a yield -- before
  // one, `generation` cannot have moved, because nothing else has had a turn to move it.
  const check = () => { if (mine !== generation) throw ABORT; };
  const t0 = performance.now();
  const cache = cacheFor(`${imgKey}|${w}x${h}`);

  // The canvas hands over RGBA bytes; the pipeline wants float32 RGB in [0,1]. This is the
  // same conversion `image.load_image` does on the Python side, minus the file decode.
  const n = w * h;
  const rgb = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    rgb[3 * i] = rgba[4 * i] / 255.0;
    rgb[3 * i + 1] = rgba[4 * i + 1] / 255.0;
    rgb[3 * i + 2] = rgba[4 * i + 2] / 255.0;
  }

  const foveal = fovealFor(mask, maskKey, n);
  const regions = regionsFor(regionLabels, regionKey, regionOverrides, h, w);
  // The placed swirl centres ride the render message as plain [[fx, fy], ...] fractions --
  // no key and no memo slot, unlike the foveal mask, because nothing derived from them is
  // cached: `fromCells` is recomputed on every plan, so a moved vortex cannot be served a
  // stale field.
  const { sb, canvas, info } = plan(rgb, h, w, params, cache, foveal,
                                    step => report.stage(step), vortices || null,
                                    regions);
  report.under = info.n_under;
  // The first thing there is to look at, and on a cold render the first pixels to exist at
  // all: the blurred ground every stroke goes on top of.
  if (canvas) report.frame(canvas, 0, sb.x.length, true);
  report.stage('painting');
  // Whether the plan was worth finishing is settled here rather than after the rasteriser:
  // it is the last cheap moment before seconds of compositing.
  await yieldToEvents();
  check();

  const nStrokes = sb.x.length;
  const ropts = {
    hard: params.hard,
    hardR: params.hard_r,
    wobbleAmp: params.wobble_amp,
    canvas,
    splitAt: info.n_under,
    bristleAmp: params.bristle_amp,
    taperAmp: params.taper_amp,
    fringePx: params.fringe_px,
    wantHeight: params.impasto,
    impastoRelief: params.impasto_relief,
    impastoLayer: params.impasto_layer,
    ground: params.base === 'none' ? 0.0 : groundHeight(params.impasto_layer),
    onProgress: (done, total, live) => report.frame(live, done, total),
  };

  // The composite, in slices, yielding between them. One call up front with an empty range
  // allocates the buffers and lays the base canvas in; every call after it continues into
  // what that returned. See render.js on why splitting the fold changes nothing.
  const buf = render(sb, h, w, Object.assign({}, ropts, { from: 0, to: 0 }));
  let step = 64;
  for (let from = 0; from < nStrokes; ) {
    const to = Math.min(nStrokes, from + step);
    const t = performance.now();
    render(sb, h, w, Object.assign({}, ropts, { into: buf, from, to }));
    const dt = performance.now() - t;
    // Re-aim at SLICE_MS from what this slice actually cost, and never let the estimate
    // collapse to a handful of strokes (all yield, no work) or run away past a few
    // thousand (a slice long enough to be the stutter the yields exist to prevent).
    step = Math.max(16, Math.min(4096,
      Math.round(step * SLICE_MS / Math.max(dt, 0.05))));
    from = to;
    await yieldToEvents();
    check();
  }
  const { out, cover, coverTail, height } = buf;
  if (params.impasto) {
    // Every stroke down, no light on it yet. Worth a frame of its own: the lighting pass is
    // ~0.7 s at 1100 px, and without this the page would sit for all of it on a preview
    // that is up to one cadence short of the finished strokes.
    report.frame(out, nStrokes, nStrokes, true);
    report.stage('lighting');
    // The lighting pass is ~0.7 s at 1100 px and has no seam to yield at, so the decision
    // to spend it is taken here, once, rather than discovered afterwards.
    await yieldToEvents();
    check();
  }
  const painting = finish(out, cover, coverTail, info, params, height);

  // Rounded here rather than on the main thread so the pixels the page shows go through the
  // same `(x*255 + 0.5)` truncation that `image.save_image` writes to disk. `finish` has
  // already left the working colour space and clamped, hence `linear` false.
  const pixels = toRgba(painting, n, false);

  const stats = {
    strokes: info.n_detail,
    underpainting: info.n_under,
    coverage_detail: round(info.coverage, 3),
    coverage_all: round(info.coverage_all, 3),
    bare: round(info.bare, 4),
    psnr: round(psnr(painting, rgb), 2),
    edge_alignment: round(edgeAlignment(sb, rgb, h, w, 2.0,
                                       tensorFor(rgb, h, w, 2.0, cache)), 3),
    budget_reached: info.budget_reachable === undefined ? true : info.budget_reachable,
    foveal: !!info.foveal,
    palette: !!info.palette,
    regions: info.regions || 0,
    region_strokes: info.region_strokes || null,
    flow: !!info.flow,
    // How many LAYERS carry a field of their own. Reported separately from `flow` because
    // a painting whose base is 'none' and whose sky is 'starry' has `flow` false and swirls
    // in it, and the panel's one line about the field would otherwise go silent there.
    flow_regions: info.flow_regions || 0,
    vortices: info.flow_vortices || 0,
    ceiling: info.stroke_ceiling || 0,
    tau: info.tau,
    seconds: round((performance.now() - t0) / 1000, 2),
    size: `${w}x${h}`,
  };
  // Keep the pre-lighting buffers for the relight path. `finish` allocated its own result
  // rather than writing into `out`, so these can be served again as they stand.
  relit = { key: relightKey(job), out, cover, coverTail, height,
            info: Object.assign({}, info), rgb, w, h, stats };
  return { pixels, w, h, stats };
}

const round = (v, d) => Number(v.toFixed(d));

async function handleInit(id) {
  // The control surface is generated from `oilpaint/schema.py` at build time -- the same
  // module whose import asserts it and PaintConfig agree. Shipping it as data rather than
  // restating it in JS is what stops the panel drifting from the config.
  if (!schema) {
    // A 404 is not a fetch failure, and `.json()` on the error body parses happily -- so
    // without this check a missing schema.json arrives as a plausible-looking object and
    // the page hangs building a panel out of it.
    const res = await fetch('./schema.json');
    if (!res.ok) throw new Error(`schema.json: HTTP ${res.status}. The page must be `
      + `served from a built tree (python web/tune/build_static.py --serve).`);
    schema = await res.json();
  }
  // schema.json is `{schema: [...], defaults: {...}}`; the page wants the two apart.
  // Posting the document under `schema` gave the panel a non-iterable object and an
  // undefined defaults, which threw inside boot() and left the page on "starting the
  // engine..." for good.
  // The pigment sets ride along with the schema. They are not tunable -- there is no
  // control for them -- but the page draws them as swatches under the palette dropdown, and
  // sending them beats restating them there: a swatch strip that disagreed with the
  // pigments actually doing the mixing would be worse than no strip at all.
  // The region tool's three facts ride along for the same reason: how many regions there
  // are, what colour each one is painted in a mask, and WHICH FIELDS a region may carry.
  // The last is the one that matters -- a page with its own list would offer a capture
  // the engine then refuses, or quietly drop one the CLI honours.
  // `flowKinds` rides along for the same reason: the swirl-centre tool has something to do
  // only for a swirl-kind preset, and the page must not be the place that decides which
  // those are. `maxVortices` too -- the cap is the engine's, so the page enforces the
  // engine's number rather than a copy of it that could drift.
  self.postMessage({ id, type: 'ready', schema: schema.schema,
                     defaults: schema.defaults, pigments: PIGMENTS,
                     flowKinds: FLOW_KINDS, maxVortices: MAX_VORTICES,
                     maxRegions: MAX_REGIONS, regionLegend: REGION_LEGEND,
                     regionParams: [...REGION_PARAMS].sort() });
}

self.onmessage = async (e) => {
  const msg = e.data;
  if (msg.type === 'init') {
    try {
      await handleInit(msg.id);
    } catch (err) {
      self.postMessage({ id: msg.id, type: 'error',
                         message: String((err && err.message) || err) });
    }
    return;
  }
  if (msg.type === 'cancel') {
    // Nothing wants the current render any more -- the page cleared the image. The same
    // mechanism as supersession, minus a replacement to run: without it the engine would
    // go on painting a picture with nowhere to be shown, for as long as it takes.
    generation++;
    if (pending) {
      self.postMessage({ id: pending.id, type: 'cancelled' });
      pending = null;
    }
    return;
  }
  // 'relight' is a render that may be able to skip the rasteriser; it goes down the same
  // supersession path as any other, because a page dragging a light slider produces exactly
  // the same flood of superseded requests a page dragging any other one does.
  if (msg.type !== 'render' && msg.type !== 'relight') return;

  // A new request supersedes BOTH the paint in flight and any request still waiting: only
  // the newest is worth the CPU, and the page has already stopped caring about the rest.
  generation++;
  if (pending) self.postMessage({ id: pending.id, type: 'cancelled' });
  pending = msg;
  // Re-entrancy is the mechanism, not an accident: this handler runs again while the paint
  // below is parked at a yield, and that is how the bump above reaches it. What must not
  // happen twice is the drain loop, hence the flag.
  if (running) return;

  running = true;
  try {
    while (pending) {
      const job = pending;
      pending = null;
      const mine = generation;
      try {
        const r = await paint(job, mine);
        // Transfer rather than copy: at 1100 px the painting is ~3.6 MB, and copying it
        // back on every draft is real time spent for nothing.
        const buf = r.pixels.buffer;
        self.postMessage({ id: job.id, type: 'done', pixels: buf, w: r.w, h: r.h,
                           stats: r.stats }, [buf]);
      } catch (err) {
        if (err === ABORT) self.postMessage({ id: job.id, type: 'cancelled' });
        else self.postMessage({ id: job.id, type: 'error',
                                message: String((err && err.message) || err) });
      }
    }
  } finally {
    running = false;
  }
};
