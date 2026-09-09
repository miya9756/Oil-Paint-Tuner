// node harness for tests/test_pipeline_parity.py. Runs the REAL ported package.
//
//     node web/tune/pipeline_node.mjs job.json out.json

import { readFileSync, writeFileSync } from 'node:fs';
import { luma, gaussianKernel1d, gaussianBlur, summedArea, satBox, decimate } from './oilpaint/image.js';
import { sobel, DetailField, FovealField } from './oilpaint/detail.js';
import { structureTensor, flatTensor, sample, sampleAngle } from './oilpaint/tensor.js';
import * as quadtree from './oilpaint/quadtree.js';
import * as strokes from './oilpaint/strokes.js';
import { defaultRng } from './oilpaint/nprandom.js';
import { plan, paint, baseLayer, makeCache, DEFAULTS, RELIGHT_PARAMS } from './oilpaint/pipeline.js';
import { PALETTES, mixLut } from './oilpaint/palette.js';
import { FLOWS, centresFor, flowDir, fieldCentres, spiralCentres, normCoords, paramsFor as flowParamsFor, vortexMap } from './oilpaint/flow.js';
import { psnr, edgeAlignment } from './oilpaint/metrics.js';
import * as regionsMod from './oilpaint/regions.js';

const [, , jobPath, outPath] = process.argv;
const job = JSON.parse(readFileSync(jobPath, 'utf8'));
const A = a => Array.from(a);
// Bit equality between two float arrays, for the arms whose whole claim is that two
// code paths produced the SAME buffer rather than a close one. Compared here rather
// than in Python so the claim is about the port's own two paths, not about the port
// against the Python -- those are different failures and they deserve separate checks.
const eqf = (a, b) => a.length === b.length && a.every((v, i) => Object.is(v, b[i]));

const H = job.h, W = job.w;
// uint8 -> the float32 [0,1] image, exactly as the page builds it from a canvas.
const rgb = new Float32Array(H * W * 3);
for (let i = 0; i < rgb.length; i++) rgb[i] = job.rgb[i] / 255.0;

const out = {};

// --- stage 1: colour + convolution primitives ------------------------------------
const lum = luma(rgb, H, W);
out.luma = A(lum);
const [gx, gy] = sobel(lum, H, W);
out.gx = A(gx);
out.gy = A(gy);
out.kernel = A(gaussianKernel1d(job.sigma));
out.blur = A(gaussianBlur(lum, H, W, job.sigma));

// --- stage 2: summed-area table ---------------------------------------------------
const sat = summedArea(lum, H, W);
out.satBoxes = job.boxes.map(([y0, x0, y1, x1]) => satBox(sat, H, W, y0, x0, y1, x1));

// --- stage 3: structure tensor ----------------------------------------------------
const [theta, coh] = structureTensor(rgb, H, W, job.sigma);
out.theta = A(theta);
out.coherence = A(coh);
out.sampled = A(sample(coh, H, W, Float64Array.from(job.sy), Float64Array.from(job.sx)));
out.sampledAngle = A(sampleAngle(theta, H, W,
  Float64Array.from(job.sy), Float64Array.from(job.sx)));

// --- stage 3b: the flat-region brush ----------------------------------------------
// The pre-filtered tensor and the blend that folds it into the fine one. Checked here
// rather than only through the strokes because a blend that silently fell back to the
// fine field would still produce a plausible stroke buffer -- just the old, blobby one.
const [thetaFlat, cohFlat, flatStep, flatH, flatW] = flatTensor(rgb, H, W, job.flat_sigma);
out.thetaFlat = A(thetaFlat);
out.coherenceFlat = A(cohFlat);
out.flatStep = flatStep;
out.flatShape = [flatH, flatW];
out.decimated = A(decimate(luma(rgb, H, W), H, W, job.flat_sigma_step)[0]);
{
  const sy = Float64Array.from(job.sy), sx = Float64Array.from(job.sx);
  const fy = Float64Array.from(job.sy, v => v / flatStep);
  const fx = Float64Array.from(job.sx, v => v / flatStep);
  const [tb, cb] = strokes.flatBlend(
    sampleAngle(theta, H, W, sy, sx), sample(coh, H, W, sy, sx),
    sampleAngle(thetaFlat, flatH, flatW, fy, fx), sample(cohFlat, flatH, flatW, fy, fx),
    job.flat_dir, job.flat_theta_deg);
  out.flatTheta = A(tb);
  out.flatCoh = A(cb);
}

