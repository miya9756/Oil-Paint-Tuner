"""Core invariants. Run: conda run -n 4dre python tests/test_core.py

Deliberately synthetic and seeded -- no sample image needed, seconds to run, so this can
go in CI. Same shape as 4d-relight's parity suites (see the python-js-parity skill); the
node-driven parity test itself arrives with the WebGL port.
"""

import json
import os
import sys

import numpy as np
from dataclasses import fields, replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oilpaint import (  # noqa: E402
    palette, quadtree, reference, regions as regions_mod, strokes as strokes_mod,
)
from oilpaint.detail import METRICS, DetailField, FovealField  # noqa: E402
from oilpaint.metrics import RANDOM_ALIGNMENT, edge_alignment  # noqa: E402
from oilpaint.pipeline import (  # noqa: E402
    BARE, RELIGHT_PARAMS, PaintConfig, paint, plan,
)
from oilpaint.render import (  # noqa: E402
    CUTOFF_D2, fringe_params, ground_height, light, render,
)
from oilpaint import flow  # noqa: E402
from oilpaint.schema import SCHEMA  # noqa: E402
from oilpaint.image import luma  # noqa: E402
from oilpaint.tensor import (  # noqa: E402
    _tensor_from_luma, flat_tensor, sample_angle, structure_tensor,
)

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def synthetic(h=192, w=256, seed=0):
    """Flat left half, vertical stripes right half: one region that must NOT subdivide
    and one that must, plus an unambiguous edge orientation to test the tensor against."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), dtype=np.float32)
    img[:, : w // 2] = 0.45
    x = np.arange(w // 2, w)
    img[:, w // 2 :] = (0.5 + 0.45 * np.sign(np.sin(x / 3.0)))[None, :, None]
    return np.clip(img + rng.normal(0, 0.005, img.shape), 0, 1).astype(np.float32)


def test_quadtree_partition():
    print("quadtree: partition + allocation")
    img = synthetic()
    h, w = img.shape[:2]
    fld = DetailField(img, "var", tree_size=quadtree.tree_size(h, w))
    y0, x0, size, depth = quadtree.build(fld, h, w, tau=1e-4, dmin=2, min_cell=4)

    check(y0.size > 0, "produces leaves")
    # Every leaf centre inside the image, and no two leaves share one.
    cy, cx = y0 + size // 2, x0 + size // 2
    check(bool(((cy < h) & (cx < w)).all()), "every leaf centre is inside the image")
    keys = cy.astype(np.int64) * (w + 1) + cx
    check(len(np.unique(keys)) == len(keys), "leaf centres are unique (a true partition)")

    # The detail metric must actually allocate: fine cells on the stripes, coarse on flat.
    left = cx < w // 2
    check(
        size[left].mean() > size[~left].mean() * 1.5,
        f"coarse on flat / fine on texture ({size[left].mean():.1f} vs "
        f"{size[~left].mean():.1f} px)",
    )


def test_budget_monotone():
    print("budget: monotone + converges + reports its ceiling")
    img = synthetic()
    h, w = img.shape[:2]
    fld = DetailField(img, "var", tree_size=quadtree.tree_size(h, w))

    counts = [
        quadtree.build(fld, h, w, t, 2, 4)[0].size for t in (1e-5, 1e-3, 1e-2, 1e-1, 1.0)
    ]
    check(all(a >= b for a, b in zip(counts, counts[1:])), f"count is monotone in tau {counts}")

    for target in (200, 800):
        tau, reachable, ceiling = quadtree.solve_tau(fld, h, w, target, 2, 4)
        n = quadtree.build(fld, h, w, tau, 2, 4)[0].size
        check(
            abs(n - target) <= 0.10 * target,
            f"budget {target} -> {n} strokes (within 10%), ceiling={ceiling}",
        )

    _, reachable, ceiling = quadtree.solve_tau(fld, h, w, 10**7, 2, 4)
    check(not reachable, f"an impossible budget reports unreachable (ceiling={ceiling})")


def test_tau_floor_resists_noise():
    print("quadtree: an absolute detail floor beats the budget")
    # Pure noise at the amplitude of JPEG artefacts -- no real structure at any scale.
    rng = np.random.default_rng(3)
    img = np.clip(0.5 + rng.normal(0, 0.006, (192, 192, 3)), 0, 1).astype(np.float32)
    h, w = img.shape[:2]
    fld = DetailField(img, "var", tree_size=quadtree.tree_size(h, w))

    # Asking for a big budget must NOT let the search subdivide noise all the way down.
    _, reach_hi, ceil_hi = quadtree.solve_tau(fld, h, w, 4000, 2, 8, tau_floor=1e-4)
    _, reach_lo, ceil_lo = quadtree.solve_tau(fld, h, w, 4000, 2, 8, tau_floor=0.0)
    check(ceil_hi < ceil_lo, f"floor caps the ceiling on noise ({ceil_hi} < {ceil_lo})")
    check(not reach_hi, "a budget above the real detail is reported unreachable")

    # ... and it must not suppress genuine structure.
    real = synthetic()
    fld2 = DetailField(real, "var", tree_size=quadtree.tree_size(*real.shape[:2]))
    n_floor = quadtree.build(fld2, *real.shape[:2], 1e-4, 2, 8, tau_floor=1e-4)[0].size
    n_free = quadtree.build(fld2, *real.shape[:2], 1e-4, 2, 8, tau_floor=0.0)[0].size
    check(n_floor == n_free, f"the floor is inert on real structure ({n_floor} == {n_free})")


def test_all_metrics_run():
    print("detail: every metric runs and separates flat from textured")
    img = synthetic()
    h, w = img.shape[:2]
    for m in METRICS:
        fld = DetailField(img, m, tree_size=quadtree.tree_size(h, w))
        y0 = np.array([0, 0], dtype=np.int64)
        x0 = np.array([0, w // 2], dtype=np.int64)
        s = fld.score(y0, x0, 64)
        check(bool(np.isfinite(s).all()), f"{m}: finite")
        check(float(s[1]) > float(s[0]), f"{m}: textured {s[1]:.4g} > flat {s[0]:.4g}")


def test_tensor_orientation():
    print("tensor: orientation is ALONG the edge, not across it")
    h = w = 128
    img = np.zeros((h, w, 3), dtype=np.float32)
    img[:, w // 2 :] = 1.0  # a single vertical edge
    theta, coh = structure_tensor(img, sigma=2.0)
    # At the edge the stroke should run vertically: |sin(theta)| ~ 1.
    col = np.abs(np.sin(theta[:, w // 2 - 1 : w // 2 + 1]))
    check(float(col.mean()) > 0.99, f"vertical edge -> vertical stroke (|sin|={col.mean():.4f})")
    check(float(coh[:, w // 2 - 1 : w // 2 + 1].mean()) > 0.9, "coherence is high on the edge")
    check(float(coh[:, : w // 4].mean()) < 0.05, "coherence is low on flat")


def test_render_falloff():
    print("render: falloff matches the closed form, alpha stays bounded")
    sb = strokes_mod.StrokeBuffer(
        x=np.array([32.0], np.float32),
        y=np.array([32.0], np.float32),
        r_major=np.array([12.0], np.float32),
        r_minor=np.array([12.0], np.float32),
        theta=np.array([0.0], np.float32),
        rgb=np.ones((1, 3), np.float32),
        alpha=np.ones(1, np.float32),
    )
    out, cover = render(sb, 64, 64, hard=False, hard_r=1.5)
    sigma = 12.0 / 1.5
    d2 = ((np.arange(64) - 32.0) / sigma) ** 2
    expect = np.where(d2 <= CUTOFF_D2, np.exp(-d2), 0.0)
    err = np.abs(cover[32] - expect).max()
    check(err < 1e-5, f"soft falloff == exp(-d2) to {err:.2e}")
    check(bool(((cover >= 0) & (cover <= 1)).all()), "coverage stays in [0,1]")

    out_h, cover_h = render(sb, 64, 64, hard=True, hard_r=1.5)
    inside = cover_h[32, 32]
    outside = cover_h[32, 32 + int(12.0 * 1.6)]
    check(inside > 0.99, f"hard mode is opaque at the centre ({inside:.4f})")
    check(outside < 0.01, f"hard mode is clear past the radius ({outside:.4f})")


def _one_stroke(theta=0.0, r_major=18.0, r_minor=7.0, phase=0.0):
    return strokes_mod.StrokeBuffer(
        x=np.array([32.0], np.float32), y=np.array([32.0], np.float32),
        r_major=np.array([r_major], np.float32), r_minor=np.array([r_minor], np.float32),
        theta=np.array([theta], np.float32), rgb=np.ones((1, 3), np.float32),
        alpha=np.ones(1, np.float32), phase=np.array([phase], np.float32),
    )


def test_paint_texture_inert_at_zero():
    print("paint texture: step 2 is exactly inert at zero (spec §8 -> step 2)")
    sb = _one_stroke(phase=1.3)
    base, _ = render(sb, 64, 64, hard=True, hard_r=1.5, wobble_amp=0.18)
    same, _ = render(sb, 64, 64, hard=True, hard_r=1.5, wobble_amp=0.18,
                     bristle_amp=0.0, taper_amp=0.0, fringe_px=0.0)
    # Not "close" -- EQUAL. This is what lets step 1's tuned parameters and every painting
    # made before step 2 still mean what they meant. It also pins the restructuring of the
    # edge threshold: `wobble` moved behind a shared atan2 and grew a sibling, and the
    # wobble-only path has to come out of that arithmetically untouched.
    check(bool(np.array_equal(base, same)),
          "bristle_amp=0, taper_amp=0, fringe_px=0 changes nothing")

    for kw in ({"bristle_amp": 0.3}, {"taper_amp": 0.3}, {"fringe_px": 2.0}):
        got, _ = render(sb, 64, 64, hard=True, hard_r=1.5, **kw)
        name = next(iter(kw))
        check(not np.array_equal(base, got), f"{name}={kw[name]} does change the paint")


def test_fringe_is_scale_invariant():
    print("paint texture: the outline fringe is the same SIZE on every stroke")
    # The whole point of the fringe, and the property that was got wrong twice -- once by
    # scaling the feature with the stroke (a 130px blob got a 130px undulation, which is
    # the smooth-boundary complaint the fringe exists to answer) and once by scaling the
    # amplitude with it (10px spikes on the big blobs, sea urchins). So both are asserted
    # in PIXELS, across a 60x range of stroke sizes.
    px = 2.0
    for r in (1.5, 4.0, 12.0, 40.0, 90.0):
        m, amp = fringe_params(r, r, px)
        feature = 2.0 * np.pi * r / (4.0 * m)  # arc length of the finest octave, in px
        depth = amp * r  # radial displacement, in px
        ok_f = 2.0 <= feature <= 8.0
        ok_d = abs(depth - px) < 0.35 * px or amp >= 0.25 - 1e-9  # or the small-end cap
        check(ok_f and ok_d,
              f"r={r:>5.1f}px -> {m:>2d} lobes, {feature:.1f}px crumble, {depth:.1f}px deep")
    # The cap has to actually engage somewhere, or it is untested code pretending to be a
    # guard: a 1.5px stroke cannot carry a 2px wobble without inverting itself.
    _, amp_small = fringe_params(1.5, 1.5, px)
    check(amp_small == 0.25, f"the small-stroke amplitude cap engages ({amp_small})")


def test_taper_breaks_the_symmetry():
    print("paint texture: the taper makes a stroke directional, not an ellipse")
    # phase 0 -> cos(phase) = 1 -> the taper is at full strength along +u, and with
    # theta = 0 that is +x. An untapered stroke is symmetric about the centre column.
    sb = _one_stroke(theta=0.0, phase=0.0)
    _, plain = render(sb, 64, 64, hard=True, hard_r=1.5)
    _, tapered = render(sb, 64, 64, hard=True, hard_r=1.5, taper_amp=0.5)
    # Columns 1..31 and 33..63, which mirror each other about the centre column 32. The
    # obvious `[:32]` vs `[32:]` split is NOT symmetric -- it puts the centre column in one
    # half and gives it 32 columns against 31.
    lo, hi = plain[:, 1:32].sum(), plain[:, 33:64].sum()
    check(abs(lo - hi) / max(lo, 1e-6) < 0.02, f"untapered is symmetric ({lo:.0f}/{hi:.0f})")
    lo, hi = tapered[:, 1:32].sum(), tapered[:, 33:64].sum()
    check(hi < 0.8 * lo, f"tapered is thinner at the +u end ({lo:.0f}/{hi:.0f})")
    # ... and the head must not have GROWN: the taper only ever shrinks the threshold, so
    # a tapered stroke is a subset of the plain one. A sign slip here would pass the test
    # above while inflating the stroke.
    check(bool((tapered <= plain + 1e-6).all()), "the taper only ever removes paint")


def test_height_field_builds_up():
    print("paint texture: the height field follows paint order [Hertzmann02]")
    n = 40
    rng = np.random.default_rng(11)
    sb = strokes_mod.StrokeBuffer(
        x=rng.uniform(8, 56, n).astype(np.float32),
        y=rng.uniform(8, 56, n).astype(np.float32),
        r_major=np.full(n, 9.0, np.float32), r_minor=np.full(n, 6.0, np.float32),
        theta=rng.uniform(-np.pi, np.pi, n).astype(np.float32),
        rgb=rng.random((n, 3)).astype(np.float32),
        alpha=np.ones(n, np.float32),
        phase=rng.uniform(0, 2 * np.pi, n).astype(np.float32),
    )
    out, cover, height = render(sb, 64, 64, hard=True, hard_r=1.5,
                                want_height=True, impasto_layer=1.0, impasto_relief=0.0)
    check(height.shape == cover.shape, "the height field is one channel, canvas sized")
    # Unpainted canvas stays at zero; painted canvas sits between the first stroke's
    # height (1) and the last's (1 + impasto_layer). Anything above that would mean the
    # heights were being SUMMED, which is the formulation the paper rejected.
    check(float(height[cover < 1e-6].max(initial=0.0)) < 1e-6, "bare canvas has no height")
    top = float(height.max())
    check(top <= 2.0 + 1e-5, f"height never exceeds first+layer ({top:.4f} <= 2.0)")
    check(top > 1.8, f"the last strokes do reach the top of the range ({top:.4f})")

    # With no layer offset and no relief every stroke deposits height 1, so the height
    # blend degenerates into exactly the coverage blend. That equality is a sharp check on
    # the composite itself: any drift in the `h = h*(1-a) + a*hv` arithmetic breaks it,
    # and it is EQUAL rather than close because both are the same expression.
    _, flat_cover, flat_h = render(sb, 64, 64, hard=True, hard_r=1.5,
                                   want_height=True, impasto_layer=0.0, impasto_relief=0.0)
    check(bool(np.array_equal(flat_h, flat_cover)),
          "with no offset and no relief the height field IS the coverage buffer")


def test_lighting_is_a_noop_on_flat_paint():
    print("paint texture: lighting a flat height field leaves the painting alone")
    rng = np.random.default_rng(5)
    rgb = rng.random((48, 48, 3)).astype(np.float32)
    cover = np.ones((48, 48), np.float32)
    for h0 in (0.0, 1.0, 7.5):
        flat = np.full((48, 48), h0, np.float32)
        # At the DEFAULT straight-on view. The diffuse normalisation makes this hold at any
        # light angle; the specular is deliberately not normalised, so an oblique eye near
        # the light's mirror direction legitimately glares -- asserted as intended
        # behaviour in test_occlusion_and_view_dependence rather than pretended away.
        lit = light(rgb, flat, cover, depth=0.4, canvas_weave=0.0, occlusion=2.0)
        err = float(np.abs(lit - rgb).max())
        # The normalisation exists so that turning impasto on is not a hidden exposure
        # change; without it every A/B against a flat arm measures brightness, not relief.
        check(err < 1e-5, f"height == {h0} is a no-op (max |lit - rgb| = {err:.2e})")

    # A slope must brighten the side facing the light and darken the side away from it.
    # light_deg=0 is the screen compass's "from the right", so a ridge running down the
    # image is lit on its right flank.
    ramp = np.tile(np.linspace(0.0, 4.0, 48, dtype=np.float32), (48, 1))
    grey = np.full((48, 48, 3), 0.5, np.float32)
    up = light(grey, ramp, cover, depth=0.6, light_deg=0.0, elev_deg=30.0, gloss=0.0)
    down = light(grey, -ramp, cover, depth=0.6, light_deg=0.0, elev_deg=30.0, gloss=0.0)
    check(float(up.mean()) < 0.5 < float(down.mean()),
          f"a slope away from the light darkens ({up.mean():.4f} < 0.5 < {down.mean():.4f})")


def test_occlusion_and_view_dependence():
    print("paint texture: occlusion ignores the light and the eye; the specular does not")
    rgb = np.full((64, 64, 3), 0.55, np.float32)
    cover = np.ones((64, 64), np.float32)
    # A plateau with a small pit cut into it: the pit floor is a crease that paint shades
    # itself in, the plateau far from it is not.
    hf = np.full((64, 64), 1.0, np.float32)
    hf[26:34, 26:34] = 0.15
    pit, top = (slice(28, 32), slice(28, 32)), (slice(4, 12), slice(4, 12))

    plain = light(rgb, hf, cover, depth=0.35, occlusion=0.0)
    occ = light(rgb, hf, cover, depth=0.35, occlusion=2.0)
    check(float(occ[pit].mean()) < float(plain[pit].mean()) - 0.02,
          f"a crease darkens ({plain[pit].mean():.4f} -> {occ[pit].mean():.4f})")
    check(abs(float(occ[top].mean()) - float(plain[top].mean())) < 1e-6,
          "flat paint away from the crease does not")

    # THE claim: the occlusion contribution is identical however the light and the eye are
    # placed. That is what ambient occlusion means, and it is why it multiplies the ambient
    # term rather than being folded into the diffuse -- so it is worth pinning rather than
    # asserting in a comment.
    deltas = []
    for ldeg, vdeg, vel in ((0.0, 90.0, 90.0), (200.0, 315.0, 45.0), (95.0, 20.0, 60.0)):
        kw = dict(depth=0.35, light_deg=ldeg, view_deg=vdeg, view_elev_deg=vel)
        a = light(rgb, hf, cover, occlusion=0.0, **kw)
        b = light(rgb, hf, cover, occlusion=2.0, **kw)
        deltas.append(float((b - a)[pit].mean()))
    spread = max(deltas) - min(deltas)
    check(spread < 1e-6,
          f"the occlusion term ignores light and eye (spread {spread:.2e} over 3 setups)")

    # ... and the specular is the term that does move with the eye. A rough surface, and
    # the eye swung round to the light's mirror direction -- light at azimuth 135 /
    # elevation 35 mirrors to azimuth 315 at the same elevation.
    rng = np.random.default_rng(4)
    rough = (1.0 + 0.35 * rng.standard_normal((64, 64))).astype(np.float32)
    straight = light(rgb, rough, cover, depth=0.35, gloss=0.6, view_elev_deg=90.0)
    mirror = light(rgb, rough, cover, depth=0.35, gloss=0.6,
                   view_deg=315.0, view_elev_deg=40.0)
    check(float(mirror.mean()) > float(straight.mean()) + 0.03,
          f"the eye at the mirror angle catches the sheen "
          f"({straight.mean():.4f} -> {mirror.mean():.4f})")
    # And the sheen must be RELIEF, not a uniform wash: a flat field at the same angle
    # lifts every pixel by the same amount, a rough one does not.
    flat = np.full((64, 64), 1.0, np.float32)
    mirror_flat = light(rgb, flat, cover, depth=0.35, gloss=0.6,
                        view_deg=315.0, view_elev_deg=40.0)
    check(float(mirror.std()) > float(mirror_flat.std()) + 0.01,
          f"the sheen varies with the relief (std {mirror_flat.std():.4f} flat vs "
          f"{mirror.std():.4f} rough)")


def test_edge_alignment_separates():
    print("metrics: edge_alignment separates the two orientation modes (spec exp. 2)")
    img = synthetic()
    base = dict(target_n=600, min_cell=8, max_cell=64)
    _, _, sb_s = paint(img, PaintConfig(**base, orient="structure"))
    _, _, sb_r = paint(img, PaintConfig(**base, orient="random"))
    a_s = edge_alignment(sb_s, img)
    a_r = edge_alignment(sb_r, img)
    check(a_s > 0.9, f"structure aligns ({a_s:.4f} > 0.9)")
    check(
        abs(a_r - RANDOM_ALIGNMENT) < 0.08,
        f"random sits on the 2/pi null ({a_r:.4f} vs {RANDOM_ALIGNMENT:.4f})",
    )
    check(a_s > a_r + 0.25, "the two modes are clearly separated")


def _gradient_image(h=192, w=256, seed=7):
    """A smooth horizontal ramp buried in noise -- the case the flat brush exists for.

    The ramp climbs 0.25 over 256 px, ~1/1000 of a level per pixel, while the noise is
    ~1.5/255 per pixel. So the detail-scale Sobel sees essentially only the noise, and the
    structure tensor there reports an incoherent field at an arbitrary angle -- even though
    the region has one perfectly well-defined direction in it.
    """
    rng = np.random.default_rng(seed)
    ramp = np.linspace(0.35, 0.60, w, dtype=np.float32)
    img = np.repeat(np.repeat(ramp[None, :, None], h, 0), 3, 2)
    return np.clip(img + rng.normal(0, 0.006, img.shape), 0, 1).astype(np.float32)


def _parallelism(theta):
    """|mean(e^{2i.theta})|: 1 = every stroke parallel, 0 = no preferred direction.

    Doubled, because a stroke is an ORIENTATION -- theta and theta+pi are the same mark and
    a mean over the raw angle would cancel them against each other."""
    return float(abs(np.mean(np.exp(2j * np.asarray(theta, dtype=np.float64)))))


def test_flat_brush_sweeps_a_gradient():
    print("flat brush: a gradient under noise gets a sweep, not a field of round blobs")
    img = _gradient_image()
    base = dict(target_n=400, min_cell=8, max_cell=32, seed=1)
    _, _, off = paint(img, PaintConfig(**base, flat_dir=0.0))
    _, _, on = paint(img, PaintConfig(**base, flat_dir=0.8))

    # Matched stroke count, and that is structural rather than lucky: the blend touches
    # neither the detail field nor the tau search, so it cannot buy or spend strokes.
    check(len(off) == len(on), f"the stroke budget is untouched ({len(off)} vs {len(on)})")

    ar_off = float(np.median(off.r_major / off.r_minor))
    ar_on = float(np.median(on.r_major / on.r_minor))
    check(ar_on > 2.0 * ar_off,
          f"the strokes elongate ({ar_off:.2f} round-ish -> {ar_on:.2f} directional)")

    p_off, p_on = _parallelism(off.theta), _parallelism(on.theta)
    check(p_on > p_off + 0.3,
          f"and they line up ({p_off:.3f} -> {p_on:.3f} parallelism)")

    # The ramp runs along x, so its iso-lines -- and the brush that follows them -- run
    # along y. This is the check that the direction is the RIGHT one and not merely a
    # consistent one; a sign error or a missing +pi/2 would still pass the two above.
    mean_ang = 0.5 * float(np.angle(np.mean(np.exp(2j * on.theta.astype(np.float64)))))
    check(abs(abs(mean_ang) - np.pi / 2) < np.deg2rad(15),
          f"along the iso-lines of the ramp ({np.degrees(abs(mean_ang)):.1f} deg vs 90)")

    # Inert at 0, and the other two knobs with it -- so every painting made before this
    # existed is still reachable, and reachable by setting one number.
    a, _, _ = paint(img, PaintConfig(**base, flat_dir=0.0))
    b, _, _ = paint(img, PaintConfig(**base, flat_dir=0.0, flat_sigma=24.0,
                                     flat_theta_deg=170.0))
    check(bool(np.array_equal(a, b)), "flat_sigma and flat_theta_deg are inert at flat_dir 0")


def test_flat_brush_leaves_real_edges_alone():
    """The failure this guards against is the plausible one: a flat-region fix that also
    drags the strokes on genuine edges towards the coarse field's opinion, quietly undoing
    change (A). The blend's fine weight is `c` and the other two carry (1 - c) precisely so
    that a fully coherent neighbourhood cannot be moved."""
    print("flat brush: genuine edges keep their alignment")
    img = synthetic()
    base = dict(target_n=600, min_cell=8, max_cell=64)
    a_off = edge_alignment(paint(img, PaintConfig(**base, flat_dir=0.0))[2], img)
    a_on = edge_alignment(paint(img, PaintConfig(**base, flat_dir=1.0))[2], img)
    check(a_on > 0.9, f"still aligned at full strength ({a_on:.4f} > 0.9)")
    check(a_on > a_off - 0.02,
          f"and no worse than with the brush off ({a_off:.4f} -> {a_on:.4f})")


def _f32(v, n=1):
    return np.full(n, v, dtype=np.float32)


def _same_orientation(a, b, tol=1e-5):
    """Compare two angles as ORIENTATIONS: equal mod pi, wrap included."""
    return abs(float(np.angle(np.exp(2j * (float(a) - float(b)))))) < tol


def test_flat_blend_hands_over_on_confidence():
    print("flat brush: the blend hands over on confidence and cancels on disagreement")
    zero, one, half = _f32(0.0), _f32(1.0), _f32(0.5)
    t0, tq = _f32(0.0), _f32(np.pi / 2)

    # A fully coherent edge is untouched at ANY strength -- the other two weights carry
    # (1 - c) and vanish. This is the invariant the previous test measures end to end.
    tb, cb = strokes_mod.flat_blend(t0, one, tq, one, 1.0, 90.0)
    check(_same_orientation(tb[0], 0.0) and abs(float(cb[0]) - 1.0) < 1e-6,
          "c = 1: the fine field wins outright, at full strength and against a rival")

    # ... and where the fine field has nothing, the coarse one takes over completely.
    tb, cb = strokes_mod.flat_blend(t0, zero, tq, one, 1.0, 0.0)
    check(_same_orientation(tb[0], np.pi / 2) and float(cb[0]) > 0.99,
          "c = 0: the coarse field takes it, and brings its coherence with it")

    # Agreement ADDS, disagreement CANCELS. This is the property that a max() or a mode
    # switch would not have: an ambiguous neighbourhood goes rounder rather than committing
    # to one of two directions at random, which is what the round blobs would have become.
    _, c_agree = strokes_mod.flat_blend(t0, half, t0, one, 1.0, 0.0)
    _, c_fight = strokes_mod.flat_blend(t0, half, tq, one, 1.0, 0.0)
    check(float(c_fight[0]) < 0.5 < float(c_agree[0]),
          f"agreement stretches and disagreement rounds ({c_fight[0]:.3f} < 0.5 < "
          f"{c_agree[0]:.3f})")

    # Nothing to read at either scale -- a uniform ground: the global fallback, which is
    # the only direction in the whole scheme that is a setting rather than a measurement.
    for deg in (0.0, 45.0, 135.0):
        tb, cb = strokes_mod.flat_blend(t0, zero, t0, zero, 1.0, deg)
        check(_same_orientation(tb[0], np.deg2rad(deg)),
              f"a uniform ground sweeps at flat_theta_deg ({deg:.0f} deg)")
    check(abs(float(cb[0]) - strokes_mod.FLAT_GLOBAL) < 1e-6,
          f"at a modest elongation, not the full one ({cb[0]:.2f})")

    # And the whole mechanism is off at flat_dir 0 -- there is no path through it.
    tb, cb = strokes_mod.flat_blend(t0, zero, tq, one, 0.0, 90.0)
    check(float(cb[0]) == 0.0, "flat_dir 0 leaves nothing to hand over")


def test_prefilter_recovers_a_buried_gradient():
    """Why the flat field needs a PRE-blur and not merely a larger tensor sigma.

    The isotropic noise power lands on both eigenvalues, so tensor smoothing averages the
    bias without removing it. Only pre-filtering the luma attacks it."""
    print("flat brush: the pre-blur, not the tensor sigma, is what recovers the direction")
    img = _gradient_image()
    _, c_fine = structure_tensor(img, 3.5)
    _, c_wide = structure_tensor(img, 16.0)        # more smoothing, same bias
    _, c_pre, step = flat_tensor(img, 8.0)         # the bias itself removed
    m_fine, m_wide, m_pre = (float(np.median(c)) for c in (c_fine, c_wide, c_pre))
    check(m_fine < 0.25, f"the fine tensor sees noise, not the ramp ({m_fine:.3f})")
    check(m_wide < 0.5, f"a wider tensor sigma barely helps ({m_wide:.3f})")
    check(m_pre > 0.9, f"the pre-blur recovers it ({m_pre:.3f})")

    # And the decimation the flat field is computed on does not cost the direction it just
    # recovered. Compared against the same field at full resolution, sampled back through
    # `step` -- which is also what proves `step` is applied the right way round: divide by
    # it where it should be multiplied and this lands on a completely different pixel.
    t_dec, _, step = flat_tensor(img, 8.0)
    check(step == 4, f"flat_sigma 8 decimates by 4 ({step})")
    h, w = img.shape[:2]
    t_full, _ = _tensor_from_luma(luma(img).astype(np.float32), 8.0, prefilter=8.0)
    ys, xs = np.mgrid[0:h, 0:w].reshape(2, -1).astype(np.float64)
    a = sample_angle(t_dec, ys / step, xs / step)
    b = sample_angle(t_full, ys, xs)
    d = np.degrees(np.abs(np.angle(np.exp(2j * (a - b)))) / 2)
    check(float(np.median(d)) < 2.0,
          f"decimated agrees with the full-resolution field (median {np.median(d):.2f} deg)")


def test_underpainting_covers():
    """base='strokes' must leave no holes. THE POINT OF THAT LAYER, and until now the only
    thing pinning it was a comment at the call site that had already been wrong once: it
    said "this layer must actually cover" while passing KAPPA_FULL_COVER, which is derived
    for a stroke sitting exactly on the cell centre at exactly its nominal radius -- a
    stroke `from_cells` never places. The layer measured 0.704 covered.

    Goes through `plan` rather than calling `from_cells` directly, because the defect was
    at the CALL SITE: a test that builds its own underpainting would have passed throughout.
    The first `n_under` strokes are then rendered ALONE, so the detail layer painted on top
    cannot carry the result, and with the full paint texture on, since that is what ships.
    """
    print("pipeline: the stroke underpainting actually covers")
    img = synthetic()
    h, w = img.shape[:2]

    def bare_of(kappa=None, seed=0):
        cfg = PaintConfig(target_n=400, min_cell=8, max_cell=32, seed=seed,
                          base="strokes", base_cell_scale=1.0)
        if kappa is None:
            sb, _, info = plan(img, cfg)
            n = info["n_under"]
            under = strokes_mod.StrokeBuffer(**{
                f: getattr(sb, f)[:n] for f in
                ("x", "y", "r_major", "r_minor", "theta", "rgb", "alpha", "phase")})
        else:  # the control: the same layer at the uncorrected kappa
            theta, coh = structure_tensor(img, cfg.tensor_sigma)
            under = strokes_mod.from_cells(
                img, strokes_mod.uniform_grid(h, w, int(cfg.max_cell * cfg.base_cell_scale)),
                theta, coh, kappa=kappa, drop_p=0.0, seed=cfg.seed + 9973,
                aniso_max=1.0, jitter_aniso=0.0, jitter_centre=cfg.jitter_centre,
                jitter_radius=cfg.jitter_radius, jitter_theta_deg=cfg.jitter_theta_deg,
                orient=cfg.orient, color=cfg.color, alpha=cfg.alpha,
                size_sigma=cfg.size_sigma, color_jitter=cfg.color_jitter)
        _, cover = render(under, h, w, hard=cfg.hard, hard_r=cfg.hard_r,
                          wobble_amp=cfg.wobble_amp, bristle_amp=cfg.bristle_amp,
                          taper_amp=cfg.taper_amp, fringe_px=cfg.fringe_px)
        return float((cover < BARE).mean())

    check(strokes_mod.covering_kappa(0.35, 0.15) > strokes_mod.KAPPA_FULL_COVER,
          "the jitter correction asks for a larger brush than the naive condition")
    # A ratio of two worst cases, so it runs away at the far end of two sliders: uncapped
    # it asks for 14.14 at the schema's limits, i.e. a stroke twice the width of a 1000px
    # canvas. Inert at the defaults, which is what keeps it from being a look change.
    check(strokes_mod.covering_kappa(1.0, 0.8) == strokes_mod.KAPPA_COVER_MAX,
          "and it is capped at the extremes of the two sliders it reads")
    check(strokes_mod.covering_kappa(0.35, 0.15) < strokes_mod.KAPPA_COVER_MAX,
          "the cap is inert at the defaults, so it changes no painting")
    got = [bare_of(seed=s) for s in (0, 1, 2)]
    check(max(got) < 0.02,
          "the underpainting leaves no holes, at every seed  "
          f"(worst {max(got):.3%} bare)")
    # Sharper than the threshold above on its own: it says the correction is what is doing
    # the work, so a future edit that removes it fails here rather than drifting past 2%.
    ctrl = bare_of(kappa=strokes_mod.KAPPA_FULL_COVER, seed=0)
    check(ctrl > 5 * max(got[0], 1e-4),
          f"and the uncorrected kappa does NOT ({ctrl:.3%} bare against {got[0]:.3%})")


def _foveal_image(h=260, w=180, seed=2):
    """Uniform texture everywhere, so the CONTENT metric has no preference and anything
    that concentrates strokes came from the map. 260x180 in a 512 tree on purpose: the
    image does not fill its padded square, which is the geometry that makes a held coarse
    cell's centre land outside the frame."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.stack([0.5 + 0.3 * np.sin(xx / 5.0) * np.cos(yy / 6.0)] * 3, axis=2)
    img += 0.02 * rng.standard_normal(img.shape).astype(np.float32)
    return np.clip(img, 0.0, 1.0).astype(np.float32)


