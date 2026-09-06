"""Parity test: web/tune/oilpaint/*.js vs oilpaint/*.py (CPU, needs node).

    conda run -n 4dre python tests/test_pipeline_parity.py

The static page runs the JS package; `scripts/paint.py` runs the Python one. Python is the
source of truth and this is what holds the port to it -- every stage, not just the picture,
because a painting that is merely a bit different is exactly the failure this project cannot
see by looking (.claude/skills/python-js-parity/SKILL.md).

Stages, coarse to fine, so a failure localises to one file:

  1  luma, Sobel, Gaussian kernel and blur          image.js / detail.js
  2  summed-area table box queries                  image.js
  3  structure tensor, and sampling from it         tensor.js
  3b the flat brush: decimation, coarse field, blend  image.js / tensor.js / strokes.js
  4  detail scores, for every metric                detail.js
  4b the foveal map's weights and weighted scores  detail.js
  5  tau solve and the quadtree leaves              quadtree.js
  6  the stroke buffer                              strokes.js
  7  the base layer                                 pipeline.js
  8  plan(), then the whole painting and its stats  pipeline.js / render.js / metrics.js
  8c the same, with a foveal map, tilted and gated detail.js / pipeline.js

Tolerances are per-stage and named, because the sources of divergence differ and lumping
them under one number would hide which one moved. Integer results -- stroke counts, the
quadtree leaves, the ceiling -- are compared EXACTLY; there is no float story that would
excuse a different number of strokes.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, replace

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np  # noqa: E402

from oilpaint import quadtree, strokes as strokes_mod  # noqa: E402
from oilpaint.detail import DetailField, FovealField, METRICS, _sobel  # noqa: E402
from oilpaint.image import (  # noqa: E402
    decimate, gaussian_blur, gaussian_kernel1d, luma, sat_box, summed_area,
)
from oilpaint.metrics import edge_alignment, psnr  # noqa: E402
from oilpaint.palette import PALETTES, _mix_lut  # noqa: E402
from oilpaint import flow as flow_mod  # noqa: E402
from oilpaint import regions as regions_mod  # noqa: E402
from oilpaint.pipeline import (  # noqa: E402
    RELIGHT_PARAMS, PaintConfig, finish, paint, plan,
)
from oilpaint.render import base_layer  # noqa: E402
from oilpaint.schema import as_dict as schema_dict  # noqa: E402
from oilpaint.tensor import flat_tensor, sample, sample_angle, structure_tensor  # noqa: E402

TUNE_DIR = os.path.join(_REPO_ROOT, "web", "tune")
SPACK_NODE = ("/cluster/software/stacks/2024-06/spack/opt/spack/linux-ubuntu22.04-x86_64_v3/"
              "gcc-12.2.0/node-js-19.2.0-i6pplhcf7voeure6rh5oeg7lu7cmokt7/bin/node")

# --- tolerances, each with the divergence it is accounting for ----------------------
# Exact. These stages are float32 arithmetic on both sides with no transcendental in the
# path, so there is nothing to round differently.
EXACT = 0.0
# numpy calls the float32 libm (expf/sinf/cosf/atan2f) where JS has only the float64 ones
# and rounds after. That is worth at most an ulp of float32, ~1.2e-7 relative, and it
# reaches the Gaussian kernel, the tensor angles and the sampled orientations.
F32_LIBM = 2e-7
# Coherence is a RATIO of blurred float32 quantities, squared, so the blur's ulp above is
# amplified rather than merely carried. Measured worst case ~4e-7.
BLUR_AMPLIFIED = 2e-6
# The PRE-FILTERED tensor puts a second Gaussian in front of the Sobel, so the kernel's ulp
# is amplified by one more blur than BLUR_AMPLIFIED accounts for -- and the pre-blur is
# applied to the luma itself, where the fine field's blurs are applied to gradient outer
# products that are already small. Measured 2.5e-6 on the fixture against 3.6e-7 for the
# fine coherence; this is the bound with margin, not the value. Its own constant rather
# than a looser BLUR_AMPLIFIED, which still has to pin the fine field tightly.
PREFILTER_AMPLIFIED = 1e-5
# The tensor angle is an atan2 of two blurred float32 quantities, so the kernel's ulp is
# amplified by the ratio. Measured worst case is ~1e-6; this is the bound, not the value.
TENSOR_SLACK = 5e-5
# Stroke geometry inherits the angle slack above and the f32 rounding of the radius chain.
STROKE_SLACK = 5e-5
# The base layer's block mean is a float32 pairwise reduction in numpy over a 5-D shape
# whose reduction order is not reproducible from outside the library; the JS accumulates the
# block in float64, which is more accurate rather than different. This is the one place the
# port deliberately does not chase numpy's exact rounding, and it is the cheapest possible
# place to give that up: the base layer is never seen, because the strokes cover it, and the
# 8-bit check at the end of stage 8 is what proves the choice costs nothing visible.
CANVAS_SLACK = 5e-6
# The pigment grade is nine libm calls deep -- two sRGB transfers (pow) and a cube root per
# channel -- all in float64 on both sides, so what is left is the last ulp of THREE
# different libm functions, amplified by the OKLab matrices and then rounded to float32.
# Measured worst case on the fixture is ~2e-7; this is the bound with margin. The graded
# canvas rides on top of CANVAS_SLACK, which is larger, and is given both.
GRADE_SLACK = 2e-6
# The reference transport is an AFFINE with gain, and it multiplies a disagreement that was
# already there. The base layer is the one place the port deliberately does not chase
# numpy's rounding (see CANVAS_SLACK), so the canvas arrives ~5e-6 apart, and the transport
# carries that straight through: operator norm 4.3 (sunflowers) to 6.2 (starry-night) on
# this fixture, measured 8.3e-6 and 3.8e-6 on the canvas.
#
# The BOUND rather than those measurements is what this number is set from, because another
# image is entitled to a thinner colour cloud than a noise fixture: `reference.SRC_SD_FLOOR`
# caps the gain at sd_target / 0.025, which is ~8 for the widest reference's L axis, so the
# worst case is ~4e-5 and 1e-4 keeps 2.5x on top of it.
#
# The STROKES, whose input is identical on both sides, stay exact at 0.00e+00, and that
# asymmetry is the real assurance: it says the transport arithmetic has not diverged and
# only its INPUT had already. 1e-4 is 1/40th of an 8-bit level, so the contract this repo
# ships on -- agreement at 8 bits -- is untouched either way.
TRANSPORT_AMPLIFIED = 1e-4

H, W = 96, 72
FAILS = []


def find_node():
    return shutil.which("node") or (SPACK_NODE if os.path.exists(SPACK_NODE) else None)


def check(ok, label, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def close(js, py, label, tol):
    a = np.asarray(js, dtype=np.float64).ravel()
    b = np.asarray(py, dtype=np.float64).ravel()
    if a.size != b.size:
        return check(False, label, f"size {a.size} != {b.size}")
    if a.size == 0:
        return check(True, label, "empty")
    worst = float(np.max(np.abs(a - b)))
    check(worst <= tol, label, f"max |js - py| = {worst:.2e}  (tol {tol:.0e})")


def same_ints(js, py, label):
    a = np.asarray(js, dtype=np.int64).ravel()
    b = np.asarray(py, dtype=np.int64).ravel()
    if a.size != b.size:
        return check(False, label, f"size {a.size} != {b.size}")
    bad = int(np.count_nonzero(a != b))
    check(bad == 0, label, f"{a.size} values" if not bad else f"{bad} differ")


def make_image(rng):
    """A synthetic photo with real structure: edges to orient to, a flat patch the tree
    should skip, and noise so tau_floor has something to reject."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    v = 0.5 + 0.32 * np.sin(xx / 11.0) * np.cos(yy / 17.0)
    img = np.stack([v * 0.9, v, 1.0 - v * 0.5], axis=2)
    img[20:50, 15:45] = 0.82                     # flat patch
    img[:, 50:52] = 0.05                         # a hard vertical edge
    img += 0.015 * rng.standard_normal((H, W, 3))
    return np.clip(img * 255, 0, 255).astype(np.uint8)