// --- stage 4: detail field --------------------------------------------------------
out.scores = {};
for (const metric of job.metrics) {
  const fld = new DetailField(rgb, H, W, metric, quadtree.treeSize(H, W));
  const cy = Int32Array.from(job.cell_y), cx = Int32Array.from(job.cell_x);
  out.scores[metric] = A(fld.score(cy, cx, job.cell_size));
}

// --- stage 4b: the foveal field ---------------------------------------------------
// A hand-painted importance map reweights the detail field, so it sits between the metric
// and the tree. The cell mean, the weight and the weighted score are checked separately --
// a single end-to-end comparison of the painting would say only "different" and not which
// of the three is wrong.
const maskF = new Float32Array(H * W);
for (let i = 0; i < maskF.length; i++) maskF[i] = job.mask[i] / 255.0;
{
  const inner = new DetailField(rgb, H, W, 'var', quadtree.treeSize(H, W));
  const cy = Int32Array.from(job.cell_y), cx = Int32Array.from(job.cell_x);
  const fv = new FovealField(inner, maskF, job.fov.strength);
  out.foveal = {
    maskMean: A(fv.maskMean(cy, cx, job.cell_size)),
    weight: A(fv.weight(cy, cx, job.cell_size)),
    score: A(fv.score(cy, cx, job.cell_size)),
    meanLuma: A(fv.meanLuma(cy, cx, job.cell_size)),
  };
}

// --- stage 5: quadtree ------------------------------------------------------------
out.treeSize = quadtree.treeSize(H, W);
const fld = new DetailField(rgb, H, W, 'var', out.treeSize);
const [tau, reachable, ceiling] = quadtree.solveTau(
  fld, H, W, job.target_n, job.dmin, job.min_cell, null, 24, 0.02, job.tau_floor);
out.tau = tau;
out.reachable = reachable;
out.ceiling = ceiling;
const cells = quadtree.build(fld, H, W, tau, job.dmin, job.min_cell, null, job.tau_floor);
out.cells = { y0: A(cells.y0), x0: A(cells.x0), size: A(cells.size), depth: A(cells.depth) };

// --- stage 6: strokes -------------------------------------------------------------
const sb = strokes.fromCells(rgb, H, W, cells, theta, coh, {
  kappa: job.cfg.kappa, jitter_centre: job.cfg.jitter_centre,
  jitter_radius: job.cfg.jitter_radius, jitter_theta_deg: job.cfg.jitter_theta_deg,
  jitter_aniso: job.cfg.jitter_aniso, aniso_max: job.cfg.aniso_max,
  orient: job.cfg.orient, color: job.cfg.color, alpha: job.cfg.alpha,
  drop_p: job.cfg.drop_p, size_sigma: job.cfg.size_sigma,
  color_jitter: job.cfg.color_jitter, rng: defaultRng(job.cfg.seed),
});
out.strokes = {};
for (const f of ['x', 'y', 'r_major', 'r_minor', 'theta', 'rgb', 'alpha', 'phase']) {
  out.strokes[f] = A(sb[f]);
}

// --- stage 7: base layer ----------------------------------------------------------
out.baseLayer = A(baseLayer(rgb, H, W, job.cfg.base_block));

// --- stage 8: plan, then the whole pipeline --------------------------------------
const planned = plan(rgb, H, W, job.cfg);
out.plan = {
  n: planned.sb.x.length,
  n_under: planned.info.n_under,
  n_detail: planned.info.n_detail,
  tau: planned.info.tau,
  dmin: planned.info.dmin,
  ceiling: planned.info.stroke_ceiling,
  budget_reachable: planned.info.budget_reachable,
  x: A(planned.sb.x), y: A(planned.sb.y), theta: A(planned.sb.theta),
  r_major: A(planned.sb.r_major), r_minor: A(planned.sb.r_minor),
  rgb: A(planned.sb.rgb), phase: A(planned.sb.phase),
};

const done = paint(rgb, H, W, job.cfg);
out.painting = A(done.out);
out.info = {
  coverage: done.info.coverage,
  coverage_all: done.info.coverage_all,
  bare: done.info.bare,
  n_detail: done.info.n_detail,
  n_under: done.info.n_under,
};
out.psnr = psnr(done.out, rgb);
out.edgeAlignment = edgeAlignment(done.sb, rgb, H, W);
out.defaults = DEFAULTS;
// The parameters `finish` can re-apply to an already-rasterised painting -- the set the
// worker's relight fast path is allowed to serve without touching the rasteriser. Pinned
// as a SET rather than through a painting: a stale entry here is a picture that should
// have changed and did not, produced 42x faster than the one that would have been right.
out.relightParams = [...RELIGHT_PARAMS].sort();