def _leaves(img, cfg, mask):
    """The quadtree the config and map produce, without painting it."""
    h, w = img.shape[:2]
    ts = quadtree.tree_size(h, w)
    fld = DetailField(img, cfg.metric, tree_size=ts)
    if mask is not None and cfg.foveal_strength > 0:
        fld = FovealField(fld, mask, cfg.foveal_strength)
    dmin = max(0, int(np.ceil(np.log2(max(1, ts / max(1, cfg.max_cell))))))
    tau, _, _ = quadtree.solve_tau(fld, h, w, cfg.target_n, dmin, cfg.min_cell,
                                   tau_floor=cfg.tau_floor)
    return quadtree.build(fld, h, w, tau, dmin, cfg.min_cell, tau_floor=cfg.tau_floor)


def test_foveal_map_is_inert_unless_asked_for():
    print("foveal: a map with strength 0 changes nothing at all")
    img = _foveal_image()
    mask = np.zeros(img.shape[:2], np.float32)
    mask[20:90, 20:90] = 1.0
    cfg = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5)
    a, _, _ = paint(img, cfg)
    b, _, _ = paint(img, cfg, mask)
    check(bool(np.array_equal(a, b)),
          "a map at strength 0 is bit-identical to no map at all")
    # ...and a BLANK map is a constant weight, which the tau search absorbs. Not
    # bit-identical -- the search grid is not scale-covariant -- but it must not concentrate
    # anything and must not cost strokes, which is the property the feature rests on.
    blank = np.zeros(img.shape[:2], np.float32)
    n_plain = _leaves(img, cfg, None)[0].size
    n_blank = _leaves(img, PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                                       foveal_strength=0.9), blank)[0].size
    check(abs(n_blank - n_plain) <= 0.05 * n_plain,
          f"an unpainted map spends the same budget ({n_blank} vs {n_plain} strokes)")
    # The one exception, and it is the gate behaving as specified rather than a bug: at
    # strength exactly 1 the weight IS the map, so a blank map scores 0 everywhere and
    # nothing can beat tau. The result is the bare max_cell grid. The tuner never sends a
    # map with no ink on it, and this pins what happens if anything ever does.
    n_gated = _leaves(img, PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                                       foveal_strength=1.0), blank)[0].size
    check(n_gated < n_plain / 10,
          f"at strength 1 a blank map closes the gate entirely ({n_gated} strokes)")