def make_mask(rng):
    """A hand-painted foveal map, in spirit: one soft blob, one hard-edged rectangle, and a
    lot of nothing. uint8 because that is exactly what the page's brush canvas hands over,
    so both sides start from identical bytes and any divergence is arithmetic, not input."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    m = np.exp(-(((yy - 30.0) ** 2 + (xx - 24.0) ** 2) / (2.0 * 12.0 ** 2)))
    m[62:78, 40:60] = np.maximum(m[62:78, 40:60], 0.65)
    return np.clip(m * 255.0 + 0.5, 0, 255).astype(np.uint8)


def run_node(node, job):
    with tempfile.TemporaryDirectory() as tmp:
        jp, op = os.path.join(tmp, "job.json"), os.path.join(tmp, "out.json")
        with open(jp, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        r = subprocess.run([node, "--max-old-space-size=4096",
                            os.path.join(TUNE_DIR, "pipeline_node.mjs"), jp, op],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"node failed:\n{r.stderr[-3000:]}")
        with open(op, encoding="utf-8") as fh:
            return json.load(fh)


def main():
    node = find_node()
    if node is None:
        print("SKIP  no node on PATH (and no spack node-js) -- the JS package is unchecked")
        return 0
    print(f"node {subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()}")

    rng = np.random.default_rng(5)
    img8 = make_image(rng)
    img = img8.astype(np.float32) / 255.0
    sigma = 2.0
    # The flat brush's own arm. 4.0 rather than the config default of 8.0 because the
    # fixture is 128px: a sigma-8 pre-blur on it flattens the whole frame to one gradient
    # and the blend then has nothing to disagree with the fine field about, which is
    # exactly the case that would pass while being inert. `flat_dir` is deliberately not
    # 1.0, so a JS side that hard-coded the weight would show up.
    flat_sigma, flat_dir, flat_theta_deg = 4.0, 0.8, 45.0
    # `decimate` is checked at a factor of its own rather than at whatever `flat_sigma`
    # happens to imply: 3 does not divide 128, so it also exercises the ragged last block.
    flat_sigma_step = 3

    cfg = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3)
    # The arm above is base='blur', which never builds an underpainting -- so the `under`
    # branch of plan(), with its own kappa, seed offset and aniso override, went unchecked
    # on both sides. base_cell_scale=1.0 keeps the grid coarse but not degenerate on 128px.
    cfg_under = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                            base="strokes", base_cell_scale=1.0)
    # Two foveal arms, because `foveal_strength` has two regimes rather than two settings:
    # at 0.7 the weight only tilts the tree and the budget still binds, while at 1.0 it
    # reaches zero and the map is a hard gate no unmarked cell can pass.
    mask8 = make_mask(rng)
    fmask = mask8.astype(np.float32) / 255.0
    cfg_fov = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                          foveal_strength=0.7)
    cfg_fov_gate = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                               foveal_strength=1.0)
    # The pigment grade, two arms. `preset` is a named palette with every branch live at
    # once -- broken colour (which reads the stroke phase), a strength below 1 (the OKLab
    # lerp), and trims in both directions on top of the preset. `trim` is palette='none'
    # with the sliders alone, which is the case a port that read 'none' as "skip nothing to
    # do" would get silently wrong. Between them every line of palette.py runs.
    cfg_grade = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                            palette="impressionist", palette_strength=0.85,
                            warm_cool=-0.2, chroma=0.15, value_compress=0.4,
                            broken_color=0.5)
    cfg_trim = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                           warm_cool=0.6, chroma=-0.3, value_compress=0.25,
                           broken_color=0.3)
    # Hue-band steering, two arms of its own. `hue` is the band with NO preset behind it --
    # the path where a port that skipped on palette='none' would do nothing at all -- and
    # it turns AND boosts at once, so both accumulators are live. `hue_pal` puts a band
    # under a preset, which is the composition that matters: the band moves the hue and the
    # pigment projection then decides what the tubes can do with it. A negative rotation
    # and a negative gain, because the two are signed and a port that dropped an abs would
    # otherwise pass.
    # The reference transport, two arms. `ref` is it alone, on 'none' -- the path where a
    # port that only ran the transport when a palette was chosen would do nothing. `ref_pal`
    # stacks it under a preset AND a hue band, which is the order that matters: transport,
    # then band, then the grade, then the pigment projection. A strength below 1 so the
    # OKLab blend is live too.
    cfg_ref = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                          reference="starry-night")
    cfg_ref_pal = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                              reference="sunflowers", reference_strength=0.65,
                              palette="old-master", palette_strength=0.8,
                              hue_target=200.0, hue_range=60.0, hue_rotate=18.0,
                              warm_cool=0.25)
    cfg_hue = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                          hue_target=140.0, hue_range=35.0, hue_rotate=-42.0,
                          hue_boost=0.45)
    cfg_hue_pal = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                              palette="fauve", palette_strength=0.9,
                              hue_target=255.0, hue_range=120.0, hue_rotate=25.0,
                              hue_boost=-0.6)
    # The flow field, two arms, one per FIELD KIND rather than one per preset -- the
    # presets' own constants are pinned directly further down, so what these have to cover
    # is the code. `swirl` runs the vortex sum, the turbulence and the drift at once, with
    # trims in both directions and a `flow_coh` that lands exactly on the clamp (0.95 +
    # 0.05). `wave` is the other branch with the trims pushed hard the other way: a
    # NEGATIVE flow_coh, a rotation past 90 degrees, and a negative drift that cancels most
    # of the preset's own -- the cases a port that dropped the trim arithmetic, or read a
    # negative trim as 0, would get silently wrong.
    #
    # `wave` also runs base='strokes', which is the only way to reach the underpainting's
    # flow override: the ground is turned by the field but never drifted by it, because
    # drift bunches strokes and that layer's whole job is to cover.
    cfg_flow = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                           flow="starry", flow_strength=0.9, flow_scale=-0.3,
                           flow_rot=25.0, flow_coh=0.05, flow_drift=0.3)
    cfg_flow_wave = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                                flow="waterlily", flow_strength=0.7, flow_scale=1.4,
                                flow_rot=115.0, flow_coh=-0.15, flow_drift=-0.10,
                                base="strokes")
    # Hand-placed swirl centres, as fractions of the image. Deliberately NOT symmetric and
    # not on the spiral: one near a corner, one off-centre, one just outside [0,1] -- a
    # click can land outside the picture at the edge of the overlay, and the field is
    # defined everywhere, so it must not be clamped or dropped. The image here is not
    # square, which is what makes the fraction -> field-frame conversion falsifiable: a
    # port dividing by the wrong side still looks plausible until it is compared.
    VORTICES = [[0.18, 0.22], [0.77, 0.41], [0.5, 0.86], [1.06, -0.04]]
    # Region-wise colour. The mask is 2D and ASYMMETRIC on a canvas that is taller than it
    # is wide, which is what makes an (x, y) swap in the label lookup falsifiable -- the
    # same reason the placed-vortex arm uses a non-square image.
    ry, rx = np.mgrid[0:H, 0:W]
    region_labels = np.zeros((H, W), dtype=np.uint8)
    region_labels[(ry < H // 2) & (rx < W * 2 // 3)] = 1
    region_labels[(ry >= H // 3) & (rx >= W // 2)] = 2
    # A base that grades AND matches a reference, so both halves of the transport rule are
    # driven: region 1 overrides no reference and must inherit the base's already-fitted
    # one, region 2 names its own and must fit it over its OWN strokes.
    cfg_regions = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                              palette="impressionist", palette_strength=0.85,
                              warm_cool=0.2, reference="great-wave",
                              reference_strength=0.6)
    region_overrides = {"1": {"palette": "zorn", "pigment": 1.0, "hue_target": 40.0,
                              "hue_range": 50.0, "hue_rotate": -30.0},
                        "2": {"reference": "starry-night", "reference_strength": 0.8,
                              "chroma": 0.2}}
    # The equivalence arm: every region asking for exactly what the base already says.
    region_equiv = {"1": {"palette": "impressionist", "warm_cool": 0.2},
                    "2": {"palette": "impressionist", "warm_cool": 0.2}}
    # Region-wise FLOW: the same mask at the OTHER attachment point, where an override
    # moves that passage's strokes instead of only recolouring them. `base='strokes'` is
    # deliberate -- it is what drives `flow_undrifted`, the underpainting's copy of the plan
    # with every region's drift stripped, which nothing else here would visit. Region 1
    # takes a flow of its own, region 2 turns the base's OFF, and the palette rides along so
    # a port that let the flow spec leak into the grade would show up as a colour.
    cfg_region_flow = PaintConfig(target_n=800, max_cell=32, min_cell=4, seed=3,
                                  base="strokes", flow="starry", flow_strength=0.9,
                                  palette="impressionist", palette_strength=0.8)
    region_flow_overrides = {"1": {"flow": "waterlily", "flow_coh": -0.15,
                                   "flow_rot": 25.0, "flow_drift": 0.2},
                             "2": {"flow": "none"}}
    region_flow_equiv = {"1": {"flow": "starry", "flow_strength": 0.9},
                         "2": {"flow": "starry", "flow_strength": 0.9}}
    # One region's OWN swirl centres. Deliberately somewhere the base's four are not, so a
    # port that quietly served the base's set to every field would land the swirls in the
    # wrong half of a picture that is taller than it is wide.
    region_vortices_own = [[0.24, 0.71], [0.61, 0.88], [0.44, 0.52]]
    # Spans the schema's range for both sliders, so it straddles the clamp: the last two
    # are past it, the first three are not.
    KAPPA_PAIRS = [[0.0, 0.0], [0.35, 0.15], [0.6, 0.4], [0.8, 0.6], [1.0, 0.8]]
    boxes = [[0, 0, H, W], [3, 5, 40, 33], [90, 60, 200, 200], [10, 10, 10, 20]]
    sy = list(np.linspace(-2.5, H + 2.5, 40))
    sx = list(np.linspace(-2.5, W + 2.5, 40))
    cell_y = [0, 0, 16, 32, 48, 64, 80, 88]
    cell_x = [0, 16, 32, 48, 64, 8, 24, 40]
    cell_size = 16

    job = {
        "h": H, "w": W, "rgb": img8.ravel().tolist(), "sigma": sigma, "boxes": boxes,
        "flat_sigma": flat_sigma, "flat_dir": flat_dir, "flat_theta_deg": flat_theta_deg,
        "flat_sigma_step": flat_sigma_step,
        "sy": sy, "sx": sx, "metrics": list(METRICS),
        "cell_y": cell_y, "cell_x": cell_x, "cell_size": cell_size,
        "target_n": cfg.target_n, "dmin": 2, "min_cell": cfg.min_cell,
        "tau_floor": cfg.tau_floor, "cfg": asdict(cfg),
        "cfg_under": asdict(cfg_under),
        "mask": mask8.ravel().tolist(),
        "fov": {"strength": 0.7},
        "cfg_fov": asdict(cfg_fov),
        "cfg_fov_gate": asdict(cfg_fov_gate),
        "cfg_grade": asdict(cfg_grade),
        "cfg_trim": asdict(cfg_trim),
        "cfg_hue": asdict(cfg_hue),
        "cfg_ref": asdict(cfg_ref),
        "cfg_ref_pal": asdict(cfg_ref_pal),
        "cfg_hue_pal": asdict(cfg_hue_pal),
        "cfg_flow": asdict(cfg_flow),
        "cfg_regions": asdict(cfg_regions),
        "region_labels": region_labels.ravel().tolist(),
        "region_overrides": region_overrides,
        "region_equiv": region_equiv,
        "cfg_region_flow": asdict(cfg_region_flow),
        "region_flow_overrides": region_flow_overrides,
        "region_flow_equiv": region_flow_equiv,
        "region_vortices_own": region_vortices_own,
        "region_swatch": np.asarray(regions_mod.LEGEND).ravel().tolist(),
        "cfg_flow_wave": asdict(cfg_flow_wave),
        "vortices": VORTICES,
        "kappa_pairs": KAPPA_PAIRS,
        # What build_static.py emits as schema.json, handed to the worker's fetch shim.
        "schema": schema_dict(),
    }
    got = run_node(node, job)

    print("\nstage 1 -- colour, Sobel, Gaussian kernel and blur")
    lum = luma(img).astype(np.float32)
    close(got["luma"], lum, "luma", EXACT)
    gx, gy = _sobel(lum)
    close(got["gx"], gx, "sobel gx", EXACT)
    close(got["gy"], gy, "sobel gy", EXACT)
    close(got["kernel"], gaussian_kernel1d(sigma), "gaussian kernel", F32_LIBM)
    close(got["blur"], gaussian_blur(lum, sigma), "gaussian blur", F32_LIBM)

    print("\nstage 2 -- summed-area table")
    sat = summed_area(lum)
    for i, (y0, x0, y1, x1) in enumerate(boxes):
        tot, area = sat_box(sat, np.array([y0]), np.array([x0]),
                            np.array([y1]), np.array([x1]))
        close([got["satBoxes"][i][0]], [float(tot[0])], f"box {i} sum", EXACT)
        close([got["satBoxes"][i][1]], [float(area[0])], f"box {i} area", EXACT)

    print("\nstage 3 -- structure tensor")
    theta, coh = structure_tensor(img, sigma)
    close(got["theta"], theta, "theta", TENSOR_SLACK)
    close(got["coherence"], coh, "coherence", BLUR_AMPLIFIED)
    ay, ax = np.array(sy), np.array(sx)
    close(got["sampled"], sample(coh, ay, ax), "sample(coherence)", BLUR_AMPLIFIED)
    close(got["sampledAngle"], sample_angle(theta, ay, ax), "sample_angle(theta)",
          TENSOR_SLACK)

    print("\nstage 3b -- the flat-region brush: the decimated coarse field, and the blend")
    # Separate from stage 6 on purpose. A JS side that dropped `theta_flat` on the floor and
    # fell back to the fine field would still hand stage 6 a perfectly plausible stroke
    # buffer -- the OLD one, with the blobs back -- and only these two rows would notice.
    # The decimation first, on its own: it is the one step whose OUTPUT SHAPE differs
    # between the two sides if it is wrong, and a shape mismatch downstream would surface
    # as a plausible-looking field sampled at the wrong pixel rather than as an error.
    close(got["decimated"], decimate(luma(img).astype(np.float32), flat_sigma_step),
          "decimate (block mean)", EXACT)
    theta_f, coh_f, step_f = flat_tensor(img, flat_sigma)
    check(got["flatStep"] == step_f, "flat_step agrees", f"{got['flatStep']} vs {step_f}")
    check(list(got["flatShape"]) == list(theta_f.shape), "the flat field has the same shape",
          f"{got['flatShape']} vs {list(theta_f.shape)}")
    close(got["thetaFlat"], theta_f, "theta (flat field)", TENSOR_SLACK)
    close(got["coherenceFlat"], coh_f, "coherence (flat field)", PREFILTER_AMPLIFIED)
    tb, cb = strokes_mod.flat_blend(
        sample_angle(theta, ay, ax), sample(coh, ay, ax),
        sample_angle(theta_f, ay / step_f, ax / step_f),
        sample(coh_f, ay / step_f, ax / step_f),
        flat_dir, flat_theta_deg)
    close(got["flatTheta"], tb, "flat_blend: theta", TENSOR_SLACK)
    close(got["flatCoh"], cb, "flat_blend: effective coherence", BLUR_AMPLIFIED)
    # The blend must actually be doing something on this fixture, or the two rows above are
    # measuring agreement about a no-op.
    moved = float(np.max(np.abs(cb - sample(coh, ay, ax))))
    print(f"  ok   the blend moves the coherence it is checked on  max |delta| = {moved:.3f}")
    assert moved > 0.05, f"flat_blend is inert on this fixture ({moved})"

    print("\nstage 4 -- detail scores, every metric")
    cy, cx = np.array(cell_y), np.array(cell_x)
    for metric in METRICS:
        fld = DetailField(img, metric, tree_size=quadtree.tree_size(H, W))
        want = fld.score(cy, cx, cell_size)
        # `dct` and `grad` run through float32 transcendentals; `var`/`residual`/`range` do
        # not, so they are held exactly.
        tol = F32_LIBM if metric in ("grad", "dct") else EXACT
        close(got["scores"][metric], want, f"score[{metric}]", tol)

    print("\nstage 4b -- the foveal map: cell weights and the weighted score")
    # EXACT throughout, and that is a claim not a hope: the mask is uint8/255 on both sides,
    # the summed-area table is the same f64 accumulation, and 'var' carries no
    # transcendental. There is nothing here that f32 could excuse.
    inner = DetailField(img, "var", tree_size=quadtree.tree_size(H, W))
    fv = FovealField(inner, fmask, 0.7)
    close(got["foveal"]["maskMean"], fv.mask_mean(cy, cx, cell_size), "mask mean", EXACT)
    close(got["foveal"]["weight"], fv.weight(cy, cx, cell_size), "cell weight", EXACT)
    close(got["foveal"]["score"], fv.score(cy, cx, cell_size), "weighted score", EXACT)
    # The parent colour must travel down UNWEIGHTED -- weighting it would tint the residual
    # metric's own reference value, which is a bug no picture would obviously show.
    close(got["foveal"]["meanLuma"], fv.mean_luma(cy, cx, cell_size),
          "mean luma is passed through unweighted", EXACT)
    # A weight that came out constant would pass every comparison above while steering
    # nothing -- which is exactly how the auto-saliency prefill failed. Pin that it varies.
    wv = got["foveal"]["weight"]
    check(max(wv) - min(wv) > 0.1, "the weight actually varies across cells",
          f"{min(wv):.3f} .. {max(wv):.3f}")

    print("\nstage 5 -- tau solve and quadtree leaves")
    check(got["treeSize"] == quadtree.tree_size(H, W), "tree_size", str(got["treeSize"]))
    fld = DetailField(img, "var", tree_size=quadtree.tree_size(H, W))
    tau, reachable, ceiling = quadtree.solve_tau(
        fld, H, W, cfg.target_n, 2, cfg.min_cell, tau_floor=cfg.tau_floor)
    close([got["tau"]], [tau], "tau", EXACT)
    check(bool(got["reachable"]) == bool(reachable), "budget reachable flag")
    check(int(got["ceiling"]) == int(ceiling), "stroke ceiling",
          f"{got['ceiling']} vs {ceiling}")
    cy0, cx0, cs, cd = quadtree.build(fld, H, W, tau, 2, cfg.min_cell,
                                      tau_floor=cfg.tau_floor)
    check(len(got["cells"]["y0"]) == cy0.size, "leaf count",
          f"{len(got['cells']['y0'])} vs {cy0.size}")
    same_ints(got["cells"]["y0"], cy0, "leaf y0 (and their order)")
    same_ints(got["cells"]["x0"], cx0, "leaf x0")
    same_ints(got["cells"]["size"], cs, "leaf size")
    same_ints(got["cells"]["depth"], cd, "leaf depth")

    print("\nstage 6 -- the stroke buffer")
    sb = strokes_mod.from_cells(
        img, (cy0, cx0, cs, cd), theta, coh,
        kappa=cfg.kappa, jitter_centre=cfg.jitter_centre, jitter_radius=cfg.jitter_radius,
        jitter_theta_deg=cfg.jitter_theta_deg, jitter_aniso=cfg.jitter_aniso,
        aniso_max=cfg.aniso_max, orient=cfg.orient, color=cfg.color, alpha=cfg.alpha,
        drop_p=cfg.drop_p, size_sigma=cfg.size_sigma, color_jitter=cfg.color_jitter,
        seed=cfg.seed)
    check(len(got["strokes"]["x"]) == len(sb), "stroke count",
          f"{len(got['strokes']['x'])} vs {len(sb)}")
    # The colours come straight from the source pixels through a median, so they carry no
    # transcendental and must be exact -- a mismatch there means the gather or the paint
    # ORDER is wrong, which is the failure this whole stage exists to catch.
    close(got["strokes"]["rgb"], sb.rgb, "stroke rgb (and paint order)", EXACT)
    close(got["strokes"]["x"], sb.x, "stroke x", STROKE_SLACK)
    close(got["strokes"]["y"], sb.y, "stroke y", STROKE_SLACK)
    close(got["strokes"]["r_major"], sb.r_major, "stroke r_major", STROKE_SLACK)
    close(got["strokes"]["r_minor"], sb.r_minor, "stroke r_minor", STROKE_SLACK)
    close(got["strokes"]["theta"], sb.theta, "stroke theta", TENSOR_SLACK)
    close(got["strokes"]["phase"], sb.phase, "stroke phase", EXACT)
    close(got["strokes"]["alpha"], sb.alpha, "stroke alpha", EXACT)

    print("\nstage 7 -- base layer")
    close(got["baseLayer"], base_layer(img, cfg.base_block), "base layer", CANVAS_SLACK)

    print("\nstage 8 -- plan(), the painting, and the reported numbers")
    psb, pcanvas, pinfo = plan(img, cfg)
    check(got["plan"]["n"] == len(psb), "plan: total strokes",
          f"{got['plan']['n']} vs {len(psb)}")
    check(got["plan"]["n_under"] == pinfo["n_under"], "plan: underpainting strokes",
          f"{got['plan']['n_under']} vs {pinfo['n_under']}")
    check(got["plan"]["n_detail"] == pinfo["n_detail"], "plan: detail strokes",
          f"{got['plan']['n_detail']} vs {pinfo['n_detail']}")
    check(got["plan"]["dmin"] == pinfo["dmin"], "plan: derived dmin")
    close([got["plan"]["tau"]], [pinfo["tau"]], "plan: tau", EXACT)
    close(got["plan"]["rgb"], psb.rgb, "plan: stroke colours, both layers", EXACT)
    close(got["plan"]["r_major"], psb.r_major, "plan: r_major, both layers", STROKE_SLACK)

    ref, rinfo, rsb = paint(img, cfg)
    close(got["painting"], ref, "the painting itself", STROKE_SLACK)
    ref8 = (np.clip(ref, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    js8 = (np.clip(np.asarray(got["painting"], np.float32).reshape(ref.shape), 0, 1)
           * 255.0 + 0.5).astype(np.uint8)
    diff = np.abs(js8.astype(np.int16) - ref8.astype(np.int16))
    check(int(diff.max()) <= 1, "the painting at 8 bits",
          f"max |delta| = {int(diff.max())} level(s), "
          f"{float((diff > 0).mean()):.4%} of samples differ at all")

    close([got["info"]["coverage"]], [rinfo["coverage"]], "coverage_detail", 2e-3)
    close([got["info"]["coverage_all"]], [rinfo["coverage_all"]], "coverage_all", 2e-3)
    # Tighter than the two above, and it can be: `bare` is a count of pixels below 0.05,
    # so the float32 slack in `cover` only ever flips a pixel that sits within an epsilon
    # of the threshold, where the `> 0.99` pair straddle the far busier fully-painted
    # boundary. A drift here is a port bug, not a rounding one.
    close([got["info"]["bare"]], [rinfo["bare"]], "bare", 1e-4)
    close([got["psnr"]], [psnr(ref, img)], "psnr", 1e-3)
    close([got["edgeAlignment"]], [edge_alignment(rsb, img)], "edge_alignment", 1e-4)

    print("\nstage 8b -- base='strokes': the underpainting layer")
    u = got["under"]
    upsb, _, upinfo = plan(img, cfg_under)
    uref, urinfo, _ = paint(img, cfg_under)
    check(u["n_under"] == upinfo["n_under"], "under: stroke count",
          f"{u['n_under']} vs {upinfo['n_under']}")
    check(u["n_under"] > 0, "under: the layer is actually populated", str(u["n_under"]))
    close(u["rgb"], upsb.rgb, "under: stroke colours, both layers", EXACT)
    close(u["r_major"], upsb.r_major, "under: r_major, both layers", STROKE_SLACK)
    # The covering guarantee is the POINT of this layer, so pin the ground round rather
    # than trusting that the aniso override was mirrored: r_major == r_minor exactly, for
    # the first n_under strokes and for those only.
    n_u = upinfo["n_under"]
    close(np.asarray(u["r_minor"][:n_u]), np.asarray(u["r_major"][:n_u]),
          "under: the ground is round (r_minor == r_major)", EXACT)
    check(not np.allclose(upsb.r_major[n_u:], upsb.r_minor[n_u:]),
          "under: the DETAIL layer is still elongated")
    close(u["painting"], uref, "under: the painting itself", STROKE_SLACK)
    close([u["coverage_all"]], [urinfo["coverage_all"]], "under: coverage_all", 2e-3)
    close([u["bare"]], [urinfo["bare"]], "under: bare", 1e-4)
    # The arm above runs at the default jitters, where the clamp inside covering_kappa is
    # inert -- so the clamp itself would go unmirrored without this. Both sides are plain
    # doubles in the same order, hence EXACT.
    close(u["kappas"], [strokes_mod.covering_kappa(jc, jr) for jc, jr in KAPPA_PAIRS],
          "under: covering kappa across the slider range, clamp included", EXACT)
    check(max(u["kappas"]) <= strokes_mod.KAPPA_COVER_MAX,
          "under: the clamp actually binds",
          f"max {max(u['kappas']):.3f} <= {strokes_mod.KAPPA_COVER_MAX}")

    print("\nstage 8d -- the pigment grade")
    # The mixture LUT first, per palette: it is the input to every projection below, so a
    # divergence here would otherwise surface as "the colours are a bit different" rather
    # than as "the subtractive mixing model disagrees".
    for name in PALETTES:
        lut = _mix_lut(name)
        js_lut = got["mixLuts"][name]
        if lut is None:
            check(js_lut is None, f"{name}: no pigments, no mixtures")
            continue
        check(len(js_lut) == lut.size, f"{name}: mixture count",
              f"{len(js_lut)} vs {lut.size}")
        close(js_lut, lut.ravel(), f"{name}: the reachable mixtures", GRADE_SLACK)

    for name, gcfg in (("preset", cfg_grade), ("trim", cfg_trim),
                       ("hue", cfg_hue), ("hue_pal", cfg_hue_pal),
                       ("ref", cfg_ref), ("ref_pal", cfg_ref_pal)):
        g = got["grade"][name]
        gsb, gcanvas, ginfo = plan(img, gcfg)
        gref, _, _ = paint(img, gcfg)
        check(g["palette"] is True, f"{name}: the grade actually ran")
        # THE invariant the whole placement rests on: colour moved, geometry did not.
        close(g["rgb"], gsb.rgb, f"{name}: graded stroke colours", GRADE_SLACK)
        close(g["x"], gsb.x, f"{name}: stroke x, unmoved by the grade", STROKE_SLACK)
        close(g["r_major"], gsb.r_major, f"{name}: r_major, unmoved by the grade",
              STROKE_SLACK)
        # A reference transport amplifies the base layer's pre-existing gap; see
        # TRANSPORT_AMPLIFIED. Everything else on this arm is held to the usual bounds.
        amp = gcfg.reference != "none"
        close(g["canvas"], gcanvas, f"{name}: graded base canvas",
              TRANSPORT_AMPLIFIED if amp else CANVAS_SLACK + GRADE_SLACK)
        close(g["painting"], gref, f"{name}: the graded painting",
              TRANSPORT_AMPLIFIED if amp else STROKE_SLACK)
        check(not np.allclose(gsb.rgb, psb.rgb), f"{name}: the grade is not a no-op",
              f"mean |delta| = {float(np.abs(gsb.rgb - psb.rgb).mean()):.4f}")
        # Same strokes, different paint: an implementation that graded the SOURCE would
        # move the tree and fail here rather than merely looking different.
        check(np.array_equal(gsb.x, psb.x) and np.array_equal(gsb.r_major, psb.r_major),
              f"{name}: the geometry is bit-identical to the ungraded plan")

    print("\nstage 8d(ii) -- the graded canvas has its own memo slot")
    # A graded plan through a warm cache must not leave the coloured ground behind for the
    # next ungraded one. Keying the slot on the grade alone would pass every check above.
    gc = got["gradeCache"]
    close(gc["rgb"], psb.rgb, "cached: ungraded colours after a graded pass", EXACT)
    close(gc["canvas"], pcanvas, "cached: ungraded canvas after a graded pass",
          CANVAS_SLACK)

    print("\nstage 8e -- the artistic flow field")
    # The PRESET BLOCKS first, per name, before any painting. Every preset is a set of
    # constants the JS repeats by hand, and a typo in one no arm below happens to select
    # would surface only as "the browser one looks a bit off" -- the exact class of bug
    # this suite exists to catch, and the same reason the pigment mixture LUTs are pinned
    # directly rather than through a painting.
    fsy, fsx = np.asarray(sy, np.float64), np.asarray(sx, np.float64)
    fu, fv = flow_mod.norm_coords(fsy, fsx, H, W)
    for name in flow_mod.FLOWS:
        fp = flow_mod.params_for(PaintConfig(flow=name))
        js_p = got["flowParams"][name]
        if fp is None:
            check(js_p is None, f"flow {name}: no block, nothing to run")
            check(got["flowDir"][name] is None, f"flow {name}: no field either")
            continue
        check(js_p is not None, f"flow {name}: the block exists on both sides")
        check(js_p["kind"] == fp["kind"], f"flow {name}: field kind", str(js_p["kind"]))
        nums = sorted(k for k, v in fp.items() if isinstance(v, (int, float)))
        close([js_p[k] for k in nums], [fp[k] for k in nums],
              f"flow {name}: the preset block ({len(nums)} constants)", EXACT)
        # And the field those constants generate, probed at the same coordinates the tensor
        # stages use -- including points off the canvas, which strokes reach via the jitter.
        close(got["flowDir"][name], flow_mod.flow_dir(fu, fv, fp),
              f"flow {name}: the field itself", TENSOR_SLACK)

    # The spiral the presets fall back to, and the fractions -> field-frame conversion --
    # the one place a swirl can end up half a canvas away from where it was clicked.
    sp = flow_mod.params_for(PaintConfig(flow="starry"))
    close(got["flowSpiral"][0], flow_mod.spiral_centres(sp)[0], "spiral centres: u", EXACT)
    close(got["flowSpiral"][1], flow_mod.spiral_centres(sp)[1], "spiral centres: v", EXACT)
    pv = flow_mod.params_for(PaintConfig(flow="starry"), VORTICES)
    pc = flow_mod.field_centres(pv, H, W)
    close(got["flowPlacedC"][0], pc[0], "placed centres: u (non-square canvas)", EXACT)
    close(got["flowPlacedC"][1], pc[1], "placed centres: v (non-square canvas)", EXACT)
    close(got["flowPlacedDir"], flow_mod.flow_dir(fu, fv, pv, pc),
          "the field around the placed centres", TENSOR_SLACK)
    wv = flow_mod.params_for(PaintConfig(flow="waterlily"), VORTICES)
    check(flow_mod.field_centres(wv, H, W) is None,
          "a wave preset has no vortices to place, on either side")
    close(got["flowWaveIgnores"], flow_mod.flow_dir(fu, fv, wv),
          "a wave field is untouched by placed points", TENSOR_SLACK)

    for name, fcfg, vtx in (("swirl", cfg_flow, None), ("wave", cfg_flow_wave, None),
                            ("placed", cfg_flow, VORTICES)):
        arm = got["flow"][name]
        wsb, _, winfo = plan(img, fcfg, None, vtx)
        wref, _, _ = paint(img, fcfg, None, vtx)
        check(arm["vortices"] == winfo["flow_vortices"],
              f"{name}: placed-vortex count", f"{arm['vortices']} vs {winfo['flow_vortices']}")
        # The SAME config with the field switched off, so the invariants below compare
        # like with like -- the wave arm runs base='strokes' and so has an underpainting
        # the top-of-file reference plan does not.
        usb, _, _ = plan(img, replace(fcfg, flow="none"), None, vtx)
        check(arm["flow"] is True, f"{name}: the field actually ran")
        check(arm["n"] == len(wsb), f"{name}: stroke count",
              f"{arm['n']} vs {len(wsb)}")
        check(arm["n_under"] == winfo["n_under"], f"{name}: underpainting size",
              f"{arm['n_under']} vs {winfo['n_under']}")
        close(arm["theta"], wsb.theta, f"{name}: stroke theta", TENSOR_SLACK)
        close(arm["r_major"], wsb.r_major, f"{name}: r_major", STROKE_SLACK)
        close(arm["r_minor"], wsb.r_minor, f"{name}: r_minor", STROKE_SLACK)
        # x and y are the DRIFT: the one term in the feature that moves a mark rather than
        # turning it, and the only reason these two are not exact.
        close(arm["x"], wsb.x, f"{name}: stroke x, drifted along the flow", STROKE_SLACK)
        close(arm["y"], wsb.y, f"{name}: stroke y, drifted along the flow", STROKE_SLACK)
        close(arm["painting"], wref, f"{name}: the painting itself", STROKE_SLACK)
        # THE invariant the placement rests on, and the mirror of the grade's: the field
        # turns and moves the marks, and touches nothing the tree decided. Colours are
        # compared EXACT against the unstyled plan, not merely against the JS -- a drifted
        # stroke must still carry its own CELL's paint, not the paint where it landed.
        check(np.array_equal(wsb.rgb, usb.rgb),
              f"{name}: stroke colours and paint order bit-identical to the unstyled plan")
        check(np.array_equal(wsb.r_major * wsb.r_minor, usb.r_major * usb.r_minor)
              or np.allclose(wsb.r_major * wsb.r_minor, usb.r_major * usb.r_minor,
                             rtol=1e-6),
              f"{name}: painted AREA per stroke is untouched -- the field changed shape "
              f"and direction, not size")
        check(not np.allclose(wsb.theta, usb.theta), f"{name}: the field is not a no-op",
              f"mean |dtheta| = {float(np.abs(wsb.theta - usb.theta).mean()):.4f}")

    print("\nstage 8g -- region-wise colour")
    # The DERIVED TABLES first, per the same rule the pigment LUTs follow: a colour field
    # added to one side's REGION_PARAMS and not the other's does not throw -- it gives the
    # CLI a per-region control the browser silently ignores.
    check(got["regionParams"] == sorted(regions_mod.REGION_PARAMS),
          "the two languages agree on what a region may set",
          str(sorted(set(got["regionParams"]) ^ set(regions_mod.REGION_PARAMS))))
    # And on the SPLIT, not only the union: the two halves attach at different points, so a
    # field that drifted from one set to the other on one side would be honoured at the
    # wrong seam -- a colour graded before the geometry, or a flow that never ran.
    for half in ("Color", "Flow"):
        want = sorted(getattr(regions_mod, f"REGION_{half.upper()}_PARAMS"))
        check(got[f"region{half}Params"] == want,
              f"...and on which of them are the {half.lower()} half",
              str(sorted(set(got[f"region{half}Params"]) ^ set(want))))
    check(got["regionMax"] == regions_mod.MAX_REGIONS,
          "and on how many regions there are", str(got["regionMax"]))
    close(np.asarray(got["regionLegend"]).ravel(),
          np.asarray(regions_mod.LEGEND).ravel(), "the mask legend", EXACT)
    same_ints(got["regionLabelsFromImage"], list(range(len(regions_mod.LEGEND))),
              "and every legend colour reads back as its own id on both sides")

    rspec = {"labels": region_labels, "overrides":
             {int(k): v for k, v in region_overrides.items()}}
    rsb, rcanvas, rinfo = plan(img, cfg_regions, regions=rspec)
    rpaint, _, _ = paint(img, cfg_regions, regions=rspec)
    r = got["regions"]
    # THE LABELS, which are the only genuinely new arithmetic on this stage -- everything
    # after them is `palette.grade` on a gathered subset. Exact, and not because the values
    # happen to be integers: `np.floor` against `Math.round` would put the strokes on a
    # boundary in the wrong passage, which is not a tolerance question.
    plab = regions_mod.stroke_labels(regions_mod.params_for(cfg_regions, rspec),
                                     rsb.x, rsb.y)
    same_ints(r["labels"], plab, "the region under each stroke centre, exactly")
    check(r["count"] == rinfo["regions"] == 2, "both sides divided the picture in two",
          f"{r['count']} vs {rinfo['regions']}")
    same_ints([r["strokes"][k] for k in sorted(r["strokes"])],
              [rinfo["region_strokes"][k] for k in sorted(rinfo["region_strokes"])],
              f"and caught the same strokes in each ({rinfo['region_strokes']})")
    close(r["rgb"], rsb.rgb, "region-graded stroke colours", GRADE_SLACK)
    close(r["x"], rsb.x, "stroke x, unmoved by the regions", STROKE_SLACK)
    # A reference transport amplifies the base layer's pre-existing gap, and this arm has
    # two of them -- the base's and region 2's own fit. See TRANSPORT_AMPLIFIED.
    close(r["canvas"], rcanvas, "the region-graded ground", TRANSPORT_AMPLIFIED)
    close(r["painting"], rpaint, "the region-graded painting", TRANSPORT_AMPLIFIED)
    check(bool(np.array_equal(rsb.x, psb.x)),
          "the geometry is bit-identical to the ungraded plan")

    # The two arms that must come back as the UNDIVIDED painting, checked INSIDE the port
    # rather than against the Python: these are claims about the port's own two code paths,
    # and comparing them to the Python instead would let a JS that skipped when it should
    # grade pass by matching a Python that did the same.
    flat_sb, flat_canvas, _ = plan(img, cfg_regions)
    check(r["inertCount"] == 0 and r["inertSame"] is True,
          "a mask nobody overrides is skipped, not graded as an identity")
    check(r["equivSame"] is True,
          "and every region set to the base's own values repaints the base's painting, "
          "bit for bit")
    # The same two, on the Python, so neither side can be the only one that holds.
    ib, ic, iinfo = plan(img, cfg_regions,
                         regions={"labels": region_labels, "overrides": {1: {}}})
    check(iinfo["regions"] == 0 and bool(np.array_equal(ib.rgb, flat_sb.rgb))
          and bool(np.array_equal(ic, flat_canvas)),
          "...and the Python skips it too")
    eb, ec, _ = plan(img, cfg_regions, regions={
        "labels": region_labels,
        "overrides": {int(k): v for k, v in region_equiv.items()}})
    check(bool(np.array_equal(eb.rgb, flat_sb.rgb)) and bool(np.array_equal(ec, flat_canvas)),
          "...and so does its equivalence arm")
    check(not np.allclose(rsb.rgb, flat_sb.rgb),
          "while a live region really does repaint one passage",
          f"mean |delta| = {float(np.abs(rsb.rgb - flat_sb.rgb).mean()):.4f}")

    # THE SAME MASK THROUGH THE REAL WORKER, which is the only place the page's own wire
    # format is exercised: a labels buffer, a serial, and the captures as plain JSON. The
    # arms above drive `plan` directly, so a worker that dropped the mask on the floor --
    # or handed it to the wrong parameter -- would pass every one of them.
    wrgn = got["worker"]["rgnStats"] or {}
    check(wrgn.get("regions") == 2,
          "the worker read the mask off the render message", str(wrgn.get("regions")))
    same_ints([wrgn["region_strokes"][k] for k in sorted(wrgn.get("region_strokes") or {})],
              [rinfo["region_strokes"][k] for k in sorted(rinfo["region_strokes"])],
              "and caught the same strokes plan() did")
    if got["worker"]["rgnPixels"]:
        wpx = np.asarray(got["worker"]["rgnPixels"], dtype=np.uint8).reshape(H, W, 4)
        ref = np.clip(rpaint * 255.0 + 0.5, 0, 255).astype(np.uint8)
        d = np.abs(wpx[:, :, :3].astype(int) - ref.astype(int))
        check(int(d.max()) <= 1, "and painted the picture plan() describes",
              f"max |delta| = {int(d.max())} level(s)")

    print("\nstage 8h -- region-wise flow")
    # The same mask at the other attachment point. Everything on stage 8g was `palette.grade`
    # on a gathered subset with the geometry pinned; here the geometry is what MOVES, so the
    # comparisons are the flow arm's (theta at TENSOR_SLACK, the drifted centres at
    # STROKE_SLACK) aimed at a picture that is combed three different ways at once.
    fspec = {"labels": region_labels,
             "overrides": {int(k): v for k, v in region_flow_overrides.items()}}
    fsb, fcanvas, finfo = plan(img, cfg_region_flow, regions=fspec)
    fpaint, _, _ = paint(img, cfg_region_flow, regions=fspec)
    f = got["regionFlow"]
    flab = regions_mod.stroke_labels(
        regions_mod.flow_params_for(cfg_region_flow, fspec), fsb.x, fsb.y)
    same_ints(f["labels"], flab, "the region under each stroke centre, exactly")
    check(f["flowCount"] == finfo["flow_regions"] == 2
          and f["count"] == finfo["regions"] == 2,
          "both sides combed two passages of the picture separately",
          f"{f['flowCount']}/{f['count']} vs {finfo['flow_regions']}/{finfo['regions']}")
    check(f["nUnder"] == finfo["n_under"] and finfo["n_under"] > 0,
          "and laid the same underpainting under them -- the drift-stripped plan",
          f"{f['nUnder']} vs {finfo['n_under']}")
    same_ints([f["strokes"][k] for k in sorted(f["strokes"])],
              [finfo["region_strokes"][k] for k in sorted(finfo["region_strokes"])],
              f"and caught the same strokes in each ({finfo['region_strokes']})")
    close(f["theta"], fsb.theta, "per-region stroke theta", TENSOR_SLACK)
    close(f["rMajor"], fsb.r_major, "per-region r_major", STROKE_SLACK)
    # x and y are the DRIFT again, and here two regions drift by different amounts.
    close(f["x"], fsb.x, "stroke x, drifted by its own region's flow", STROKE_SLACK)
    close(f["y"], fsb.y, "stroke y, drifted by its own region's flow", STROKE_SLACK)
    close(f["rgb"], fsb.rgb, "per-region stroke colours", GRADE_SLACK)
    close(f["painting"], fpaint, "the per-region-combed painting", STROKE_SLACK)

    # THE CLAIM, on the Python, since the JS matching it above is only half the story: the
    # passage that was left alone must come back to the base's field BIT-identically, and
    # the one that named 'none' to the picture's own -- served by skipping, not by blending.
    all_sb, _, _ = plan(img, cfg_region_flow)
    off_sb, _, _ = plan(img, replace(cfg_region_flow, flow="none"))
    check(finfo["flow_regions"] == 2 and not np.array_equal(fsb.theta, all_sb.theta),
          "a live flow region really does re-comb one passage")
    check(bool(np.array_equal(fsb.rgb, all_sb.rgb)),
          "while every stroke still carries its own cell's paint, in the same order")
    # Region 2 asked for no flow at all, so its strokes must equal the UNSTYLED plan's.
    # Compared on the mask's own labels rather than on the buffer, because region 1 drifts.
    two = regions_mod.stroke_labels({"labels": region_labels}, off_sb.x, off_sb.y) == 2
    check(bool(two.any()) and bool(np.array_equal(fsb.theta[two], off_sb.theta[two])),
          "and a region set to flow='none' comes back to the picture's own angle exactly")

    # The two port-internal arms, checked INSIDE the JS for the reason stage 8g gives: a
    # port that skipped when it should blend would pass a comparison against a Python that
    # did the same, so each side has to hold the claim on its own.
    check(f["inertFlowCount"] == 0, "a colour-only region never enters the port's flow path")
    check(f["equivSame"] is True,
          "and every region set to the base's own flow repaints the base's painting, "
          "bit for bit")
    ib2, _, ii2 = plan(img, cfg_region_flow,
                       regions={"labels": region_labels, "overrides": {1: {"chroma": 0.2}}})
    check(ii2["flow_regions"] == 0 and bool(np.array_equal(ib2.theta, all_sb.theta)),
          "...and the Python skips it too")
    eb2, _, _ = plan(img, cfg_region_flow, regions={
        "labels": region_labels,
        "overrides": {int(k): v for k, v in region_flow_equiv.items()}})
    check(all(bool(np.array_equal(getattr(eb2, k), getattr(all_sb, k)))
              for k in ("theta", "x", "y", "r_major", "r_minor", "rgb")),
          "...and so does its equivalence arm")

    print("\nstage 8i -- swirl centres per region")
    # The RULE first, not only a painting made under it: an inheritance that agreed on this
    # one picture and diverged on the next would pass a pixel comparison and still be wrong.
    vm = flow_mod.vortex_map({0: VORTICES, 1: region_vortices_own})
    vc = got["vortexCentres"]
    check(vc["plainIsBase"] is True,
          "a plain list is the base's set on both sides, so no caller had to change")
    for name, want in (("base", flow_mod.centres_for(vm, 0)),
                       ("own", flow_mod.centres_for(vm, 1)),
                       ("inherited", flow_mod.centres_for(
                           flow_mod.vortex_map(VORTICES), 1)),
                       ("spiral", flow_mod.centres_for(
                           flow_mod.vortex_map({0: VORTICES, 1: []}), 1))):
        close(np.asarray(vc[name] or [], dtype=float).ravel(),
              np.asarray(want or (), dtype=float).ravel(),
              f"centres_for: the {name} set", EXACT)

    equiv_ov = {int(k): v for k, v in region_flow_equiv.items()}
    rspec_e = {"labels": region_labels, "overrides": equiv_ov}
    inh_sb, _, inh_i = plan(img, cfg_region_flow, vortices=VORTICES, regions=rspec_e)
    own_sb, _, own_i = plan(img, cfg_region_flow,
                            vortices={0: VORTICES, 1: region_vortices_own},
                            regions=rspec_e)
    own_paint, _, _ = paint(img, cfg_region_flow,
                            vortices={0: VORTICES, 1: region_vortices_own},
                            regions=rspec_e)
    rv = got["regionVortices"]
    close(rv["inhTheta"], inh_sb.theta, "theta with every region inheriting", TENSOR_SLACK)
    close(rv["ownTheta"], own_sb.theta, "theta with one region placing its own",
          TENSOR_SLACK)
    close(rv["ownX"], own_sb.x, "stroke x under two sets of centres", STROKE_SLACK)
    close(rv["ownY"], own_sb.y, "stroke y under two sets of centres", STROKE_SLACK)
    close(rv["painting"], own_paint, "the painting under two sets of centres", STROKE_SLACK)
    check(rv["inhCount"] == inh_i["flow_vortices"] == len(VORTICES),
          "centres read by several fields are counted once, on both sides",
          f"{rv['inhCount']} vs {inh_i['flow_vortices']}")
    check(rv["ownCount"] == own_i["flow_vortices"]
          == len(VORTICES) + len(region_vortices_own),
          "and two sets are counted once each", f"{rv['ownCount']} vs "
          f"{own_i['flow_vortices']}")
    # The equivalence arm on both sides, for the reason stage 8g gives: a port that skipped
    # where it should inherit would match a Python that made the same mistake.
    flat_sb, _, _ = plan(img, cfg_region_flow, vortices=VORTICES)
    check(rv["inhSame"] is True, "the port repaints the base's painting when every region "
          "inherits its centres")
    check(all(bool(np.array_equal(getattr(inh_sb, k), getattr(flat_sb, k)))
              for k in ("theta", "x", "y", "r_major", "r_minor", "rgb")),
          "...and so does the Python, bit for bit")
    check(not np.array_equal(own_sb.theta, inh_sb.theta),
          "while a set of its own really does re-aim that passage")

    print("\nstage 8f -- the relight parameter set")
    # Which parameters `finish` can re-apply to an ALREADY-RASTERISED painting, and so which
    # the worker's relight fast path may serve without touching the rasteriser. The Python
    # holds the reasoning next to the `finish` that makes it true, and test_core.py checks the
    # claim by perturbation; what is checked HERE is only that the port agrees about the set.
    # A stale entry on the JS side is a wrong picture produced 42x faster.
    check(got["relightParams"] == sorted(RELIGHT_PARAMS),
          "the two languages agree on which parameters are lighting-only",
          str(got["relightParams"]))

    print("\nstage 8c -- the whole pipeline with a foveal map")
    fov_cfgs = {"blend": cfg_fov, "gate": cfg_fov_gate}
    fov_ref8 = {}
    for name, fcfg in fov_cfgs.items():
        arm = got["fovArms"][name]
        fpsb, _, fpinfo = plan(img, fcfg, fmask)
        fref, frinfo, _ = paint(img, fcfg, fmask)
        check(arm["foveal"] is True, f"{name}: the map is actually in play")
        check(arm["n"] == len(fpsb), f"{name}: stroke count",
              f"{arm['n']} vs {len(fpsb)}")
        close([arm["tau"]], [fpinfo["tau"]], f"{name}: tau", EXACT)
        check(bool(arm["reachable"]) == bool(fpinfo["budget_reachable"]),
              f"{name}: budget reachable flag", str(arm["reachable"]))
        close(arm["rgb"], fpsb.rgb, f"{name}: stroke colours and paint order", EXACT)
        close(arm["r_major"], fpsb.r_major, f"{name}: r_major", STROKE_SLACK)
        close(arm["painting"], fref, f"{name}: the painting itself", STROKE_SLACK)
        fov_ref8[name] = (np.clip(fref, 0, 1) * 255.0 + 0.5).astype(np.uint8)
        jsf8 = (np.clip(np.asarray(arm["painting"], np.float32).reshape(fref.shape), 0, 1)
                * 255.0 + 0.5).astype(np.uint8)
        fd = np.abs(jsf8.astype(np.int16) - fov_ref8[name].astype(np.int16))
        check(int(fd.max()) <= 1, f"{name}: the painting at 8 bits",
              f"max |delta| = {int(fd.max())} level(s)")
    # The map must MOVE the allocation, not merely be accepted. Without this every check
    # above would pass on a map that was read and then quietly ignored.
    check(got["fovArms"]["blend"]["n"] != got["plan"]["n"],
          "the map actually changes the tree",
          f"{got['fovArms']['blend']['n']} strokes vs {got['plan']['n']} without it")
    # ...and the gate must bite harder than the tilt, or the strength slider's top half is
    # doing nothing that its bottom half was not already doing.
    check(got["fovArms"]["gate"]["n"] < got["fovArms"]["blend"]["n"],
          "strength 1 gates harder than 0.7 tilts",
          f"{got['fovArms']['gate']['n']} vs {got['fovArms']['blend']['n']} strokes")

    print("\nstage 8c(ii) -- the foveal memo, which has its own key")
    fc = got["fovCached"]
    check(fc["firstN"] == fc["secondN"], "cached: same stroke count on the second pass")
    close(fc["secondRgb"], plan(img, cfg_fov, fmask)[0].rgb,
          "cached: stroke colours still match Python", EXACT)
    # THE stale-map bug: same config, same cache, a different mask. A memo key that named
    # only the parameters would hand back the previous brush stroke's tree, and every
    # uncached check in this file would still be green.
    check(fc["invertedN"] == fc["invertedUncachedN"],
          "a new map through a warm cache is not served the old one",
          f"{fc['invertedN']} cached vs {fc['invertedUncachedN']} uncached")
    close([fc["invertedTau"]], [fc["invertedUncachedTau"]], "cached: tau for the new map",
          EXACT)
    check(fc["invertedN"] != fc["firstN"], "the two maps really do differ in the tree",
          f"{fc['invertedN']} vs {fc['firstN']}")

    print("\nstage 9 -- the real engine.worker.js, driven as the page drives it")
    wk = got["worker"]
    check(wk["readyPigments"] == sorted(PALETTES),
          "worker's ready message carries a pigment set per palette",
          f"{wk['readyPigments']}")
    check(wk["ready"], "worker boots and returns the schema",
          f"{wk['schemaControls']} controls")
    check(wk["schemaControls"] == len(job["cfg"]) - 1,   # every field but `tau`
          "worker's schema covers every tunable field",
          f"{wk['schemaControls']} vs {len(job['cfg']) - 1}")
    # The page starts every control from these, so a ready message without them builds a
    # panel with no values in it. Counted separately from the schema because the two used
    # to arrive as one object, which is exactly how the page came to hang on boot.
    check(wk["readyDefaults"] == wk["schemaControls"],
          "worker's ready message carries a default per control",
          f"{wk['readyDefaults']} defaults vs {wk['schemaControls']} controls")
    check(wk["type"] == "done", "worker completes a render", str(wk.get("message")))
    # The progress stream the page's busy pill and in-progress canvas are driven by. It
    # shares an id with the answer, so a regression here does not look like missing
    # feedback -- it looks like the page reading a stage name as its finished painting.
    # Named for the `info.timing` keys on purpose; the pill and the stats then agree.
    stages = wk.get("stages") or []
    want = ["detail", "quadtree", "tensor", "strokes", "canvas", "painting"]
    check(stages[:len(want)] == want, "worker reports every stage, in pipeline order",
          str(stages))
    if wk["type"] == "done":
        check(wk["w"] == W and wk["h"] == H, "worker returns the right size",
              f"{wk['w']}x{wk['h']}")
        px = np.asarray(wk["pixels"], dtype=np.uint8).reshape(H, W, 4)
        check(bool((px[:, :, 3] == 255).all()), "worker's result is fully opaque")
        # The glue must not merely produce *a* painting -- it must produce THE painting.
        wdiff = np.abs(px[:, :, :3].astype(np.int16) - ref8.astype(np.int16))
        check(int(wdiff.max()) <= 1, "worker's pixels match paint()",
              f"max |delta| = {int(wdiff.max())} level(s)")
        check(wk["stats"]["strokes"] == rinfo["n_detail"], "worker reports the stroke count",
              f"{wk['stats']['strokes']} vs {rinfo['n_detail']}")
        check(wk["stats"]["size"] == f"{W}x{H}", "worker reports the size",
              wk["stats"]["size"])
        # The SECOND render goes through the worker's memo. A cache that returns something
        # subtly different on a hit would pass every other check in this file, all of which
        # run uncached -- and it has already been wrong once.
        check(wk["secondType"] == "done", "worker completes a second, cached render",
              str(wk.get("secondMessage")))
        if wk["secondType"] == "done":
            px2 = np.asarray(wk["secondPixels"], dtype=np.uint8).reshape(H, W, 4)
            check(bool((px2 == px).all()),
                  "the cached render is identical to the uncached one",
                  f"{int((px2 != px).sum())} bytes differ")
            check(wk["secondStats"]["strokes"] == wk["stats"]["strokes"],
                  "the cached render reports the same stroke count")
        # A render WITH a mask, through the same worker. This is the only check on the
        # worker's own byte -> float conversion and its mask plumbing, and marshalling glue
        # is where the last hard-to-see bug in this file lived.
        check(wk["fovType"] == "done", "worker completes a render with a foveal map",
              str(wk.get("fovMessage")))
        if wk["fovType"] == "done":
            fpx = np.asarray(wk["fovPixels"], dtype=np.uint8).reshape(H, W, 4)
            fwdiff = np.abs(fpx[:, :, :3].astype(np.int16)
                            - fov_ref8["blend"].astype(np.int16))
            check(int(fwdiff.max()) <= 1, "worker's foveal pixels match paint()",
                  f"max |delta| = {int(fwdiff.max())} level(s)")
            check(wk["fovStats"]["foveal"] is True, "worker reports the map as in play")
            check(not bool((fpx[:, :, :3] == px[:, :, :3]).all()),
                  "the masked render differs from the unmasked one")

        # Hand-placed swirl centres, through the page's own path: plain fractions on the
        # render message. `plan` takes them as its EIGHTH argument, so a worker that passed
        # them in the seventh slot would hand the pipeline an onStage callback and paint the
        # spiral -- a wrong picture with a right-looking stroke count, which is exactly the
        # class of glue bug this stage exists for.
        check(wk["readyMaxVortices"] == flow_mod.MAX_VORTICES,
              "the ready message carries the engine's vortex cap, so the page need not "
              "keep a copy", f"{wk['readyMaxVortices']} vs {flow_mod.MAX_VORTICES}")
        kinds = {n: flow_mod.PRESETS[n]["kind"] for n in flow_mod.FLOWS}
        check(wk["readyFlowKinds"] == kinds,
              "the ready message says which presets are swirl-kind, so the page never "
              "decides that itself", str(wk["readyFlowKinds"]))
        if wk.get("vtxPixels"):
            vref = paint(img, cfg_flow, None, VORTICES)[0]
            vref8 = (np.clip(vref, 0, 1) * 255.0 + 0.5).astype(np.uint8)
            vpx = np.asarray(wk["vtxPixels"], dtype=np.uint8).reshape(H, W, 4)
            vdiff = np.abs(vpx[:, :, :3].astype(np.int16) - vref8.astype(np.int16))
            check(int(vdiff.max()) <= 1, "worker's placed-vortex pixels match paint()",
                  f"max |delta| = {int(vdiff.max())} level(s)")
            check(wk["vtxFlow"] is True and wk["vtxCount"] == len(VORTICES),
                  "worker reports the flow field and how many centres were placed",
                  f"flow={wk['vtxFlow']} vortices={wk['vtxCount']}")
            sref8 = (np.clip(paint(img, cfg_flow)[0], 0, 1) * 255.0 + 0.5).astype(np.uint8)
            check(not bool((vref8 == sref8).all()),
                  "and the placed render is not just the spiral one under another name")

        # THE RELIGHT FAST PATH. An optimisation that returns something subtly different is
        # worse than no optimisation: it is a wrong picture produced 42x faster, and every
        # other check in this suite would pass while it did. So the frame the worker served
        # WITHOUT rasterising is compared against a full `paint()` at those same lighting
        # settings -- byte for byte, at 8 bits.
        lit = replace(cfg_flow, light_deg=42.0, gloss=0.7)
        lref8 = (np.clip(paint(img, lit)[0], 0, 1) * 255.0 + 0.5).astype(np.uint8)
        if wk.get("relitPixels"):
            check(wk["relitFast"] is True,
                  "the relight actually skipped the rasteriser rather than quietly "
                  "re-rendering", f"stats.relit = {wk['relitFast']}")
            rpx = np.asarray(wk["relitPixels"], dtype=np.uint8).reshape(H, W, 4)
            rdiff = np.abs(rpx[:, :, :3].astype(np.int16) - lref8.astype(np.int16))
            check(int(rdiff.max()) <= 1,
                  "the relit frame is the same painting a full render would have made",
                  f"max |delta| = {int(rdiff.max())} level(s)")
            # And it must actually be a DIFFERENT picture from the render it was relit from,
            # or the cache is serving the old frame and the check above is vacuous.
            fref8 = (np.clip(paint(img, cfg_flow)[0], 0, 1) * 255.0 + 0.5).astype(np.uint8)
            check(not bool((lref8 == fref8).all()),
                  "and the new lighting really does change the picture")
        # A relight through a COLD cache -- a different image key -- must fall through to a
        # full render rather than serving the buffers of another picture. This is the failure
        # the whole key exists to prevent, and it is invisible without asking for it.
        if wk.get("relitMissPixels"):
            check(wk["relitMissFast"] is False,
                  "a relight whose cache does not match falls through to a full render",
                  f"stats.relit = {wk['relitMissFast']}")
            mpx = np.asarray(wk["relitMissPixels"], dtype=np.uint8).reshape(H, W, 4)
            mdiff = np.abs(mpx[:, :, :3].astype(np.int16) - lref8.astype(np.int16))
            check(int(mdiff.max()) <= 1,
                  "and it still produces the right painting",
                  f"max |delta| = {int(mdiff.max())} level(s)")

    # Cancellation. Changing a slider mid-render used to mean waiting out the render you
    # had already abandoned; now the newest request supersedes the one in flight. The
    # engine can only do that because the rasteriser is drawn in slices with a yield
    # between them -- a worker in a straight-line loop never reads its message queue at
    # all. Two distinct paths are covered here: id 10 is superseded while RUNNING, id 11
    # while still WAITING to run.
    for row in wk.get("superseded") or []:
        want = ["cancelled"] if row["id"] in (10, 11) else ["done"]
        check(row["terminal"] == want,
              f"request {row['id']} ends in exactly one {want[0]}",
              f"got {row['terminal']}")
    # The survivor must be the SAME painting an undisturbed render produces. An abandoned
    # render shares the cache with the one that replaced it, so this is what would catch a
    # half-written entry -- cancellation turning into a wrong painting rather than no
    # painting, which is the failure mode nothing about looking at it would reveal.
    if wk["type"] == "done" and wk.get("survivorPixels"):
        sv = np.asarray(wk["survivorPixels"], dtype=np.uint8)
        check(bool((sv == np.asarray(wk["pixels"], dtype=np.uint8)).all()),
              "the render that survived supersession is byte-identical to an undisturbed one",
              f"{int((sv != np.asarray(wk['pixels'], dtype=np.uint8)).sum())} bytes differ")

    print("\nstage 10 -- plan() with a cache equals plan() without one")
    c = got["cached"]
    check(c["first"]["n"] == c["second"]["n"], "same stroke count on the cached pass")
    close(c["second"]["rgb"], c["first"]["rgb"], "cached: stroke colours", EXACT)
    close(c["second"]["r_major"], c["first"]["r_major"], "cached: r_major", EXACT)
    close([c["second"]["tau"]], [c["first"]["tau"]], "cached: tau", EXACT)
    close(c["second"]["canvas"], c["first"]["canvas"], "cached: base canvas", EXACT)
    # ...and against the Python, so a cache that is merely self-consistent is not enough.
    close(c["second"]["rgb"], psb.rgb, "cached: stroke colours still match Python", EXACT)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED")
        return 1
    print("the JS package matches the Python at every stage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