// --- stage 8b: the same, with base='strokes' --------------------------------------
// The arm above runs base='blur', which allocates NO underpainting, so every check on it
// passes with the whole `under` branch dead. That branch has its own kappa, its own seed
// offset and now its own aniso override, and none of it was pinned on either side.
const plannedU = plan(rgb, H, W, job.cfg_under);
const doneU = paint(rgb, H, W, job.cfg_under);
out.under = {
  n_under: plannedU.info.n_under,
  n_detail: plannedU.info.n_detail,
  x: A(plannedU.sb.x), y: A(plannedU.sb.y),
  r_major: A(plannedU.sb.r_major), r_minor: A(plannedU.sb.r_minor),
  rgb: A(plannedU.sb.rgb),
  painting: A(doneU.out),
  coverage_all: doneU.info.coverage_all,
  bare: doneU.info.bare,
  // The covering kappa across the sliders' whole range, INCLUDING the clamp -- which the
  // arm above cannot reach, since it runs at the defaults where the clamp is inert.
  kappas: job.kappa_pairs.map(([jc, jr]) => strokes.coveringKappa(jc, jr)),
};

// --- stage 8d: the pigment grade ---------------------------------------------------
// Two arms, because the grade has two entry points that share no code path in `paramsFor`:
// a named preset (which also exercises the broken-colour branch, the strength lerp and the
// cool half of the warm-cool split) and the 'none' preset with the sliders alone, which is
// the case where a port that treated 'none' as "skip" would silently do nothing.
// The mixture LUT itself, per palette. Without this a divergence in the subtractive mixing
// model shows up only as a slightly different painting, which is exactly the class of bug
// this suite exists to catch before it becomes "the browser one looks a bit off".
out.mixLuts = {};
for (const name of PALETTES) {
  const lut = mixLut(name);
  out.mixLuts[name] = lut === null ? null : A(lut);
}
out.grade = {};
for (const [name, gcfg] of [['preset', job.cfg_grade], ['trim', job.cfg_trim],
                            ['hue', job.cfg_hue], ['hue_pal', job.cfg_hue_pal],
                            ['ref', job.cfg_ref], ['ref_pal', job.cfg_ref_pal]]) {
  const p = plan(rgb, H, W, gcfg);
  const d = paint(rgb, H, W, gcfg);
  out.grade[name] = {
    palette: p.info.palette,
    rgb: A(p.sb.rgb), x: A(p.sb.x), r_major: A(p.sb.r_major),
    canvas: A(p.canvas),
    painting: A(d.out),
  };
}
// The ungraded plan again, through a WARM cache, after a graded one has been through it:
// the graded canvas has its own memo slot and its own key, and grading in place -- or
// keying only on the grade -- would hand this one the coloured ground.
{
  const c = makeCache();
  plan(rgb, H, W, job.cfg, c);
  plan(rgb, H, W, job.cfg_grade, c);
  const after = plan(rgb, H, W, job.cfg, c);
  out.gradeCache = { rgb: A(after.sb.rgb), canvas: A(after.canvas) };
}

// --- stage 8e: the artistic flow field --------------------------------------------
// The PRESET BLOCKS themselves, per name, and the field they generate -- pinned directly
// rather than only through a painting. Every preset's constants are a claim the JS repeats
// by hand, and a typo in one that no arm below happens to select would surface only as
// "the browser one looks a bit off", which is the exact class of bug this suite exists for.
const flowIdent = {
  flow_strength: 1.0, flow_scale: 0.0, flow_rot: 0.0, flow_coh: 0.0, flow_drift: 0.0,
};
out.flowParams = {};
out.flowDir = {};
{
  // The same sample coordinates the tensor stages use, run through normCoords so the field
  // is probed in its own frame -- including points off the canvas, which strokes reach via
  // the centre jitter.
  const sy = Float64Array.from(job.sy), sx = Float64Array.from(job.sx);
  const [fu, fv] = normCoords(sy, sx, H, W);
  for (const name of FLOWS) {
    const p = flowParamsFor(Object.assign({ flow: name }, flowIdent));
    out.flowParams[name] = p;
    out.flowDir[name] = p === null ? null : A(flowDir(fu, fv, p));
  }
}
// HAND-PLACED CENTRES. Two things pinned before any painting: the spiral the presets fall
// back to, and the fractions -> field-frame conversion, which is the one place a swirl can
// end up half a canvas from where it was clicked (the image is not square here, and a port
// that divided by the wrong side would still look plausible).
{
  const p = flowParamsFor(Object.assign({ flow: 'starry' }, flowIdent));
  out.flowSpiral = spiralCentres(p).map(A);
  const q = flowParamsFor(Object.assign({ flow: 'starry' }, flowIdent), job.vortices);
  out.flowPlacedC = fieldCentres(q, H, W).map(A);
  const sy = Float64Array.from(job.sy), sx = Float64Array.from(job.sx);
  const [fu, fv] = normCoords(sy, sx, H, W);
  out.flowPlacedDir = A(flowDir(fu, fv, q, fieldCentres(q, H, W)));
  // A 'wave' preset has no vortices, so placed points must leave its field untouched
  // rather than half-applied.
  const wv = flowParamsFor(Object.assign({ flow: 'waterlily' }, flowIdent), job.vortices);
  out.flowWaveIgnores = A(flowDir(fu, fv, wv, fieldCentres(wv, H, W)));
}
out.flow = {};
for (const [name, fcfg, vtx] of [['swirl', job.cfg_flow, null],
                                 ['wave', job.cfg_flow_wave, null],
                                 ['placed', job.cfg_flow, job.vortices]]) {
  const p = plan(rgb, H, W, fcfg, null, null, null, vtx);
  const d = paint(rgb, H, W, fcfg, null, null, vtx);
  out.flow[name] = {
    flow: p.info.flow, vortices: p.info.flow_vortices,
    n: p.sb.x.length, n_under: p.info.n_under,
    x: A(p.sb.x), y: A(p.sb.y), theta: A(p.sb.theta),
    r_major: A(p.sb.r_major), r_minor: A(p.sb.r_minor), rgb: A(p.sb.rgb),
    painting: A(d.out),
  };
}