def test_foveal_map_moves_the_budget():
    print("foveal: strokes migrate into the marked region at a matched budget")
    img = _foveal_image()
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.float32)
    mask[20:90, 20:90] = 1.0            # ~15% of the frame
    cfg = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5)
    fov = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                      foveal_strength=0.85)

    def share(cells):
        y0, x0, size, _ = cells
        cy, cx = y0 + size // 2, x0 + size // 2
        inside = (cy >= 20) & (cy < 90) & (cx >= 20) & (cx < 90)
        return inside.mean(), y0.size

    base_share, n_base = share(_leaves(img, cfg, None))
    fov_share, n_fov = share(_leaves(img, fov, mask))
    check(fov_share > 3.0 * base_share,
          f"the marked 15% of the frame takes {fov_share:.1%} of the strokes, "
          f"against {base_share:.1%} without the map")
    # The budget is CONSERVED: this is a redistribution, not an increase, which is what
    # keeps an A/B against the unweighted tree honest (spec §4.6).
    check(abs(n_fov - n_base) <= 0.1 * n_base,
          f"at a matched budget ({n_fov} strokes against {n_base})")
    # The top of the slider is a GATE, not just more of the same tilt: at strength 1 the
    # weight reaches zero and nothing outside the mark can score at all, so it must
    # concentrate strictly harder than 0.85 does.
    gate = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                       foveal_strength=1.0)
    gate_share, _ = share(_leaves(img, gate, mask))
    check(gate_share > fov_share,
          f"strength 1 gates rather than tilts ({gate_share:.1%})")
    # ...and it must not have quietly bought that by making strokes larger than max_cell.
    # A `foveal_coarsen` knob that reached into quadtree.build to do exactly that was tried
    # and removed: one octave of it emptied every intermediate cell size out of the tree,
    # which is not a distribution anyone can tune. max_cell stays a hard ceiling.
    check(int(_leaves(img, fov, mask)[2].max()) <= cfg.max_cell,
          f"max_cell is still a hard ceiling on the leaf ({int(_leaves(img, fov, mask)[2].max())} px)")


