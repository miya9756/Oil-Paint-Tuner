"""Parity test: web/tune/oilpaint/render.js vs oilpaint/render.py (CPU, needs node).

    conda run -n 4dre python tests/test_raster_parity.py

The rasteriser, on its own, against synthetic strokes. `test_pipeline_parity.py` already
runs a whole painting through both packages, but only through ONE set of render options --
whatever the config it paints with happens to use. This file is what covers the branches
that painting never reaches: the soft Gaussian falloff, glazing with alpha < 1, a stroke
whose bounding box hangs off the edge of the canvas, and the split coverage accumulator.

If the rasteriser drifts from `render.py`, the page shows a painting the pipeline never
produced -- and paint is the worst case for that, because the wrong answer is merely a bit
different rather than visibly broken (.claude/skills/python-js-parity/SKILL.md). So:
synthetic strokes from a seeded rng, `node` runs the real render.js over them, and every
stage is compared -- the scalar helpers, the single-stroke alpha field, then the composite
and both coverage buffers.

No image, no asset, seconds to run. Skips (exit 0) when node is unavailable -- but CI
installs node and asserts it, because a skip nobody notices is how a suite goes green while
checking nothing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np  # noqa: E402

from oilpaint.render import (  # noqa: E402
    _fwidth_d, bristle, fringe, fringe_params, light, render, smoothstep, taper, weave,
    wobble,
)
from oilpaint.strokes import StrokeBuffer  # noqa: E402

TUNE_DIR = os.path.join(_REPO_ROOT, "web", "tune")
# node is not on PATH in a non-login shell; the spack stack carries one.
SPACK_NODE = ("/cluster/software/stacks/2024-06/spack/opt/spack/linux-ubuntu22.04-x86_64_v3/"
              "gcc-12.2.0/node-js-19.2.0-i6pplhcf7voeure6rh5oeg7lu7cmokt7/bin/node")

# The inverse of the sibling repo's slack, and worth stating precisely because it is
# counter-intuitive: here PYTHON is the float32 side. numpy's value-based casting keeps a
# Python float scalar from upcasting a float32 array, so every intermediate in render.py --
# u, v, d2, the smoothstep -- rounds to f32 at each operation. raster.js carries those same
# intermediates in f64 and rounds only on the stores into Float32Array. The port is
# therefore slightly MORE accurate than its reference; this bound is the size of that gap,
# not a defect in either side. Measured worst case over these cases is ~2e-6 (printed on
# every run) -- if it ever exceeds this, something other than f32 rounding has changed and
# the number to investigate is the divergence, not this constant.
FLOAT32_SLACK = 1e-5

# The lighting pass gets its own, looser bound, and it is derived rather than tuned: the
# Phong term is `rz ** (8 + 56*gloss)`, so a gap in `rz` comes out multiplied by the
# exponent -- 56 at the gloss=0.85 the `weave` case deliberately uses. 56 x the ~1e-6 of
# FLOAT32_SLACK is 6e-5, and the measured worst case is 9.5e-6. The tight cases still run
# at FLOAT32_SLACK: `light/plain` sits at 9e-7, so a real divergence in the geometry or
# the normalisation still fails, and only the specular lobe is granted the extra room.
SPECULAR_SLACK = 1e-4

# The outline fringe gets its own bound for the same kind of reason, and it is likewise
# derived rather than tuned. Its finest octave is `4 * m` with m capped at FRINGE_MAX_M=48,
# so `sin(192 * phi)` carries any difference in phi multiplied by 192. The chain:
#
#   phi gap             1 f32 ulp near pi                            2.4e-7
#   x 192               the finest octave's order                    4.6e-5
#   x 0.20              that octave's weight in the fBm              9.2e-6
#   x 0.25              FRINGE_MAX_AMP                               2.3e-6
#   x 1.5               hard_r, edge threshold -> sigma              3.5e-6
#   x ~15               the smoothstep's slope, edge -> alpha        5e-5
#
# so 2e-4 is ~4x headroom on the worst case. The MEASURED worst case is 1.4e-6, printed on
# every run -- three orders under the bound, because that f32 ulp in phi almost never
# actually materialises once the JS rounds its atan2 to f32 too. Cases without the fringe
# still run at FLOAT32_SLACK: this buys room for one term, not for the rasteriser generally.
FRINGE_SLACK = 2e-4

H, W = 72, 96
FAILS = []


def find_node():
    return shutil.which("node") or (SPACK_NODE if os.path.exists(SPACK_NODE) else None)


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def close(js, py, label, tol=FLOAT32_SLACK):
    """Compare and REPORT the worst divergence, pass or fail -- a parity test whose margin
    is invisible cannot tell you it is about to break."""
    a = np.asarray(js, dtype=np.float64).ravel()
    b = np.asarray(py, dtype=np.float64).ravel()
    if a.size != b.size:
        return check(False, label, f"size {a.size} != {b.size}")
    worst = float(np.max(np.abs(a - b))) if a.size else 0.0
    check(worst < tol, label, f"max |js - py| = {worst:.2e}  (tol {tol:.0e})")


def synth_strokes(rng, n, h=H, w=W, alpha=1.0):
    """Strokes scattered over the canvas, deliberately including some that hang off the
    edge -- the bounding-box clip is exactly where an off-by-one port bug hides."""
    r = rng.uniform(3.0, 11.0, n)
    ratio = rng.uniform(1.0, 3.0, n)
    return StrokeBuffer(
        x=rng.uniform(-6, w + 6, n).astype(np.float32),
        y=rng.uniform(-6, h + 6, n).astype(np.float32),
        r_major=(r * np.sqrt(ratio)).astype(np.float32),
        r_minor=(r / np.sqrt(ratio)).astype(np.float32),
        theta=rng.uniform(-np.pi, np.pi, n).astype(np.float32),
        rgb=rng.random((n, 3)).astype(np.float32),
        alpha=np.full(n, alpha, dtype=np.float32),
        phase=rng.uniform(0, 2 * np.pi, n).astype(np.float32),
    )


def sb_json(sb):
    return {
        "x": sb.x.tolist(), "y": sb.y.tolist(),
        "r_major": sb.r_major.tolist(), "r_minor": sb.r_minor.tolist(),
        "theta": sb.theta.tolist(), "rgb": sb.rgb.ravel().tolist(),
        "alpha": sb.alpha.tolist(), "phase": sb.phase.tolist(),
    }


def build_job(rng):
    """The synthetic workload, as (job dict for node, {name: python kwargs})."""
    # Stage 1 probes: the scalar helpers, on their own, so a failure localises to the
    # falloff rather than to "the picture differs".
    probes = {
        "smoothstep": [[float(a), float(a + b), float(x)] for a, b, x in
                       zip(rng.uniform(0, 2, 64), rng.uniform(.01, 1, 64), rng.uniform(-1, 3, 64))],
        "fwidth": [],
        "wobble": [[float(phi), float(p), float(a)] for phi, p, a in
                   zip(rng.uniform(-np.pi, np.pi, 64),
                       rng.uniform(0, 6.28, 64), rng.uniform(0, .4, 64))],
        # The fringe is probed at orders up to the cap, because its whole point is that the
        # order rises with the stroke -- and because that order is exactly what multiplies
        # any float32 gap in phi (see FRINGE_SLACK).
        "fringe": [[float(phi), float(p), float(a), int(m)] for phi, p, a, m in
                   zip(rng.uniform(-np.pi, np.pi, 96), rng.uniform(0, 6.28, 96),
                       rng.uniform(0, .25, 96), rng.integers(1, 49, 96))],
        # `fringe_params` returns an INTEGER order. Python floors halves to even and JS
        # rounds them up, so this probe spans a wide range of radii to catch the two
        # disagreeing -- an off-by-one there is a different outline, not a rounding error.
        "fringeParams": [[float(a), float(b), float(px)] for a, b, px in
                         zip(rng.uniform(0.5, 90, 96), rng.uniform(0.5, 90, 96),
                             rng.uniform(0, 5, 96))],
        # Step 2. `bristle` is probed over PIXEL coordinates, out to +-120, because its
        # arguments are already pitch-scaled by the caller and a big underpainting stroke
        # really does reach that far -- and a sine's f32/f64 gap grows with its argument,
        # so probing it only near the origin would prove nothing about the strokes that
        # actually show the texture.
        "bristle": [[float(up), float(vp), float(p)] for up, vp, p in
                    zip(rng.uniform(-120, 120, 96), rng.uniform(-40, 40, 96),
                        rng.uniform(0, 6.28, 96))],
        "taper": [[float(u), float(c), float(a)] for u, c, a in
                  zip(rng.uniform(-2, 2, 64), rng.uniform(-1, 1, 64),
                      rng.uniform(0, .7, 64))],
        # The weave is sampled on the integer grid it is actually evaluated on, at indices
        # large enough to reach the f32 rounding of `k * y` that the JS has to reproduce.
        "weave": [[float(y), float(x)] for y, x in
                  zip(rng.integers(0, 1024, 64), rng.integers(0, 1024, 64))],
    }
    for u, v, ct, sx, sy in zip(rng.uniform(-2, 2, 64), rng.uniform(-2, 2, 64),
                                rng.uniform(-1, 1, 64), rng.uniform(1, 9, 64),
                                rng.uniform(1, 9, 64)):
        d = float(np.sqrt(u * u + v * v))
        st = float(np.sqrt(max(0.0, 1 - ct * ct)))
        probes["fwidth"].append([float(u), float(v), d, float(ct), st, float(sx), float(sy)])

    # A single WHITE stroke on a BLACK canvas: `out` is then the alpha field itself, so the
    # falloff is compared per pixel before any compositing can hide a difference in it.
    one = synth_strokes(rng, 1)
    one.x[:] = W * 0.5
    one.y[:] = H * 0.5
    one.r_major[:] = 19.0
    one.r_minor[:] = 8.0
    one.theta[:] = 0.7
    one.rgb[:] = 1.0

    canvas = rng.random((H, W, 3)).astype(np.float32)
    many = synth_strokes(rng, 120)
    glaze = synth_strokes(rng, 120, alpha=0.45)

    # Step 2 is OFF in this block, so these cases stay the exact regression they were
    # before it existed -- if any of them moves, the claim that `bristle_amp=0,
    # taper_amp=0` reproduces a step-1 painting is false on one side or the other.
    off = dict(bristle_amp=0.0, taper_amp=0.0, fringe_px=0.0, want_height=False,
               impasto_relief=0.0, impasto_layer=0.0, ground=0.0)
    cases = {
        # stage 2 -- the hard-edged falloff, alone, as a pure alpha field
        "alpha_hard": dict(sb=one, hard=True, hard_r=1.5, wobble_amp=0.0,
                           canvas=None, split_at=None, **off),
        # stage 3 -- the same with the outline wobble, whose atan2/sin chain is the most
        # port-fragile expression in the file
        "alpha_wobble": dict(sb=one, hard=True, hard_r=1.5, wobble_amp=0.28,
                             canvas=None, split_at=None, **off),
        # stage 4 -- the soft Gaussian branch
        "alpha_soft": dict(sb=one, hard=False, hard_r=1.5, wobble_amp=0.0,
                           canvas=None, split_at=None, **off),
        # stage 5 -- the real thing: many strokes over a canvas, split coverage, wobble on
        "composite": dict(sb=many, hard=True, hard_r=1.5, wobble_amp=0.2,
                          canvas=canvas, split_at=40, **off),
        # stage 6 -- alpha < 1, where `over` accumulates rather than replaces and any
        # divergence in the blend compounds across every overlapping stroke
        "glaze": dict(sb=glaze, hard=True, hard_r=1.1, wobble_amp=0.0,
                      canvas=canvas, split_at=0, **off),
        # stage 7 -- the taper alone. Separated from the bristle because it is the only
        # one of the two that moves the SILHOUETTE, so a divergence here is a differently
        # shaped stroke rather than a differently textured one.
        "taper_only": dict(sb=one, hard=True, hard_r=1.5, wobble_amp=0.0,
                           canvas=None, split_at=None,
                           **{**off, "taper_amp": 0.45}),
        # stage 8 -- the bristle alone, as a pure alpha field, where the pitch scaling and
        # the elongation weight are both per-stroke f32 scalars the port has to round the
        # same way.
        "bristle_only": dict(sb=one, hard=True, hard_r=1.5, wobble_amp=0.0,
                             canvas=None, split_at=None,
                             **{**off, "bristle_amp": 0.30}),
        # stage 9a -- the fringe alone, on the big stroke where its order is highest and
        # `wobble` is least able to help. Kept apart from the full case so a divergence
        # localises to the fBm rather than to "the painting differs".
        "fringe_only": dict(sb=one, hard=True, hard_r=1.5, wobble_amp=0.0,
                            canvas=None, split_at=None,
                            **{**off, "fringe_px": 2.5}),
        # stage 9 -- everything at once, including the height field. `many` spans a wide
        # range of stroke sizes and elongations, which is exactly what the sqrt pitch
        # scaling and the elongation weight are functions of.
        "impasto": dict(sb=many, hard=True, hard_r=1.5, wobble_amp=0.2,
                        canvas=canvas, split_at=40,
                        bristle_amp=0.24, taper_amp=0.35, fringe_px=2.0, want_height=True,
                        impasto_relief=0.45, impasto_layer=0.6,
                        # A NON-ZERO ground, which is the shipped configuration: the height
                        # buffer no longer starts at zero, and a port that still zero-fills
                        # it would differ only where nothing was painted -- the quietest
                        # possible divergence, and invisible in the composited colour.
                        ground=1.3),
    }
    job = {"probes": probes, "cases": {
        name: {"h": H, "w": W, "hard": c["hard"], "hard_r": c["hard_r"],
               "wobble_amp": c["wobble_amp"], "split_at": c["split_at"],
               "bristle_amp": c["bristle_amp"], "taper_amp": c["taper_amp"],
               "fringe_px": c["fringe_px"],
               "want_height": c["want_height"],
               "impasto_relief": c["impasto_relief"], "impasto_layer": c["impasto_layer"],
               "ground": c["ground"],
               "canvas": None if c["canvas"] is None else c["canvas"].ravel().tolist(),
               "sb": sb_json(c["sb"])}
        for name, c in cases.items()}}

    # The lighting pass, driven directly on synthetic fields rather than on a painting --
    # the height field a painting produces is smooth almost everywhere, and the branches
    # that break a port are the borders (where the gradient clamps) and the steep steps.
    lit_rgb = rng.random((H, W, 3)).astype(np.float32)
    lit_h = rng.random((H, W)).astype(np.float32) * 1.7
    lit_h[H // 3: 2 * H // 3, W // 3: 2 * W // 3] += 2.0  # a hard step, all four borders
    lit_cover = rng.random((H, W)).astype(np.float32)
    base_lit = dict(occlusion=0.0, view_deg=90.0, view_elev_deg=90.0)
    lit = {
        # no weave: the gradient, the Phong lobe and the flat-surface normalisation alone
        "plain": dict(depth=0.4, light_deg=135.0, elev_deg=35.0, gloss=0.3,
                      canvas_weave=0.0, **base_lit),
        # with weave: adds the f32-rounded sine on the pixel grid, the one expression in
        # this file whose naive port is wrong by far more than the float32 slack
        "weave": dict(depth=0.4, light_deg=40.0, elev_deg=62.0, gloss=0.85,
                      canvas_weave=0.6, **base_lit),
        # the cavity map: two cascaded separable blurs, which is the only part of `light`
        # that is not a per-pixel closed form and so the only part where an edge-handling
        # difference in the blur could hide
        "occlusion": dict(depth=0.4, light_deg=135.0, elev_deg=35.0, gloss=0.3,
                          canvas_weave=0.0,
                          **{**base_lit, "occlusion": 1.5}),
        # the eye off-centre and swung toward the light's mirror direction, where the Phong
        # lobe is largest and therefore where a wrong sign in the view vector's y term --
        # invisible at the default straight-on view -- shows up
        "oblique": dict(depth=0.4, light_deg=135.0, elev_deg=35.0, gloss=0.5,
                        canvas_weave=0.0,
                        **{**base_lit, "occlusion": 1.5, "view_deg": 315.0,
                           "view_elev_deg": 45.0}),
        # depth 0 must be an exact no-op up to a specular lobe pointing away from the eye
        "flat": dict(depth=0.0, light_deg=135.0, elev_deg=35.0, gloss=0.3,
                     canvas_weave=0.0, **base_lit),
    }
    job["lit"] = {
        name: dict(c, h=H, w=W, rgb=lit_rgb.ravel().tolist(),
                   height=lit_h.ravel().tolist(), cover=lit_cover.ravel().tolist())
        for name, c in lit.items()
    }
    return job, cases, (lit, lit_rgb, lit_h, lit_cover)


def run_node(node, job):
    with tempfile.TemporaryDirectory() as tmp:
        job_path = os.path.join(tmp, "job.json")
        out_path = os.path.join(tmp, "out.json")
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        r = subprocess.run([node, os.path.join(TUNE_DIR, "parity_node.mjs"),
                            job_path, out_path], capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"node failed:\n{r.stderr[-2000:]}")
        with open(out_path, encoding="utf-8") as fh:
            return json.load(fh)


def main():
    node = find_node()
    if node is None:
        print("SKIP  no node on PATH (and no spack node-js) -- raster.js parity not checked")
        return 0
    print(f"node {subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()}")

    rng = np.random.default_rng(7)
    job, cases, (lit, lit_rgb, lit_h, lit_cover) = build_job(rng)
    got = run_node(node, job)

    print("\nstage 1 -- scalar helpers")
    close(got["stages"]["smoothstep"],
          [smoothstep(e0, e1, x) for e0, e1, x in job["probes"]["smoothstep"]],
          "smoothstep")
    close(got["stages"]["fwidth"],
          [_fwidth_d(u, v, d, ct, st, sx, sy)
           for u, v, d, ct, st, sx, sy in job["probes"]["fwidth"]],
          "fwidth(d) analytic stand-in")
    close(got["stages"]["wobble"],
          [wobble(phi, ph, amp) for phi, ph, amp in job["probes"]["wobble"]],
          "outline wobble")
    close(got["stages"]["fringe"],
          [fringe(phi, ph, amp, m) for phi, ph, amp, m in job["probes"]["fringe"]],
          "outline fringe (fBm)")
    py_fp = [fringe_params(a, b, px) for a, b, px in job["probes"]["fringeParams"]]
    js_fp = got["stages"]["fringeParams"]
    # The ORDER is compared as an exact integer, not within a tolerance. There is no such
    # thing as being one lobe out by a little.
    check([int(m) for m, _ in js_fp] == [m for m, _ in py_fp],
          "fringe order agrees exactly (integer)",
          f"{sum(int(a[0]) != b[0] for a, b in zip(js_fp, py_fp))} of {len(py_fp)} differ")
    close([a for _, a in js_fp], [a for _, a in py_fp], "fringe amplitude")
    close(got["stages"]["bristle"],
          [bristle(up, vp, ph) for up, vp, ph in job["probes"]["bristle"]],
          "bristle field")
    close(got["stages"]["taper"],
          [taper(u, cp, amt) for u, cp, amt in job["probes"]["taper"]],
          "silhouette taper")
    # `weave` builds a whole grid, so it is probed by indexing the grid it builds -- which
    # also checks the row/column convention, an axis swap being invisible to a scalar call.
    wv = weave(1024, 1024)
    close(got["stages"]["weave"], [wv[int(y), int(x)] for y, x in job["probes"]["weave"]],
          "canvas weave")

    labels = {"alpha_hard": "stage 2 -- hard-edge alpha field",
              "alpha_wobble": "stage 3 -- alpha field with outline wobble",
              "alpha_soft": "stage 4 -- soft Gaussian alpha field",
              "composite": "stage 5 -- multi-stroke composite over a canvas",
              "glaze": "stage 6 -- alpha < 1 (glazing)",
              "taper_only": "stage 7 -- silhouette taper alone",
              "bristle_only": "stage 8 -- bristle alpha alone",
              "fringe_only": "stage 9a -- fractal outline fringe alone",
              "impasto": "stage 9 -- bristle + taper + the height field"}
    for name, c in cases.items():
        print(f"\n{labels[name]}")
        py = render(c["sb"], H, W, hard=c["hard"], hard_r=c["hard_r"],
                    wobble_amp=c["wobble_amp"], canvas=c["canvas"], split_at=c["split_at"],
                    bristle_amp=c["bristle_amp"], taper_amp=c["taper_amp"],
                    fringe_px=c["fringe_px"],
                    want_height=c["want_height"], impasto_relief=c["impasto_relief"],
                    impasto_layer=c["impasto_layer"], ground=c["ground"])
        py_out, py_cover = py[0], py[1]
        js = got["cases"][name]
        # The fringe's harmonic order multiplies any float32 gap in phi, so cases that use
        # it get the derived FRINGE_SLACK; everything else stays at the tight bound.
        tol = FRINGE_SLACK if c["fringe_px"] > 0 else FLOAT32_SLACK
        close(js["out"], py_out, f"{name}: pixels", tol)
        close(js["cover"], py_cover, f"{name}: coverage buffer", tol)
        if c["split_at"] is not None:
            close(js["coverTail"], py[2], f"{name}: detail-layer coverage", tol)
            # The number the page actually prints. Compared separately from the buffer
            # because it is a THRESHOLD of it -- a pixel sitting on 0.99 can land either
            # side, which is a real risk the buffer comparison cannot see.
            close([js["coveredTail"]], [float((py[2] > 0.99).mean())],
                  f"{name}: coverage_detail statistic")
        close([js["coveredAll"]], [float((py_cover > 0.99).mean())],
              f"{name}: coverage_all statistic")
        if c["want_height"]:
            py_height = py[-1]
            close(js["height"], py_height, f"{name}: height field", tol)
            # A height field that never rose would compare equal to another that never
            # rose, so the paint-order offset has to be shown to be doing something.
            check(float(np.max(py_height)) > 1.5, f"{name}: the height field builds up",
                  f"max height {float(np.max(py_height)):.3f}")
        # A stroke buffer that painted nothing would pass every check above trivially.
        check(float(np.max(py_cover)) > 0.5, f"{name}: the case actually paints something",
              f"max cover {float(np.max(py_cover)):.3f}")
        # The composite drawn in PIECES must equal the same composite drawn whole -- and
        # exactly, not within a tolerance. engine.worker.js slices it so it can reach its
        # message queue between slices and abandon a render the visitor has already
        # superseded; `over` is a left fold along the stroke array, so splitting it and
        # carrying the accumulators is the same computation. A non-zero delta here means
        # the resumption drops or repeats state -- an invisible defect otherwise, because
        # a painting that is merely a bit different still looks like a painting.
        sl = js["sliced"]
        worst = max(sl["out"], sl["cover"], sl["coverTail"], sl["height"])
        check(worst == 0.0, f"{name}: drawn in {len(sl['cuts']) - 1} slices, identical",
              f"max |sliced - whole| = {worst:g} over {sl['cuts']}")

    print("\nstage 10 -- the lighting pass")
    for name, c in lit.items():
        py_lit = light(lit_rgb, lit_h, lit_cover, depth=c["depth"],
                       light_deg=c["light_deg"], elev_deg=c["elev_deg"],
                       gloss=c["gloss"], canvas_weave=c["canvas_weave"],
                       occlusion=c["occlusion"], view_deg=c["view_deg"],
                       view_elev_deg=c["view_elev_deg"])
        close(got["lit"][name], py_lit, f"light/{name}: pixels",
              tol=SPECULAR_SLACK if c["gloss"] > 0.5 else FLOAT32_SLACK)
        if name == "flat":
            # The invariant the whole normalisation exists for: with no slope, shading is
            # a no-op. Without it, switching impasto on would silently rescale the image
            # and every A/B against a non-impasto arm would measure the exposure change.
            worst = float(np.max(np.abs(py_lit - lit_rgb)))
            check(worst < 1e-5, "light/flat: a flat height field leaves the paint alone",
                  f"max |lit - rgb| = {worst:.2e}")
        else:
            moved = float(np.max(np.abs(py_lit - lit_rgb)))
            check(moved > 0.05, f"light/{name}: the shading actually changes the paint",
                  f"max |lit - rgb| = {moved:.3f}")

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED")
        return 1
    print("raster.js matches render.py at every stage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