// --- stage 8c: the whole pipeline with a foveal map -------------------------------
// Two strengths, because they are two regimes rather than two settings: at 0.7 the weight
// only tilts the tree and the budget still binds, while at 1.0 it reaches zero and the map
// becomes a hard gate that no unmarked cell can pass.
const fovArg = { mask: maskF, key: 'parity' };
out.fovArms = {};
for (const [name, fcfg] of [['blend', job.cfg_fov], ['gate', job.cfg_fov_gate]]) {
  const p = plan(rgb, H, W, fcfg, null, fovArg);
  const d = paint(rgb, H, W, fcfg, null, fovArg);
  out.fovArms[name] = {
    n: p.sb.x.length, tau: p.info.tau, dmin: p.info.dmin,
    reachable: p.info.budget_reachable, ceiling: p.info.stroke_ceiling,
    foveal: p.info.foveal,
    rgb: A(p.sb.rgb), r_major: A(p.sb.r_major),
    painting: A(d.out),
  };
}
// ...and the cached path, which has its own memo key. A key that ignored the map would
// serve the previous brush stroke's tree, and every uncached check above would still pass.
{
  const c = makeCache();
  const a = plan(rgb, H, W, job.cfg_fov, c, fovArg);
  const b = plan(rgb, H, W, job.cfg_fov, c, fovArg);
  // A SECOND map through the same cache: same cfg, different key. This is the stale-table
  // failure the key exists to prevent, and nothing else in this file would see it.
  const other = new Float32Array(H * W);
  for (let i = 0; i < other.length; i++) other[i] = 1.0 - maskF[i];
  const e = plan(rgb, H, W, job.cfg_fov, c, { mask: other, key: 'parity-inverted' });
  const f = plan(rgb, H, W, job.cfg_fov, null, { mask: other, key: 'parity-inverted' });
  out.fovCached = {
    firstN: a.sb.x.length, secondN: b.sb.x.length,
    secondRgb: A(b.sb.rgb), secondTau: b.info.tau,
    invertedN: e.sb.x.length, invertedUncachedN: f.sb.x.length,
    invertedTau: e.info.tau, invertedUncachedTau: f.info.tau,
  };
}