def test_palette_is_inert_and_geometry_preserving():
    print("palette: the grade moves colour and nothing else")
    img = _foveal_image()
    cfg = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5)
    check(palette.params_for(cfg) is None, "the default config asks for no grade at all")
    a, _, _ = paint(img, cfg)
    # A preset at strength 0 is the same statement a second way: the skip has to happen in
    # `params_for`, not by an identity block, or the OKLab round trip moves the last bit.
    off = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                      palette="fauve", palette_strength=0.0)
    b, _, _ = paint(img, off)
    check(bool(np.array_equal(a, b)),
          "a preset at strength 0 is bit-identical to no grade at all")

    base = plan(img, cfg)[0]
    for name in palette.PALETTES:
        if name == "none":
            continue
        cfgp = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, palette=name)
        sb = plan(img, cfgp)[0]
        # THE invariant the placement rests on: the grade runs after the geometry, so it
        # cannot move a leaf, a stroke or the RNG stream. An implementation that graded the
        # SOURCE IMAGE instead would fail here rather than merely looking different.
        same = all(np.array_equal(getattr(sb, f), getattr(base, f))
                   for f in ("x", "y", "r_major", "r_minor", "theta", "phase"))
        moved = float(np.abs(sb.rgb - base.rgb).mean())
        check(same and moved > 0.01,
              f"'{name}': same strokes, different paint (mean |delta| {moved:.3f})")

    # The four sliders are TRIMS, so they have to work on 'none' as well -- the preset
    # supplies a character, not permission to run.
    trim = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, warm_cool=0.7)
    check(float(np.abs(plan(img, trim)[0].rgb - base.rgb).mean()) > 0.005,
          "a trim alone grades, with no preset chosen")
    # Value compression is the one claim that is checkable as a NUMBER rather than as a
    # difference: it must pull both ends of the range in.
    c = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, value_compress=1.0)
    v = plan(img, c)[0].rgb
    check(float(v.min()) > float(base.rgb.min()) and float(v.max()) < float(base.rgb.max()),
          f"value_compress leaves no pure black or white "
          f"({base.rgb.min():.3f}-{base.rgb.max():.3f} -> {v.min():.3f}-{v.max():.3f})")


