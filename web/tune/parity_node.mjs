// node harness for tests/test_raster_parity.py. Reads a job JSON, runs the REAL raster.js
// (not a transcription of it -- the transcription is the thing that drifts), writes the
// results back as JSON.
//
//     node web/tune/parity_node.mjs job.json out.json

import { readFileSync, writeFileSync } from 'node:fs';
import { render, smoothstep, fwidthD, wobble, bristle, taper, weave, light,
         fringe, fringeParams, coveredFraction } from './oilpaint/render.js';

const [, , jobPath, outPath] = process.argv;
const job = JSON.parse(readFileSync(jobPath, 'utf8'));

const f32 = a => Float32Array.from(a);

const result = { stages: {}, cases: {} };

// --- stage 1: the scalar helpers, probed directly --------------------------------
result.stages.smoothstep = job.probes.smoothstep.map(([e0, e1, x]) => smoothstep(e0, e1, x));
result.stages.fwidth = job.probes.fwidth.map(
  ([u, v, d, ct, st, sx, sy]) => fwidthD(u, v, d, ct, st, sx, sy));
result.stages.wobble = job.probes.wobble.map(([phi, ph, amp]) => wobble(phi, ph, amp));
result.stages.fringe = job.probes.fringe.map(([phi, ph, amp, m]) => fringe(phi, ph, amp, m));
result.stages.fringeParams = job.probes.fringeParams.map(
  ([rma, rmi, px]) => fringeParams(rma, rmi, px));
result.stages.bristle = job.probes.bristle.map(([up, vp, ph]) => bristle(up, vp, ph));
result.stages.taper = job.probes.taper.map(([u, cp, amt]) => taper(u, cp, amt));
result.stages.weave = job.probes.weave.map(([y, x]) => weave(y, x));

// --- stages 2+: whole renders -----------------------------------------------------
for (const [name, c] of Object.entries(job.cases)) {
  const sb = {
    x: f32(c.sb.x), y: f32(c.sb.y),
    r_major: f32(c.sb.r_major), r_minor: f32(c.sb.r_minor),
    theta: f32(c.sb.theta), rgb: f32(c.sb.rgb),
    alpha: f32(c.sb.alpha), phase: f32(c.sb.phase),
  };
  const ropts = {
    hard: c.hard, hardR: c.hard_r, wobbleAmp: c.wobble_amp,
    canvas: c.canvas ? f32(c.canvas) : null,
    splitAt: c.split_at === null ? null : c.split_at,
    bristleAmp: c.bristle_amp, taperAmp: c.taper_amp, fringePx: c.fringe_px,
    wantHeight: c.want_height, impastoRelief: c.impasto_relief,
    impastoLayer: c.impasto_layer, ground: c.ground,
  };
  const { out, cover, coverTail, height } = render(sb, c.h, c.w, ropts);

  // The SAME composite, drawn in pieces. engine.worker.js draws it this way so it can
  // reach its own message queue between slices and drop a render nobody wants any more --
  // a worker in a straight-line loop never can. `over` is a left fold along the stroke
  // array, so splitting it and carrying the accumulators forward is the same computation,
  // not an approximation of one: this must come out EXACT, and any delta at all means the
  // slicing is wrong. Boundaries are deliberately uneven, including a single-stroke slice
  // and one that ends on the last stroke.
  const nStrokes = sb.x.length;
  const cuts = [...new Set([0, 1, 7, Math.floor(nStrokes / 3), nStrokes - 1, nStrokes]
    .filter(k => k >= 0 && k <= nStrokes))].sort((a, b) => a - b);
  const buf = render(sb, c.h, c.w, Object.assign({}, ropts, { from: 0, to: 0 }));
  for (let k = 1; k < cuts.length; k++) {
    render(sb, c.h, c.w,
           Object.assign({}, ropts, { into: buf, from: cuts[k - 1], to: cuts[k] }));
  }
  const delta = (a, b) => {
    if (a === null || b === null) return (a === null && b === null) ? 0 : Infinity;
    if (a.length !== b.length) return Infinity;
    let m = 0;
    for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]));
    return m;
  };

  result.cases[name] = {
    out: Array.from(out),
    cover: Array.from(cover),
    coverTail: coverTail ? Array.from(coverTail) : null,
    height: height ? Array.from(height) : null,
    coveredAll: coveredFraction(cover),
    coveredTail: coverTail ? coveredFraction(coverTail) : null,
    sliced: {
      cuts,
      out: delta(out, buf.out),
      cover: delta(cover, buf.cover),
      coverTail: delta(coverTail, buf.coverTail),
      height: delta(height, buf.height),
    },
  };
}

// --- the lighting pass, driven on its own so a divergence localises to the shading
// rather than to "the painting differs" ------------------------------------------------
result.lit = {};
for (const [name, c] of Object.entries(job.lit || {})) {
  result.lit[name] = Array.from(light(f32(c.rgb), f32(c.height), f32(c.cover), c.h, c.w, {
    depth: c.depth, lightDeg: c.light_deg, elevDeg: c.elev_deg,
    gloss: c.gloss, canvasWeave: c.canvas_weave, occlusion: c.occlusion,
    viewDeg: c.view_deg, viewElevDeg: c.view_elev_deg,
  }));
}

writeFileSync(outPath, JSON.stringify(result));