// --- stage 8g: region-wise colour --------------------------------------------------
// The DERIVED TABLES first, per the same rule the pigment LUTs follow: a field added to
// REGION_PARAMS on one side and forgotten on the other would not throw, it would give the
// browser a control the CLI honours and this one ignores. The legend is pinned for the
// same reason -- a mask painted for the CLI has to mean the same thing here.
out.regionParams = [...regionsMod.REGION_PARAMS].sort();
out.regionColorParams = [...regionsMod.REGION_COLOR_PARAMS].sort();
out.regionFlowParams = [...regionsMod.REGION_FLOW_PARAMS].sort();
out.regionLegend = regionsMod.LEGEND;
out.regionMax = regionsMod.MAX_REGIONS;
out.regionLabelsFromImage = A(regionsMod.labelsFromImage(
  Float32Array.from(job.region_swatch), 1, job.region_swatch.length / 3));
{
  const labels = Uint8Array.from(job.region_labels);
  const mk = (overrides, key) => ({ labels, h: H, w: W, overrides, key });
  const live = mk(job.region_overrides, 'r1');
  const p = plan(rgb, H, W, job.cfg_regions, null, null, null, null, live);
  const d = paint(rgb, H, W, job.cfg_regions, null, null, null, live);
  // The labels themselves, which are the only genuinely new arithmetic here -- everything
  // downstream is `palette.grade` on a gathered subset. Integers, so they are compared
  // EXACTLY: a stroke on the wrong side of a boundary is not a tolerance question.
  const lab = regionsMod.strokeLabels(
    regionsMod.paramsFor(job.cfg_regions, live), p.sb.x, p.sb.y);
  // The two arms that must produce the UNDIVIDED painting: a mask nobody overrides (the
  // port has to skip, not grade an identity), and every region set to the base's own
  // values (the gather/grade/scatter has to be the same arithmetic as the whole-buffer
  // grade, and the transport has to be fitted once rather than once per region).
  const inert = plan(rgb, H, W, job.cfg_regions, null, null, null, null, mk({}, 'r1'));
  const equiv = plan(rgb, H, W, job.cfg_regions, null, null, null, null,
                     mk(job.region_equiv, 'r1'));
  const flat = plan(rgb, H, W, job.cfg_regions);
  out.regions = {
    rgb: A(p.sb.rgb), x: A(p.sb.x), canvas: A(p.canvas), painting: A(d.out),
    labels: A(lab), count: p.info.regions, strokes: p.info.region_strokes,
    inertCount: inert.info.regions,
    inertSame: eqf(inert.sb.rgb, flat.sb.rgb) && eqf(inert.canvas, flat.canvas),
    equivSame: eqf(equiv.sb.rgb, flat.sb.rgb) && eqf(equiv.canvas, flat.canvas),
  };
}

// --- stage 8h: region-wise FLOW ----------------------------------------------------------
// The other half of a region, and the other attachment point: these overrides run inside
// step 3 of `fromCells`, so unlike stage 8g they MOVE the strokes in their passage. Three
// things have to hold at once and the arm drives all three -- a region with a flow of its
// own, a region that turns the base's flow OFF, and an underpainting (`base: 'strokes'`)
// which gets the same plan with every drift stripped out.
{
  const labels = Uint8Array.from(job.region_labels);
  const mk = (overrides, key) => ({ labels, h: H, w: W, overrides, key });
  const cfg = job.cfg_region_flow;
  const live = mk(job.region_flow_overrides, 'f1');
  const p = plan(rgb, H, W, cfg, null, null, null, null, live);
  const d = paint(rgb, H, W, cfg, null, null, null, live);
  // The labels again, and exact for the same reason: `Math.floor` against numpy's, on a
  // stroke sitting on a boundary, is a stroke combed the wrong way rather than an ulp.
  const lab = regionsMod.strokeLabels(regionsMod.flowParamsFor(cfg, live), p.sb.x, p.sb.y);
  // A colour-only region must not enter the flow path at all (the port has to SKIP, not
  // blend an identity, which would rebuild every angle through atan2), and every region
  // set to the base's own flow must reproduce the undivided painting bit for bit -- the
  // gather/scatter has to be the same arithmetic as the whole-buffer blend.
  const inert = plan(rgb, H, W, cfg, null, null, null, null,
                     mk({ 1: { palette: 'zorn' } }, 'f1'));
  const equiv = plan(rgb, H, W, cfg, null, null, null, null,
                     mk(job.region_flow_equiv, 'f1'));
  const flat = plan(rgb, H, W, cfg);
  out.regionFlow = {
    theta: A(p.sb.theta), x: A(p.sb.x), y: A(p.sb.y), rMajor: A(p.sb.r_major),
    rgb: A(p.sb.rgb), painting: A(d.out), labels: A(lab),
    count: p.info.regions, flowCount: p.info.flow_regions, nUnder: p.info.n_under,
    strokes: p.info.region_strokes,
    inertFlowCount: inert.info.flow_regions,
    equivSame: eqf(equiv.sb.theta, flat.sb.theta) && eqf(equiv.sb.x, flat.sb.x)
      && eqf(equiv.sb.y, flat.sb.y) && eqf(equiv.sb.r_major, flat.sb.r_major)
      && eqf(equiv.sb.r_minor, flat.sb.r_minor) && eqf(equiv.sb.rgb, flat.sb.rgb),
  };
}