def _hue_image(h=260, w=180, seed=4):
    """Every hue on the circle, at a chroma the grade can actually move.

    The foveal fixture is GREYSCALE -- `[...] * 3` stacked -- which is right for testing an
    allocator and useless for testing a hue control: with no hue, a band holds nothing and
    every claim below reads 0 against 0. So the sweep runs a full turn across the frame,
    with enough texture on top for the quadtree to have something to subdivide.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = 2.0 * np.pi * xx / w
    two3 = 2.0 * np.pi / 3.0
    im = np.stack([0.5 + 0.34 * np.cos(t), 0.5 + 0.34 * np.cos(t - two3),
                   0.5 + 0.34 * np.cos(t + two3)], -1)
    im += (0.06 * np.sin(yy / 4.0) * np.cos(xx / 5.0))[:, :, None]
    im += 0.015 * rng.standard_normal(im.shape).astype(np.float32)
    return np.clip(im, 0.0, 1.0).astype(np.float32)


def test_hue_band_steers_one_family_only():
    print("palette: a hue band turns one family of hues and leaves the rest")
    img = _hue_image()
    cfg = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5)
    base = plan(img, cfg)[0]

    # INERT unless it is asked to do something. Parking the band over a hue -- which is
    # what the page does the moment anyone drags `hue_target` -- must not start grading.
    park = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                       hue_target=200.0, hue_range=25.0)
    check(palette.params_for(park) is None,
          "a band with no rotation and no gain asks for no grade at all")
    check(bool(np.array_equal(plan(img, park)[0].rgb, base.rgb)),
          "...so parking the band is bit-identical to no grade")

    # THE CLAIM, and the reason this exists rather than a global rotation: the band moves
    # the hues it holds and leaves the ones it does not. Measured per stroke, in OKLab, on
    # the strokes that are actually IN the band against the ones a quarter-turn away.
    probe = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                        hue_target=140.0, hue_range=30.0, hue_rotate=-40.0)
    sb = plan(img, probe)[0]
    same = all(np.array_equal(getattr(sb, f), getattr(base, f))
               for f in ("x", "y", "r_major", "r_minor", "theta", "phase"))
    check(same, "a band moves colour and no geometry")

    _, a0, b0 = palette._to_oklab(np.asarray(base.rgb, np.float64))
    _, a1, b1 = palette._to_oklab(np.asarray(sb.rgb, np.float64))
    h0 = np.degrees(np.arctan2(b0, a0)) % 360.0
    turn = np.degrees(np.arctan2(b1, a1)) % 360.0 - h0
    turn = (turn + 180.0) % 360.0 - 180.0
    r0 = np.hypot(a0, b0)
    lit = r0 > 0.02                                   # a neutral has no hue to steer
    inb = lit & (np.abs((h0 - 140.0 + 180.0) % 360.0 - 180.0) < 30.0)
    out = lit & (np.abs((h0 - 140.0 + 180.0) % 360.0 - 180.0) > 120.0)
    check(inb.sum() > 20 and out.sum() > 20,
          f"the test image has strokes on both sides of the band "
          f"({int(inb.sum())} in, {int(out.sum())} far)")
    t_in = float(np.abs(turn[inb]).mean())
    t_out = float(np.abs(turn[out]).mean())
    check(t_in > 20.0 and t_out < 3.0,
          f"in-band hues turn and far ones do not ({t_in:.1f} deg vs {t_out:.1f} deg)")

    # ...which is exactly what a GLOBAL rotation cannot do. The widest band is that global
    # rotation, and it must turn both groups by about the same amount -- the honest way to
    # say "this control contains the one you were reaching for".
    wide = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                       hue_target=140.0, hue_range=180.0, hue_rotate=-40.0)
    _, aw, bw = palette._to_oklab(np.asarray(plan(img, wide)[0].rgb, np.float64))
    gturn = np.degrees(np.arctan2(bw, aw)) % 360.0 - h0
    gturn = (gturn + 180.0) % 360.0 - 180.0
    g_in, g_out = float(np.abs(gturn[inb]).mean()), float(np.abs(gturn[out]).mean())
    check(g_out > 8.0 * t_out,
          f"at the widest range it is a global rotation instead "
          f"({g_in:.1f} deg in-band, {g_out:.1f} deg far)")

    # The gain is a saturation control for ONE family: -1 takes the band's own hue out and
    # leaves the rest of the picture where it was.
    cut = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                      hue_target=140.0, hue_range=30.0, hue_boost=-1.0)
    _, ac, bc = palette._to_oklab(np.asarray(plan(img, cut)[0].rgb, np.float64))
    rc = np.hypot(ac, bc)
    drop_in = float((r0[inb] - rc[inb]).mean())
    drop_out = float((r0[out] - rc[out]).mean())
    check(drop_in > 0.02 and abs(drop_out) < 0.005,
          f"hue_boost -1 desaturates the band alone "
          f"(chroma {drop_in:+.3f} in-band, {drop_out:+.3f} far)")


def test_reference_transport_matches_the_painting():
    print("reference: the transport lands the colours on a named painting's statistics")
    img = _hue_image()
    cfg = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5)
    base = plan(img, cfg)[0]
    check(reference.params_for(cfg) is None, "the default config asks for no transport")
    off = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                      reference="starry-night", reference_strength=0.0)
    check(bool(np.array_equal(plan(img, off)[0].rgb, base.rgb)),
          "a reference at strength 0 is bit-identical to none at all")

    def lab_stats(rgb):
        return reference.stats(np.asarray(rgb, np.float64))

    seen = []
    for name in reference.NAMES:
        if name == "none":
            continue
        c = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, reference=name)
        sb = plan(img, c)[0]
        same = all(np.array_equal(getattr(sb, f), getattr(base, f))
                   for f in ("x", "y", "r_major", "r_minor", "theta", "phase"))
        check(same, f"'{name}': the transport moves colour and no geometry")
        # THE CLAIM, and it is the definition of the feature rather than a proxy for it:
        # at full strength the painting's own colour statistics ARE the reference's. Not
        # exact, and the gap is honest -- `_from_oklab` clips back into sRGB, so a target
        # that asks for colours no monitor has cannot be reached. 0.05 in OKLab is about
        # one visible step, which is the right order for "it landed".
        mu, _ = lab_stats(sb.rgb)
        want = reference.params_for(c)["mu"]
        d = max(abs(mu[i] - want[i]) for i in range(3))
        check(d < 0.05, f"'{name}': the stroke colours land on its mean "
                        f"(off by {d:.3f} in OKLab)")
        seen.append(tuple(np.round(mu, 4)))
    # Assert the negative: six references that all produced the same painting would pass
    # every check above.
    check(len(set(seen)) == len(seen),
          f"each reference gives a different painting "
          f"({len(set(seen))} distinct of {len(seen)})")

    # Strength is a real dial, not a switch: half the transport is half the move.
    full = lab_stats(plan(img, PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                                           reference="sunflowers"))[0].rgb)[0]
    half = lab_stats(plan(img, PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                                           reference="sunflowers",
                                           reference_strength=0.5))[0].rgb)[0]
    b0 = lab_stats(base.rgb)[0]
    frac = [(half[i] - b0[i]) / (full[i] - b0[i]) for i in range(3)
            if abs(full[i] - b0[i]) > 0.02]
    check(bool(frac) and all(0.35 < f < 0.65 for f in frac),
          "strength 0.5 travels about half way ("
          + ", ".join(f"{f:.2f}" for f in frac) + ")")

    # A SOURCE WITH NO COLOUR AT ALL. Its covariance is near-singular, so the inverse
    # square root of it is what EIG_FLOOR exists to bound -- without the floor this is a
    # division by ~0 that stretches three pixels of noise across the whole gamut, and the
    # painting comes back NaN.
    flat = np.full((120, 90, 3), 0.5, dtype=np.float32)
    flat[40:80, 30:60] = 0.52                      # enough structure for a few strokes
    fsb = plan(flat, PaintConfig(target_n=300, max_cell=16, min_cell=4, seed=2,
                                 reference="the-scream"))[0]
    check(bool(np.all(np.isfinite(fsb.rgb))),
          f"a source with almost no colour transports to something finite "
          f"(range {float(fsb.rgb.min()):.3f}-{float(fsb.rgb.max()):.3f})")


def test_pigment_projection_respects_the_palette():
    print("palette: the projection lands on colours the tubes can actually mix")
    img = _foveal_image()

    def sb_for(**kw):
        return plan(img, PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5,
                                     **kw))[0]

    # A preset's own projection is a default, so a negative trim has to be able to cancel
    # it exactly -- otherwise the four statistical knobs cannot be judged on their own.
    off = sb_for(palette="zorn", pigment=-palette.PRESETS["zorn"]["pigment"])
    check(bool(np.array_equal(off.rgb, sb_for(palette="zorn", palette_strength=1.0,
                                              pigment=-palette.PRESETS["zorn"]["pigment"]).rgb)),
          "a negative trim cancels the preset's own projection")

    for name in ("zorn", "impressionist", "old-master"):
        pull = 1.0 - palette.PRESETS[name]["pigment"]      # trims the preset up to a snap
        sb = sb_for(palette=name, pigment=pull)
        lut = palette._mix_lut(name)
        L, a, b = palette._to_oklab(np.asarray(sb.rgb, np.float64))
        d = np.sqrt(np.min((L[:, None] - lut[None, :, 0]) ** 2
                           + (a[:, None] - lut[None, :, 1]) ** 2
                           + (b[:, None] - lut[None, :, 2]) ** 2, axis=1))
        # At a full pull every stroke IS a mixture, up to the float32 the buffer is stored
        # in and the sRGB clip on the way out.
        check(float(d.max()) < 5e-3,
              f"'{name}': at a full pull every stroke is a real mixture "
              f"(worst OKLab distance {d.max():.2e})")

    # The claim the preset names make, as a number. Four pigments with no blue among them
    # cannot mix one, so a fully projected Zorn painting has nothing cool and saturated in
    # it -- and a palette that does carry blue must, on the same photograph.
    def bluest(name):
        sb = sb_for(palette=name, pigment=1.0 - palette.PRESETS[name]["pigment"])
        _, a, b = palette._to_oklab(np.asarray(sb.rgb, np.float64))
        return float(np.max(-b))          # -b is the blue axis in OKLab
    z, i = bluest("zorn"), bluest("impressionist")
    check(z < 0.02, f"zorn cannot mix a blue ({z:.3f} on the blue axis)")
    check(i > 3 * max(z, 1e-3), f"impressionist can ({i:.3f})")

    # The ground is NOT projected: a stroke is one mixture, a blurred gradient is not, and
    # quantising a smooth passage onto a few hundred mixtures would band it.
    cfg = PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, palette="zorn")
    gp = palette.params_for(cfg)
    flat = np.linspace(0.0, 1.0, 256, dtype=np.float32)[None, :, None] * np.ones((1, 1, 3),
                                                                                np.float32)
    graded = palette.grade_image(flat, gp)
    steps = len(np.unique(np.round(graded[0, :, 0], 4)))
    check(steps > 200, f"a smooth ramp stays smooth through the ground grade ({steps}/256)")


def test_regions_grade_one_passage_only():
    print("regions: the same grade, aimed at one part of the picture")
    img = _hue_image()
    h, w = img.shape[:2]

    def cfg_for(**kw):
        return PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, **kw)

    # The mask splits by ROW on a canvas that is taller than it is wide, which is the shape
    # that makes an (x, y) swap falsifiable -- the same reason the placed-vortex test uses a
    # non-square image. A mask indexed the wrong way round would still label something.
    top = np.zeros((h, w), dtype=np.int32)
    top[:h // 2] = 1

    cfg = cfg_for()
    base_sb, base_canvas, _ = plan(img, cfg)

    # INERT, and inert by SKIPPING: a mask nobody overrides is not a divided picture, so the
    # region path must not run at all. `params_for` returning None is what keeps this
    # bit-identical rather than merely close -- gather/grade/scatter with the base block is
    # the same arithmetic, but proving that is not a reason to spend it.
    check(regions_mod.params_for(cfg, None) is None, "no mask asks for no regions")
    check(regions_mod.params_for(cfg, {"labels": top, "overrides": {}}) is None,
          "and a mask with nothing overridden asks for none either")
    check(regions_mod.params_for(cfg, {"labels": top, "overrides": {1: {}}}) is None,
          "an empty override is not a region")
    a, _, _ = paint(img, cfg)
    b, ib, _ = paint(img, cfg, regions={"labels": top, "overrides": {1: {}}})
    check(bool(np.array_equal(a, b)) and ib["regions"] == 0,
          "so a picture with an inert mask is bit-identical to one with no mask")

    # THE EQUIVALENCE INVARIANT, and it is the one that proves the two paths are one path:
    # hand every region the base config's own values and the painting must come back
    # BIT-identical, canvas included. It is the counterpart of handing a flow preset its own
    # spiral back as placed vortices. It also pins the transport-inheritance rule: a region
    # that does not name its own reference reuses the base's already-fitted one, so there is
    # still exactly one fit in the picture and the subsets cannot drift apart.
    graded = cfg_for(palette="impressionist", warm_cool=0.3, hue_target=220.0,
                     hue_range=40.0, hue_rotate=-25.0, reference="great-wave",
                     reference_strength=0.7)
    same = {"palette": "impressionist", "warm_cool": 0.3}
    g_sb, g_canvas, _ = plan(img, graded)
    e_sb, e_canvas, e_info = plan(img, graded,
                                  regions={"labels": top, "overrides": {0: same, 1: same}})
    check(bool(np.array_equal(g_sb.rgb, e_sb.rgb)),
          "every region set to the base's own values repaints the same strokes exactly")
    check(bool(np.array_equal(g_canvas, e_canvas)),
          "and the same ground under them")

    # THE CLAIM. One half is graded, the other is not touched at all -- which is the whole
    # difference between this and a global grade, and it is checked on the strokes' own
    # labels rather than on pixels, because a stroke is what carries a colour here.
    live = {"labels": top, "overrides": {1: {"palette": "fauve", "pigment": 1.0}}}
    sb, canvas, info = plan(img, cfg, regions=live)
    lab = regions_mod.stroke_labels(regions_mod.params_for(cfg, live), sb.x, sb.y)
    check(bool(np.array_equal(sb.rgb[lab == 0], base_sb.rgb[lab == 0])),
          "the region nobody asked about keeps the photograph's own colour, exactly")
    moved = float(np.abs(sb.rgb[lab == 1] - base_sb.rgb[lab == 1]).mean())
    check(moved > 0.01, f"and the region that was asked is repainted ({moved:.3f})")
    # The axis check: the mask says the TOP half, so the strokes that moved must be the ones
    # with a small y. A transposed lookup labels a different set and this is what says so.
    ymax = float(sb.y[lab == 1].max()) if np.any(lab == 1) else h
    check(ymax <= h / 2 + base_sb.r_major.max(),
          f"the strokes it caught are the ones the mask covers (max y {ymax:.0f} of {h})")
    check(info["region_strokes"].get(1, 0) > 20,
          f"and the count is reported rather than assumed ({info.get('region_strokes')})")

    # GEOMETRY, which is the invariant the attachment point rests on. Same as `palette`'s,
    # one level up: a region is still only a colour, so nothing about where the paint went
    # may move -- and unlike the global grade this one reads `sb.x`/`sb.y`, so a version
    # that reached back into the allocator would fail here rather than merely look different.
    check(all(np.array_equal(getattr(sb, f), getattr(base_sb, f))
              for f in ("x", "y", "r_major", "r_minor", "theta", "phase")),
          "a live region moves no stroke, no radius, no angle and no draw")

    # REGION_COLOR_PARAMS IS A TRUE CLAIM, checked the way RELIGHT_PARAMS is: every field in
    # it must change the colour of its region and nothing else, and the near misses must be
    # refused outright. A wrong entry would not raise -- it would repaint one passage from a
    # quadtree the rest of the picture does not share. The flow half is the OPPOSITE claim
    # and is checked in its own test below.
    names = {f.name for f in fields(PaintConfig)}
    check(regions_mod.REGION_PARAMS <= names,
          f"every region field is a real PaintConfig field "
          f"(stray: {sorted(regions_mod.REGION_PARAMS - names)})")
    check(not (regions_mod.REGION_COLOR_PARAMS & regions_mod.REGION_FLOW_PARAMS),
          "and no field is in both halves, which would attach it at two seams at once")
    for name in sorted(regions_mod.REGION_COLOR_PARAMS):
        d = getattr(cfg, name)
        alt = ("zorn" if name == "palette" else "great-wave" if name == "reference"
               else d + (37.0 if abs(d) > 5.0 else 0.41))
        s2 = plan(img, cfg, regions={"labels": top, "overrides": {1: {name: alt}}})[0]
        check(all(np.array_equal(getattr(s2, f), getattr(base_sb, f))
                  for f in ("x", "y", "r_major", "r_minor", "theta", "phase")),
              f"'{name}' in a region is colour only ({d!r} -> {alt!r})")
    # The near misses, and they are now the fields that would want the QUADTREE back --
    # `flow` and its trims moved into the set when regions grew a structural half, so the
    # line between accepted and refused is "does this need the strokes re-allocated".
    for name in ("target_n", "max_cell", "kappa", "aniso_max", "seed"):
        check(name not in regions_mod.REGION_PARAMS,
              f"'{name}' is correctly NOT something a region may set")
        try:
            plan(img, cfg, regions={"labels": top, "overrides": {1: {name: 1}}})
            ok = False
        except ValueError:
            ok = True
        check(ok, f"...and asking for it is refused rather than silently ignored")

    # The artistic claim, as a number and per region: four pigments with no blue among them
    # cannot mix one THERE, while the same painting still can everywhere else. This is the
    # thing a global palette cannot do, so it is the thing worth measuring.
    zoned = plan(img, cfg, regions={"labels": top, "overrides": {
        1: {"palette": "zorn", "pigment": 1.0}}})[0]
    _, _, bb = palette._to_oklab(np.asarray(zoned.rgb, np.float64))
    inside, outside = float(np.max(-bb[lab == 1])), float(np.max(-bb[lab == 0]))
    check(inside < 0.02 and outside > 3 * max(inside, 1e-3),
          f"zorn on one region cannot mix a blue there ({inside:.3f}) while the rest "
          f"of the picture still can ({outside:.3f})")

    # A region the mask does not contain. It is the class of failure that produces NOTHING
    # to look at -- a mask painted at one size against a picture rendered at another catches
    # no strokes at all -- so it must be counted, not assumed, and must not throw on the way.
    miss = plan(img, cfg, regions={"labels": top, "overrides": {
        5: {"palette": "zorn"}}})
    check(bool(np.array_equal(miss[0].rgb, base_sb.rgb))
          and miss[2]["region_strokes"] == {5: 0},
          f"a region the mask does not contain paints nothing and says so "
          f"({miss[2].get('region_strokes')})")

    # The legend is a parser, not part of the transform, but it is the only way a mask on
    # disk becomes ids -- and every colour in it has to round-trip or a hand-painted region
    # silently becomes its neighbour.
    swatch = np.asarray(regions_mod.LEGEND, dtype=np.float32).reshape(1, -1, 3)
    check(list(regions_mod.labels_from_image(swatch)[0]) == list(range(len(regions_mod.LEGEND))),
          "every legend colour reads back as its own region id")
    # ...and it has to survive being SAVED: a mask that went through a lossy encoder, or an
    # editor's antialiased brush, must still land on the corner it is nearest to.
    noisy = np.clip(swatch + np.float32(0.18) * np.array(
        [[[1, -1, 1]]], dtype=np.float32), 0.0, 1.0)
    check(list(regions_mod.labels_from_image(noisy)[0]) == list(range(len(regions_mod.LEGEND))),
          "and still does with 0.18 of noise on it, which is why the legend is cube corners")


def test_regions_carry_a_flow_of_their_own():
    print("regions: van Gogh's sky over Monet's water, in one painting")
    img = _flow_image()
    h, w = img.shape[:2]

    def cfg_for(**kw):
        return PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, **kw)

    # Split by ROW on a canvas taller than it is wide -- the shape that makes an (x, y) swap
    # falsifiable, which is the same reason the placed-vortex test uses a non-square image.
    # It caught exactly that swap while this was being written.
    top = np.zeros((h, w), dtype=np.int32)
    top[:h // 2] = 1

    cfg = cfg_for()
    base_sb, base_canvas, _ = plan(img, cfg)
    lab = regions_mod.stroke_labels({"labels": top}, base_sb.x, base_sb.y)
    inside, outside = lab == 1, lab == 0

    # INERT, and inert by SKIPPING, on the flow half too: `flow_params_for` has to return
    # None when nothing asks, or an identity blend rebuilds every angle through atan2.
    check(regions_mod.flow_params_for(cfg, None) is None, "no mask asks for no flow regions")
    check(regions_mod.flow_params_for(
        cfg, {"labels": top, "overrides": {1: {"palette": "zorn"}}}) is None,
          "and a mask that only regrades asks for none either")
    a, ia, _ = paint(img, cfg)
    b, ib, _ = paint(img, cfg, regions={"labels": top, "overrides": {1: {"palette": "zorn"}}})
    check(ib["flow_regions"] == 0, "a colour-only region never enters the flow path")

    # THE CLAIM, and it is the mirror image of the colour half's: the strokes INSIDE the
    # region must move and the ones outside it must be BIT-identical. A structural control
    # that moved nothing looks exactly like one that moved everything a little, so both
    # halves have to be said.
    live = {"labels": top, "overrides": {1: {"flow": "starry"}}}
    sb, canvas, info = plan(img, cfg, regions=live)
    check(all(np.array_equal(getattr(sb, f)[outside], getattr(base_sb, f)[outside])
              for f in ("x", "y", "r_major", "r_minor", "theta", "rgb", "phase")),
          "the passage nobody asked about keeps the photograph's own brushwork, exactly")
    turned = float(np.abs(np.sin(sb.theta[inside] - base_sb.theta[inside])).mean())
    check(turned > 0.05, f"and the passage that was asked is re-combed ({turned:.3f})")
    check(info["flow_regions"] == 1 and info["regions"] == 1
          and info["region_strokes"].get(1, 0) > 20,
          f"counted rather than assumed ({info.get('region_strokes')})")
    # The axis check: the mask says the TOP half, so the strokes that moved are the small-y
    # ones. A transposed lookup labels a different set, and this is what says so.
    moved = np.flatnonzero(sb.theta != base_sb.theta)
    check(moved.size and float(base_sb.y[moved].max()) <= h / 2 + base_sb.r_major.max(),
          f"the strokes it caught are the ones the mask covers "
          f"(max y {float(base_sb.y[moved].max()):.0f} of {h})")

    # WHAT A REGION STILL MAY NOT MOVE. This is the whole reason the attachment point is
    # step 3 and not step 1: the leaves, the radii, the cell colours and the paint order are
    # decided by the rng stream, and a per-region flow draws no random number. So the
    # painting is re-combed in one passage and re-ALLOCATED nowhere.
    check(len(sb) == len(base_sb)
          and all(np.array_equal(getattr(sb, f), getattr(base_sb, f))
                  for f in ("rgb", "alpha", "phase")),
          "a live flow region moves no cell, no colour, no paint order and no draw")
    check(np.allclose(sb.r_major * sb.r_minor, base_sb.r_major * base_sb.r_minor,
                      rtol=1e-5),
          "and the elongation it does change is area-preserving, as everywhere else")

    # THE EQUIVALENCE INVARIANT, the counterpart of the colour half's and of handing a flow
    # preset its own spiral back as placed vortices: give every region the base config's own
    # flow and the painting must come back BIT-identical, canvas included. It is what makes
    # "one code path" a fact rather than a hope -- the gather/scatter has to be exactly the
    # whole-array arithmetic, not merely close to it.
    styled = cfg_for(flow="starry", flow_strength=0.8, flow_coh=-0.2, flow_drift=0.3,
                     palette="impressionist")
    same = {"flow": "starry", "flow_strength": 0.8, "flow_coh": -0.2, "flow_drift": 0.3}
    g_sb, g_canvas, _ = plan(img, styled)
    e_sb, e_canvas, _ = plan(img, styled,
                             regions={"labels": top, "overrides": {0: same, 1: same}})
    check(all(np.array_equal(getattr(g_sb, f), getattr(e_sb, f))
              for f in ("x", "y", "r_major", "r_minor", "theta", "rgb", "phase")),
          "every region set to the base's own flow repaints the same strokes exactly")
    check(bool(np.array_equal(g_canvas, e_canvas)), "and the same ground under them")

    # THE CARVE-OUT, which is the thing a global flow cannot do at any setting: one passage
    # left alone under a swirling picture. Served by SKIPPING those strokes, so what they
    # come back to is the unstyled painting's own angle, bit for bit -- not an approximation
    # of it. The base's drift is cancelled by its own trim so the two sets line up by index.
    still = cfg_for(flow="starry", flow_drift=-flow.PRESETS["starry"]["drift"])
    all_sb, _, _ = plan(img, still)
    carved, _, ci = plan(img, still,
                         regions={"labels": top, "overrides": {1: {"flow": "none"}}})
    check(bool(np.array_equal(carved.x, base_sb.x)),
          "with drift trimmed off nothing has moved, so the two labellings line up")
    check(bool(np.array_equal(carved.theta[inside], base_sb.theta[inside])),
          "a region set to flow='none' comes back to the picture's own angle, exactly")
    check(bool(np.array_equal(carved.theta[outside], all_sb.theta[outside]))
          and not np.allclose(carved.theta[outside], base_sb.theta[outside]),
          "while the rest of the picture keeps swirling")
    check(ci["flow_regions"] == 1,
          "and 'none' in a region is a live region, not an absent one")

    # THE ARTISTIC CLAIM, as a number, and it is the one the feature exists for: two
    # painters in one picture. Elongation is driven by coherence, so what separates van
    # Gogh's ribbons from Monet's dabs at a glance is a RATIO, and a global flow has exactly
    # one of them. `flow_coh` is what carries it, which is why it is in REGION_FLOW_PARAMS.
    two, _, _ = plan(img, cfg_for(flow="starry"),
                     regions={"labels": top, "overrides": {1: {"flow": "waterlily"}}})
    el = two.r_major / two.r_minor
    dabs, ribbons = float(el[inside].mean()), float(el[outside].mean())
    check(dabs < 0.6 * ribbons,
          f"Monet's dabs in one passage and van Gogh's ribbons in the other, in one "
          f"painting ({dabs:.2f} against {ribbons:.2f})")

    # REGION_FLOW_PARAMS IS A TRUE CLAIM, checked the way the colour half is but demanding
    # the opposite: each field must MOVE its own passage and leave the rest bit-identical. A
    # stray entry here would name something `flow.params_for` does not read, and would then
    # be a control the CLI accepts and nothing honours.
    lit = cfg_for(flow="starry")
    lit_sb, _, _ = plan(img, lit)
    for name in sorted(regions_mod.REGION_FLOW_PARAMS):
        d = getattr(lit, name)
        alt = "waterlily" if name == "flow" else (d + 0.37 if name != "flow_rot"
                                                  else d + 47.0)
        s2 = plan(img, lit, regions={"labels": top, "overrides": {1: {name: alt}}})[0]
        # Outside is bit-identical INCLUDING the positions, so a field that leaked into the
        # rng stream or into the tree would fail here rather than look different.
        untouched = all(np.array_equal(getattr(s2, f)[outside], getattr(lit_sb, f)[outside])
                        for f in ("x", "y", "r_major", "r_minor", "theta", "rgb", "phase"))
        # Over the whole of the geometry a flow can touch, not over `theta` alone: `flow_coh`
        # changes the mark's SHAPE and `flow_drift` moves it, and neither turns it. A check
        # written against the angle would have passed those two by saying nothing.
        moved_here = [f for f in ("x", "y", "theta", "r_major", "r_minor")
                      if not np.array_equal(getattr(s2, f)[inside],
                                            getattr(lit_sb, f)[inside])]
        check(untouched and moved_here,
              f"'{name}' in a region moves {moved_here or 'nothing'} in that passage and "
              f"only that one ({d!r} -> {alt!r})")

    # WHERE THE SWIRLS GO IS PER PASSAGE TOO, and it had to become so: once the flow could
    # differ per region, one global list meant a swirl placed in the sky was also read by
    # the field painting the ground. `vortex_map` takes either shape, and a plain list -- what
    # every caller that predates regions passes -- is still exactly the base's set.
    V = [(0.30, 0.20), (0.72, 0.35)]
    U = [(0.20, 0.80), (0.55, 0.90), (0.40, 0.55)]
    check(flow.vortex_map(None) == {} and flow.vortex_map(V) == {0: tuple(V)},
          "a plain list of points is the base's set, and None is nobody's")
    check(flow.centres_for(flow.vortex_map(V), 1) == tuple(V),
          "a region with no set of its own INHERITS the base's")
    check(flow.centres_for(flow.vortex_map({0: V, 1: U}), 1) == tuple(U),
          "...and one with its own uses that instead")
    check(flow.centres_for(flow.vortex_map({0: V, 1: []}), 1) == (),
          "...while an explicitly empty set is the spiral, which is the third state")

    swirl = cfg_for(flow="starry")
    placed, _, pi = plan(img, swirl, vortices=V)
    check(pi["flow_vortices"] == 2 and bool(np.array_equal(
        placed.theta, plan(img, swirl, vortices={0: V})[0].theta)),
          "a plain list and {0: list} are the same painting, so no caller had to change")

    # THE EQUIVALENCE INVARIANT AGAIN, and this is the arm that forced the inheritance rule
    # rather than leaving it a preference: a region given the base's own flow and no set of
    # its own must reproduce the base's painting BIT for bit. A region that fell back to the
    # preset's SPIRAL here would be a different picture, silently.
    st = {"flow": "starry"}
    inh, inh_c, ii = plan(img, swirl, vortices=V,
                          regions={"labels": top, "overrides": {0: st, 1: st}})
    check(all(np.array_equal(getattr(inh, f), getattr(placed, f))
              for f in ("x", "y", "theta", "r_major", "r_minor", "rgb")),
          "a region inheriting the base's centres repaints the base's painting exactly")
    check(ii["flow_vortices"] == 2,
          f"and they are counted ONCE, not once per region that read them "
          f"({ii['flow_vortices']})")
    # Handing the region the base's list EXPLICITLY has to be the same painting as
    # inheriting it -- the proof that the two are one code path rather than two that agree.
    exp = plan(img, swirl, vortices={0: V, 1: V},
               regions={"labels": top, "overrides": {0: st, 1: st}})[0]
    check(bool(np.array_equal(exp.theta, inh.theta)),
          "and handing it that same list explicitly is the same painting again")

    # A SET OF ITS OWN moves that passage and nothing else. The mask splits by ROW on a
    # canvas taller than it is wide, so a placement converted through the wrong side of the
    # frame -- the failure `field_centres` exists to prevent -- lands somewhere else here.
    ownc, _, oi = plan(img, swirl, vortices={0: V, 1: U},
                       regions={"labels": top, "overrides": {1: st}})
    check(all(np.array_equal(getattr(ownc, f)[outside], getattr(placed, f)[outside])
              for f in ("x", "y", "theta", "r_major", "r_minor", "rgb")),
          "a region's own swirl centres leave the rest of the picture untouched")
    check(not np.array_equal(ownc.theta[inside], placed.theta[inside]),
          "...and re-comb its own passage around them")
    check(oi["flow_vortices"] == 5,
          f"both sets are counted, once each ({oi['flow_vortices']} = 2 + 3)")

    # And the mask must reach the UNDERPAINTING too, or a covering ground painted with the
    # base's field shows through the gaps of a passage combed the other way.
    ub = cfg_for(base="strokes", flow="hatch")
    u0, _, ui0 = plan(img, ub)
    u1, _, ui1 = plan(img, ub, regions={"labels": top, "overrides": {
        1: {"flow": "waterlily"}}})
    n_under = ui0["n_under"]
    check(n_under > 0 and not np.array_equal(u1.theta[:n_under], u0.theta[:n_under]),
          f"the ground under a re-combed passage is re-combed with it ({n_under} strokes)")


def _flow_image(h=260, w=180, seed=2):
    """A scene with both regimes in it: smooth gradients the tensor is confident about,
    and one hard-edged block it is very confident about. The flow field has to be judged
    against a picture that already has a direction of its own, not against noise."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    im = np.stack([0.45 + 0.35 * np.sin(xx / 29.0), 0.5 + 0.3 * np.cos(yy / 23.0),
                   0.5 + 0.25 * np.sin((xx - yy) / 37.0)], -1)
    im[60:140, 40:120] += 0.2 * np.sign(np.sin(xx[60:140, 40:120] / 2.5))[:, :, None]
    return np.clip(im + rng.normal(0, 0.01, im.shape), 0, 1).astype(np.float32)