// --- stage 8i: swirl centres per region ---------------------------------------------------
// Once the flow could differ per passage, one global list of centres meant a swirl placed in
// the sky was read by the field painting the ground too -- so the centres are addressed the
// same way the fields are. Three states have to survive the port: a region INHERITING the
// base's set, one with a set of its OWN, and the explicitly empty set that means the spiral.
{
  const labels = Uint8Array.from(job.region_labels);
  const mk = (overrides, key) => ({ labels, h: H, w: W, overrides, key });
  const cfg = job.cfg_region_flow;
  const live = mk(job.region_flow_equiv, 'f1');       // every region on the base's own flow
  const V = job.vortices, U = job.region_vortices_own;
  // `centresFor` decides which set each field reads, so it is pinned directly as well as
  // through a painting: an inheritance rule that agreed only on this picture is not a rule.
  const vm = vortexMap({ 0: V, 1: U });
  out.vortexCentres = {
    base: centresFor(vm, 0), own: centresFor(vm, 1),
    inherited: centresFor(vortexMap(V), 1),
    spiral: centresFor(vortexMap({ 0: V, 1: [] }), 1),
    plainIsBase: JSON.stringify(vortexMap(V).get(0)) === JSON.stringify(V),
  };
  const inh = plan(rgb, H, W, cfg, null, null, null, V, live);
  const own = plan(rgb, H, W, cfg, null, null, null, { 0: V, 1: U }, live);
  const ownPaint = paint(rgb, H, W, cfg, null, null, { 0: V, 1: U }, live);
  const flat = plan(rgb, H, W, cfg, null, null, null, V);
  out.regionVortices = {
    inhTheta: A(inh.sb.theta), ownTheta: A(own.sb.theta), ownX: A(own.sb.x),
    ownY: A(own.sb.y), painting: A(ownPaint.out),
    inhCount: inh.info.flow_vortices, ownCount: own.info.flow_vortices,
    // The equivalence arm, inside the port: a region inheriting the base's centres has to
    // repaint the base's painting bit for bit, or the inheritance is a different picture.
    inhSame: eqf(inh.sb.theta, flat.sb.theta) && eqf(inh.sb.x, flat.sb.x)
      && eqf(inh.sb.y, flat.sb.y),
  };
}

// --- stage 9: the REAL engine.worker.js ------------------------------------------
// The glue between the page and the package is the one part no other test touches, and
// glue is where the last hard-to-see bug lived: a marshalling slip that returned an empty
// array painted a blank canvas while reporting a full stroke count. So the actual shipped
// worker is loaded here, with `self` and `fetch` shimmed, and driven exactly as the page
// drives it -- init, then one render -- and its pixels are compared against paint() above.
let onmessage = null;
const posted = [];
globalThis.self = {
  set onmessage(fn) { onmessage = fn; },
  get onmessage() { return onmessage; },
  postMessage: (m) => posted.push(m),
  addEventListener: () => {},
};
// The schema is a BUILD product (build_static.py emits it from oilpaint/schema.py), so it
// is served from the job here rather than from disk -- the test must not depend on a build
// having been run, but it must still hand the worker the real thing.
// `ok`/`status` are not decoration: the worker checks them, because a 404 body parses as
// JSON perfectly happily and an unchecked `.json()` turns a missing schema into a
// plausible-looking object. A shim that omits them is not a Response, and the worker is
// right to reject it.
globalThis.fetch = async (url) => {
  if (String(url).endsWith('schema.json')) {
    return { ok: true, status: 200, json: async () => job.schema };
  }
  throw new Error(`unexpected fetch: ${url}`);
};
await import('./engine.worker.js');

await onmessage({ data: { id: 1, type: 'init' } });
const ready = posted.find(m => m.type === 'ready');

// RGBA in, exactly as a canvas would hand it over.
const rgba = new Uint8ClampedArray(H * W * 4);
for (let i = 0; i < H * W; i++) {
  rgba[4 * i] = job.rgb[3 * i];
  rgba[4 * i + 1] = job.rgb[3 * i + 1];
  rgba[4 * i + 2] = job.rgb[3 * i + 2];
  rgba[4 * i + 3] = 255;
}
// TWICE, with the same image key. The second render is the one that takes the worker's
// memo path, and a cache that returns something subtly different on a hit is invisible to
// every other test here -- they all run uncached. It has already been wrong once.
await onmessage({ data: { id: 2, type: 'render', rgba: rgba.buffer, w: W, h: H,
                          params: job.cfg, imgKey: 1 } });
const rgba2 = rgba.slice();
await onmessage({ data: { id: 3, type: 'render', rgba: rgba2.buffer, w: W, h: H,
                          params: job.cfg, imgKey: 1 } });
// A fourth render, this one WITH a foveal map, so the worker's own byte -> float
// conversion and its mask plumbing are pinned against paint() rather than assumed.
const maskU8 = Uint8Array.from(job.mask);
await onmessage({ data: { id: 4, type: 'render', rgba: rgba.slice().buffer, w: W, h: H,
                          params: job.cfg_fov, imgKey: 1,
                          mask: maskU8.buffer, maskKey: 'parity' } });