def _flow_cfg(**kw):
    return PaintConfig(target_n=1200, max_cell=32, min_cell=4, seed=5, **kw)


def test_flow_is_inert_and_placement_preserving():
    print("flow: the field turns and slides the marks, and moves nothing else")
    img = _flow_image()
    cfg = _flow_cfg()
    check(flow.params_for(cfg) is None, "the default config asks for no flow field at all")
    a, _, _ = paint(img, cfg)
    # A preset at strength 0 is the same statement a second way: the skip has to happen in
    # `params_for`. An identity block would still rebuild theta through atan2 and move the
    # last bit of every angle.
    off = _flow_cfg(flow="starry", flow_strength=0.0)
    check(bool(np.array_equal(a, paint(img, off)[0])),
          "a preset at strength 0 is bit-identical to no field at all")
    # And the trims cannot act on their own: with no preset there is no field to shape.
    trims = _flow_cfg(flow_coh=0.5, flow_rot=90.0, flow_drift=0.8, flow_scale=1.0)
    check(bool(np.array_equal(a, paint(img, trims)[0])),
          "every trim pushed, with flow='none', is still bit-identical")

    base = plan(img, cfg)[0]
    for name in flow.FLOWS:
        if name == "none":
            continue
        sb = plan(img, _flow_cfg(flow=name))[0]
        # THE invariant the placement rests on, and the mirror of the palette's. The field
        # runs inside the one step of `from_cells` that draws no random numbers, so it
        # cannot move a leaf, a radius, a colour or the paint order. An implementation that
        # steered the QUADTREE instead would fail here rather than merely looking different.
        same = (len(sb) == len(base)
                and all(np.array_equal(getattr(sb, f), getattr(base, f))
                        for f in ("rgb", "alpha", "phase")))
        # Area-preserving, so the field changed stroke SHAPE and not stroke SIZE -- which
        # is what keeps the coverage statistics comparable across an A/B of it.
        area = np.allclose(sb.r_major * sb.r_minor, base.r_major * base.r_minor, rtol=1e-5)
        moved = float(np.abs(np.sin(sb.theta - base.theta)).mean())
        check(same and area and moved > 0.05,
              f"'{name}': same cells, same paint, same painted area; turned by "
              f"mean |sin d.theta| {moved:.3f}")

    # DRIFT is the one term that moves a mark, so it has to be separable from the turning.
    # Cancelled by ITS OWN preset's value, not by a hard-coded one: what is being tested is
    # that the slider can undo what the preset asked for, which is the rule every trim in
    # this repo owes (.claude/skills/artistic-controls). x + (-x) is exactly 0 in floating
    # point, so this really is drift off and not drift nearly off.
    lo = next(row[3] for row in SCHEMA if row[1] == "flow_drift")
    worst = max(q["drift"] for q in flow.PRESETS.values())
    check(lo <= -worst,
          f"the flow_drift slider reaches far enough to cancel every preset's own drift "
          f"({lo} vs the largest, {worst})")
    no_drift = plan(img, _flow_cfg(flow="starry",
                                   flow_drift=-flow.PRESETS["starry"]["drift"]))[0]
    check(bool(np.array_equal(no_drift.x, base.x) and np.array_equal(no_drift.y, base.y)),
          "drift trimmed to zero moves no centre AT ALL, while the angles still change")
    drifted = plan(img, _flow_cfg(flow="starry"))[0]
    check(not np.array_equal(drifted.x, base.x)
          and np.array_equal(drifted.rgb, base.rgb),
          "the preset's own drift moves the marks but not the paint they carry")