// A fifth, with HAND-PLACED swirl centres. This is the page's own path for them -- plain
// fractions on the render message, no key and no transfer -- and it is glue nothing else
// covers: `plan` takes them as its EIGHTH argument, so a worker that passed them in the
// seventh slot would silently hand the pipeline an onStage callback and paint the spiral.
await onmessage({ data: { id: 5, type: 'render', rgba: rgba.slice().buffer, w: W, h: H,
                          params: job.cfg_flow, imgKey: 1, vortices: job.vortices } });
// A REGION MASK through the worker, which is the page's own path for one -- labels as one
// byte per pixel plus a serial, and the captures as plain JSON. Glue nothing else covers:
// `plan` takes regions as its NINTH argument, so a worker passing them one slot early would
// hand the pipeline a vortex list and quietly paint the undivided picture.
const rgnU8 = Uint8Array.from(job.region_labels);
await onmessage({ data: { id: 9, type: 'render', rgba: rgba.slice().buffer, w: W, h: H,
                          params: job.cfg_regions, imgKey: 1,
                          regionLabels: rgnU8.buffer, regionKey: 'parity',
                          regionOverrides: job.region_overrides } });
// A sixth and a seventh: the RELIGHT fast path. Frame 6 is an ordinary render, which fills
// the worker's pre-lighting cache; frame 7 asks for the same painting under a different
// light through `relight`, which must skip the rasteriser and STILL produce exactly what a
// full render at those settings produces. An optimisation that returns something subtly
// different is worse than no optimisation -- it is a wrong picture that renders 42x faster.
const litParams = Object.assign({}, job.cfg_flow, { light_deg: 42.0, gloss: 0.7 });
await onmessage({ data: { id: 6, type: 'render', rgba: rgba.slice().buffer, w: W, h: H,
                          params: job.cfg_flow, imgKey: 7 } });
await onmessage({ data: { id: 7, type: 'relight', rgba: rgba.slice().buffer, w: W, h: H,
                          params: litParams, imgKey: 7 } });
// Studio buffers belong to a completed render id. Asking for an obsolete painting
// must never reveal the newest painting's surface under an older image.
await onmessage({ data: { id: 70, type: 'surface', renderId: 7 } });
await onmessage({ data: { id: 71, type: 'surface', renderId: 6 } });
const studioData = posted.find(m => m.id === 70)?.data;
out.studio = {
  present: !!studioData,
  size: studioData ? [studioData.w, studioData.h] : null,
  colors: studioData ? studioData.color.byteLength : 0,
  normals: studioData ? studioData.surface.byteLength : 0,
  finite: studioData ? [...new Float32Array(studioData.surface)].every(Number.isFinite) : false,
  staleRejected: posted.find(m => m.id === 71)?.data === null,
};
// And a relight that MISSES -- a different image key, so the cached buffers are for another
// picture -- must fall through to a full render rather than serving the wrong one.
await onmessage({ data: { id: 8, type: 'relight', rgba: rgba.slice().buffer, w: W, h: H,
                          params: litParams, imgKey: 99 } });
// The worker now streams `progress` (the stage it is in) and, on a render slow enough to be
// worth watching, `preview` (the painting so far) under the SAME id as the answer. So the
// terminal message has to be picked out BY TYPE rather than by being the first with that id.
// The page makes exactly this distinction; a harness that kept taking the first message
// would have gone green on a worker whose answer the page could never see.
// --- supersession: three requests back to back, only the last is worth finishing --------
// `onmessage` returns as soon as the paint it started parks at its first yield, so calling
// it again here is exactly what the page does when a slider moves mid-render: the second
// and third land while the first is parked. 10 is superseded while RUNNING, 11 is dropped
// while WAITING -- two different paths to the same terminal message, and a page left
// waiting on either would hang on a promise nobody settles.
const p10 = onmessage({ data: { id: 10, type: 'render', rgba: rgba.slice().buffer,
                                w: W, h: H, params: job.cfg, imgKey: 1 } });
const p11 = onmessage({ data: { id: 11, type: 'render', rgba: rgba.slice().buffer,
                                w: W, h: H, params: job.cfg, imgKey: 1 } });
const p12 = onmessage({ data: { id: 12, type: 'render', rgba: rgba.slice().buffer,
                                w: W, h: H, params: job.cfg, imgKey: 1 } });
await Promise.all([p10, p11, p12]);