def test_flow_vortices_can_be_placed_by_hand():
    print("flow: the swirl centres can be placed, and land where they were put")
    img = _flow_image()
    h, w = img.shape[:2]
    cfg = _flow_cfg(flow="starry")
    base = plan(img, cfg)[0]

    # THE test that the two paths are one path: hand the preset its OWN spiral back as
    # placed points and the painting must be bit-identical, not merely similar. A second
    # copy of the vortex arithmetic on the placed side would pass every other check here
    # and fail this one.
    p = flow.params_for(cfg)
    px, py = flow.spiral_centres(p)
    s = float(min(h, w))
    same_pts = list(zip((px * s + 0.5 * w) / w, (py * s + 0.5 * h) / h))
    echo = plan(img, cfg, vortices=same_pts)[0]
    check(all(np.array_equal(getattr(echo, f), getattr(base, f))
              for f in ("x", "y", "r_major", "r_minor", "theta", "rgb", "phase")),
          "the spiral, handed back in as placed points, is bit-identical to the spiral")

    # Empty is "nothing placed", which is the spiral -- NOT "no vortices", which would
    # leave the background drift alone and paint a uniform sweep. Clearing the markers on
    # the page has to give the preset back.
    check(np.array_equal(plan(img, cfg, vortices=[])[0].theta, base.theta),
          "an empty list means the preset's own spiral, not a field with no swirls in it")

    # A placed centre lands WHERE IT WAS PUT: the field winds a full turn around that exact
    # pixel and around no other. The frame conversion is the thing being tested, and it is
    # only falsifiable on a NON-SQUARE canvas -- dividing by the wrong side, or forgetting
    # to centre, still looks plausible until the winding is measured somewhere specific.
    check(h != w, f"the test image is non-square ({h}x{w}), so the frame is falsifiable")
    want = [(0.30, 0.25), (0.72, 0.68)]
    pv = flow.params_for(cfg, want)
    cu, cv = flow.field_centres(pv, h, w)
    circ = np.linspace(0.0, 2.0 * np.pi, 721)
    probe = 0.35 * pv["scale"]
    for i, (fx, fy) in enumerate(want):
        # Where norm_coords says that fraction is -- and the vortex has to be THERE.
        eu, ev = flow.norm_coords(np.array([fy * h]), np.array([fx * w]), h, w)
        check(abs(cu[i] - eu[0]) < 1e-12 and abs(cv[i] - ev[0]) < 1e-12,
              f"placed centre {i + 1} converts to the same frame the strokes use")
        a = flow.flow_dir(cu[i] + probe * np.cos(circ), cv[i] + probe * np.sin(circ), pv,
                          (cu, cv))
        step = (np.diff(a) + np.pi) % (2.0 * np.pi) - np.pi
        turns = float(step.sum() / (2.0 * np.pi))
        check(abs(turns - 1.0) < 0.02,
              f"the field winds a full turn around placed centre {i + 1} "
              f"({turns:.3f} turns)")

    # Click order is the handedness: they alternate, which is what the page's markers draw.
    hands = []
    for i in range(len(want)):
        du, dv = probe * np.cos(circ), probe * np.sin(circ)
        a = flow.flow_dir(cu[i] + du, cv[i] + dv, pv, (cu, cv))
        hands.append(float(np.sign(np.mean(du * np.sin(a) - dv * np.cos(a)))))
    check(hands[0] == -hands[1],
          f"click order sets the spin, and neighbours counter-rotate "
          f"({''.join('+' if x > 0 else '-' for x in hands)})")

    # A field kind with no vortices must be left alone rather than half-applied.
    wcfg = _flow_cfg(flow="waterlily")
    check(np.array_equal(plan(img, wcfg, vortices=want)[0].theta,
                         plan(img, wcfg)[0].theta),
          "placed points handed to a 'wave' preset leave it bit-identical")

    # And they cannot move anything the tree decided, exactly as the presets cannot.
    placed = plan(img, cfg, vortices=want)[0]
    check(np.array_equal(placed.rgb, base.rgb) and len(placed) == len(base)
          and not np.allclose(placed.theta, base.theta),
          "placed centres re-aim the marks and leave every colour and cell alone")

    # The cap is the engine's, and it truncates rather than failing: the page is told the
    # number (it rides the worker's ready message) but a CLI caller is not.
    over = [(0.5, 0.5)] * (flow.MAX_VORTICES + 5)
    check(len(flow.params_for(cfg, over)["vortices"]) == flow.MAX_VORTICES,
          f"more than MAX_VORTICES ({flow.MAX_VORTICES}) placed points are truncated")


def test_flow_presets_make_their_claim():
    print("flow: each preset does the thing its name promises")
    img = _flow_image()
    h, w = img.shape[:2]
    base = plan(img, _flow_cfg())[0]

    def stats(name):
        cfg = _flow_cfg(flow=name)
        sb = plan(img, cfg)[0]
        p = flow.params_for(cfg)
        u, v = flow.norm_coords(sb.y, sb.x, h, w)
        # |cos 2d| is the doubled-angle parallelism: 1 when the mark lies along the field,
        # 0 when it crosses it. The right statistic for `starry`, whose swirls are of every
        # direction at once -- a spread or a mean angle would say nothing about them.
        par = float(np.abs(np.cos(2.0 * (sb.theta - flow.flow_dir(u, v, p)))).mean())
        t = np.arctan2(np.sin(2.0 * sb.theta), np.cos(2.0 * sb.theta)) / 2.0
        return par, float((sb.r_major / sb.r_minor).mean()), \
            float(np.cos(2.0 * t).mean()), float(np.degrees(t).std())

    b_ratio = float((base.r_major / base.r_minor).mean())
    tb = np.arctan2(np.sin(2.0 * base.theta), np.cos(2.0 * base.theta)) / 2.0
    b_horiz, b_sd = float(np.cos(2.0 * tb).mean()), float(np.degrees(tb).std())

    # starry -- THE claim the word "swirl" makes, and the only one that actually tests it:
    # the field's direction winds a full turn on a circuit around each vortex centre. A
    # uniform flow winds 0, and a field of parallel marks that merely looked wavy would too.
    p = flow.params_for(_flow_cfg(flow="starry"))
    rot, nv = np.deg2rad(p["rot"]), int(p["n"])
    circ = np.linspace(0.0, 2.0 * np.pi, 721)
    winds, hands = [], []
    for k in range(nv):
        ang = k * flow.GOLDEN_ANGLE + rot
        rad = p["spread"] * np.sqrt((k + 0.5) / nv)
        cu, cv = rad * np.cos(ang), rad * np.sin(ang)
        probe = 0.35 * p["scale"]
        du, dv = probe * np.cos(circ), probe * np.sin(circ)
        a = flow.flow_dir(cu + du, cv + dv, p)
        step = (np.diff(a) + np.pi) % (2.0 * np.pi) - np.pi
        winds.append(float(step.sum() / (2.0 * np.pi)))
        # Handedness: the sign of radial x flow. Alternating, so neighbouring swirls
        # counter-rotate -- a field of same-handed vortices reads as one rotating disc.
        hands.append(float(np.sign(np.mean(du * np.sin(a) - dv * np.cos(a)))))
    check(all(abs(x - 1.0) < 0.02 for x in winds),
          f"starry: the field winds a full turn around every one of its {nv} vortices "
          f"({min(winds):.3f}..{max(winds):.3f} turns)")
    check(all(hands[k] == -hands[k + 1] for k in range(nv - 1)),
          f"starry: neighbouring vortices counter-rotate ({''.join('+' if x > 0 else '-' for x in hands)})")
    s_par, s_ratio, _, _ = stats("starry")
    check(s_par > 0.8,
          f"starry: the marks lie ALONG the field they were given (|cos 2d| {s_par:.3f})")

    # waterlily -- two claims, and they have to hold at once or the preset is not Monet.
    # cos(2*theta) is +1 for a horizontal mark and -1 for a vertical one.
    w_par, w_ratio, w_horiz, _ = stats("waterlily")
    check(w_horiz > b_horiz + 0.3,
          f"waterlily: the marks lie down onto the water plane "
          f"(mean cos 2.theta {b_horiz:+.3f} -> {w_horiz:+.3f})")
    check(w_ratio < 0.65 * b_ratio,
          f"waterlily: and they are DABS, not ribbons "
          f"(mean elongation {b_ratio:.2f} -> {w_ratio:.2f})")

    # hatch -- Cezanne's constructive stroke is one direction laid over everything, so the
    # test is that the SPREAD collapses. The other two must not pass this one.
    _, _, _, h_sd = stats("hatch")
    check(h_sd < 0.45 * b_sd,
          f"hatch: the angles collapse onto one diagonal "
          f"(spread {b_sd:.1f} deg -> {h_sd:.1f} deg)")
    check(stats("starry")[3] > 0.8 * b_sd,
          f"hatch's collapse is its own: starry keeps every direction "
          f"({stats('starry')[3]:.1f} deg)")


def _prelight(img, cfg):
    """Everything the rasteriser produces, BEFORE any lighting. The buffers the relight
    fast path serves again."""
    sb, canvas, info = plan(img, cfg)
    r = render(sb, *img.shape[:2], hard=cfg.hard, hard_r=cfg.hard_r,
               wobble_amp=cfg.wobble_amp, canvas=canvas, split_at=info["n_under"],
               bristle_amp=cfg.bristle_amp, taper_amp=cfg.taper_amp,
               fringe_px=cfg.fringe_px, want_height=cfg.impasto,
               impasto_relief=cfg.impasto_relief, impasto_layer=cfg.impasto_layer,
               ground=0.0 if cfg.base == "none" else ground_height(cfg.impasto_layer))
    return (r[0], r[1], r[2], r[-1] if cfg.impasto else None)


def test_relight_params_is_a_true_claim():
    print("relight: the lighting-only parameter set is what it says it is")
    img = _flow_image()
    cfg = _flow_cfg()
    base = _prelight(img, cfg)

    # THE CLAIM THE FAST PATH RESTS ON, and the reason it is checked rather than asserted in
    # a comment: a parameter wrongly listed here would be served 42x faster out of the
    # pre-lighting cache and produce a picture that is silently IDENTICAL to the one before
    # the slider moved. Nothing about looking at it would say so -- there is no wrong
    # painting to see, only a control that appears to do nothing.
    for name in sorted(RELIGHT_PARAMS):
        d = getattr(cfg, name)
        alt = d + (37.0 if abs(d) > 5.0 else 0.31)
        same = all(np.array_equal(a, b)
                   for a, b in zip(base, _prelight(img, replace(cfg, **{name: alt}))))
        check(same, f"'{name}' cannot move a stroke ({d} -> {alt}), so a frame may reuse "
                    f"the rasterisation")

    # The other direction, on the near misses rather than on obviously-different knobs.
    # `impasto_relief` and `impasto_layer` are the trap: they are impasto parameters, they
    # sit next to the lighting in `PaintConfig`, and they are read by the RASTERISER when it
    # builds the height field. Listing either would be the easy mistake.
    for name in ("impasto_relief", "impasto_layer", "wobble_amp", "bristle_amp",
                 "taper_amp", "fringe_px", "hard_r"):
        check(name not in RELIGHT_PARAMS, f"'{name}' is correctly NOT in the set")
        d = getattr(cfg, name)
        moved = not all(np.array_equal(a, b)
                        for a, b in zip(base, _prelight(img, replace(cfg, **{name: d + 0.29}))))
        check(moved, f"...because changing '{name}' really does change what was rastered")

    # And `linear` and `impasto`, which `finish` reads but which are NOT relightable: the
    # first also chooses the working colour space back in `plan`, the second decides whether
    # a height field is allocated at all.
    for name in ("linear", "impasto"):
        check(name not in RELIGHT_PARAMS,
              f"'{name}' is read by finish() but is correctly not relightable")

    names = {f.name for f in fields(PaintConfig)}
    check(RELIGHT_PARAMS <= names,
          f"every name in the set is a real PaintConfig field "
          f"(stray: {sorted(RELIGHT_PARAMS - names)})")


def test_pipeline_determinism():
    print("pipeline: same seed -> same painting")
    img = synthetic()
    cfg = PaintConfig(target_n=400, min_cell=8, max_cell=64, seed=7)
    a, _, _ = paint(img, cfg)
    b, _, _ = paint(img, cfg)
    check(bool(np.array_equal(a, b)), "deterministic under a fixed seed")
    c, _, _ = paint(img, PaintConfig(target_n=400, min_cell=8, max_cell=64, seed=8))
    check(not np.array_equal(a, c), "a different seed gives a different painting")


def test_project_round_trips():
    print("project: one file carries the whole setup, and carries it back")
    import io as _io
    from PIL import Image
    from oilpaint import project as proj

    # A setup with all five parts in it: fields, per-layer overrides, per-layer swirl
    # centres, a region mask and a foveal map.
    cfg = PaintConfig(target_n=1234, palette="zorn", palette_strength=0.65, seed=11)
    overrides = {1: {"flow": "starry", "flow_strength": 0.8, "palette": "nocturne"},
                 2: {"hue_rotate": -12.0}}
    vortices = {0: [], 1: [(0.25, 0.1), (0.6, 0.2)]}
    lab = np.zeros((32, 48, 3), dtype="uint8")
    lab[:10] = (255, 0, 0)
    lab[24:] = (0, 255, 0)
    b = _io.BytesIO(); Image.fromarray(lab).save(b, "PNG")
    fov = (np.linspace(0, 255, 32 * 48).reshape(32, 48)).astype("uint8")
    c = _io.BytesIO(); Image.fromarray(fov, "L").save(c, "PNG")

    doc = proj.to_dict(cfg, overrides, vortices, b.getvalue(), c.getvalue(),
                       source="photo.jpg", note="a test")
    got = proj.from_dict(doc)

    # EVERY field, not a spot check: the whole claim of the format is that it is a complete
    # record, and a round trip that compared three of them would pass a writer that dropped
    # the other sixty.
    back = got.config()
    bad = [f.name for f in fields(PaintConfig)
           if getattr(back, f.name) != getattr(cfg, f.name)]
    check(not bad, f"every PaintConfig field survives the round trip ({bad[:4]})")
    # `tau` defaults to None and means "search", which is not the same painting as 0.0 --
    # so the one optional field is the one most worth naming.
    check(back.tau is None, "an unset optional field comes back unset, not zeroed")
    check(got.overrides == {1: {"flow": "starry", "flow_strength": 0.8,
                                "palette": "nocturne"},
                            2: {"hue_rotate": -12.0}}, "per-layer overrides survive")
    check(got.vortices == {0: [], 1: [(0.25, 0.1), (0.6, 0.2)]},
          "per-layer swirl centres survive, empty sets included")
    check(got.region_mask is not None and got.region_mask.size == (48, 32),
          "the region mask comes back at its own size")
    check(got.foveal is not None and got.foveal.mode == "L",
          "and the foveal map comes back as grey")
    # The mask is LABELS, so it has to survive as labels rather than as something close.
    got_lab = np.asarray(got.region_mask, dtype="float32") / 255.0
    ids = set(np.unique(regions_mod.labels_from_image(got_lab)).tolist())
    check(ids == {0, 1, 2}, f"and its passages are still 0, 1 and 2 ({sorted(ids)})")

    # FLAGS WIN OVER THE FILE, which is what makes a project a starting point.
    check(got.config({"target_n": 99}).target_n == 99, "an explicit value overrides the file")
    check(got.config({"target_n": None}).target_n == 1234,
          "and an absent one does not")

    # A PARTIAL FILE IS VALID -- three fields and the defaults for the rest, which is what
    # makes the format writable by hand.
    thin = proj.from_dict({"format": proj.FORMAT, "version": 1,
                           "params": {"target_n": 77}})
    check(thin.config().target_n == 77 and thin.config().seed == PaintConfig().seed,
          "a partial file means those fields and the defaults for the rest")

    # ...and the refusals. Each of these would otherwise be a wrong picture rather than an
    # error: a stray field repaints one passage from a quadtree the rest does not share,
    # and a future version could mean anything at all.
    for bad_doc, why in (
        ({"format": "something-else"}, "a file that is not a project"),
        ({"format": proj.FORMAT, "version": proj.VERSION + 1}, "a version from the future"),
        ({"format": proj.FORMAT, "regions": {"1": {"target_n": 10}}},
         "a per-layer field the engine cannot vary per layer"),
        ({"format": proj.FORMAT, "regions": {"99": {"palette": "zorn"}}},
         "a region id outside the legend"),
        ({"format": proj.FORMAT, "vortices": [[0.1]]}, "a swirl centre that is not a pair"),
    ):
        try:
            proj.from_dict(bad_doc)
            check(False, f"refused: {why}")
        except proj.ProjectError:
            check(True, f"refused: {why}")

    # A BUNDLE IS THE SAME PROJECT WITH ITS MASKS AS FILES. Embedded base64 makes one
    # self-contained document, which is what an example committed to a repository has to
    # be; it is also the one form an image editor cannot open, so a mask still being
    # worked on wants the other. Both load through `load`, which is what makes the choice
    # about how you want to WORK rather than a fork in the format.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "valley.oilpaint.json")
        wrote = proj.save_bundle(path, doc)
        names = [os.path.basename(w) for w in wrote]
        check(names == ["valley.oilpaint.json", "valley.regions.png", "valley.foveal.png"],
              f"save_bundle writes the recipe and one file per mask ({names})")
        with open(path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        check(on_disk["masks"] == {"regions": "valley.regions.png",
                                   "foveal": "valley.foveal.png"},
              f"and the document points at them by name ({on_disk['masks']})")
        # The caller's document is NOT mutated: exporting a project must not quietly turn
        # the copy in memory into one whose masks live in a directory it never chose.
        check(str(doc["masks"]["regions"]).startswith("data:"),
              "without rewriting the document it was handed")

        back = proj.load(path)
        check(back.region_mask is not None and back.foveal is not None,
              "a bundle loads its masks from beside the file")
        # THE SAME PIXELS, not merely two images that opened. A bundle that silently
        # re-encoded a label mask would invent passages along every boundary.
        a = np.asarray(got.region_mask, dtype="uint8")
        b = np.asarray(back.region_mask, dtype="uint8")
        check(a.shape == b.shape and bool((a == b).all()),
              "and they are the same pixels the embedded form carried")
        # Idempotent: a bundle re-exported stays a bundle, and the masks are not rewritten.
        again = proj.save_bundle(path, on_disk)
        check(len(again) == 1,
              f"re-exporting a bundle rewrites only the document ({len(again)} file)")

        # A REFERENCE NOTHING SATISFIES IS AN ERROR WITH THE NAME IN IT, not a project that
        # loads with no passages -- which is what an empty mask also looks like.
        os.remove(os.path.join(td, "valley.regions.png"))
        try:
            proj.load(path)
            check(False, "refused: a bundle whose mask file is missing")
        except proj.ProjectError as e:
            check("valley.regions.png" in str(e),
                  "refused: a bundle whose mask file is missing, and it names the file")


def test_project_paints_what_it_describes():
    print("project: the file and the flags paint the same picture")
    from oilpaint import project as proj
    img = synthetic()
    cfg = PaintConfig(target_n=400, min_cell=8, max_cell=64, seed=5,
                      palette="impressionist", palette_strength=0.7)
    want, _, _ = paint(img, cfg)
    # Through the document and back, which is the path every project file takes.
    got_cfg = proj.from_dict(proj.to_dict(cfg)).config()
    got, _, _ = paint(img, got_cfg)
    # BIT-IDENTICAL, not close. A format that is a lossy record of a setup is a format that
    # quietly produces a different painting from the one it claims to describe.
    check(bool(np.array_equal(want, got)),
          "a painting from a round-tripped project is bit-identical")


if __name__ == "__main__":
    for fn in (
        test_quadtree_partition,
        test_budget_monotone,
        test_tau_floor_resists_noise,
        test_all_metrics_run,
        test_tensor_orientation,
        test_render_falloff,
        test_paint_texture_inert_at_zero,
        test_fringe_is_scale_invariant,
        test_taper_breaks_the_symmetry,
        test_height_field_builds_up,
        test_lighting_is_a_noop_on_flat_paint,
        test_occlusion_and_view_dependence,
        test_edge_alignment_separates,
        test_flat_brush_sweeps_a_gradient,
        test_flat_brush_leaves_real_edges_alone,
        test_flat_blend_hands_over_on_confidence,
        test_prefilter_recovers_a_buried_gradient,
        test_underpainting_covers,
        test_foveal_map_is_inert_unless_asked_for,
        test_foveal_map_moves_the_budget,
        test_palette_is_inert_and_geometry_preserving,
        test_hue_band_steers_one_family_only,
        test_reference_transport_matches_the_painting,
        test_pigment_projection_respects_the_palette,
        test_regions_grade_one_passage_only,
        test_regions_carry_a_flow_of_their_own,
        test_flow_is_inert_and_placement_preserving,
        test_flow_presets_make_their_claim,
        test_flow_vortices_can_be_placed_by_hand,
        test_relight_params_is_a_true_claim,
        test_pipeline_determinism,
        test_project_round_trips,
        test_project_paints_what_it_describes,
    ):
        fn()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("all passed")