const answer = id => posted.find(m => m.id === id && (m.type === 'done' || m.type === 'error'));
const done2 = answer(2);
const done3 = answer(3);
const done4 = answer(4);
const done5 = answer(5);
const done7 = answer(7), done8 = answer(8), done9 = answer(9);
out.worker = {
  ready: !!ready,
  // The placed-vortex render: its pixels, and the count the worker reports back. The count
  // is what tells the page's stats line "(4 placed)", so a placement that never reached the
  // engine would otherwise look identical to one that reached it and did very little.
  vtxPixels: done5 && done5.pixels ? Array.from(new Uint8Array(done5.pixels)) : null,
  vtxCount: done5 && done5.stats ? done5.stats.vortices : -1,
  vtxFlow: done5 && done5.stats ? done5.stats.flow : null,
  // The relight fast path: its pixels, whether it took the fast route, and the same
  // request through a COLD cache, which must fall through to a full render.
  relitPixels: done7 && done7.pixels ? Array.from(new Uint8Array(done7.pixels)) : null,
  relitFast: done7 && done7.stats ? !!done7.stats.relit : null,
  relitMissPixels: done8 && done8.pixels ? Array.from(new Uint8Array(done8.pixels)) : null,
  relitMissFast: done8 && done8.stats ? !!done8.stats.relit : null,
  // `flowKinds` and `maxVortices` ride the ready message: the page must not be the place
  // that decides which presets have vortices, nor keep its own copy of the cap.
  readyFlowKinds: ready ? ready.flowKinds : null,
  readyMaxVortices: ready ? ready.maxVortices : -1,
  // The stage stream for the first render, in the order it arrived.
  stages: posted.filter(m => m.id === 2 && m.type === 'progress').map(m => m.step),
  // Supersession. `terminal` is every id's done/error/cancelled -- exactly one each, or
  // the page is left holding a promise that never settles.
  superseded: [10, 11, 12].map(id => ({
    id,
    terminal: posted.filter(m => m.id === id &&
        (m.type === 'done' || m.type === 'error' || m.type === 'cancelled'))
      .map(m => m.type),
  })),
  // The survivor's pixels. Identical to the ordinary render's, or an abandoned render has
  // left something behind it -- a half-written cache entry is the obvious way for
  // cancellation to turn into a wrong painting rather than a missing one.
  survivorPixels: (() => { const d = answer(12); return d && d.pixels
    ? Array.from(new Uint8Array(d.pixels)) : null; })(),
  // The page does `SCHEMA = j.schema; DEFAULTS = j.defaults`, so the worker splits the
  // schema.json document into those two fields. Reading them the same way here is what
  // makes this stage cover the handshake -- posting the whole document under `schema` gave
  // boot() a non-iterable object and hung the page on "starting the engine...".
  schemaControls: ready && Array.isArray(ready.schema) ? ready.schema.length : 0,
  readyDefaults: ready && ready.defaults ? Object.keys(ready.defaults).length : 0,
  // The page draws these as swatches under the palette dropdown. If they stop arriving the
  // strip is silently empty and nothing else notices.
  readyPigments: ready && ready.pigments ? Object.keys(ready.pigments).sort() : [],
  type: done2 ? done2.type : 'none',
  message: done2 && done2.message,
  w: done2 && done2.w,
  h: done2 && done2.h,
  stats: done2 && done2.stats,
  pixels: done2 && done2.pixels ? Array.from(new Uint8Array(done2.pixels)) : null,
  secondType: done3 ? done3.type : 'none',
  secondMessage: done3 && done3.message,
  secondPixels: done3 && done3.pixels ? Array.from(new Uint8Array(done3.pixels)) : null,
  secondStats: done3 && done3.stats,
  fovType: done4 ? done4.type : 'none',
  fovMessage: done4 && done4.message,
  fovPixels: done4 && done4.pixels ? Array.from(new Uint8Array(done4.pixels)) : null,
  fovStats: done4 && done4.stats,
  rgnPixels: done9 && done9.pixels ? Array.from(new Uint8Array(done9.pixels)) : null,
  rgnStats: done9 && done9.stats,
};

// The cached plan must equal the uncached one, field for field, not merely look similar.
const cache = makeCache();
const c1 = plan(rgb, H, W, job.cfg, cache);
const c2 = plan(rgb, H, W, job.cfg, cache);
out.cached = {
  first: { n: c1.sb.x.length, rgb: A(c1.sb.rgb), r_major: A(c1.sb.r_major),
           tau: c1.info.tau, canvas: A(c1.canvas) },
  second: { n: c2.sb.x.length, rgb: A(c2.sb.rgb), r_major: A(c2.sb.r_major),
            tau: c2.info.tau, canvas: A(c2.canvas) },
};

writeFileSync(outPath, JSON.stringify(out));
